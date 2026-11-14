"""FastAPI 服务应用：把 LangGraph 编排暴露为 HTTP 三接口 + 健康检查。

接口契约（与开发计划 M1 对齐）：
    POST /generate          提交任务，返回 job_id（异步生成）
    GET  /status/{job_id}   查询进度（已完成节点列表）
    GET  /result/{job_id}   查询结果（四件套文件路径）
    GET  /health            健康检查（部署探活）

启动方式：
    python serve/run.py
    uvicorn serve.app:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

import os

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from serve.schemas import (
    GenerateRequest,
    GenerateResponse,
    HealthResponse,
    ResultResponse,
    StatusResponse,
)
from serve.tasks import JobManager

# 服务配置（环境变量驱动，均可被 .env / docker-compose 覆盖）
MAX_WORKERS = int(os.getenv("SERVE_MAX_WORKERS", "3"))
CHECKPOINT_DIR = os.getenv("SERVE_CHECKPOINT_DIR", "jobs/checkpoints")
OUTPUT_ROOT = os.getenv("SERVE_OUTPUT_ROOT", "output")

app = FastAPI(
    title="Patent Writer Agent API",
    version="0.1.0",
    description="基于 LangGraph 的专利写作智能体服务化接口（技术交底书 → 完整专利文档）",
)

# 允许跨域（供本地 Gradio / 前端调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

manager = JobManager(
    max_workers=MAX_WORKERS,
    checkpoint_dir=CHECKPOINT_DIR,
    output_root=OUTPUT_ROOT,
)


@app.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """健康检查：供容器探活 / 负载均衡健康探测。"""
    return HealthResponse(status="ok", service="patent-writer-langgraph", version="0.1.0")


@app.post("/generate", response_model=GenerateResponse, tags=["pipeline"])
def generate(req: GenerateRequest) -> GenerateResponse:
    """提交生成任务，立即返回 job_id，生成在后台线程异步执行。"""
    if not req.input_path and not req.project_dir:
        raise HTTPException(status_code=400, detail="必须提供 input_path 或 project_dir 之一")

    job_id = manager.submit(
        input_path=req.input_path,
        project_dir=req.project_dir,
        hint=req.hint,
        output_format=req.output_format,
        do_search=req.do_search,
        save_individual=req.save_individual,
        max_files=req.max_files,
        disclosure_path=req.disclosure_path,
    )
    return GenerateResponse(
        job_id=job_id,
        status="pending",
        status_url=f"/status/{job_id}",
        result_url=f"/result/{job_id}",
    )


@app.get("/status/{job_id}", response_model=StatusResponse, tags=["pipeline"])
def status(job_id: str) -> StatusResponse:
    """查询任务进度。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")
    return StatusResponse(
        job_id=job.job_id,
        status=job.status,
        completed_nodes=job.completed_nodes,
        progress=job.progress,
        created_at=job.created_at,
        started_at=job.started_at,
        finished_at=job.finished_at,
        error=job.error,
    )


@app.get("/result/{job_id}", response_model=ResultResponse, tags=["pipeline"])
def result(job_id: str) -> ResultResponse:
    """查询生成结果。未完成时返回当前状态（不报错），完成时返回文件路径。"""
    job = manager.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"任务不存在: {job_id}")

    if job.status in ("pending", "running"):
        return ResultResponse(job_id=job_id, status=job.status)

    if job.status == "failed":
        return ResultResponse(
            job_id=job_id, status="failed", success=False, error=job.error
        )

    r = job.result or {}
    return ResultResponse(
        job_id=job_id,
        status="succeeded",
        files=r.get("files", {}),
        errors=r.get("errors", []),
        success=bool(r.get("success")),
    )
