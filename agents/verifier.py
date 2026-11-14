"""专利合规校验器：运行时质量门禁（verify 节点核心）。

定位：在 claims/description 生成之后、merge 之前执行，把「三项合规检查」
从「事后评测」前移到「运行时门禁」，产出可量化、可复现的校验报告。

三项检查（规则优先，规则判不了的再用 LLM-as-judge）：
1. 权项编号连续性（规则）：权利要求编号 1..N 连续
2. 术语一致性（规则）：权项中的「所述X」术语必须在说明书正文出现
3. 说明书支持性（规则）：关键特征的核心词必须在说明书正文展开
4. LLM 语义支持性（可选，开关 VERIFY_USE_LLM 控制）：语义级支持关系判断

成本设计：规则层 0 次 LLM 调用（纯 CPU 正则/集合运算）；LLM 层仅在
VERIFY_USE_LLM=true 且规则层发现问题时触发 1 次调用，成本可控。

回退策略（graph.py 的 route_after_verify 决定）：
- 通过 → merge；
- 不通过且 VERIFY_ENFORCE=true → 回退重写 claims（限 1 次，防死循环）；
- 不通过且未开启强制 → 仅落 errors/warning，仍进 merge。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List

from utils.llm_client import get_llm_client
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class VerificationIssue:
    """单条校验问题。"""

    check: str
    severity: str  # "error"（阻断级）/ "warning"（提示级）
    detail: str


@dataclass
class VerificationReport:
    """校验报告。"""

    passed: bool
    issues: List[VerificationIssue] = field(default_factory=list)
    checks: Dict[str, bool] = field(default_factory=dict)
    llm_calls: int = 0  # 校验过程中的 LLM 调用次数（成本核算）

    def to_markdown(self) -> str:
        lines = ["# 专利合规校验报告\n"]
        lines.append(f"- 总体结论：{'✅ 通过' if self.passed else '❌ 未通过'}")
        lines.append(f"- LLM 调用次数：{self.llm_calls}\n")
        lines.append("## 检查项\n")
        for k, v in self.checks.items():
            lines.append(f"- {k}: {'✅' if v else '❌'}")
        if self.issues:
            lines.append("\n## 问题清单\n")
            for i, iss in enumerate(self.issues, 1):
                tag = "🔴" if iss.severity == "error" else "🟡"
                lines.append(f"{i}. {tag} [{iss.check}] {iss.detail}")
        else:
            lines.append("\n## 问题清单\n\n无问题。")
        return "\n".join(lines)


class Verifier:
    """合规校验器（默认纯规则，0 LLM 成本）。"""

    def __init__(self, use_llm: bool = False) -> None:
        self.llm_client = get_llm_client()
        self.use_llm = use_llm

    def verify(self, technical_info: Any, claims_doc: Any, description_doc: Any) -> VerificationReport:
        issues: List[VerificationIssue] = []
        checks: Dict[str, bool] = {}

        claims_text = "\n".join(getattr(c, "full_text", "") for c in claims_doc.claims)
        desc_text = "\n".join(getattr(s, "content", "") for s in description_doc.sections)

        # 1. 权项编号连续性（规则）
        nums = [getattr(c, "number", 0) for c in claims_doc.claims]
        checks["claim_numbering"] = nums == list(range(1, len(nums) + 1))
        if not checks["claim_numbering"]:
            issues.append(VerificationIssue(
                "权项编号连续性", "error", f"编号不连续：{nums}"
            ))

        # 2. 术语一致性（规则）：权项「所述X」须在说明书出现
        terms = self._extract_terms(claims_text)
        missing_terms = sorted({t for t in terms if t and t not in desc_text})
        checks["terminology"] = not missing_terms
        if missing_terms:
            issues.append(VerificationIssue(
                "术语一致性", "error", f"权项术语未在说明书出现：{missing_terms[:6]}"
            ))

        # 3. 说明书支持性（规则）：关键特征核心词须在说明书展开
        missing_features = self._check_support(technical_info, desc_text)
        checks["support"] = not missing_features
        if missing_features:
            issues.append(VerificationIssue(
                "说明书支持性", "warning", f"特征未在说明书展开：{missing_features[:5]}"
            ))

        # 4. LLM 语义支持性（可选，仅在发现问题时触发 1 次）
        llm_calls = 0
        if self.use_llm and issues:
            llm_calls = self._llm_judge(claims_text, desc_text, issues)

        passed = all(i.severity != "error" for i in issues)
        return VerificationReport(
            passed=passed, issues=issues, checks=checks, llm_calls=llm_calls
        )

    @staticmethod
    def _extract_terms(claims_text: str) -> List[str]:
        """提取权项中的「所述X」术语。

        原正则 `所述(.{2,15})` 为贪婪匹配，会把「所述图像采集装置被配置为…」
        整段从句吞入，产生「图像采集装置被配置为采集载带图」这类句子片段误报。
        这里改为非贪婪 + 停止字符（动词首字/虚词/标点），在第一个功能字处截断，
        从而提取出真正的术语（如「图像采集装置」「处理器」）。
        """
        stop = "被用配执进包为可与和或存采构划获实设连控处分具于以而及等使将着过了到从对把让中其之有所"
        pattern = re.compile(
            rf"所述的?([\u4e00-\u9fa5A-Za-z0-9]{{2,15}}?)(?=[{re.escape(stop)}，。；、：:（）()\s]|$)"
        )
        terms = [m for m in pattern.findall(claims_text) if m]
        # 后处理：去掉末尾「的」、过滤高频泛化词与过短碎片
        generic = {"图像", "系统", "方法", "装置", "步骤", "数据", "目标", "信息", "模块", "单元"}
        cleaned = []
        for t in terms:
            t = t.strip("的")
            if t in generic or len(t) < 3:
                continue
            cleaned.append(t)
        return cleaned

    @staticmethod
    def _check_support(technical_info: Any, desc_text: str) -> List[str]:
        """关键特征的核心词（2-6 字中文词）是否在说明书出现。"""
        missing: List[str] = []
        for f in (getattr(technical_info, "key_features", None) or []):
            core_words = re.findall(r"[\u4e00-\u9fa5]{2,6}", str(f))
            if core_words and not any(w in desc_text for w in core_words):
                missing.append(str(f)[:20])
        return missing

    def _llm_judge(self, claims_text: str, desc_text: str, issues: List[VerificationIssue]) -> int:
        """LLM-as-judge：语义级支持性判断，返回调用次数（成功=1，失败=0）。"""
        prompt = f"""你是专利审查员。请判断权利要求是否得到说明书的支持，输出 JSON：
{{"supported": true/false, "reason": "一句话理由"}}

权利要求（节选）：
{claims_text[:800]}

说明书（节选）：
{desc_text[:1500]}
"""
        try:
            from utils.json_utils import extract_json
            resp = self.llm_client.complete(prompt=prompt, temperature=0.3, max_tokens=300)
            data = extract_json(resp) or {}
            if data.get("supported") is False:
                issues.append(VerificationIssue(
                    "LLM支持性判断", "warning", str(data.get("reason", ""))[:80]
                ))
            return 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[verify] LLM 判断失败，跳过: {e}")
            return 0
