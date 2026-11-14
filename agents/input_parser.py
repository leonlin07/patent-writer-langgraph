"""
输入解析器模块
解析技术交底书，提取关键技术要素
"""
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass
from utils.llm_client import get_llm_client
from utils.json_utils import extract_json
from config import PARSE_MAX_CHARS
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class TechnicalInfo:
    """技术信息数据结构"""
    invention_name: str = ""                    # 发明名称
    technical_field: str = ""                   # 技术领域
    background_art: str = ""                    # 背景技术
    technical_problem: str = ""                 # 技术问题
    technical_solution: str = ""                # 技术方案
    technical_effects: str = ""                 # 技术效果
    specific_implementations: List[str] = None  # 具体实施方式
    key_features: List[str] = None              # 关键技术特征
    dependent_features: List[str] = None        # 从属权项附加特征清单（交底书显式列举时优先使用）
    independent_claim_rules: str = ""           # 独立权利要求撰写规则（交底书显式给出时注入提示词）
    
    def __post_init__(self):
        if self.specific_implementations is None:
            self.specific_implementations = []
        if self.key_features is None:
            self.key_features = []
        if self.dependent_features is None:
            self.dependent_features = []


class InputParser:
    """输入解析器类"""
    
    def __init__(self):
        """初始化输入解析器"""
        self.llm_client = get_llm_client()
    
    def parse(self, content: str) -> TechnicalInfo:
        """
        解析技术交底书内容
        
        Args:
            content: 技术交底书文本内容
            
        Returns:
            TechnicalInfo对象，包含解析后的技术信息
        """
        # 首先尝试使用LLM进行结构化解析
        technical_info = self._parse_with_llm(content)
        
        # 补充提取关键技术特征
        technical_info.key_features = self._extract_key_features(content)

        # 提取交底书显式列举的从属权项附加特征清单（无则为空，由权项生成器回退）
        technical_info.dependent_features = self._extract_dependent_features(content)

        # 提取交底书显式给出的独立权利要求撰写规则
        technical_info.independent_claim_rules = self._extract_independent_claim_rules(
            content
        )

        return technical_info
    
    def parse_from_file(self, file_path: str) -> TechnicalInfo:
        """
        从文件解析技术交底书
        
        Args:
            file_path: 文件路径
            
        Returns:
            TechnicalInfo对象
        """
        content = self._read_file(file_path)
        return self.parse(content)
    
    def _read_file(self, file_path: str) -> str:
        """读取文件内容"""
        import os
        
        ext = os.path.splitext(file_path)[1].lower()
        
        if ext == '.txt':
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        
        elif ext == '.docx':
            try:
                from docx import Document
                doc = Document(file_path)
                return '\n'.join([para.text for para in doc.paragraphs])
            except ImportError:
                raise ImportError("请安装 python-docx: pip install python-docx")
        
        elif ext == '.pdf':
            try:
                import PyPDF2
                with open(file_path, 'rb') as f:
                    reader = PyPDF2.PdfReader(f)
                    text = ''
                    for page in reader.pages:
                        text += page.extract_text() + '\n'
                    return text
            except ImportError:
                raise ImportError("请安装 PyPDF2: pip install PyPDF2")
        
        else:
            # 默认按文本读取
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
    
    def _parse_with_llm(self, content: str) -> TechnicalInfo:
        """使用LLM解析技术交底书"""
        
        system_prompt = """你是一位专业的专利代理师，擅长解析技术交底书。
请从以下技术交底书中提取关键信息，并以JSON格式返回。

需要提取的字段：
1. invention_name: 发明名称（字符串）
2. technical_field: 技术领域（字符串）
3. background_art: 背景技术（现有技术及其缺陷，字符串）
4. technical_problem: 本发明要解决的技术问题（字符串）
5. technical_solution: 技术方案（详细描述如何实现，字符串）
6. technical_effects: 有益效果（技术效果，字符串）
7. specific_implementations: 具体实施方式（字符串列表）

重要：所有字段必须是字符串类型，specific_implementations必须是字符串列表。
请确保提取的信息准确、完整。"""

        # 截断上限（字符）。原值 3000 会静默丢弃长交底书的大部分内容。
        limit = PARSE_MAX_CHARS
        used = content[:limit]
        if len(content) > limit:
            logger.warning(
                f"[WARN] 技术交底书共 {len(content)} 字，超过解析上限 {limit} 字，"
                f"尾部 {len(content) - limit} 字未参与解析（如需全文解析请调大 "
                f"config.PARSE_MAX_CHARS）"
            )

        prompt = f"请解析以下技术交底书：\n\n{used}"

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=4000
            )

            # 空响应保护：LLM 返回空串时视为失败，走规则解析
            if not response or not response.strip():
                raise ValueError("LLM 返回空响应")

            # 解析JSON响应
            import json
            
            # 尝试从响应中提取JSON（稳健解析：容忍裸换行、围栏、尾逗号）
            data = extract_json(response)
            if data and isinstance(data, dict):
                
                # 确保所有字段都是字符串类型
                def ensure_str(value):
                    if isinstance(value, list):
                        return '\n'.join(str(v) for v in value)
                    return str(value) if value is not None else ""
                
                # 确保specific_implementations是列表
                impl = data.get('specific_implementations', [])
                if isinstance(impl, str):
                    impl = [impl]
                elif not isinstance(impl, list):
                    impl = []
                
                return TechnicalInfo(
                    invention_name=ensure_str(data.get('invention_name', '')),
                    technical_field=ensure_str(data.get('technical_field', '')),
                    background_art=ensure_str(data.get('background_art', '')),
                    technical_problem=ensure_str(data.get('technical_problem', '')),
                    technical_solution=ensure_str(data.get('technical_solution', '')),
                    technical_effects=ensure_str(data.get('technical_effects', '')),
                    specific_implementations=impl,
                )
        except Exception as e:
            logger.warning(f"LLM解析失败，使用规则解析: {e}")
        
        # 如果LLM解析失败，使用规则解析
        return self._parse_with_rules(content)
    
    def _parse_with_rules(self, content: str) -> TechnicalInfo:
        """使用规则解析技术交底书"""
        info = TechnicalInfo()
        
        # 提取发明名称
        name_patterns = [
            r'发明名称[：:]\s*(.+?)(?=\n|$)',
            r'名称[：:]\s*(.+?)(?=\n|$)',
            r'标题[：:]\s*(.+?)(?=\n|$)',
        ]
        info.invention_name = self._extract_with_patterns(content, name_patterns)
        
        # 提取技术领域
        field_patterns = [
            r'技术领域[：:]\s*([\s\S]+?)(?=背景技术|发明内容|$)',
            r'所属领域[：:]\s*([\s\S]+?)(?=背景|内容|$)',
        ]
        info.technical_field = self._extract_with_patterns(content, field_patterns)
        
        # 提取背景技术
        bg_patterns = [
            r'背景技术[：:]\s*([\s\S]+?)(?=发明内容|技术问题|$)',
            r'现有技术[：:]\s*([\s\S]+?)(?=发明|$)',
        ]
        info.background_art = self._extract_with_patterns(content, bg_patterns)
        
        # 提取技术问题
        problem_patterns = [
            r'技术问题[：:]\s*([\s\S]+?)(?=技术方案|$)',
            r'要解决的技术问题[：:]\s*([\s\S]+?)(?=技术方案|$)',
            r'发明目的[：:]\s*([\s\S]+?)(?=技术方案|$)',
        ]
        info.technical_problem = self._extract_with_patterns(content, problem_patterns)
        
        # 提取技术方案
        solution_patterns = [
            r'技术方案[：:]\s*([\s\S]+?)(?=有益效果|技术效果|$)',
            r'技术内容[：:]\s*([\s\S]+?)(?=效果|实施|$)',
        ]
        info.technical_solution = self._extract_with_patterns(content, solution_patterns)
        
        # 提取技术效果
        effect_patterns = [
            r'有益效果[：:]\s*([\s\S]+?)(?=具体实施|附图|$)',
            r'技术效果[：:]\s*([\s\S]+?)(?=实施|附图|$)',
            r'发明效果[：:]\s*([\s\S]+?)(?=实施|$)',
        ]
        info.technical_effects = self._extract_with_patterns(content, effect_patterns)
        
        # 提取具体实施方式
        impl_patterns = [
            r'具体实施方式[：:]\s*([\s\S]+?)(?=附图|$)',
            r'实施例[：:]\s*([\s\S]+?)(?=附图|$)',
        ]
        impl_text = self._extract_with_patterns(content, impl_patterns)
        if impl_text:
            # 分割多个实施例
            info.specific_implementations = [
                s.strip() for s in re.split(r'(?:实施例|示例)\s*\d+', impl_text) if s.strip()
            ]
        
        return info
    
    def _extract_with_patterns(self, content: str, patterns: List[str]) -> str:
        """使用多个模式提取内容"""
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return ""
    
    @staticmethod
    def _extract_independent_claim_rules(content: str) -> str:
        """
        提取交底书显式给出的独立权利要求撰写规则（上位化约束）。

        为什么需要：独权若由 LLM 自由发挥，会把本该下沉到从属权项的具体特征
        （特定形状、具体位深、具体阈值）写进独权，导致保护范围被不必要地限缩，
        且三条独权之间范围不一致。交底书里若已给出撰写规则，就直接注入提示词。
        """
        if not content:
            return ""

        start_m = re.search(r"^[^\S\n]*权利要求布局建议[^\n]*$", content, re.MULTILINE)
        if not start_m:
            return ""

        rest = content[start_m.end():]
        # 到「从属权利要求」清单为止，前面的部分即独权规则
        end_m = re.search(r"^[^\S\n]*从属权利要求", rest, re.MULTILINE)
        block = rest[: end_m.start()] if end_m else rest

        # 截断到下一个空行分隔的段落，避免吃进无关内容
        para_end = re.search(r"\n\s*\n", block)
        if para_end:
            block = block[: para_end.start()]

        return block.strip()

    @staticmethod
    def _extract_dependent_features(content: str) -> List[str]:
        """
        提取交底书显式列举的从属权项附加特征清单。

        为什么需要这一步：从属权项原本只由 LLM 从 key_features 自由发挥，
        实测会编造交底书中不存在的细节（例如把「长圆形」写成「椭圆」），
        导致真正的发明点反而没被从属权项保住。交底书里若已明确列举，
        就以交底书为准，避免模型自由发挥。

        支持两种常见写法：
          （a）特征一；（b）特征二；……
          （1）特征一；（2）特征二；……
        """
        if not content:
            return []

        # 优先匹配以「从属权利要求」开头的行（清单标题行）；
        # 行内顺带提及（如「……下沉至从属权利要求」）不应作为清单起点。
        m = re.search(r"^[^\S\n]*从属权利要求[^\n]*$", content, re.MULTILINE)
        if not m:
            m = re.search(r"^[^\n]*从属权利要求[^\n]*$", content, re.MULTILINE)
        if not m:
            return []

        start = m.start()
        nxt = re.search(r"\n\s*\n|^\s*#", content[start:], re.MULTILINE)
        block = content[start:start + nxt.start()] if nxt else content[start:]

        # 清单正文从第一个编号标记处开始，丢弃前面的引导语
        marker = re.search(r"[（(]\s*[a-zA-Z0-9]{1,2}\s*[)）]", block)
        if not marker:
            return []
        block = block[marker.start():]

        # 按 （a）（b）（c）… 或 （1）（2）（3）… 切分
        parts = re.split(r"[（(]\s*[a-zA-Z0-9]{1,2}\s*[)）]\s*", block)

        # 过滤：过短片段、撰写要求类说明语、以及混入的独立权利要求内容
        noise_kw = ("独立权利要求", "独权中", "撰写要求", "应表述为", "不得限定", "必须明确")
        features = []
        for p in parts:
            text = re.sub(r"\s+", " ", p).strip(" 　;；,，。:：")
            if len(text) < 6 or len(text) > 160:
                continue
            if text.startswith("—") or text.startswith("-"):
                continue
            if any(kw in text for kw in noise_kw):
                continue
            features.append(text)

        return features

    def _extract_key_features(self, content: str) -> List[str]:
        """提取关键技术特征（实例方法：函数体内使用 self.llm_client，故不能是 @staticmethod）"""
        # 查找包含"特征"、"包括"、"步骤"等关键词的句子
        feature_patterns = [
            r'其特征在于[：:]\s*(.+?)(?=。|$)',
            r'包括[：:]\s*(.+?)(?=。|$)',
            r'步骤[：:]\s*(.+?)(?=。|$)',
        ]
        
        features = []
        for pattern in feature_patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            features.extend(matches)
        
        # 使用LLM进一步提取
        try:
            # 注意：这里曾截取 content[:2000]，而交底书前 2000 字通常是
            # "发明名称/技术领域/背景技术"，导致 key_features 全是泛泛而谈的
            # 概念，具体细节（算法名、阈值、几何形状）根本进不来，最终从属
            # 权利要求只能复述独立权利要求。改为全文参与提取。
            prompt = f"""请从以下技术内容中提取 10-15 个关键技术特征，每行一个。

要求：
1. 既要有方法层面的核心构思（用于独立权利要求）；
2. 也要有可写入从属权利要求的具体实现细节，例如具体算法名称、
   具体阈值或阈值区间、具体的几何形状、具体的参数取值、具体的模块构成；
3. 每条独立成行，不要编号，不要空行。

{content[:PARSE_MAX_CHARS]}

请只列出关键技术特征："""

            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.3,
                max_tokens=2000
            )
            
            llm_features = [f.strip() for f in response.strip().split('\n') if f.strip()]
            if llm_features:
                features = llm_features
        except Exception as e:
            logger.warning(f"LLM特征提取失败: {e}")
        
        return features[:15]  # 最多返回15个特征
