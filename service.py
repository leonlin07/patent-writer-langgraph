"""服务化 API 封装层。

把 LangGraph 编排暴露为纯函数接口，供命令行、HTTP 服务（FastAPI/Flask）、
消息队列消费端等复用。后续接入服务框架时只需在本层之上加一层路由/适配，
无需改动 workflow/ 与 agents/ 的编排与业务逻辑。

对外主要接口：
    run_pipeline(...)      完整流水线（阶段A可选 + 阶段B）
    generate_disclosure()  仅阶段A：项目 -> 技术交底书
    generate_patent()      仅阶段B：技术交底书 -> 专利文档
"""
from __future__ import annotations

import os
from contextlib import nullcontext
from typing import Any, Callable, Dict, Optional

from logging_config import get_logger
from workflow import build_graph, build_initial_state

logger = get_logger(__name__)


def _make_checkpointer(ckpt_path: str):
    """按需构造 checkpointer。

    langgraph-checkpoint-sqlite 3.x 起 ``SqliteSaver.from_conn_string()`` 返回
    上下文管理器，因此 SQLite 分支走 ``with``；内存版直接返回实例。
    """
    if ckpt_path:
        from langgraph.checkpoint.sqlite import SqliteSaver
        return SqliteSaver.from_conn_string(ckpt_path)
    from langgraph.checkpoint.memory import MemorySaver
    return nullcontext(MemorySaver())


def run_pipeline(
    *,
    input_path: str = "",
    project_dir: str = "",
    hint: str = "",
    output_dir: str = "output",
    output_format: str = "markdown",
    do_search: bool = False,
    save_individual: bool = True,
    max_files: int = 40,
    disclosure_path: str = "",
    checkpoint_path: str = "",
    thread_id: str = "default",
    progress_callback: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    """执行完整流水线，返回结构化结果。

    Args:
        input_path: 技术交底书路径（阶段B入口）；与 project_dir 二选一。
        project_dir: 项目目录（阶段A入口，走「项目→交底书」）。
        hint: 项目方向提示（阶段A 强烈建议填写）。
        output_dir: 输出目录。
        output_format: markdown / text / docx。
        do_search: 是否专利检索。
        save_individual: 是否分别保存各部分文档。
        max_files: 阶段A 最多读取文件数。
        disclosure_path: 阶段A 交底书输出路径。
        checkpoint_path: SQLite checkpoint 路径（断点续跑，可选）。
        thread_id: 线程 ID（同一 ID 共享断点）。
        progress_callback: 可选进度回调，每完成一个节点调用一次（入参为节点名）。
            服务化部署用它上报进度；CLI 传 None 走最快路径。

    Returns:
        {"success": bool, "files": {...}, "errors": [...], "state": <final state>}
    """
    if not input_path and not project_dir:
        raise ValueError("必须提供 input_path（交底书）或 project_dir（项目目录）之一")
    if input_path and not os.path.exists(input_path):
        raise FileNotFoundError(f"交底书不存在: {input_path}")

    state = build_initial_state(
        input_path=input_path,
        project_dir=project_dir,
        hint=hint,
        output_dir=output_dir,
        output_format=output_format,
        do_search=do_search,
        save_individual=save_individual,
        max_files=max_files,
    )
    if disclosure_path:
        state["disclosure_path"] = disclosure_path

    graph_cm = _make_checkpointer(checkpoint_path)
    config = {"configurable": {"thread_id": thread_id}}

    logger.info(
        "pipeline start: input=%s project=%s output=%s search=%s",
        input_path or "-", project_dir or "-", output_dir, do_search,
    )

    with graph_cm as checkpointer:
        graph = build_graph(checkpointer=checkpointer)
        if progress_callback is not None:
            # 有进度需求：用 updates 模式逐节点回调，结束后从 checkpointer 取最终态
            for chunk in graph.stream(state, config, stream_mode="updates"):
                for node_name in chunk:
                    progress_callback(node_name)
            final = graph.get_state(config).values
        else:
            final = graph.invoke(state, config)

    result = {
        "success": bool(final.get("success")),
        "files": final.get("files", {}),
        "errors": final.get("errors", []),
        "state": final,
    }
    logger.info("pipeline done: success=%s files=%s", result["success"], list(result["files"]))
    return result


def generate_disclosure(
    project_dir: str,
    output_path: str,
    hint: str = "",
    max_files: int = 40,
) -> Dict[str, Any]:
    """仅阶段A：项目材料 -> 技术交底书（复用 ProjectToDisclosure）。"""
    from agents.project_to_disclosure import ProjectToDisclosure

    gen = ProjectToDisclosure()
    return gen.generate(
        project_dir=project_dir,
        output_path=output_path,
        hint=hint,
        max_files=max_files,
    )


def generate_patent(
    input_path: str,
    output_dir: str = "output",
    output_format: str = "markdown",
    do_search: bool = False,
    save_individual: bool = True,
    **kwargs: Any,
) -> Dict[str, Any]:
    """仅阶段B：技术交底书 -> 完整专利文档。"""
    return run_pipeline(
        input_path=input_path,
        output_dir=output_dir,
        output_format=output_format,
        do_search=do_search,
        save_individual=save_individual,
        **kwargs,
    )
