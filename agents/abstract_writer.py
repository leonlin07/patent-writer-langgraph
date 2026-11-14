"""
摘要撰写器模块
撰写专利摘要
"""
from typing import Optional
from dataclasses import dataclass
from agents.input_parser import TechnicalInfo
from utils.llm_client import get_llm_client
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class AbstractDocument:
    """摘要文档数据结构"""
    invention_title: str
    abstract_text: str
    word_count: int = 0
    
    def __post_init__(self):
        self.word_count = len(self.abstract_text)


class AbstractWriter:
    """摘要撰写器类"""
    
    def __init__(self):
        """初始化摘要撰写器"""
        self.llm_client = get_llm_client()
    
    def write(
        self,
        technical_info: TechnicalInfo,
        max_length: int = 300
    ) -> AbstractDocument:
        """
        撰写专利摘要
        
        Args:
            technical_info: 技术信息
            max_length: 最大字数（中文）
            
        Returns:
            AbstractDocument对象
        """
        prompt = f"""请为以下发明撰写专利摘要（不超过{max_length}字）：

发明名称: {technical_info.invention_name}
技术领域: {technical_info.technical_field}
技术问题: {technical_info.technical_problem}
技术方案: {technical_info.technical_solution[:600]}
技术效果: {technical_info.technical_effects[:300]}

撰写要求：
1. 首先说明发明名称和所属技术领域
2. 简要说明要解决的技术问题
3. 概括技术方案的要点
4. 说明主要用途和有益效果
5. 字数控制在{max_length}字以内
6. 不要加标题，直接输出摘要内容
7. 结尾不要加句号

摘要："""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=600
            )
            
            abstract_text = (response or "").strip()
            # 空/过短响应不静默返回，交给下方兜底生成默认摘要
            if len(abstract_text) < 30:
                raise ValueError(
                    f"摘要内容为空或过短（{len(abstract_text)}字），判定为生成失败"
                )
            
            # 确保字数限制
            if len(abstract_text) > max_length:
                abstract_text = abstract_text[:max_length]
            
            # 移除结尾的句号（专利摘要规范）
            if abstract_text.endswith('。'):
                abstract_text = abstract_text[:-1]
            
            return AbstractDocument(
                invention_title=technical_info.invention_name,
                abstract_text=abstract_text
            )
            
        except Exception as e:
            logger.warning(f"摘要撰写失败: {e}")
            # 生成默认摘要
            return self._generate_default_abstract(technical_info, max_length)
    
    def _generate_default_abstract(
        self,
        technical_info: TechnicalInfo,
        max_length: int
    ) -> AbstractDocument:
        """生成默认摘要"""
        parts = []
        
        # 发明名称和技术领域
        parts.append(f"本发明公开了一种{technical_info.invention_name}")
        if technical_info.technical_field:
            parts.append(f"，属于{technical_info.technical_field}")
        
        # 技术问题
        if technical_info.technical_problem:
            parts.append(f"。本发明要解决的技术问题是{technical_info.technical_problem}")
        
        # 技术方案
        if technical_info.technical_solution:
            solution_summary = technical_info.technical_solution[:100]
            parts.append(f"。本发明采用的技术方案是{solution_summary}")
        
        # 技术效果
        if technical_info.technical_effects:
            parts.append(f"。本发明的有益效果是{technical_info.technical_effects[:80]}")
        
        abstract_text = ''.join(parts)
        
        # 确保字数限制
        if len(abstract_text) > max_length:
            abstract_text = abstract_text[:max_length]
        
        # 移除结尾的句号
        if abstract_text.endswith('。'):
            abstract_text = abstract_text[:-1]
        
        return AbstractDocument(
            invention_title=technical_info.invention_name,
            abstract_text=abstract_text
        )
    
    def to_text(self, document: AbstractDocument) -> str:
        """
        将摘要转换为文本格式
        
        Args:
            document: 摘要文档
            
        Returns:
            文本格式的摘要
        """
        return f"""摘要

{document.abstract_text}

摘要附图：图1"""
    
    def to_markdown(self, document: AbstractDocument) -> str:
        """
        将摘要转换为Markdown格式
        
        Args:
            document: 摘要文档
            
        Returns:
            Markdown格式的摘要
        """
        return f"""# 摘要 - {document.invention_title}

**字数**: {document.word_count}字

{document.abstract_text}

**摘要附图**: 图1"""
