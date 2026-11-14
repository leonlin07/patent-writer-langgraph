"""评测脚本：跑测试集，输出 Markdown 指标报告。

指标（对齐开发计划 M2）：
1. 生成成功率（success=True 占比）
2. 端到端耗时（P50 / P95，秒）
3. 合规检查（规则实现，无需 LLM-as-judge）：
   a. 文件完整性：四件套（权利要求/说明书/摘要/附图）均存在且非空
   b. 权项编号连续性：权利要求书中编号 1..N 连续
   c. 术语一致性：发明名称与关键特征在说明书正文中出现

说明：单次生成成本（token 用量）需在 llm_client 增加 usage 采集后统计，
当前版本先记录耗时，成本列为 TODO（详见 report 输出）。

用法：
    python eval/metrics.py --dataset data --output eval/report.md --no-search
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from service import run_pipeline  # noqa: E402
from logging_config import get_logger
logger = get_logger(__name__)


# ------------------------------------------------------------------ 合规检查

def _read(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def check_files_complete(files: dict) -> bool:
    """四件套齐全且非空。"""
    for key in ("claims", "description", "abstract", "diagrams", "full_document"):
        p = files.get(key)
        if not p or not os.path.exists(p):
            return False
        if not _read(p).strip():
            return False
    return True


def check_claim_numbering(files: dict) -> bool:
    """权项编号连续性：权利要求书里出现 1..N 连续编号。"""
    text = _read(files.get("claims", ""))
    nums = sorted({int(m) for m in re.findall(r"(?m)^\s*(\d+)\.\s", text)})
    if not nums:
        return False
    return nums == list(range(1, len(nums) + 1))


def check_terminology(files: dict, keywords: list) -> bool:
    """术语一致性：至少一个关键词出现在说明书正文。"""
    desc = _read(files.get("description", ""))
    if not desc or not keywords:
        return False
    return any(kw and kw in desc for kw in keywords)


# ------------------------------------------------------------------ 主流程

def run_one(input_path: str, output_dir: str, do_search: bool) -> dict:
    """跑单条测试，返回指标字典（含 token 用量与成本）。"""
    from utils.llm_client import get_llm_client
    llm = get_llm_client()
    llm.reset_usage()  # 串行评测，每 case 清零得到单次成本

    started = time.time()
    try:
        r = run_pipeline(
            input_path=input_path,
            output_dir=output_dir,
            do_search=do_search,
            save_individual=True,
        )
        elapsed = time.time() - started
        usage = llm.get_usage()
        cost = llm.estimate_cost()
        files = r.get("files", {})
        success = bool(r.get("success"))
        return {
            "success": success,
            "elapsed": elapsed,
            "files": files,
            "errors": r.get("errors", []),
            "usage": usage,
            "cost": cost,
            "checks": {
                "files_complete": check_files_complete(files),
                "claim_numbering": check_claim_numbering(files),
                "terminology": check_terminology(files, _keywords(files)),
            },
        }
    except Exception as e:  # noqa: BLE001
        return {"success": False, "elapsed": time.time() - started, "files": {},
                "errors": [str(e)], "checks": {}, "usage": {}, "cost": 0.0}


def _keywords(files: dict) -> list:
    """从权利要求书提取「所述X」术语（复用 verifier 的非贪婪正则，避免吞入整句）。"""
    from agents.verifier import Verifier
    text = _read(files.get("claims", ""))
    return Verifier._extract_terms(text)[:5]


def _p95(vals: list) -> float:
    if not vals:
        return 0.0
    idx = int(len(vals) * 0.95)
    return sorted(vals)[min(idx, len(vals) - 1)]


def main() -> None:
    p = argparse.ArgumentParser(description="专利写作 Agent 评测")
    p.add_argument("--dataset", default="data", help="交底书目录（*.txt）")
    p.add_argument("--output", default="eval/report.md", help="报告输出路径")
    p.add_argument("--output-dir", default="output/eval", help="评测产物目录")
    p.add_argument("--no-search", action="store_true", help="关闭专利检索")
    args = p.parse_args()

    inputs = sorted(glob.glob(os.path.join(args.dataset, "*.txt")))
    inputs = [f for f in inputs if ".bak" not in f]
    if not inputs:
        logger.info(f"未找到测试交底书：{args.dataset}")
        sys.exit(1)

    results = []
    for i, inp in enumerate(inputs, 1):
        out_dir = os.path.join(args.output_dir, f"case{i}")
        logger.info(f"[{i}/{len(inputs)}] {os.path.basename(inp)} ...")
        results.append(run_one(inp, out_dir, do_search=not args.no_search))

    # 汇总
    n = len(results)
    ok = sum(1 for r in results if r["success"])
    success_rate = ok / n if n else 0.0
    elapsed = [r["elapsed"] for r in results if r["success"]]
    p50 = sorted(elapsed)[len(elapsed) // 2] if elapsed else 0.0
    checks = [r["checks"] for r in results if r["checks"]]
    files_ok = sum(1 for c in checks if c.get("files_complete"))
    num_ok = sum(1 for c in checks if c.get("claim_numbering"))
    term_ok = sum(1 for c in checks if c.get("terminology"))

    avg_cost = sum(r.get("cost", 0.0) for r in results) / n if n else 0.0
    avg_prompt = sum(r.get("usage", {}).get("prompt_tokens", 0) for r in results) / n if n else 0.0
    avg_completion = sum(r.get("usage", {}).get("completion_tokens", 0) for r in results) / n if n else 0.0
    lines = [
        "# 专利写作 Agent 评测报告\n",
        f"- 测试集规模：{n} 题（{args.dataset}）",
        f"- 生成成功率：{ok}/{n} = {success_rate:.0%}",
        f"- 端到端耗时：P50={p50:.1f}s，P95={_p95(elapsed):.1f}s",
        f"- 平均 token：prompt {avg_prompt:.0f} / completion {avg_completion:.0f}",
        f"- 单次生成成本：平均 ¥{avg_cost:.3f}（按 config.TOKEN_PRICE 单价）",
        "",
        "## 合规检查（规则实现）\n",
        f"| 检查项 | 通过/总数 | 通过率 |",
        f"|--------|-----------|--------|",
        f"| 文件完整性（四件套非空） | {files_ok}/{n} | {files_ok/n:.0%} |",
        f"| 权项编号连续性 | {num_ok}/{n} | {num_ok/n:.0%} |",
        f"| 术语一致性（关键词出现在说明书） | {term_ok}/{n} | {term_ok/n:.0%} |",
        "",
        "## 逐题明细\n",
        "| # | 交底书 | 成功 | 耗时(s) | 文件完整 | 编号连续 | 术语一致 |",
        "|---|--------|------|---------|----------|----------|----------|",
    ]
    for i, r in enumerate(results, 1):
        c = r["checks"]
        lines.append(
            f"| {i} | {os.path.basename(inputs[i-1])} | {'✅' if r['success'] else '❌'} "
            f"| {r['elapsed']:.1f} | {c.get('files_complete', False)} "
            f"| {c.get('claim_numbering', False)} | {c.get('terminology', False)} |"
        )
    lines.append("\n## 备注\n")
    lines.append("- 成本按 config.TOKEN_PRICE 单价估算，单价须按实际供应商价目表确认。")
    lines.append("- 数字全部来自本次实测，不编造；失败题目见逐题明细中的 ❌ 项。")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"\n报告已生成：{args.output}")


if __name__ == "__main__":
    main()
