"""LangGraph 状态图：把原串行流水线组装成可并行的有向图。

图拓扑：

    START
      │
   ┌──▼───┐
   │ parse │  解析交底书 -> technical_info
   └──┬───┘
      │  route_after_parse（条件边）
      ├─────────────── do_search=False ──────────────┐
      │                                              │
   ┌──▼─────┐  do_search=True                       │
   │ search │  检索 -> search_result                 │
   └──┬─────┘                                       │
      └──────────────►┌─────────┐◄───────────────────┘
                      │ outline │  大纲 -> outline
                      └──┬──┬──┬──┘
          ┌──────────────┘  │  │  └──────────────┐   ← 并行扇出
     ┌────▼────┐ ┌────▼────┐ │  │ ┌────▼────┐ ┌──▼─────┐
     │ claims  │ │descript │ │  │ │ abstract │ │diagram │
     └────┬────┘ └───┬─────┘ │  │ └────┬─────┘ └───┬────┘
          └──────────┴───┐   │  └──────┴───────────┘
                          ▼   ▼         ▲
                      ┌─────────┐  ← 并行汇聚（四者齐备才执行）
                      │  merge  │  合并最终文档
                      └────┬────┘
                           │
                          END

与原串行实现相比的差异点：
1. claims / description / abstract / diagram 四项只依赖 outline，彼此无依赖，
   在 LangGraph 里会落到同一超步并行执行（4 次 LLM 调用并发，而非 4 次串行）。
2. 状态外置到 PatentState，配合 checkpointer 可实现断点续跑（见 main.py）。
3. claims/description 生成后先经 verify 节点做合规校验（条件边可回退重写，
   默认仅标记不阻断），再进入 merge 合并。
"""
from langgraph.graph import END, START, StateGraph

from workflow.nodes import (
    node_abstract,
    node_claims,
    node_description,
    node_diagram,
    node_disclosure,
    node_merge,
    node_outline,
    node_parse_input,
    node_search,
    node_verify,
    route_after_parse,
    route_after_verify,
    route_entry,
)
from workflow.state import PatentState


def build_graph(checkpointer=None, node_overrides: dict = None):
    """构建并编译状态图。

    Args:
        checkpointer: None 时无断点续跑能力（仍可正常跑）；传 MemorySaver/SqliteSaver
                      则启用 checkpoint，可断点续跑。
        node_overrides: 节点名 -> 函数 的覆盖表，用于测试注入故障节点或替换实现。
    """
    nodes = {
        "disclosure": node_disclosure,
        "parse": node_parse_input,
        "search": node_search,
        "outline": node_outline,
        "claims": node_claims,
        "description": node_description,
        "abstract": node_abstract,
        "diagram": node_diagram,
        "verify": node_verify,
        "merge": node_merge,
    }
    if node_overrides:
        nodes.update(node_overrides)

    g = StateGraph(PatentState)
    for name, fn in nodes.items():
        g.add_node(name, fn)

    # 入口条件边：可选阶段 A（项目 -> 交底书）
    g.add_conditional_edges(
        START,
        route_entry,
        {"disclosure": "disclosure", "parse": "parse"},
    )
    g.add_edge("disclosure", "parse")

    # 条件边：do_search 决定是否走检索节点
    g.add_conditional_edges(
        "parse",
        route_after_parse,
        {"search": "search", "outline": "outline"},
    )
    g.add_edge("search", "outline")

    # 并行扇出：四项产物只依赖 outline
    g.add_edge("outline", "claims")
    g.add_edge("outline", "description")
    g.add_edge("outline", "abstract")
    g.add_edge("outline", "diagram")

    # claims/description 生成后先过合规校验，再进 merge（并行汇聚）
    g.add_edge("claims", "verify")
    g.add_edge("description", "verify")
    # 校验条件边：通过 → merge；不通过且强制 → 回退重写 claims（限 1 次）
    g.add_conditional_edges(
        "verify",
        route_after_verify,
        {"merge": "merge", "claims": "claims"},
    )

    # abstract/diagram 不参与校验，直接进 merge
    g.add_edge("abstract", "merge")
    g.add_edge("diagram", "merge")

    g.add_edge("merge", END)

    return g.compile(checkpointer=checkpointer)
