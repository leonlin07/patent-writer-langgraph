"""
权利要求撰写器模块
撰写专利权利要求书
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from agents.input_parser import TechnicalInfo
from agents.outline_generator import PatentOutline
from utils.llm_client import get_llm_client
from config import PATENT_CONFIG
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class Claim:
    """权利要求数据结构"""
    number: int
    type: str  # "independent" 或 "dependent"
    refers_to: Optional[int]  # 引用的权利要求号（独立权利要求为None）
    subject: str  # 主题（产品/方法）
    features: List[str]  # 技术特征列表
    full_text: str = ""  # 完整文本


@dataclass
class ClaimsDocument:
    """权利要求书数据结构"""
    invention_title: str
    claims: List[Claim]
    total_count: int = 0
    
    def __post_init__(self):
        self.total_count = len(self.claims)


class ClaimsWriter:
    """权利要求撰写器类"""
    
    def __init__(self):
        """初始化权利要求撰写器"""
        self.llm_client = get_llm_client()
    
    def write(
        self,
        technical_info: TechnicalInfo,
        outline: PatentOutline,
        min_claims: Optional[int] = None,
        max_independent: Optional[int] = None
    ) -> ClaimsDocument:
        """
        撰写权利要求书
        
        Args:
            technical_info: 技术信息
            outline: 专利大纲
            min_claims: 最少权利要求数量（默认取 config.PATENT_CONFIG）
            max_independent: 最大独立权利要求数量（默认取 config.PATENT_CONFIG）
            
        Returns:
            ClaimsDocument对象
        """
        cfg = PATENT_CONFIG.get("claims", {})
        min_claims = cfg.get("min_claims", 5) if min_claims is None else min_claims
        max_independent = (
            cfg.get("max_independent_claims", 3)
            if max_independent is None else max_independent
        )

        claims = []
        
        # 撰写独立权利要求
        independent_claims = self._write_independent_claims(
            technical_info, outline, max_independent
        )
        claims.extend(independent_claims)
        
        # 撰写从属权利要求
        dependent_claims = self._write_dependent_claims(
            technical_info, outline, independent_claims, min_claims - len(independent_claims)
        )
        claims.extend(dependent_claims)
        
        # 重新编号
        for i, claim in enumerate(claims, 1):
            claim.number = i
        
        return ClaimsDocument(
            invention_title=technical_info.invention_name,
            claims=claims
        )
    
    # ------------------------------------------------------------ 内容守卫

    @staticmethod
    def _require_text(response: str, label: str, min_len: int = 20) -> str:
        """
        校验 LLM 返回的权利要求文本，空内容直接抛错以触发上层兜底。

        曾经的问题：response 为空串时 `response.strip()` 不报错，
        一条 full_text='' 的权利要求会被静默追加，最终产出"看起来完整、
        实际是空"的法律文件。这里显式拦截。

        Args:
            response: LLM 原始返回
            label: 用于日志的标识
            min_len: 最短有效长度（权项低于此值基本可判定为生成失败）
        """
        text = (response or "").strip()
        if not text:
            raise ValueError(f"{label}: LLM 返回空响应")
        if len(text) < min_len:
            raise ValueError(f"{label}: 内容过短（{len(text)}字 < {min_len}），判定为生成失败")
        return text

    def _write_independent_claims(
        self,
        technical_info: TechnicalInfo,
        outline: PatentOutline,
        max_count: int
    ) -> List[Claim]:
        """
        撰写独立权利要求
        
        Args:
            technical_info: 技术信息
            outline: 专利大纲
            max_count: 最大数量
            
        Returns:
            独立权利要求列表
        """
        claims = []

        # 判断发明类型并撰写相应权利要求
        invention_types = self._determine_invention_types(technical_info)
        
        # 类型 → 主题规则。旧实现只把类型名丢给 LLM，LLM 会无视它，
        # 把"产品/装置"类型也写成一条方法权项，结果多条独权实质重复。
        TYPE_RULE = {
            '方法': '主题写作"一种……方法"，特征部分按流程步骤表述',
            '产品/装置': '主题写作"一种……系统"，特征部分必须写明该系统的组成'
                         '（图像采集装置、处理器、存储器）以及各组成部分被配置为'
                         '实现相应功能的限定，不得写成步骤流程',
            '存储介质': '主题写作"一种计算机可读存储介质"，特征部分必须写明'
                        '其上存储有计算机程序、该程序被处理器执行时实现所述方法',
        }

        for i, inv_type in enumerate(invention_types[:max_count], 1):
            type_rule = TYPE_RULE.get(inv_type, '')

            prompt = f"""请撰写第{i}项独立权利要求。

