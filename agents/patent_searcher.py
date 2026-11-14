"""
专利检索器模块
检索现有技术和相关专利
"""
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from utils.search_client import get_search_client
from utils.llm_client import get_llm_client
from logging_config import get_logger
logger = get_logger(__name__)


@dataclass
class PatentInfo:
    """专利信息数据结构"""
    title: str = ""
    abstract: str = ""
    patent_id: str = ""
    url: str = ""
    relevance_score: float = 0.0
    source: str = ""


@dataclass
class PriorArtInfo:
    """现有技术信息数据结构"""
    title: str = ""
    snippet: str = ""
    url: str = ""
    source: str = ""


@dataclass
class SearchResult:
    """搜索结果数据结构"""
    patents: List[PatentInfo]
    prior_art: List[PriorArtInfo]
    novelty_assessment: str = ""
    inventiveness_assessment: str = ""


class PatentSearcher:
    """专利检索器类"""
    
    def __init__(self):
        """初始化专利检索器"""
        self.search_client = get_search_client()
        self.llm_client = get_llm_client()
    
    def search(
        self,
        invention_name: str,
        technical_solution: str,
        key_features: List[str],
        max_results: int = 10
    ) -> SearchResult:
        """
        检索相关专利和现有技术
        
        Args:
            invention_name: 发明名称
            technical_solution: 技术方案
            key_features: 关键技术特征
            max_results: 最大结果数
            
        Returns:
            SearchResult对象
        """
        # Agentic RAG 开关：启用时走迭代检索（查询改写 + 多轮去重合并），
        # 否则走原单次检索（向后兼容，默认关闭以控制成本）。
        rag_trace = None
        if os.getenv("RAG_ENABLED", "false").lower() == "true":
            from agents.retrieval_agent import RetrievalAgent
            patent_results, prior_art_results, rag_trace = RetrievalAgent().search(
                invention_name=invention_name,
                technical_solution=technical_solution,
                key_features=key_features,
                max_results=max_results,
            )
        else:
            search_query = self._build_search_query(
                invention_name, technical_solution, key_features
            )
            patent_results = self.search_client.search_patents(
                search_query, max_results=max_results
            )
            prior_art_results = self.search_client.search_prior_art(
                search_query, max_results=max_results
            )

        # 搜索相关专利
        patents = [
            PatentInfo(
                title=p.get("title", ""),
                abstract=p.get("abstract", ""),
                patent_id=p.get("patent_id", ""),
                url=p.get("url", ""),
                source=p.get("source", ""),
            )
            for p in patent_results
        ]
        
        # 搜索现有技术
        prior_art = [
            PriorArtInfo(
                title=p.get("title", ""),
                snippet=p.get("snippet", ""),
                url=p.get("url", ""),
                source=p.get("source", ""),
            )
            for p in prior_art_results
        ]

        if rag_trace:
            rounds = len(rag_trace)
            logger.info(f"  [RAG] 迭代检索完成：{rounds} 轮，召回专利 {len(patents)} 项、"
                  f"现有技术 {len(prior_art)} 项")
        
        # 评估新颖性和创造性
        novelty, inventiveness = self._assess_novelty_and_inventiveness(
            invention_name, technical_solution, key_features, patents, prior_art
        )
        
        return SearchResult(
            patents=patents,
            prior_art=prior_art,
            novelty_assessment=novelty,
            inventiveness_assessment=inventiveness,
        )
    
    def _build_search_query(
        self,
        invention_name: str,
        technical_solution: str,
        key_features: List[str]
    ) -> str:
        """
        构建搜索查询
        
        Args:
            invention_name: 发明名称
            technical_solution: 技术方案
            key_features: 关键技术特征
            
        Returns:
            搜索查询字符串
        """
        # 使用LLM优化搜索查询
        try:
            prompt = f"""请基于以下发明信息，生成一个简洁的专利搜索查询（英文，不超过10个词）：

发明名称: {invention_name}
技术方案: {technical_solution[:500]}
关键特征: {', '.join(key_features[:5])}

搜索查询:"""

            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.3,
                max_tokens=100
            )
            
            query = response.strip()
            if query and len(query) > 5:
                return query
        except Exception as e:
            logger.warning(f"LLM查询构建失败: {e}")
        
        # 备选方案：使用发明名称和关键特征
        query_parts = [invention_name]
        if key_features:
            query_parts.extend(key_features[:3])
        
        return " ".join(query_parts)
    
    def _assess_novelty_and_inventiveness(
        self,
        invention_name: str,
        technical_solution: str,
        key_features: List[str],
        patents: List[PatentInfo],
        prior_art: List[PriorArtInfo]
    ) -> tuple:
        """
        评估新颖性和创造性
        
        Args:
            invention_name: 发明名称
            technical_solution: 技术方案
            key_features: 关键技术特征
            patents: 相关专利列表
            prior_art: 现有技术列表
            
        Returns:
            (新颖性评估, 创造性评估) 元组
        """
        # 准备对比信息
        patent_summaries = []
        for i, p in enumerate(patents[:5], 1):
            patent_summaries.append(
                f"{i}. {p.title}\n   摘要: {p.abstract[:200]}..."
            )
        
        prior_art_summaries = []
        for i, p in enumerate(prior_art[:3], 1):
            prior_art_summaries.append(
                f"{i}. {p.title}\n   内容: {p.snippet[:200]}..."
            )
        
        prompt = f"""作为专利审查专家，请评估以下发明的新颖性和创造性。

发明信息：
- 名称: {invention_name}
- 技术方案: {technical_solution[:800]}
- 关键特征: {', '.join(key_features)}

相关专利：
{chr(10).join(patent_summaries) if patent_summaries else "无"}

现有技术：
{chr(10).join(prior_art_summaries) if prior_art_summaries else "无"}

请提供：
1. 新颖性评估（该发明是否已被公开）
2. 创造性评估（该发明是否显而易见）

格式要求：
新颖性评估：[详细分析]
创造性评估：[详细分析]"""

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                temperature=0.4,
                max_tokens=1500
            )
            
            # 解析响应
            novelty = ""
            inventiveness = ""
            
            lines = response.split('\n')
            current_section = None
            
            for line in lines:
                if '新颖性' in line or 'novelty' in line.lower():
                    current_section = 'novelty'
                    novelty = line.split('：', 1)[-1].strip() if '：' in line else ""
                elif '创造性' in line or 'inventiveness' in line.lower() or 'inventive' in line.lower():
                    current_section = 'inventiveness'
                    inventiveness = line.split('：', 1)[-1].strip() if '：' in line else ""
                elif current_section == 'novelty' and line.strip():
                    novelty += ' ' + line.strip()
                elif current_section == 'inventiveness' and line.strip():
                    inventiveness += ' ' + line.strip()
            
            return novelty.strip(), inventiveness.strip()
            
        except Exception as e:
            logger.warning(f"评估失败: {e}")
            return "无法评估", "无法评估"
    
    def generate_search_report(self, result: SearchResult) -> str:
        """
        生成检索报告
        
        Args:
            result: 搜索结果
            
        Returns:
            检索报告文本
        """
        report = ["# 专利检索报告\n"]
        
        # 相关专利
        report.append("## 相关专利\n")
        if result.patents:
            for i, p in enumerate(result.patents, 1):
                report.append(f"### {i}. {p.title}")
                report.append(f"- 专利号: {p.patent_id}")
                report.append(f"- 来源: {p.source}")
                report.append(f"- 链接: {p.url}")
                report.append(f"- 摘要: {p.abstract[:300]}...\n")
        else:
            report.append("未找到相关专利。\n")
        
        # 现有技术
        report.append("## 现有技术\n")
        if result.prior_art:
            for i, p in enumerate(result.prior_art, 1):
                report.append(f"### {i}. {p.title}")
                report.append(f"- 来源: {p.source}")
                report.append(f"- 链接: {p.url}")
                report.append(f"- 内容: {p.snippet[:300]}...\n")
        else:
            report.append("未找到相关现有技术。\n")
        
        # 评估
        report.append("## 新颖性评估\n")
        report.append(result.novelty_assessment + "\n")
        
        report.append("## 创造性评估\n")
        report.append(result.inventiveness_assessment + "\n")
        
        return '\n'.join(report)
