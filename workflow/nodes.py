"""LangGraph 节点：每个节点 = 原串行流水线的一个步骤。

与原 main.py 的对应关系（节点名 -> 原步骤）：
    node_parse_input      -> [1/7] 解析技术交底书
    node_search           -> [2/7] 专利检索
    node_outline          -> [3/7] 生成专利大纲
    node_claims           -> [4/7] 撰写权利要求书
    node_description      -> [5/7] 撰写说明书
    node_abstract         -> [6/7] 撰写摘要
    node_diagram          -> [7/7] 生成附图说明
    node_merge            -> 合并生成最终文档

业务逻辑全部复用原 agents/* 模块；本文件只做「从 state 取入参、把结果写回 state」。
"""
import os

from agents.input_parser import InputParser
from agents.patent_searcher import PatentSearcher
from agents.outline_generator import OutlineGenerator
from agents.claims_writer import ClaimsWriter
from agents.description_writer import DescriptionWriter
from agents.abstract_writer import AbstractWriter
from agents.diagram_generator import DiagramGenerator
from agents.document_merger import DocumentMerger
from agents.project_to_disclosure import ProjectToDisclosure
from agents.verifier import Verifier
from logging_config import get_logger
logger = get_logger(__name__)


def node_disclosure(state: dict) -> dict:
    """[阶段A] 项目目录 -> 技术交底书（可选前置节点，复用 ProjectToDisclosure）。"""
    gen = ProjectToDisclosure()
    r = gen.generate(
        project_dir=state["project_dir"],
        output_path=state.get("disclosure_path") or os.path.join("data", "技术交底书.txt"),
        hint=state.get("hint", ""),
        max_files=state.get("max_files", 40),
    )
    if not r.get("path"):
        raise RuntimeError(f"交底书生成失败: {r.get('problems')}")
    logger.info(f"  [disclosure] 交底书: {r['path']}（参考 {r.get('files_used', 0)} 个文件）")
    return {"input_path": r["path"], "disclosure_path": r["path"]}


def node_parse_input(state: dict) -> dict:
    """[1] 技术交底书 -> TechnicalInfo。"""
    parser = InputParser()
    technical_info = parser.parse_from_file(state["input_path"])
    logger.info(f"  [parse] 发明名称: {technical_info.invention_name}，"
          f"关键特征 {len(technical_info.key_features)} 个")
    return {"technical_info": technical_info}