发明名称: {technical_info.invention_name}
本项权利要求的类型: {inv_type}
【主题强制要求】{type_rule}
技术方案: {technical_info.technical_solution[:1000]}
关键特征: {', '.join(technical_info.key_features[:10])}
{self._ref_claim_block(claims)}

撰写要求：
1. 采用"其特征在于"的格式
2. 只包含必要技术特征（解决技术问题所必需的特征）
3. 避免写入非必要特征
4. 使用规范的技术术语
5. 一项权利要求只能有一个句号
6. 主题名称必须严格符合上面的【主题强制要求】，不得写成其他类型

{self._independent_rules_block(technical_info, i)}
请直接输出权利要求文本，格式如下：
{i}. [主题]，其特征在于，[必要技术特征描述]。

权利要求："""

            try:
                response = self.llm_client.complete(
                    prompt=prompt,
                    temperature=0.3,
                    max_tokens=1500
                )
                
                full_text = self._require_text(response, f"独立权利要求{i}")

                # 解析主题和特征
                subject = self._extract_subject(full_text)
                features = self._extract_features(full_text)
                
                claims.append(Claim(
                    number=i,
                    type="independent",
                    refers_to=None,
                    subject=subject,
                    features=features,
                    full_text=full_text
                ))
            except Exception as e:
                logger.warning(f"独立权利要求{i}撰写失败: {e}")
        
        return claims
    
    @staticmethod
    def _independent_rules_block(technical_info: "TechnicalInfo", index: int) -> str:
        """
        把交底书显式给出的独立权利要求撰写规则注入提示词。

        没有这几条约束时，LLM 会把本该下沉到从属权项的具体特征（特定形状、
        具体位深、具体阈值）写进独权，既限缩保护范围，又让多条独权范围不一致。
        """
        rules = getattr(technical_info, "independent_claim_rules", "") or ""
        if not rules:
            return ""
        return (
            "【交底书规定的独权撰写规则，必须严格遵守】\n"
            f"{rules}\n\n"
            f"当前撰写的是第{index}项独立权利要求，请按其对应的条款执行上述规则。\n"
        )

    @staticmethod
    def _ref_claim_block(claims: List["Claim"]) -> str:
        """
        把已写好的第一条独权原文作为基准传给后续独权。

        三条独权若各自独立生成，保护范围会漂移（实测出现独权2 限定四级分级、
        独权3 漏掉预处理步骤的情况），给审查和后续答复带来麻烦。
        """
        if not claims:
            return ""
        base = claims[0].full_text
        return (
            "\n【范围一致性基准】第1项独立权利要求已确定如下，"
            "本项权利要求的保护范围必须与之对应一致，"
            "不得引入第1项中没有的限定，也不得遗漏其中的必要步骤：\n"
            f"{base}\n"
        )

    @staticmethod
    def _extract_dependent_features_placeholder() -> None:  # pragma: no cover
        return None

    def _write_dependent_claims(
        self,
        technical_info: TechnicalInfo,
        outline: PatentOutline,
        independent_claims: List[Claim],
        min_count: int
    ) -> List[Claim]:
        """
        撰写从属权利要求
        
        Args:
            technical_info: 技术信息
            outline: 专利大纲
            independent_claims: 独立权利要求列表
            min_count: 最少数量
            
        Returns:
            从属权利要求列表
        """
        claims = []
        
        # 为每个独立权利要求撰写从属权利要求
        # 优先采用交底书显式列举的从属权项附加特征清单。
        # 实测表明：若让 LLM 从 key_features 自由发挥，会编造交底书中不存在的技术细节
        # （例如把「长圆形」写成「椭圆」），导致真正的发明点反而没被从属权项保住。
        dep_features = list(getattr(technical_info, "dependent_features", None) or [])
        if dep_features:
            logger.info(f"  [INFO] 采用交底书列举的从属权项特征清单: {len(dep_features)}项")
            remaining_features = dep_features
        else:
            remaining_features = technical_info.key_features.copy()

            # 移除已在独立权利要求中使用的特征
            for ic in independent_claims:
                for f in ic.features:
                    if f in remaining_features:
                        remaining_features.remove(f)
        
        claim_num = len(independent_claims) + 1
        
        # 基于剩余特征撰写从属权利要求
        # 从属权利要求统一挂在方法独权下。旧实现用 i % len() 交替引用，
        # 造成权项链混乱、主题名称前后不一致。
        primary = independent_claims[0].number if independent_claims else 1
        ref_text = independent_claims[0].full_text if independent_claims else ""

        # 交底书显式列举的清单代表撰写人意图，应全部落地；
        # 回退到 LLM 归纳的 key_features 时才按最低数量截断，避免噪声特征混入。
        limit = len(remaining_features) if dep_features else min_count + 5

        for i, feature in enumerate(remaining_features[:limit]):
            refers_to = primary
            
            prompt = f"""请撰写第{claim_num}项从属权利要求。

