"""离线测试：验证 LangGraph 状态图的拓扑、数据流与断点续跑。

不调用真实 LLM / 搜索 API：通过替换 utils.llm_client 的全局单例为 MockLLM，
让每个节点拿到固定返回，从而验证「图跑得通、状态在各节点间正确传递」。

运行（需在 guiji_agent_patent 环境）：
    D:/SoftWare/Conda/envs/guiji_agent_patent/python.exe test_graph.py
"""
import json
import os
import sys
import tempfile

# 把本工程目录加入 sys.path，保证 agents/utils/workflow 可导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import utils.llm_client as llm_client_mod  # noqa: E402
from logging_config import get_logger
logger = get_logger(__name__)


DISCLOSURE_JSON = {
    "invention_name": "一种基于视觉语言模型的多模态红队测试方法及系统",
    "technical_field": "人工智能安全测试",
    "background_art": "现有红队测试依赖人工编写攻击样本，效率低、覆盖不全。",
    "technical_problem": "解决多模态大模型红队测试自动化程度低、覆盖率不足的问题。",
    "technical_solution": "采用视觉语言模型与蒙特卡洛树搜索，通过多工具调用自动生成攻击样本。",
    "technical_effects": "显著提升测试覆盖率与攻击成功率，降低人工成本。",
    "specific_implementations": ["实施例1：单轮攻击样本生成", "实施例2：多步搜索式攻击"],
}


class MockLLM:
    """按 prompt 特征返回固定内容的离线假 LLM。"""

    def complete(self, prompt, model=None, temperature=0.7, max_tokens=None,
                 system_prompt=None, **kwargs):
        p = prompt or ""
        s = system_prompt or ""
        if "专利代理师" in s:                      # 交底书结构化解析
            return json.dumps(DISCLOSURE_JSON, ensure_ascii=False)
        if "关键技术特征" in p:                     # 关键特征提取
            return "视觉语言模型\n蒙特卡洛树搜索\n多工具调用\n奖励函数\n攻击样本生成\n"
        if "章节大纲" in p:                        # 说明书章节大纲
            return json.dumps({"sections": [
                {"title": "技术领域", "content_outline": "x", "subsections": []},
                {"title": "背景技术", "content_outline": "x", "subsections": []},
                {"title": "发明内容", "content_outline": "x", "subsections": []},
                {"title": "附图说明", "content_outline": "x", "subsections": []},
                {"title": "具体实施方式", "content_outline": "x", "subsections": []},
            ]}, ensure_ascii=False)
        if "权利要求书的结构" in p:                  # 权利要求结构
            return "独立权利要求1: 一种方法 - 必要技术特征\n从属权利要求2: 引用权1，增加特征\n"
        if "需要附图的类型" in p:                    # 附图大纲
            return "图1: 系统架构图 - 整体结构\n图2: 方法流程图 - 主要步骤\n"
        if "生成图" in p:                          # 附图详细说明
            return json.dumps({
                "description": "系统整体架构说明",
                "components": [{"number": "1", "name": "输入模块", "function": "接收输入"}],
            }, ensure_ascii=False)
        if "从属权利要求" in p:                     # 从属权项正文
            return ("2. 根据权利要求1所述的方法，其特征在于，"
                    "所述蒙特卡洛树搜索采用置信上限树策略进行节点选择。")
        if "独立权利要求" in p:                     # 独立权项正文
            return ("1. 一种基于视觉语言模型的多模态红队测试方法，其特征在于，"
                    "包括利用视觉语言模型与蒙特卡洛树搜索进行多工具调用生成攻击样本。")
        if "专利摘要" in p:                        # 摘要
            return ("本发明公开了一种基于视觉语言模型的多模态红队测试方法及系统，"
                    "通过多工具调用实现自动化攻击样本生成。")
        return "本发明通过视觉语言模型与蒙特卡洛树搜索实现多模态红队测试。"  # 说明书各章节兜底


