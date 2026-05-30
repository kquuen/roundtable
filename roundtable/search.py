"""Phase 7A: 搜索适配器 — SerpAPI + Mock fallback。

SearchAdapter:
  - backend='mock': 返回模拟结果（测试用，零成本）
  - backend='serpapi': 调用 SerpAPI
  - 每个 query 返回 top-3 结果 × 200 字符摘要
"""

from __future__ import annotations

import logging

from pydantic import BaseModel, Field

logger = logging.getLogger("roundtable.search")


class SearchResultItem(BaseModel):
    """单条搜索结果。"""
    title: str = ""
    url: str = ""
    snippet: str = ""


class SearchResult(BaseModel):
    """一次搜索的完整结果。"""
    query: str
    items: list[SearchResultItem] = Field(default_factory=list)
    total_results: int = 0


class SearchAdapter:
    """搜索适配器——Mock 优先，SerpAPI 可选。"""

    def __init__(
        self,
        backend: str = "mock",
        api_key: str | None = None,
        max_results: int = 3,
    ):
        self.backend = backend
        self.api_key = api_key
        self.max_results = max_results

        if backend == "serpapi":
            if not api_key:
                import os
                self.api_key = os.getenv("SERPAPI_API_KEY", "")
            if not self.api_key:
                logger.warning("SERPAPI_API_KEY not set, falling back to mock")
                self.backend = "mock"

    async def search(self, query: str) -> SearchResult:
        """执行搜索。Mock 模式返回模拟结果。"""
        if self.backend == "mock":
            return self._mock_search(query)

        if self.backend == "serpapi":
            return await self._serpapi_search(query)

        return SearchResult(query=query)

    def _mock_search(self, query: str) -> SearchResult:
        """Mock 搜索——返回模板化结果，保证数据流完整。"""
        items = []
        kw = query.lower()

        if "简历" in kw or "resume" in kw:
            items = [
                SearchResultItem(
                    title="AI Resume Builder - Top 10 Tools 2024",
                    url="https://example.com/resume-tools",
                    snippet="Top AI resume builders: Kickresume, Resume.io, Teal. Most support Chinese and target entry-level job seekers.",
                ),
                SearchResultItem(
                    title="应届生简历优化指南",
                    url="https://example.com/resume-guide",
                    snippet="2024年应届生简历优化最佳实践：聚焦项目经验、量化成果、使用AI辅助优化关键词匹配。",
                ),
                SearchResultItem(
                    title="Resume API Services Comparison",
                    url="https://example.com/resume-api",
                    snippet="OpenAI, DeepSeek and Anthropic APIs can be used for resume optimization. RESTful APIs available with token-based pricing.",
                ),
            ]
        elif "API" in kw or "api" in kw.lower():
            items = [
                SearchResultItem(
                    title="API Integration Best Practices",
                    url="https://example.com/api-best-practices",
                    snippet="RESTful API design patterns, rate limiting, authentication best practices for 2024.",
                ),
            ]

        if not items:
            items = [
                SearchResultItem(
                    title=f"Search: {query[:80]}",
                    url="https://example.com/search",
                    snippet=f"Mock result for query: {query[:150]}",
                ),
            ]

        return SearchResult(
            query=query,
            items=items[:self.max_results],
            total_results=len(items),
        )

    async def _serpapi_search(self, query: str) -> SearchResult:
        """SerpAPI 搜索。"""
        import httpx
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://serpapi.com/search",
                    data={
                        "q": query,
                        "api_key": self.api_key,
                        "engine": "google",
                        "num": self.max_results,
                    },
                )
                data = resp.json()

            items = []
            for r in data.get("organic_results", [])[:self.max_results]:
                items.append(SearchResultItem(
                    title=r.get("title", ""),
                    url=r.get("link", ""),
                    snippet=r.get("snippet", "")[:200],
                ))

            return SearchResult(
                query=query,
                items=items,
                total_results=data.get("search_information", {}).get("total_results", 0),
            )
        except Exception as e:
            logger.warning("SerpAPI search failed: %s, falling back to mock", e)
            return self._mock_search(query)
