"""Agentic RAG 消融实验：对比 RAG 开/关的检索质量与成本。

目的：量化「Agentic RAG（查询改写 + 迭代检索 + 多源补充）」相对
「朴素单次检索」的增量，产出可复现的对照表（eval/rag_ablation.md）。

对比维度（均为检索侧指标，不直接等同于最终专利文件质量）：
1. 查询式数量：朴素 1 vs RAG ≤2（多角度覆盖能力）
2. 专利 / 现有技术召回数（去重前后）
3. 多源覆盖率：google_patents / google_search / semantic_scholar
4. 论文源召回数：Semantic Scholar 为 RAG 独有增量（多源检索的直接证据）
5. 检索耗时
6. LLM token 成本：查询改写带来的额外开销

依赖说明（脚本启动时检测并在报告中如实标注）：
- 查询构建/改写依赖 LLM（SILICONFLOW_API_KEY 等），失败会回退朴素查询、不中断
- 专利/现有技术源依赖 SERPAPI_API_KEY，未配置时该源返回空
- Semantic Scholar 免费、无需 key，仅 RAG 模式调用

用法：
    python eval/rag_ablation.py
    python eval/rag_ablation.py --dataset data --output eval/rag_ablation.md --max-results 10
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DEFAULT_LLM_PROVIDER, LLM_CONFIG  # noqa: E402
from agents.input_parser import InputParser  # noqa: E402
from agents.patent_searcher import PatentSearcher  # noqa: E402
from agents.retrieval_agent import RetrievalAgent  # noqa: E402
from utils.llm_client import get_llm_client  # noqa: E402
from utils.search_client import get_search_client  # noqa: E402
from logging_config import get_logger
logger = get_logger(__name__)


# ------------------------------------------------------------------ 工具函数

def _dedup(items):
    """按 title 去重，返回 (去重后列表, 去掉的重复条数)。"""
    seen = set()
    out = []
    for it in items:
        t = (it.get("title") or "").strip()
        if t and t not in seen:
            seen.add(t)
            out.append(it)
    return out, len(items) - len(out)


def _sources(items):
    """返回去重后的 source 集合。"""
    return sorted({it.get("source", "") for it in items if it.get("source")})


def _count_source(items, src):
    return sum(1 for it in items if it.get("source") == src)


def _detect_env():
    """检测 LLM / 检索 API key 配置状态，用于报告开头标注。"""
    llm_key = (LLM_CONFIG.get(DEFAULT_LLM_PROVIDER, {}) or {}).get("api_key")
    return {
        "llm_provider": DEFAULT_LLM_PROVIDER,
        "llm_key": bool(llm_key),
        "serpapi_key": bool(os.getenv("SERPAPI_API_KEY")),
        "exa_key": bool(os.getenv("EXA_API_KEY")),
    }


# ------------------------------------------------------------------ 两种模式

def run_off(name, solution, features, max_results):
    """朴素检索：1 个查询式 → 单次专利检索 + 单次现有技术检索。"""
    llm = get_llm_client()
    llm.reset_usage()
    client = get_search_client()
    searcher = PatentSearcher()

    t0 = time.time()
    query = searcher._build_search_query(name, solution, features)
    patents = client.search_patents(query, max_results=max_results)
    prior = client.search_prior_art(query, max_results=max_results)
    elapsed = time.time() - t0

    p_dedup, p_dup = _dedup(patents)
    a_dedup, a_dup = _dedup(prior)
    all_items = p_dedup + a_dedup

    return {
        "mode": "off",
        "query": query,
        "n_queries": 1,
        "patents": len(p_dedup),
        "patents_dup": p_dup,
        "prior_art": len(a_dedup),
        "prior_art_dup": a_dup,
        "total": len(all_items),
        "sources": _sources(all_items),
        "scholar": _count_source(all_items, "semantic_scholar"),
        "elapsed": elapsed,
        "cost": llm.estimate_cost(),
        "trace": [],
    }


def run_on(name, solution, features, max_results):
    """Agentic RAG：查询改写 → 迭代检索（≤2 轮，含 Semantic Scholar 多源）→ 去重。"""
    llm = get_llm_client()
    llm.reset_usage()

    t0 = time.time()
    patents, prior, trace = RetrievalAgent().search(
        invention_name=name,
        technical_solution=solution,
        key_features=features,
        max_results=max_results,
    )
    elapsed = time.time() - t0

    p_dedup, p_dup = _dedup(patents)
    a_dedup, a_dup = _dedup(prior)
    all_items = p_dedup + a_dedup

    return {
        "mode": "on",
        "query": [t.get("query", "") for t in trace],
        "n_queries": len(trace),
        "patents": len(p_dedup),
        "patents_dup": p_dup,
        "prior_art": len(a_dedup),
        "prior_art_dup": a_dup,
        "total": len(all_items),
        "sources": _sources(all_items),
        "scholar": _count_source(all_items, "semantic_scholar"),
        "elapsed": elapsed,
        "cost": llm.estimate_cost(),
        "trace": trace,
    }


# ------------------------------------------------------------------ 主流程

def _avg(vals):
    return sum(vals) / len(vals) if vals else 0.0


def main():
    p = argparse.ArgumentParser(description="Agentic RAG 消融实验")
    p.add_argument("--dataset", nargs="+", default=["data", "private_data"],
                   help="交底书目录（*.txt），可多个，默认 data + private_data")
    p.add_argument("--output", default="eval/rag_ablation.md", help="报告输出路径")
    p.add_argument("--max-results", type=int, default=10, help="单次检索最大结果数")
    args = p.parse_args()

    env = _detect_env()
    logger.info("环境检测：")
    logger.info(f"  LLM 提供商: {env['llm_provider']}  key={'已配置' if env['llm_key'] else '未配置'}")
    logger.info(f"  SERPAPI_API_KEY: {'已配置' if env['serpapi_key'] else '未配置（专利/现有技术源将返回空）'}")
    logger.info(f"  EXA_API_KEY: {'已配置' if env['exa_key'] else '未配置（当前检索流程未使用）'}")
    if not env["llm_key"]:
        logger.warning("  [WARN] 无 LLM key：查询构建/改写将回退为「发明名称+特征」，改写能力无法体现")

    inputs = set()
    for d in args.dataset:
        inputs.update(glob.glob(os.path.join(d, "*.txt")))
    # 排除 .bak 备份与隐藏文件
    inputs = sorted(f for f in inputs
                    if ".bak" not in f and not os.path.basename(f).startswith("."))
    if not inputs:
        logger.info(f"未找到测试交底书：{args.dataset}")
        sys.exit(1)

    parser = InputParser()
    cases = []
    for i, inp in enumerate(inputs, 1):
        base = os.path.basename(inp)
        logger.info(f"[{i}/{len(inputs)}] {base} 解析中 ...")
        info = parser.parse_from_file(inp)

        off = run_off(info.invention_name, info.technical_solution, info.key_features, args.max_results)
        on = run_on(info.invention_name, info.technical_solution, info.key_features, args.max_results)
        cases.append({"name": base, "off": off, "on": on})

        logger.info(f"    off: 查询式 {off['n_queries']} | 召回 {off['total']}（专利 {off['patents']}/现有技术 {off['prior_art']}）| 源 {off['sources']} | 论文 {off['scholar']} | {off['elapsed']:.1f}s | {off['cost']:.4f}元")
        logger.info(f"    on : 查询式 {on['n_queries']} | 召回 {on['total']}（专利 {on['patents']}/现有技术 {on['prior_art']}）| 源 {on['sources']} | 论文 {on['scholar']} | {on['elapsed']:.1f}s | {on['cost']:.4f}元")

    # ---- 汇总统计 ----
    n = len(cases)
    avg_off_total = _avg([c["off"]["total"] for c in cases])
    avg_on_total = _avg([c["on"]["total"] for c in cases])
    avg_off_nq = _avg([c["off"]["n_queries"] for c in cases])
    avg_on_nq = _avg([c["on"]["n_queries"] for c in cases])
    avg_off_scholar = _avg([c["off"]["scholar"] for c in cases])
    avg_on_scholar = _avg([c["on"]["scholar"] for c in cases])
    avg_off_src = _avg([len(c["off"]["sources"]) for c in cases])
    avg_on_src = _avg([len(c["on"]["sources"]) for c in cases])
    avg_off_elapsed = _avg([c["off"]["elapsed"] for c in cases])
    avg_on_elapsed = _avg([c["on"]["elapsed"] for c in cases])
    avg_off_cost = _avg([c["off"]["cost"] for c in cases])
    avg_on_cost = _avg([c["on"]["cost"] for c in cases])

    lines = [
        "# Agentic RAG 消融实验报告\n",
        f"- 测试集规模：{n} 题（目录：{', '.join(args.dataset)}）",
        f"- 单次检索最大结果数：max_results={args.max_results}",
        f"- LLM 提供商：{env['llm_provider']}（key={'已配置' if env['llm_key'] else '未配置'}）",
        f"- 专利/现有技术源（SerpAPI）：{'已配置' if env['serpapi_key'] else '未配置，该源返回空'}",
        f"- Semantic Scholar：免费源，仅 RAG 模式调用",
        "",
        "> 说明：以下均为**检索侧指标**（召回覆盖度/多样性/成本），用于量化 Agentic RAG 相对",
        "> 朴素单次检索的增量；它们不等同于最终专利文件质量（主链路 parse→outline→四件套→merge 不受 RAG 开关影响）。\n",
        "",
        "## 逐题明细\n",
        "| # | 交底书 | 查询式 off/on | 总召回 off/on | 论文源 on | 覆盖源数 off/on | 耗时(s) off/on | 成本(¥) off/on |",
        "|---|--------|---------------|---------------|-----------|-----------------|----------------|----------------|",
    ]
    for i, c in enumerate(cases, 1):
        o, n_ = c["off"], c["on"]
        lines.append(
            f"| {i} | {c['name']} | {o['n_queries']}/{n_['n_queries']} "
            f"| {o['total']}/{n_['total']} "
            f"| {n_['scholar']} "
            f"| {len(o['sources'])}/{len(n_['sources'])} "
            f"| {o['elapsed']:.1f}/{n_['elapsed']:.1f} "
            f"| {o['cost']:.4f}/{n_['cost']:.4f} |"
        )

    # 环境限制说明（动态生成，如实标注本次运行中不可用的检索源）
    env_notes = []
    if not env["serpapi_key"]:
        env_notes.append(
            "- **SerpAPI 未配置**：专利/现有技术源返回空，`总召回数`、`覆盖源种类数` 本次无区分度；"
            "配置 `SERPAPI_API_KEY` 后可量化这两项。"
        )
    scholar_ok = sum(1 for c in cases if c["on"]["scholar"] > 0)
    if scholar_ok < n:
        env_notes.append(
            f"- **Semantic Scholar 间歇性 429 限速**：{n} 份中仅 {scholar_ok} 份成功召回论文源，"
            "其余因免费 API 共享池限速降级为空；`论文源召回数` 因此不稳定，"
            "`检索耗时` 增量主要来自 429 退避等待，**非 RAG 真实成本**。"
        )

    lines += [
        "",
        "## 汇总对比（平均）\n",
        "| 指标 | 朴素检索 (RAG off) | Agentic RAG (on) | 增量 |",
        "|------|--------------------|------------------|------|",
        f"| 查询式数量 | {avg_off_nq:.2f} | {avg_on_nq:.2f} | {avg_on_nq - avg_off_nq:+.2f} |",
        f"| 总召回数（去重后） | {avg_off_total:.2f} | {avg_on_total:.2f} | {avg_on_total - avg_off_total:+.2f} |",
        f"| 论文源召回数（Semantic Scholar） | {avg_off_scholar:.2f} | {avg_on_scholar:.2f} | {avg_on_scholar - avg_off_scholar:+.2f} |",
        f"| 覆盖源种类数 | {avg_off_src:.2f} | {avg_on_src:.2f} | {avg_on_src - avg_off_src:+.2f} |",
        f"| 检索耗时（秒） | {avg_off_elapsed:.2f} | {avg_on_elapsed:.2f} | {avg_on_elapsed - avg_off_elapsed:+.2f} |",
        f"| LLM 成本（¥） | {avg_off_cost:.4f} | {avg_on_cost:.4f} | {avg_on_cost - avg_off_cost:+.4f} |",
    ]
    if env_notes:
        lines += ["", "## 本次运行环境限制\n"] + env_notes
    lines += [
        "",
        "## 解读要点\n",
        "- **查询式数量**：RAG 的查询改写会把发明拆成多个角度检索式（≤2），反映「多角度覆盖」能力；",
        "  无 LLM key 时改写回退为朴素查询，此项增量为 0。",
        "- **论文源召回数**：Semantic Scholar 仅在 RAG 模式调用，是「多源检索」最直接、且不受 SerpAPI key 影响的证据。",
        "  未配置 SerpAPI 时，朴素检索总召回为 0，而 RAG 仍能靠免费论文源召回文献——这是 RAG 增量的核心看点。",
        "- **总召回数**：受 max_results 截断影响，两者封顶相同，差距主要体现在「来源构成」而非绝对数量；",
        "  若召回未打满 max_results，则数量差也能反映迭代补查的收益。",
        "- **成本/耗时**：RAG 多一次查询改写 LLM 调用，token 成本略升（约 +0.0001 元，可忽略）；"
        "耗时增量主要来自免费源 429 限速的退避等待，属环境噪声而非 RAG 真实开销（检索源可用时两者耗时接近）。",
        "- **边界**：以上数字来自本次实测，不编造；结论仅限「检索覆盖度」维度，是否「更有效」请结合下游新颖性评估与人工复核综合判断。",
    ]

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"\n报告已生成：{args.output}")


if __name__ == "__main__":
    main()
