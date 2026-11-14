"""统一命令行入口（LangGraph 编排）。

合并了两个原项目的入口，一个命令覆盖全部场景：
    - 原 patent_writer_agent2/main.py（--input 阶段B）
    - 原 patent_writer_agent2/generate_from_project.py（--project 阶段A + --stage 拆分）
    - 原 patent_agent_langgraph/main.py（LangGraph 状态图 + --checkpoint 断点续跑）

用法示例：
    # 阶段B：已有交底书，直接出专利
    python main.py --input data/技术交底书.txt --output output/我的专利 --no-search

    # 阶段A+B：项目目录一键到底
    python main.py --project ../my_project --hint "基于视觉语言模型的多模态红队测试" --output output/rededit

    # 只出交底书（阶段A）
    python main.py --project ../my_project --hint "..." --stage disclosure

    # 断点续跑：首次落盘，中途失败后用同一 --thread-id 重跑即续
    python main.py --input data/技术交底书.txt --output output --checkpoint ckpt.sqlite --thread-id job-1
"""
import argparse
import sys

from service import generate_disclosure, run_pipeline
from logging_config import get_logger
logger = get_logger(__name__)


def main() -> None:
    p = argparse.ArgumentParser(description="LangGraph 专利写作 Agent（统一入口）")

    # 入口：--input（阶段B）与 --project（阶段A）二选一
    p.add_argument("--input", "-i", default="", help="技术交底书路径（阶段B入口）")
    p.add_argument("--project", "-p", default="", help="项目目录（阶段A入口）")
    p.add_argument("--hint", default="", help="项目方向提示，强烈建议填")
    p.add_argument("--output", "-o", default="output", help="输出目录")
    p.add_argument("--format", "-f", choices=["markdown", "text", "docx"], default="markdown")
    p.add_argument("--disclosure", default="data/技术交底书.txt", help="交底书输出路径（阶段A用）")
    p.add_argument("--max-files", type=int, default=40, help="阶段A 最多读取文件数")
    p.add_argument("--stage", choices=["all", "disclosure", "patent"], default="all",
                   help="运行阶段：all=全流程, disclosure=只出交底书, patent=只用已有交底书出专利")
    p.add_argument("--no-search", action="store_true", help="跳过专利检索")
    p.add_argument("--no-individual", action="store_true", help="不分别保存各部分文档")
    p.add_argument("--checkpoint", default="", help="SQLite checkpoint 路径（断点续跑）")
    p.add_argument("--thread-id", default="default", help="线程 ID（同一 ID 共享断点）")
    args = p.parse_args()

    logger.info("=" * 60)
    logger.info("LangGraph 专利写作 Agent（统一入口）")
    logger.info("=" * 60)

    # 阶段A：只出交底书
    if args.stage == "disclosure":
        if not args.project:
            logger.error("错误：--stage disclosure 需要 --project")
            sys.exit(2)
        r = generate_disclosure(args.project, args.disclosure, args.hint, args.max_files)
        if r.get("success"):
            logger.info(f"\n交底书已就绪: {r['path']}")
            logger.info("请人工审阅后执行阶段B：")
            logger.info(f'  python main.py --input "{r["path"]}" --output "{args.output}"')
            sys.exit(0)
        logger.info("\n交底书生成存在问题：")
        for prob in r.get("problems", []):
            logger.info(f"  - {prob}")
        logger.info(f"\n已生成草稿: {r.get('path')}")
        sys.exit(1)

    # 全流程 / 阶段B（run_pipeline 自动路由：有 input 直接 parse，有 project 先出交底书）
    if not args.input and not args.project:
        logger.error("错误：阶段 all/patent 需提供 --input 或 --project 之一")
        sys.exit(2)

    result = run_pipeline(
        input_path=args.input,
        project_dir=args.project,
        hint=args.hint,
        output_dir=args.output,
        output_format=args.format,
        do_search=not args.no_search,
        save_individual=not args.no_individual,
        max_files=args.max_files,
        disclosure_path=args.disclosure,
        checkpoint_path=args.checkpoint,
        thread_id=args.thread_id,
    )

    logger.info("\n" + "=" * 60)
    if result["success"]:
        logger.info("全部完成")
        logger.info("=" * 60)
        logger.info("生成的文件:")
        for key, path in result["files"].items():
            logger.info(f"  - {key}: {path}")
        sys.exit(0)
    else:
        logger.info("生成未完全成功")
        logger.info("=" * 60)
        for e in result["errors"]:
            logger.info(f"  - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