def node_search(state: dict) -> dict:
    """[2] 专利检索 -> SearchResult + 检索报告（可被条件分支跳过）。"""
    ti = state["technical_info"]
    searcher = PatentSearcher()
    search_result = searcher.search(
        invention_name=ti.invention_name,
        technical_solution=ti.technical_solution,
        key_features=ti.key_features,
        max_results=10,
    )

    files: dict = {}
    errors: list = []
    if not search_result.patents and not search_result.prior_art:
        errors.append(
            "检索返回 0 条结果（若未配置 SERPAPI/EXA Key，实际未执行，勿据此判新颖性）"
        )

    report = searcher.generate_search_report(search_result)
    os.makedirs(state["output_dir"], exist_ok=True)
    report_path = os.path.join(state["output_dir"], "检索报告.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    files["search_report"] = report_path
    logger.info(f"  [search] 相关专利 {len(search_result.patents)} 项，"
          f"现有技术 {len(search_result.prior_art)} 项")
    return {"search_result": search_result, "files": files, "errors": errors}


def node_outline(state: dict) -> dict:
    """[3] TechnicalInfo + SearchResult -> PatentOutline。"""
    generator = OutlineGenerator()
    outline = generator.generate(state["technical_info"], state.get("search_result"))

    os.makedirs(state["output_dir"], exist_ok=True)
    outline_path = os.path.join(state["output_dir"], "专利大纲.md")
    with open(outline_path, "w", encoding="utf-8") as f:
        f.write(generator.to_markdown(outline))
    logger.info(f"  [outline] 章节 {len(outline.sections)} 个，"
          f"权项结构 {len(outline.claims_structure)} 项")
    return {"outline": outline, "files": {"outline": outline_path}}


def node_claims(state: dict) -> dict:
    """[4] 撰写权利要求书（与 description/abstract/diagram 并行）。"""
    writer = ClaimsWriter()
    claims_doc = writer.write(state["technical_info"], state["outline"])
    logger.info(f"  [claims] 权利要求 {claims_doc.total_count} 项")
    return {"claims_doc": claims_doc}


def node_description(state: dict) -> dict:
    """[5] 撰写说明书。"""
    writer = DescriptionWriter()
    description_doc = writer.write(state["technical_info"], state["outline"])
    logger.info(f"  [description] 章节 {len(description_doc.sections)} 个，"
          f"约 {description_doc.word_count} 字")
    return {"description_doc": description_doc}


def node_abstract(state: dict) -> dict:
    """[6] 撰写摘要。"""
    writer = AbstractWriter()
    abstract_doc = writer.write(state["technical_info"])
    logger.info(f"  [abstract] 摘要 {abstract_doc.word_count} 字")
    return {"abstract_doc": abstract_doc}


def node_diagram(state: dict) -> dict:
    """[7] 生成附图说明。"""
    generator = DiagramGenerator()
    diagram_doc = generator.generate(state["technical_info"])
    logger.info(f"  [diagram] 附图 {len(diagram_doc.diagrams)} 幅")
    return {"diagram_doc": diagram_doc}


def node_merge(state: dict) -> dict:
    """[合并] 四项产物齐备后合成完整专利文档。"""
    merger = DocumentMerger(state["output_dir"])
    ti = state["technical_info"]
    files: dict = {}
    errors: list = []

    try:
        merged_path = merger.merge(
            invention_title=ti.invention_name,
            claims_doc=state["claims_doc"],
            description_doc=state["description_doc"],
            abstract_doc=state["abstract_doc"],
            diagram_doc=state["diagram_doc"],
            format=state.get("output_format", "markdown"),
        )
        files["full_document"] = merged_path
        logger.info(f"  [merge] 完整专利文档: {merged_path}")
    except Exception as e:  # noqa: BLE001
        errors.append(f"合并失败: {e}")

    if state.get("save_individual"):
        try:
            files.update(
                merger.merge_individual_files(
                    invention_title=ti.invention_name,
                    claims_doc=state["claims_doc"],
                    description_doc=state["description_doc"],
                    abstract_doc=state["abstract_doc"],
                    diagram_doc=state["diagram_doc"],
                )
            )
        except Exception as e:  # noqa: BLE001
            errors.append(f"分部保存失败: {e}")

    return {"files": files, "errors": errors, "success": "full_document" in files}


def route_after_parse(state: dict) -> str:
    """条件分支：根据 do_search 决定是走检索还是直接进大纲。"""
    return "search" if state.get("do_search") else "outline"


def route_entry(state: dict) -> str:
    """条件分支：给了 project_dir 且没有现成交底书时，先走阶段 A 生成交底书。"""
    if state.get("project_dir") and not state.get("input_path"):
        return "disclosure"
    return "parse"


def node_verify(state: dict) -> dict:
    """[校验] 生成后合规校验（规则 + 可选 LLM），产出校验报告并落盘。"""
    use_llm = os.getenv("VERIFY_USE_LLM", "false").lower() == "true"
    verifier = Verifier(use_llm=use_llm)
    report = verifier.verify(
        state["technical_info"], state["claims_doc"], state["description_doc"]
    )

    os.makedirs(state["output_dir"], exist_ok=True)
    report_path = os.path.join(state["output_dir"], "校验报告.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report.to_markdown())

    verify_attempts = state.get("verify_attempts", 0) + 1
    logger.info(f"  [verify] {'通过' if report.passed else '未通过'}，"
          f"问题 {len(report.issues)} 项，LLM 调用 {report.llm_calls} 次")

    errors = []
    if not report.passed:
        errors.append(f"合规校验未通过（{len(report.issues)} 项问题），详见 {report_path}")

    return {
        "verification_report": report,
        "verify_attempts": verify_attempts,
        "files": {"verification_report": report_path},
        "errors": errors,
    }


def route_after_verify(state: dict) -> str:
    """条件分支：校验通过进 merge；不通过且开启强制时回退重写 claims（限 1 次）。"""
    report = state.get("verification_report")
    if report is not None and getattr(report, "passed", False):
        return "merge"
    enforce = os.getenv("VERIFY_ENFORCE", "false").lower() == "true"
    attempts = state.get("verify_attempts", 0)
    if enforce and attempts < 2:
        return "claims"
    return "merge"
