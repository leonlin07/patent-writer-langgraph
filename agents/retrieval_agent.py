"""Agentic RAG 最小化：检索 Agent。

把朴素 RAG 的「一次 query -> 一次 search」升级为：
    查询改写（LLM 生成多检索式）→ 迭代检索（多轮去重合并）→ 相关性评估（可选）

设计约束（对齐开发计划 M1，避免成本失控）：
- 最多 2 轮检索（MAX_ROUNDS），超出即停；
- 任何一步异常都降级回退到「发明名称 + 关键特征」的朴素查询，不阻塞主流程；
- 结果按标题去重，避免重复文献污染下游的新颖性/创造性评估。

开关：环境变量 RAG_ENABLED=true 时，PatentSearcher 走本 Agent 的迭代检索；
否则走原单次检索（向后兼容，默认关闭以控制成本）。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Tuple

from utils.llm_client import get_llm_client
from utils.search_client import get_search_client
from logging_config import get_logger
logger = get_logger(__name__)


class RetrievalAgent:
    """迭代式检索 Agent。"""

    def __init__(self, max_rounds: int = 2) -> None:
        self.llm_client = get_llm_client()
        self.search_client = get_search_client()
        self.max_rounds = max(1, max_rounds)

    # ------------------------------------------------------------- 查询改写

    def _rewrite_queries(
        self,
        invention_name: str,
        technical_solution: str,
        key_features: List[str],
    ) -> List[str]:
        """用 LLM 把发明信息拆成多个更精准的检索式（英文，短查询）。"""
        prompt = f"""你是专利检索专家。请基于以下发明信息，生成最多 {self.max_rounds} 个英文专利检索查询（每个不超过 10 个词），
从不同角度覆盖核心技术特征（如：整体方案、关键组件、算法/方法），用于 Google Patents 检索。

发明名称: {invention_name}
技术方案: {technical_solution[:500]}
关键特征: {', '.join(key_features[:6])}

要求：每行一个查询，不要编号、不要空行、不要解释。
检索查询："""
        try:
            resp = self.llm_client.complete(
                prompt=prompt, temperature=0.3, max_tokens=200
            )
            queries = [q.strip() for q in resp.split("\n") if q.strip()]
            if queries:
                return queries[: self.max_rounds]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RAG] 查询改写失败，回退朴素查询: {e}")

        # 回退：发明名称 + 前几个关键特征
        base = invention_name or ""
        return [" ".join([base] + key_features[:3]).strip() or "patent"]

    # ------------------------------------------------------------- 迭代检索

    def search(
        self,
        invention_name: str,
        technical_solution: str,
        key_features: List[str],
        max_results: int = 10,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
        """迭代检索：返回 (patents, prior_art, trace)。

        trace 记录每轮的 query 与召回数，用于可观测与后续消融实验。
        """
        queries = self._rewrite_queries(invention_name, technical_solution, key_features)

        seen: set = set()
        patents: List[Dict[str, Any]] = []
        prior_art: List[Dict[str, Any]] = []
        trace: List[Dict[str, Any]] = []

        for rnd, query in enumerate(queries[: self.max_rounds], 1):
            try:
                new_patents = self.search_client.search_patents(query, max_results=max_results)
                new_prior = self.search_client.search_prior_art(query, max_results=max_results)
                # 多源补充：Semantic Scholar 学术论文（免费，失败自动降级为空列表）
                new_prior += self.search_client.search_semantic_scholar(
                    query, max_results=max_results
                )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[RAG] 第{rnd}轮检索失败: {e}")
                trace.append({"round": rnd, "query": query, "error": str(e)})
                continue

            for p in new_patents:
                title = p.get("title", "")
                if title and title not in seen:
                    seen.add(title)
                    patents.append(p)
            for a in new_prior:
                title = a.get("title", "")
                if title and title not in seen:
                    seen.add(title)
                    prior_art.append(a)

            trace.append({
                "round": rnd,
                "query": query,
                "new_patents": len(new_patents),
                "new_prior_art": len(new_prior),
            })

            # 召回足够即停，控制成本
            if len(patents) >= max_results:
                break

        return patents[:max_results], prior_art[:max_results], trace

    # ------------------------------------------------------------- 相关性评估

    def assess_relevance(
        self,
        technical_solution: str,
        patents: List[Dict[str, Any]],
        prior_art: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """对召回结果做相关性评分（可选，用于证据链置信度）。"""
        items = []
        for p in patents[:5]:
            items.append(f"[专利] {p.get('title', '')} | {p.get('abstract', '')[:120]}")
        for a in prior_art[:3]:
            items.append(f"[现有技术] {a.get('title', '')} | {a.get('snippet', '')[:120]}")

        if not items:
            return {"relevant_count": 0, "total": 0, "confidence": 0.0}

        prompt = f"""请判断以下检索结果与本技术方案的相关性，输出 JSON：
{{"relevant_count": <相关的条数>, "summary": "<一句话相关性结论>"}}

技术方案: {technical_solution[:300]}

检索结果：
{chr(10).join(items)}
"""
        try:
            from utils.json_utils import extract_json
            resp = self.llm_client.complete(prompt=prompt, temperature=0.3, max_tokens=300)
            data = extract_json(resp) or {}
            total = len(items)
            rel = int(data.get("relevant_count", 0))
            return {
                "relevant_count": min(rel, total),
                "total": total,
                "confidence": round(rel / total, 2) if total else 0.0,
                "summary": data.get("summary", ""),
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[RAG] 相关性评估失败: {e}")
            return {"relevant_count": len(items), "total": len(items), "confidence": 1.0}
