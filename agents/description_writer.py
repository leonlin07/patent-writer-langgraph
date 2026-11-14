"""
具体实施方式撰写器模块
撰写专利说明书的具体实施方式部分
"""
import re
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from agents.input_parser import TechnicalInfo
from agents.outline_generator import PatentOutline, Section
from utils.llm_client import get_llm_client
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class DescriptionSection:
    """说明书章节数据结构"""
    title: str
    content: str
    order: int


def _strip_md_headings(text: str) -> str:
    """
    去掉章节正文里的 Markdown 标题行。

    章节标题由合并器统一输出；LLM 常在正文中重复输出「### 附图说明」之类标题，
    导致合并后同一标题嵌套两层（## 附图说明 → ### 附图说明）。
    """
    if not text:
        return text
    lines = text.split("\n")
    kept = [ln for ln in lines if not re.match(r"^\s*#{1,6}\s", ln)]
    # 标题后常跟一个空行，一并去掉开头的空行
    while kept and not kept[0].strip():
        kept.pop(0)
    return "\n".join(kept).strip()


@dataclass
class DescriptionDocument:
    """说明书数据结构"""
    invention_title: str
    sections: List[DescriptionSection]
    word_count: int = 0
    
    def __post_init__(self):
        self.word_count = sum(len(s.content) for s in self.sections)


