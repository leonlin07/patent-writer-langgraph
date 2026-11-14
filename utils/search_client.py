"""
搜索客户端模块
支持专利检索和现有技术搜索
"""
import os
import time
import requests
from typing import Optional, List, Dict, Any
from urllib.parse import quote
from config import SEARCH_CONFIG
from logging_config import get_logger
logger = get_logger(__name__)


class SearchClient:
    """搜索客户端类"""
    
    def __init__(self):
        """初始化搜索客户端"""
        self.serpapi_key = SEARCH_CONFIG.get("serpapi", {}).get("api_key")
        self.exa_key = SEARCH_CONFIG.get("exa", {}).get("api_key")
    
    def search_patents(
        self,
        query: str,
        max_results: int = 10,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        搜索相关专利
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            **kwargs: 其他参数
            
        Returns:
            专利搜索结果列表
        """
        results = []
        
        # 使用Google Patents搜索
        if self.serpapi_key:
            try:
                patents = self._search_google_patents(query, max_results)
                results.extend(patents)
            except Exception as e:
                logger.warning(f"Google Patents搜索失败: {e}")
        
        # 使用通用搜索作为备选
        if len(results) < max_results:
            try:
                general_results = self._search_general(
                    f"patent {query}", 
                    max_results - len(results)
                )
                results.extend(general_results)
            except Exception as e:
                logger.warning(f"通用搜索失败: {e}")
        
        return results[:max_results]
    
    def search_prior_art(
        self,
        query: str,
        max_results: int = 10,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        搜索现有技术
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            **kwargs: 其他参数
            
        Returns:
            现有技术搜索结果列表
        """
        results = []
        
        # 搜索学术论文和技术文档
        try:
            academic_results = self._search_general(
                f"{query} technical paper research",
                max_results
            )
            results.extend(academic_results)
        except Exception as e:
            logger.warning(f"学术搜索失败: {e}")
        
        return results[:max_results]
    
    def _search_google_patents(
        self,
        query: str,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        使用SerpAPI搜索Google Patents
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            专利搜索结果列表
        """
        if not self.serpapi_key:
            return []
        
        url = "https://serpapi.com/search"
        params = {
            "engine": "google_patents",
            "q": query,
            "api_key": self.serpapi_key,
            "num": min(max_results, 20),
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("patents_results", []):
            results.append({
                "title": item.get("title", ""),
                "abstract": item.get("abstract", ""),
                "patent_id": item.get("patent_id", ""),
                "url": item.get("url", ""),
                "source": "google_patents",
            })
        
        return results
    
    def _search_general(
        self,
        query: str,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        通用搜索
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            搜索结果列表
        """
        if not self.serpapi_key:
            return []
        
        url = "https://serpapi.com/search"
        params = {
            "engine": "google",
            "q": query,
            "api_key": self.serpapi_key,
            "num": min(max_results, 10),
        }
        
        response = requests.get(url, params=params, timeout=30)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for item in data.get("organic_results", []):
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "url": item.get("link", ""),
                "source": "google_search",
            })
        
        return results
    
    def search_with_exa(
        self,
        query: str,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        使用Exa API搜索
        
        Args:
            query: 搜索关键词
            max_results: 最大结果数
            
        Returns:
            搜索结果列表
        """
        if not self.exa_key:
            return []
        
        url = "https://api.exa.ai/search"
        headers = {
            "Authorization": f"Bearer {self.exa_key}",
            "Content-Type": "application/json",
        }
        data = {
            "query": query,
            "numResults": min(max_results, 20),
            "useAutoprompt": True,
        }
        
        response = requests.post(url, headers=headers, json=data, timeout=30)
        response.raise_for_status()
        result_data = response.json()
        
        results = []
        for item in result_data.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("text", ""),
                "source": "exa",
            })
        
        return results

    def search_semantic_scholar(
        self,
        query: str,
        max_results: int = 10
    ) -> List[Dict[str, Any]]:
        """
        使用 Semantic Scholar API 检索学术论文（免费，无需 key，有限速）。

        多源检索的论文源：补充现有技术/背景技术。失败自动降级为空列表，
        不影响主流程。对 429（限速）做有限退避重试：免费 API 共享池在
        高峰期易触发限速，等待后重试可显著提升召回率。
        """
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            "query": query,
            "limit": min(max_results, 10),
            "fields": "title,abstract,url,year",
        }
        last_err = "未知原因"
        for attempt in range(3):  # 最多 3 次尝试
            try:
                response = requests.get(url, params=params, timeout=30)
                if response.status_code == 429:
                    wait = 3.0 * (attempt + 1)
                    last_err = "429 rate limited"
                    logger.info(f"Semantic Scholar 429 限速，{wait:.0f}s 后重试 ...")
                    time.sleep(wait)
                    continue
                response.raise_for_status()
                data = response.json()
                results = []
                for item in data.get("data", []):
                    results.append({
                        "title": item.get("title", ""),
                        "snippet": (item.get("abstract") or "")[:300],
                        "url": item.get("url", ""),
                        "source": "semantic_scholar",
                    })
                return results
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Semantic Scholar 搜索失败（已降级）: {e}")
                return []
        logger.warning(f"Semantic Scholar 搜索失败（已降级，重试耗尽）: {last_err}")
        return []


# 全局搜索客户端实例
_search_client: Optional[SearchClient] = None


def get_search_client() -> SearchClient:
    """获取全局搜索客户端实例"""
    global _search_client
    if _search_client is None:
        _search_client = SearchClient()
    return _search_client
