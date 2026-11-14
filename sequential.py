"""串行流水线兼容层。

保留原 patent_writer_agent2/main.py 中的 PatentWriterAgent 串行编排，作为：
    1. 对照实现：LangGraph 状态图（workflow/）与串行实现的等价性校验；
    2. 轻量 API：write_from_text 等内存直写接口，供脚本/测试快速调用；
    3. 降级兜底：当 LangGraph 依赖缺失时仍可跑通核心流程。

主推入口是 LangGraph 版（main.py / service.py），本模块仅作兼容与对照。
"""
import os
from typing import Optional

from agents.input_parser import InputParser, TechnicalInfo
from agents.patent_searcher import PatentSearcher, SearchResult
from agents.outline_generator import OutlineGenerator, PatentOutline
from agents.claims_writer import ClaimsWriter, ClaimsDocument
from agents.description_writer import DescriptionWriter, DescriptionDocument
from agents.abstract_writer import AbstractWriter, AbstractDocument
from agents.diagram_generator import DiagramGenerator, DiagramDocument
from agents.document_merger import DocumentMerger
from logging_config import get_logger
logger = get_logger(__name__)


class PatentWriterAgent:
    """专利写作智能体（串行版，兼容层）"""

    def __init__(self, output_dir: str = "output"):
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)

        self.input_parser = InputParser()
        self.patent_searcher = PatentSearcher()
        self.outline_generator = OutlineGenerator()
        self.claims_writer = ClaimsWriter()
        self.description_writer = DescriptionWriter()
        self.abstract_writer = AbstractWriter()
        self.diagram_generator = DiagramGenerator()
        self.document_merger = DocumentMerger(output_dir)

    def write_patent(
        self,
        input_path: str,
        output_format: str = "markdown",
        do_search: bool = True,
        save_individual: bool = True,
    ) -> dict:
        """撰写完整专利文档（串行 7 步，与原实现一致）。"""
        result = {"success": False, "files": {}, "errors": [], "info": {}}

        try:
            logger.info(f"\n{'=' * 60}")
            logger.info("专利写作智能体 - 开始工作（串行模式）")
            logger.info(f"{'=' * 60}\n")

            # 1. 解析输入
            logger.info("[1/7] 正在解析技术交底书...")
            technical_info = self.input_parser.parse_from_file(input_path)
            logger.info(f"  [OK] 发明名称: {technical_info.invention_name}")
            logger.info(f"  [OK] 提取关键特征: {len(technical_info.key_features)}个")
            result["info"]["invention_name"] = technical_info.invention_name

            # 2. 专利检索
            search_result = None
            if do_search:
                logger.info("\n[2/7] 正在进行专利检索...")
                try:
                    search_result = self.patent_searcher.search(
                        invention_name=technical_info.invention_name,
                        technical_solution=technical_info.technical_solution,
                        key_features=technical_info.key_features,
                        max_results=10,
                    )
                    logger.info(f"  [OK] 找到相关专利: {len(search_result.patents)}项")
                    logger.info(f"  [OK] 找到现有技术: {len(search_result.prior_art)}项")
                    if not search_result.patents and not search_result.prior_art:
                        logger.warning("  [WARN] 检索返回 0 条结果（若未配置 SERPAPI/EXA Key，实际未执行）")
                    search_report = self.patent_searcher.generate_search_report(search_result)
                    search_path = os.path.join(self.output_dir, "检索报告.md")
                    with open(search_path, "w", encoding="utf-8") as f:
                        f.write(search_report)
                    result["files"]["search_report"] = search_path
                except Exception as e:
                    logger.warning(f"  [WARN] 专利检索失败: {e}")
                    result["errors"].append(f"专利检索失败: {e}")
            else:
                logger.info("\n[2/7] 跳过专利检索")

            # 3. 生成大纲
            logger.info("\n[3/7] 正在生成专利大纲...")
            outline = self.outline_generator.generate(technical_info, search_result)
            outline_md = self.outline_generator.to_markdown(outline)
            outline_path = os.path.join(self.output_dir, "专利大纲.md")
            with open(outline_path, "w", encoding="utf-8") as f:
                f.write(outline_md)
            result["files"]["outline"] = outline_path

            # 4. 权利要求书
            logger.info("\n[4/7] 正在撰写权利要求书...")
            claims_doc = self.claims_writer.write(technical_info, outline)

            # 5. 说明书
            logger.info("\n[5/7] 正在撰写说明书...")
            description_doc = self.description_writer.write(technical_info, outline)

            # 6. 摘要
            logger.info("\n[6/7] 正在撰写摘要...")
            abstract_doc = self.abstract_writer.write(technical_info)

            # 7. 附图说明
            logger.info("\n[7/7] 正在生成附图说明...")
            diagram_doc = self.diagram_generator.generate(technical_info)

            # 合并
            merged_path = self.document_merger.merge(
                invention_title=technical_info.invention_name,
                claims_doc=claims_doc,
                description_doc=description_doc,
                abstract_doc=abstract_doc,
                diagram_doc=diagram_doc,
                format=output_format,
            )
            result["files"]["full_document"] = merged_path

            if save_individual:
                individual_files = self.document_merger.merge_individual_files(
                    invention_title=technical_info.invention_name,
                    claims_doc=claims_doc,
                    description_doc=description_doc,
                    abstract_doc=abstract_doc,
                    diagram_doc=diagram_doc,
                )
                result["files"].update(individual_files)

            result["success"] = True
            logger.info("\n专利写作完成！")

        except Exception as e:
            result["success"] = False
            result["errors"].append(str(e))
            logger.error(f"\n[ERROR] 错误: {e}")
            import traceback
            traceback.print_exc()

        return result

    def write_from_text(
        self,
        text: str,
        invention_name: Optional[str] = None,
        output_format: str = "markdown",
    ) -> dict:
        """从文本直接撰写专利（写临时文件后走 write_patent）。"""
        temp_path = os.path.join(self.output_dir, "temp_input.txt")
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(text)
        return self.write_patent(temp_path, output_format)
