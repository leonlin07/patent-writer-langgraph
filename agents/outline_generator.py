"""
大纲生成器模块
生成专利文档结构大纲
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from agents.input_parser import TechnicalInfo
from utils.llm_client import get_llm_client
from utils.json_utils import extract_json
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class Section:
    """章节数据结构"""
    title: str
    content_outline: str
    subsections: List['Section'] = field(default_factory=list)


@dataclass
class PatentOutline:
    """专利大纲数据结构"""
    invention_title: str
    sections: List[Section]
    claims_structure: List[str]
    diagram_descriptions: List[str]


class OutlineGenerator:
    """大纲生成器类"""
    
    def __init__(self):
        """初始化大纲生成器"""
        self.llm_client = get_llm_client()
    
    @staticmethod
    def _format_prior_art(search_result: Optional[Any], limit: int = 6) -> str:
        """把检索结果压成一段"需规避的现有技术"提示文本"""
        if search_result is None:
            return ""

        patents = getattr(search_result, "patents", None) or []
        prior_art = getattr(search_result, "prior_art", None) or []
        if not patents and not prior_art:
            return ""

        lines = []
        for i, p in enumerate(patents[:limit], 1):
            title = p.get("title", "") if isinstance(p, dict) else str(p)
            snippet = p.get("snippet", "") if isinstance(p, dict) else ""
            lines.append(f"{i}. [专利] {title}" + (f" —— {snippet}" if snippet else ""))
        for i, a in enumerate(prior_art[:limit], len(lines) + 1):
            title = a.get("title", "") if isinstance(a, dict) else str(a)
            snippet = a.get("snippet", "") if isinstance(a, dict) else ""
            lines.append(f"{i}. [现有技术] {title}" + (f" —— {snippet}" if snippet else ""))
        return "\n".join(lines)

    def generate(
        self,
        technical_info: TechnicalInfo,
        search_result: Optional[Any] = None
    ) -> PatentOutline:
        """
        生成专利文档大纲
        
        Args:
            technical_info: 技术信息
            search_result: 搜索结果（可选）。非 None 时其现有技术会参与
                           权利要求结构设计，用于规避已被公开的技术特征。
            
        Returns:
            PatentOutline对象
        """
        prior_art_text = self._format_prior_art(search_result)
        if prior_art_text:
            logger.info(f"  [INFO] 已注入 {prior_art_text.count(chr(10)) + 1} 条现有技术，用于权项规避设计")

        # 生成说明书大纲
        sections = self._generate_description_outline(technical_info)
        
        # 生成权利要求结构（传入现有技术，使其影响保护范围设计）
        claims_structure = self._generate_claims_structure(
            technical_info, prior_art_text
        )
        
        # 生成附图说明
        diagram_descriptions = self._generate_diagram_outline(technical_info)
        
        return PatentOutline(
            invention_title=technical_info.invention_name,
            sections=sections,
            claims_structure=claims_structure,
            diagram_descriptions=diagram_descriptions,
        )
    
    def _generate_description_outline(
        self,
        technical_info: TechnicalInfo
    ) -> List[Section]:
        """
        生成说明书章节大纲
        
        Args:
            technical_info: 技术信息
            
        Returns:
            章节列表
        """
        prompt = f"""请为以下发明生成专利说明书的详细章节大纲。

发明名称: {technical_info.invention_name}
技术领域: {technical_info.technical_field}
背景技术: {technical_info.background_art[:500]}
技术问题: {technical_info.technical_problem}
技术方案: {technical_info.technical_solution[:800]}
技术效果: {technical_info.technical_effects[:400]}

请生成符合中国专利法要求的说明书大纲，包括：
1. 技术领域
2. 背景技术
3. 发明内容（技术问题、技术方案、有益效果）
4. 附图说明
5. 具体实施方式

每个章节需要包含简要的内容概述。以JSON格式返回：
{{
  "sections": [
    {{
      "title": "章节标题",
      "content_outline": "内容概述",
      "subsections": [
        {{"title": "子章节标题", "content_outline": "子章节内容概述"}}
      ]
    }}
  ]
}}"""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=2000
            )
            
            import json
            import re
            
            # 提取JSON（稳健解析：容忍裸换行、围栏、尾逗号）
            data = extract_json(response)
            if data and isinstance(data, dict):
                sections = []
                for sec_data in data.get("sections", []):
                    subsections = [
                        Section(title=sub["title"], content_outline=sub.get("content_outline", ""))
                        for sub in sec_data.get("subsections", [])
                    ]
                    sections.append(Section(
                        title=sec_data["title"],
                        content_outline=sec_data.get("content_outline", ""),
                        subsections=subsections
                    ))
                return sections
        except Exception as e:
            logger.warning(f"LLM大纲生成失败: {e}")
        
        # 使用默认大纲
        return self._get_default_outline(technical_info)
    
    def _generate_claims_structure(
        self,
        technical_info: TechnicalInfo,
        prior_art_text: str = ""
    ) -> List[str]:
        """
        生成权利要求结构
        
        Args:
            technical_info: 技术信息
            prior_art_text: 现有技术提示文本（来自专利检索）。非空时会要求
                            模型把已被公开的特征排除出独立权利要求。
            
        Returns:
            权利要求结构列表
        """
        prior_block = ""
        if prior_art_text:
            prior_block = f"""