def _tmp_input_file() -> str:
    """写一份带全字段的规则可解析交底书（即便 LLM 解析失败也能兜底）。"""
    d = tempfile.mkdtemp(prefix="patent_lg_test_")
    path = os.path.join(d, "交底书.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write("发明名称：一种基于视觉语言模型的多模态红队测试方法及系统\n"
                "技术领域：人工智能安全测试\n"
                "背景技术：现有红队测试依赖人工\n"
                "技术问题：自动化程度低\n"
                "技术方案：采用视觉语言模型与蒙特卡洛树搜索\n"
                "有益效果：提升覆盖率\n"
                "具体实施方式：实施例1 单轮生成；实施例2 多步搜索\n")
    return path, d


def test_end_to_end():
    input_path, tmpdir = _tmp_input_file()
    out = os.path.join(tmpdir, "output")

    from workflow.graph import build_graph
    from workflow.state import build_initial_state

    graph = build_graph(checkpointer=None)  # 无需断点，纯跑通
    state = build_initial_state(
        input_path=input_path, output_dir=out,
        do_search=False, save_individual=True,
    )
    final = graph.invoke(state)

    assert final["success"] is True, f"errors={final.get('errors')}"
    assert final["technical_info"].invention_name.startswith("一种基于视觉语言模型")
    assert final["outline"] is not None and len(final["outline"].sections) >= 3
    assert final["claims_doc"] is not None and final["claims_doc"].total_count >= 1
    assert final["description_doc"] is not None
    assert final["abstract_doc"] is not None
    assert final["diagram_doc"] is not None and len(final["diagram_doc"].diagrams) >= 1
    assert "full_document" in final["files"], final["files"]
    assert os.path.exists(final["files"]["full_document"])
    assert "claims" in final["files"] and "abstract" in final["files"]  # 分部保存

    logger.info("[PASS] end-to-end 离线跑通：parse→outline→claims/description/abstract/diagram→merge")
    logger.info(f"      产物: {final['files']['full_document']}")
    return True


def test_routes():
    from workflow.nodes import route_after_parse, route_entry

    assert route_entry({"project_dir": "x", "input_path": ""}) == "disclosure"
    assert route_entry({"project_dir": "", "input_path": "y"}) == "parse"
    assert route_entry({"project_dir": "x", "input_path": "y"}) == "parse"  # 已有交底书优先
    assert route_after_parse({"do_search": True}) == "search"
    assert route_after_parse({"do_search": False}) == "outline"
    logger.info("[PASS] 条件分支路由正确（阶段A入口 + do_search 开关）")
    return True


def test_checkpoint_resume():
    from langgraph.checkpoint.memory import MemorySaver

    from workflow.graph import build_graph
    from workflow.state import build_initial_state

    input_path, tmpdir = _tmp_input_file()
    out = os.path.join(tmpdir, "output")

    saver = MemorySaver()

    def boom(state):  # 注入一个会在 claims 处崩溃的节点
        raise RuntimeError("模拟 claims 节点崩溃")

    cfg = {"configurable": {"thread_id": "resume-1"}}
    state = build_initial_state(input_path=input_path, output_dir=out, do_search=False)

    # 第一次跑：claims 崩溃
    g_fail = build_graph(checkpointer=saver, node_overrides={"claims": boom})
    try:
        g_fail.invoke(state, cfg)
        raise AssertionError("应当抛出异常")
    except RuntimeError as e:
        assert "崩溃" in str(e)

    # 第二次跑：同一 saver + 同一 thread_id，从断点续跑（claims 之后继续）
    g_ok = build_graph(checkpointer=saver)
    final = g_ok.invoke(state, cfg)
    assert final["success"] is True
    assert final["claims_doc"].total_count >= 1
    logger.info("[PASS] 断点续跑：claims 崩溃后同 thread_id 重跑，从断点恢复并完成")
    return True


if __name__ == "__main__":
    llm_client_mod._llm_client = MockLLM()  # 全局替换为离线假 LLM
    ok = all([
        test_end_to_end(),
        test_routes(),
        test_checkpoint_resume(),
    ])
    logger.info("\n" + ("全部通过 ✅" if ok else "存在失败 ❌"))
    sys.exit(0 if ok else 1)