class DescriptionWriter:
    """具体实施方式撰写器类"""
    
    def __init__(self):
        """初始化撰写器"""
        self.llm_client = get_llm_client()
    
    def write(
        self,
        technical_info: TechnicalInfo,
        outline: PatentOutline,
        include_examples: bool = True,
        min_examples: int = 1
    ) -> DescriptionDocument:
        """
        撰写说明书
        
        Args:
            technical_info: 技术信息
            outline: 专利大纲
            include_examples: 是否包含实施例
            min_examples: 最少实施例数
            
        Returns:
            DescriptionDocument对象
        """
        sections = []
        
        # 按大纲顺序撰写各章节
        for i, section_outline in enumerate(outline.sections, 1):
            content = self._write_section(
                section_outline, technical_info, i
            )
            sections.append(DescriptionSection(
                title=section_outline.title,
                content=content,
                order=i
            ))
        
        # 添加具体实施方式详细内容
        implementation_content = self._write_implementation_section(
            technical_info, include_examples, min_examples
        )
        
        # 查找并替换或添加具体实施方式章节
        impl_index = None
        for i, sec in enumerate(sections):
            if "实施方式" in sec.title or "实施例" in sec.title:
                impl_index = i
                break
        
        if impl_index is not None:
            sections[impl_index].content = implementation_content
        else:
            sections.append(DescriptionSection(
                title="具体实施方式",
                content=implementation_content,
                order=len(sections) + 1
            ))
        
        return DescriptionDocument(
            invention_title=technical_info.invention_name,
            sections=sections
        )
    
    def _write_section(
        self,
        section_outline: Section,
        technical_info: TechnicalInfo,
        section_num: int
    ) -> str:
        """
        撰写单个章节
        
        Args:
            section_outline: 章节大纲
            technical_info: 技术信息
            section_num: 章节序号
            
        Returns:
            章节内容
        """
        # 根据章节类型选择撰写策略
        if "技术领域" in section_outline.title:
            content = self._write_technical_field(technical_info)

        elif "背景技术" in section_outline.title:
            content = self._write_background_art(technical_info)

        elif "发明内容" in section_outline.title:
            content = self._write_invention_content(technical_info, section_outline)

        elif "附图说明" in section_outline.title:
            content = self._write_diagram_description(technical_info)

        elif "实施方式" in section_outline.title or "实施例" in section_outline.title:
            return ""  # 单独处理

        else:
            # 通用章节撰写
            content = self._write_generic_section(section_outline, technical_info)

        # 章节标题由合并器统一输出，正文内再出现 Markdown 标题会重复嵌套
        return _strip_md_headings(content)
    
    @staticmethod
    def _normalize_field_text(text: str) -> str:
        """
        归一化技术领域文本，避免拼接出双重前缀病句。

        LLM 抽取的 technical_field 常自带"本发明涉及/属于……"前缀，
        再套进"本发明涉及{field}，尤其涉及{name}"模板就会变成
        "本发明涉及本发明涉及……"。
        """
        t = (text or "").strip()
        # 去掉开头的主语前缀（可能重复出现，循环剥离）
        pattern = r"^(?:本(?:发明|实用新型|申请)|本实用新型)\s*(?:涉及|属于|关于|涉及于)\s*"
        while re.match(pattern, t):
            t = re.sub(pattern, "", t).strip()
        # 去掉收尾的句号，避免句中出现"。，"
        return t.rstrip("。.；;，, ")

    def _write_technical_field(self, technical_info: TechnicalInfo) -> str:
        """撰写技术领域章节"""
        if technical_info.technical_field:
            field = self._normalize_field_text(technical_info.technical_field)
            name = (technical_info.invention_name or "").strip()
            if not field:
                return f"本发明涉及{name}。" if name else ""
            if not name:
                return f"本发明涉及{field}。"
            # 名称本身已能点明领域时不再重复"尤其涉及"
            if name and name in field:
                return f"本发明涉及{field}。"

            # 若 field 已自带"尤其涉及……"从句，合并而不是再拼一个，
            # 否则会出现"尤其涉及A，尤其涉及B"的重复句式
            m = re.search(r"[，,]\s*尤其涉及\s*(.+?)\s*$", field)
            if m:
                base = field[:m.start()].rstrip("，, ")
                existing = m.group(1).rstrip("。.；;，, ")
                return f"本发明涉及{base}，尤其涉及{existing}及{name}。"

            return f"本发明涉及{field}，尤其涉及{name}。"
        
        prompt = f"""请为以下发明撰写"技术领域"章节（50-100字）：

发明名称: {technical_info.invention_name}
技术方案概述: {technical_info.technical_solution[:300]}

要求：
1. 说明本发明所属的技术领域
2. 简要说明本发明涉及的具体技术内容

直接输出内容："""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=300
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"技术领域撰写失败: {e}")
            return f"本发明涉及{technical_info.invention_name}相关技术领域。"
    
    def _write_background_art(self, technical_info: TechnicalInfo) -> str:
        """撰写背景技术章节"""
        if technical_info.background_art:
            base_content = technical_info.background_art
        else:
            prompt = f"""请为以下发明撰写"背景技术"章节（200-400字）：

发明名称: {technical_info.invention_name}
技术问题: {technical_info.technical_problem}
技术方案: {technical_info.technical_solution[:400]}

要求：
1. 介绍与本发明相关的现有技术
2. 分析现有技术存在的问题和缺陷
3. 说明为什么需要本发明

直接输出内容："""

            try:
                response = self.llm_client.complete(
                    prompt=prompt,
                    temperature=0.4,
                    max_tokens=800
                )
                base_content = response.strip()
            except Exception as e:
                logger.warning(f"背景技术撰写失败: {e}")
                base_content = f"现有技术中，{technical_info.technical_problem}是一个亟待解决的问题。"
        
        return base_content
    
    def _write_invention_content(
        self,
        technical_info: TechnicalInfo,
        section_outline: Section
    ) -> str:
        """撰写发明内容章节"""
        content_parts = []
        
        # 技术问题
        problem = technical_info.technical_problem or "现有技术存在的问题"
        content_parts.append(f"针对现有技术的不足，本发明要解决的技术问题是：{problem}")
        content_parts.append("")
        
        # 技术方案
        solution = technical_info.technical_solution
        if solution:
            content_parts.append("为解决上述技术问题，本发明采用的技术方案是：")
            # 确保solution是字符串
            if isinstance(solution, list):
                solution = '\n'.join(str(s) for s in solution)
            content_parts.append(str(solution))
            content_parts.append("")
        
        # 有益效果
        effects = technical_info.technical_effects
        if effects:
            content_parts.append("与现有技术相比，本发明的有益效果是：")
            # 确保effects是字符串
            if isinstance(effects, list):
                effects = '\n'.join(str(e) for e in effects)
            content_parts.append(str(effects))
        
        # 确保所有元素都是字符串
        content_parts = [str(p) for p in content_parts]
        
        return '\n'.join(content_parts)
    
    def _write_diagram_description(self, technical_info: TechnicalInfo) -> str:
        """撰写附图说明章节"""
        prompt = f"""请为以下发明撰写"附图说明"章节：

发明名称: {technical_info.invention_name}
技术方案: {technical_info.technical_solution[:500]}

要求：
1. 假设有图1（系统框图/流程图）和图2（详细结构图）
2. 简要说明每幅图的内容
3. 说明图中各标号对应的部件/步骤

直接输出内容："""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=600
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"附图说明撰写失败: {e}")
            return """图1为本发明的系统架构示意图；
图2为本发明的方法流程图；
图3为本发明的详细结构示意图。

图中：1-第一部件，2-第二部件，3-第三部件。"""
    
    def _write_implementation_section(
        self,
        technical_info: TechnicalInfo,
        include_examples: bool,
        min_examples: int
    ) -> str:
        """
        撰写具体实施方式章节
        
        Args:
            technical_info: 技术信息
            include_examples: 是否包含实施例
            min_examples: 最少实施例数
            
        Returns:
            章节内容
        """
        content_parts = []
        
        # 总体实施方式说明
        prompt = f"""请撰写具体实施方式的总体说明（300-500字）：

发明名称: {technical_info.invention_name}
技术方案: {technical_info.technical_solution[:800]}

要求：
1. 详细描述本发明的具体实施方式
2. 结合技术方案说明各组成部分/步骤
3. 说明各组成部分/步骤之间的连接关系或执行顺序
4. 使用具体的实施细节

直接输出内容："""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=1200
            )
            content_parts.append(response.strip())
        except Exception as e:
            logger.warning(f"具体实施方式撰写失败: {e}")
            content_parts.append(technical_info.technical_solution or "详见技术方案描述。")
        
        # 添加实施例
        if include_examples:
            content_parts.append("")
            content_parts.append("**实施例**")
            content_parts.append("")
            
            # 使用用户提供的技术实施方式，或生成新的
            implementations = technical_info.specific_implementations
            
            if not implementations or len(implementations) < min_examples:
                # 生成实施例
                for i in range(1, min_examples + 1):
                    example = self._generate_example(technical_info, i)
                    content_parts.append(f"**实施例{i}**")
                    content_parts.append(example)
                    content_parts.append("")
            else:
                # 使用提供的实施方式
                for i, impl in enumerate(implementations[:min_examples + 1], 1):
                    content_parts.append(f"**实施例{i}**")
                    content_parts.append(impl)
                    content_parts.append("")
        
        return '\n'.join(content_parts)
    
    def _generate_example(self, technical_info: TechnicalInfo, example_num: int) -> str:
        """生成实施例"""
        prompt = f"""请为以下发明撰写实施例{example_num}（200-400字）：

发明名称: {technical_info.invention_name}
技术方案: {technical_info.technical_solution[:600]}

要求：
1. 提供具体的实施细节
2. 可以包括具体的参数、数值或配置
3. 说明实施后的效果

直接输出内容："""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.5,
                max_tokens=800
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"实施例{example_num}生成失败: {e}")
            return f"本实施例详细描述了{technical_info.invention_name}的具体实现方式。"
    
    def _write_generic_section(
        self,
        section_outline: Section,
        technical_info: TechnicalInfo
    ) -> str:
        """撰写通用章节"""
        prompt = f"""请撰写专利说明书的"{section_outline.title}"章节：

发明名称: {technical_info.invention_name}
内容概述: {section_outline.content_outline}

直接输出内容："""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=800
            )
            return response.strip()
        except Exception as e:
            logger.warning(f"章节撰写失败: {e}")
            return section_outline.content_outline
    
    def to_text(self, document: DescriptionDocument) -> str:
        """
        将说明书转换为文本格式
        
        Args:
            document: 说明书文档
            
        Returns:
            文本格式的说明书
        """
        lines = [
            f"说明书",
            "",
            f"技术领域",
            "",
        ]
        
        for section in sorted(document.sections, key=lambda s: s.order):
            lines.append(section.title)
            lines.append("")
            lines.append(section.content)
            lines.append("")
        
        return '\n'.join(lines)
    
    def to_markdown(self, document: DescriptionDocument) -> str:
        """
        将说明书转换为Markdown格式
        
        Args:
            document: 说明书文档
            
        Returns:
            Markdown格式的说明书
        """
        lines = [
            f"# 说明书 - {document.invention_title}",
            "",
            f"**字数**: 约{document.word_count}字",
            ""
        ]
        
        for section in sorted(document.sections, key=lambda s: s.order):
            lines.append(f"## {section.title}")
            lines.append("")
            lines.append(section.content)
            lines.append("")
        
        return '\n'.join(lines)
