"""LangGraph 共享状态（State）定义。

对比原串行实现：main.py 的 PatentWriterAgent.write_patent() 里用局部变量
technical_info / search_result / outline / claims_doc / ... 在函数体内顺序传递，
中间任一节点崩溃，前面已跑完的 LLM 调用全部白费。

这里改成显式的 TypedDict 状态，作为图里所有节点共用的「黑板」：
每个节点读取 state、返回它改写的字段，LangGraph 负责按 reducer 合并。

字段分三类：
1. 输入配置：invoke 前一次性给定，之后只读
2. 中间产物：上游节点写入、下游节点读取（dataclass 对象）
3. 累加字段：多个节点都会写（files / errors），必须配 reducer 才能正确合并
"""
from typing import Annotated, Any, Dict, List, TypedDict


def _merge_dict(left: dict, right: dict) -> dict:
    """dict 累加 reducer：后写的键覆盖先写的（如 full_document 覆盖空值）。"""
    merged = dict(left or {})
    merged.update(right or {})
    return merged


class PatentState(TypedDict, total=False):
    # ---- 输入配置 ----
    input_path: str          # 技术交底书路径（阶段 B 入口）
    project_dir: str         # （可选）项目目录，走阶段 A 项目→交底书
    hint: str                # 项目方向提示
    output_dir: str          # 输出目录
    output_format: str       # markdown / text / docx
    do_search: bool          # 是否专利检索（条件分支开关）
    save_individual: bool    # 是否分别保存各部分文档
    max_files: int           # 阶段 A 最多读取文件数

    # ---- 阶段 A 产物（可选）----
    disclosure_path: str     # 生成的交底书路径

    # ---- 中间产物（dataclass 对象，节点间传递）----
    technical_info: Any      # TechnicalInfo
    search_result: Any       # SearchResult | None
    outline: Any             # PatentOutline
    claims_doc: Any          # ClaimsDocument
    description_doc: Any     # DescriptionDocument
    abstract_doc: Any        # AbstractDocument
    diagram_doc: Any         # DiagramDocument

    # ---- 校验产物（verify 节点写入）----
    verification_report: Any  # VerificationReport
    verify_attempts: int      # 校验/回退次数（防死循环）

    # ---- 累加字段（多节点写入，需 reducer）----
    files: Annotated[Dict[str, str], _merge_dict]   # 各步骤产出的文件路径
    errors: Annotated[List[str], lambda a, b: a + b]  # 非致命错误（如检索失败）

    # ---- 终态 ----
    success: bool            # 是否成功产出完整专利文档


def build_initial_state(
    input_path: str = "",
    output_dir: str = "output",
    output_format: str = "markdown",
    do_search: bool = True,
    save_individual: bool = True,
    project_dir: str = "",
    hint: str = "",
    max_files: int = 40,
) -> PatentState:
    """构造 invoke 用的初始状态（累加字段必须显式给初值，否则 reducer 无基线）。"""
    return PatentState(
        input_path=input_path,
        project_dir=project_dir,
        hint=hint,
        output_dir=output_dir,
        output_format=output_format,
        do_search=do_search,
        save_individual=save_individual,
        max_files=max_files,
        files={},
        errors=[],
        success=False,
    )
