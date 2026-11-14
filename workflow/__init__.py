"""LangGraph 编排层。

把「技术交底书 → 完整专利文档」的串行流水线重写为可并行的有向状态图，
并支持断点续跑（checkpoint）。

对外暴露：
    build_graph()          组装并编译 StateGraph
    build_initial_state()  构造 invoke 用的初始状态
    PatentState            共享状态 TypedDict
"""
from workflow.graph import build_graph
from workflow.state import PatentState, build_initial_state

__all__ = ["build_graph", "build_initial_state", "PatentState"]