发明名称: {technical_info.invention_name}
引用的权利要求: 第{refers_to}项
该权利要求的全文如下（务必先读完，避免重复）：
{ref_text}

本次要写入的附加特征: {feature}

撰写要求：
1. 格式："根据权利要求{refers_to}所述的[主题]，其特征在于，[附加特征]。"
2. 附加特征必须是上面引用的权利要求【没有记载】的技术细节，例如具体
   算法名称、具体阈值或阈值区间、具体的几何形状、具体的参数取值、
   具体的模块构成；严禁把该权项已有的内容换一种说法再写一遍
3. 附加特征要写成完整的句子，主谓宾齐全，不能只写一个名词短语
4. 一项权利要求只能有一个句号

请直接输出权利要求文本。

权利要求："""

            try:
                response = self.llm_client.complete(
                    prompt=prompt,
                    temperature=0.3,
                    max_tokens=800
                )
                
                full_text = self._require_text(response, f"从属权利要求{claim_num}")
                
                claims.append(Claim(
                    number=claim_num,
                    type="dependent",
                    refers_to=refers_to,
                    subject=independent_claims[0].subject if independent_claims else "",
                    features=[feature],
                    full_text=full_text
                ))
                
                claim_num += 1
            except Exception as e:
                logger.warning(f"从属权利要求{claim_num}撰写失败: {e}")
        
        # 如果数量不足，生成更多通用从属权利要求
        while len(claims) < min_count:
            refers_to = independent_claims[0].number if independent_claims else 1
            
            prompt = f"""请撰写第{claim_num}项从属权利要求。

发明名称: {technical_info.invention_name}
引用的权利要求: 第{refers_to}项
该权利要求的全文如下（务必先读完，避免重复）：
{ref_text}

请基于技术方案，撰写一个附加技术特征的从属权利要求。
格式："根据权利要求{refers_to}所述的[主题]，其特征在于，[附加特征]。"
要求：附加特征必须是该权利要求未记载的技术细节（具体算法、阈值、形状、
参数或模块构成），写成完整句子，一项权利要求只能有一个句号。

