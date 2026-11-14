"""异步任务管理器：提交 / 进度 / 结果三态管理 + 断点续跑。

设计要点：
1. 用 ThreadPoolExecutor 在后台线程执行同步的 run_pipeline（LLM 调用是阻塞的，
   FastAPI 端点用普通 def 由框架调度到线程池，避免阻塞事件循环）。
2. 每个 job 独立的 SQLite checkpoint 文件 + 独立 output 目录，job_id 作为
   thread_id 实现断点续跑；独立文件避免多 job 并发写同一 SQLite 的锁冲突。
3. 内存任务表 + 线程锁，记录状态 / 已完成节点 / 结果 / 错误，供 status/result 查询。

稳定性保障：
- max_workers 限制并发，超出排队（ThreadPoolExecutor 内部队列），避免 LLM 调用雪崩；
- 任务异常被捕获写入 job.error，不使 worker 线程崩溃；
- 进度通过 run_pipeline 的 progress_callback 逐节点上报。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from service import run_pipeline

# 图拓扑节点总数（用于估算进度）：disclosure/parse/search/outline/
# claims/description/abstract/diagram/merge，search 可能被条件边跳过。
TOTAL_NODES = 9


@dataclass
class JobStatus:
    """单个任务的运行时状态。"""

    job_id: str
    status: str = "pending"  # pending / running / succeeded / failed
    completed_nodes: List[str] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None

    @property
    def progress(self) -> float:
        """按已完成节点数估算进度，上限 1.0。"""
        return min(1.0, len(self.completed_nodes) / TOTAL_NODES)


class JobManager:
    """任务管理器（由 app.py 实例化并持有）。"""

    def __init__(
        self,
        max_workers: int = 3,
        checkpoint_dir: str = "jobs/checkpoints",
        output_root: str = "output",
    ) -> None:
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="patent-job"
        )
        self._jobs: Dict[str, JobStatus] = {}
        self._lock = threading.Lock()
        self._checkpoint_dir = checkpoint_dir
        self._output_root = output_root
        os.makedirs(checkpoint_dir, exist_ok=True)
        os.makedirs(output_root, exist_ok=True)

    def submit(self, **pipeline_kwargs: Any) -> str:
        """提交一个生成任务，立即返回 job_id（后台异步执行）。"""
        job_id = uuid.uuid4().hex[:12]
        # 每个 job 独立输出目录与 checkpoint 文件，避免产物/断点相互覆盖及 SQLite 锁冲突
        pipeline_kwargs["output_dir"] = os.path.join(self._output_root, job_id)
        pipeline_kwargs["checkpoint_path"] = os.path.join(
            self._checkpoint_dir, f"{job_id}.sqlite"
        )
        pipeline_kwargs["thread_id"] = job_id

        with self._lock:
            self._jobs[job_id] = JobStatus(job_id=job_id)

        self._executor.submit(self._run, job_id, pipeline_kwargs)
        return job_id

    def _run(self, job_id: str, pipeline_kwargs: Dict[str, Any]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.status = "running"
            job.started_at = time.time()

        def on_progress(node_name: str) -> None:
            with self._lock:
                j = self._jobs[job_id]
                if node_name not in j.completed_nodes:
                    j.completed_nodes.append(node_name)

        try:
            result = run_pipeline(progress_callback=on_progress, **pipeline_kwargs)
            with self._lock:
                j = self._jobs[job_id]
                j.status = "succeeded"
                j.result = result
                j.finished_at = time.time()
        except Exception as e:  # noqa: BLE001 —— 任务级兜底，不使 worker 崩溃
            with self._lock:
                j = self._jobs[job_id]
                j.status = "failed"
                j.error = str(e)
                j.finished_at = time.time()

    def get(self, job_id: str) -> Optional[JobStatus]:
        with self._lock:
            return self._jobs.get(job_id)

    def list_jobs(self) -> List[str]:
        with self._lock:
            return list(self._jobs.keys())

    def shutdown(self, wait: bool = False) -> None:
        self._executor.shutdown(wait=wait)