【已检索到的现有技术/相近专利 —— 必须规避】
{prior_art_text}

规避要求：独立权利要求的必要技术特征**不得**与上述现有技术整体相同；
若某特征已被上述文献公开，应将其下放到从属权利要求，或补充区别技术特征。
"""

        prompt = f"""请为以下发明设计权利要求书的结构。

发明名称: {technical_info.invention_name}
技术方案: {technical_info.technical_solution[:800]}
关键特征: {', '.join(technical_info.key_features[:8])}
{prior_block}
请设计：
1. 独立权利要求的主题和必要技术特征
2. 从属权利要求的层次结构
3. 每项权利要求应保护的技术点

以列表形式返回，例如：
- 独立权利要求1: [主题] - [必要技术特征]
- 从属权利要求2: 引用权1，增加[特征]
- 从属权利要求3: 引用权1或2，增加[特征]
..."""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=1500
            )

            # 解析列表
            lines = [line.strip() for line in response.strip().split('\n') if line.strip()]
            if lines:
                return lines
            # 空响应不静默返回空列表，交给下方兜底
            raise ValueError("LLM 返回空响应")
        except Exception as e:
            logger.warning(f"权利要求结构生成失败，使用默认结构: {e}")
        
        # 使用默认结构
        return self._get_default_claims_structure(technical_info)
    
    def _generate_diagram_outline(
        self,
        technical_info: TechnicalInfo
    ) -> List[str]:
        """
        生成附图说明大纲
        
        Args:
            technical_info: 技术信息
            
        Returns:
            附图说明列表
        """
        prompt = f"""请为以下发明设计需要附图的类型和说明。

发明名称: {technical_info.invention_name}
技术方案: {technical_info.technical_solution[:600]}

请建议：
1. 需要哪些附图（系统框图、流程图、结构示意图等）
2. 每幅图的内容说明

以列表形式返回，例如：
- 图1: [图类型] - [内容说明]
- 图2: [图类型] - [内容说明]
..."""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=1000
            )
            
            lines = [line.strip() for line in response.strip().split('\n') if line.strip()]
            return lines
        except Exception as e:
            logger.warning(f"附图大纲生成失败: {e}")
        
        # 使用默认附图
        return [
            "图1: 系统架构图 - 展示系统的整体结构和各组件之间的关系",
            "图2: 方法流程图 - 展示方法的主要步骤和流程",
        ]
    
    def _get_default_outline(self, technical_info: TechnicalInfo) -> List[Section]:
        """获取默认大纲"""
        return [
            Section(
                title="技术领域",
                content_outline=f"本发明涉及{technical_info.technical_field or '相关技术领域'}",
            ),
            Section(
                title="背景技术",
                content_outline="描述现有技术的状况及其存在的问题",
                subsections=[
                    Section(title="现有技术概述", content_outline="介绍现有技术方案"),
                    Section(title="现有技术的缺陷", content_outline="分析现有技术存在的问题"),
                ]
            ),
            Section(
                title="发明内容",
                content_outline="阐述本发明要解决的技术问题和采用的技术方案",
                subsections=[
                    Section(title="技术问题", content_outline=technical_info.technical_problem or "要解决的技术问题"),
                    Section(title="技术方案", content_outline=technical_info.technical_solution[:200] if technical_info.technical_solution else "技术方案概述"),
                    Section(title="有益效果", content_outline=technical_info.technical_effects[:200] if technical_info.technical_effects else "技术效果"),
                ]
            ),
            Section(
                title="附图说明",
                content_outline="对附图进行简要说明",
            ),
            Section(
                title="具体实施方式",
                content_outline="详细描述本发明的具体实施方式",
            ),
        ]
    
    def _get_default_claims_structure(self, technical_info: TechnicalInfo) -> List[str]:
        """获取默认权利要求结构"""
        structures = [
            "独立权利要求1: 产品/方法主题 - 包含必要技术特征",
        ]
        
        # 根据关键特征添加从属权利要求
        for i, feature in enumerate(technical_info.key_features[:5], 2):
            structures.append(f"从属权利要求{i}: 引用权1，进一步限定{feature[:30]}...")
        
        return structures
    
    def to_markdown(self, outline: PatentOutline) -> str:
        """
        将大纲转换为Markdown格式
        
        Args:
            outline: 专利大纲
            
        Returns:
            Markdown格式的大纲
        """
        lines = [f"# {outline.invention_title}", ""]
        
        lines.append("## 说明书大纲\n")
        for i, section in enumerate(outline.sections, 1):
            lines.append(f"### {i}. {section.title}")
            lines.append(f"{section.content_outline}\n")
            
            for sub in section.subsections:
                lines.append(f"- **{sub.title}**: {sub.content_outline}")
            
            if section.subsections:
                lines.append("")
        
        lines.append("## 权利要求结构\n")
        for item in outline.claims_structure:
            lines.append(f"- {item}")
        
        lines.append("\n## 附图说明\n")
        for item in outline.diagram_descriptions:
            lines.append(f"- {item}")
        
        return '\n'.join(lines)