权利要求："""

            try:
                response = self.llm_client.complete(
                    prompt=prompt,
                    temperature=0.4,
                    max_tokens=800
                )
                
                full_text = self._require_text(response, f"补充从属权利要求{claim_num}")
                
                claims.append(Claim(
                    number=claim_num,
                    type="dependent",
                    refers_to=refers_to,
                    subject=independent_claims[0].subject if independent_claims else "",
                    features=[],
                    full_text=full_text
                ))
                
                claim_num += 1
            except Exception as e:
                logger.warning(f"从属权利要求{claim_num}撰写失败: {e}")
                break
        
        return claims
    
    def _determine_invention_types(self, technical_info: TechnicalInfo) -> List[str]:
        """
        确定独立权利要求的类型序列。

        【为什么要重写】
        旧实现只扫描 technical_solution 的关键词。而 technical_solution 是
        LLM 在 temperature=0.3 下从交底书概括出来的，措辞每次都不同——
        实测同一份交底书连续解析两次，一次含"系统"、一次不含，导致独立
        权利要求的条数在 1 条和 2 条之间随机波动。法律文件的结构不能由
        LLM 的措辞决定。

        新策略：
        1. 以"发明名称"为主要判据。名称是交底书的确定性字段，且按撰写
           惯例必然包含保护客体（如"……方法及系统"）；
        2. 技术方案文本作为兜底补充判据；
        3. 涉及计算机程序实现的，固定补一条存储介质独权——旧实现只有
           "方法/产品/用途"三类，介质独权永远生成不出来。
        """
        name = technical_info.invention_name or ""
        solution = technical_info.technical_solution or ""
        text = f"{name} {solution}"

        types = []

        # 方法类
        if any(kw in text for kw in ['方法', '步骤', '流程', '工艺', '过程']):
            types.append('方法')

        # 产品/装置类
        if any(kw in text for kw in ['系统', '装置', '设备', '结构', '产品', '组件', '模块', '仪器']):
            types.append('产品/装置')

        # 存储介质类：计算机程序实现的发明按惯例应布局一条介质独权
        if any(kw in text for kw in ['系统', '装置', '处理器', '存储器', '程序', '软件',
                                     '模块', '计算机']):
            types.append('存储介质')

        # 兜底：默认按方法处理
        if not types:
            types = ['方法']

        return types
    
    def _extract_subject(self, claim_text: str) -> str:
        """从权利要求文本中提取主题"""
        import re
        
        # 匹配 "1. 一种/所述的[主题]，其特征在于"
        match = re.search(r'^\d+\.\s*(?:一种|所述的)?\s*([^，,]+?)[，,]', claim_text)
        if match:
            return match.group(1).strip()
        
        return ""
    
    def _extract_features(self, claim_text: str) -> List[str]:
        """从权利要求文本中提取技术特征"""
        import re
        
        # 提取"其特征在于"之后的内容
        match = re.search(r'其特征在于[，,]\s*(.+)', claim_text)
        if match:
            features_text = match.group(1)
            # 按标点符号分割
            features = re.split(r'[；;。]', features_text)
            return [f.strip() for f in features if f.strip()]
        
        return []
    
    def to_text(self, document: ClaimsDocument) -> str:
        """
        将权利要求书转换为文本格式
        
        Args:
            document: 权利要求书文档
            
        Returns:
            文本格式的权利要求书
        """
        lines = [
            "权利要求书",
            "",
            f"本发明涉及{document.invention_title}，以下为本发明的权利要求：",
            ""
        ]
        
        for claim in document.claims:
            lines.append(claim.full_text)
            lines.append("")
        
        return '\n'.join(lines)
    
    def to_markdown(self, document: ClaimsDocument) -> str:
        """
        将权利要求书转换为Markdown格式
        
        Args:
            document: 权利要求书文档
            
        Returns:
            Markdown格式的权利要求书
        """
        lines = [
            f"# 权利要求书 - {document.invention_title}",
            "",
            f"**权利要求总数**: {document.total_count}项",
            ""
        ]
        
        # 独立权利要求
        lines.append("## 独立权利要求\n")
        for claim in document.claims:
            if claim.type == "independent":
                lines.append(f"### 权利要求{claim.number}")
                lines.append(f"**主题**: {claim.subject}")
                lines.append(f"**类型**: 独立权利要求")
                lines.append(f"\n{claim.full_text}\n")
        
        # 从属权利要求
        lines.append("## 从属权利要求\n")
        for claim in document.claims:
            if claim.type == "dependent":
                lines.append(f"### 权利要求{claim.number}")
                lines.append(f"**引用**: 权利要求{claim.refers_to}")
                lines.append(f"**附加特征**: {', '.join(claim.features) if claim.features else '详见全文'}")
                lines.append(f"\n{claim.full_text}\n")
        
        return '\n'.join(lines)
