"""
AI Search - Free web search and page fetching.

Uses DuckDuckGo (no API key) + Playwright for full page content.

Usage:
    from ai_search import search, fetch, research

    results = search("Python tutorials")
    content = fetch("https://python.org")
    info = research("what is machine learning")
"""

import os
import re
from dataclasses import dataclass, asdict
from typing import Optional, List


@dataclass
class SearchResult:
    """A search result"""
    title: str
    url: str
    snippet: str
    position: int


@dataclass
class PageContent:
    """Fetched page content"""
    url: str
    title: str
    text: str
    success: bool
    error: Optional[str] = None


class TavilyBackend:
    """Search backend using Tavily API."""

    def __init__(self, api_key: str):
        from tavily import TavilyClient
        self.client = TavilyClient(api_key=api_key)

    def search(self, query: str, num_results: int = 10) -> List[SearchResult]:
        try:
            response = self.client.search(
                query=query,
                max_results=num_results,
                search_depth="basic",
            )
            results = []
            for i, r in enumerate(response.get("results", [])):
                results.append(SearchResult(
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("content", ""),
                    position=i + 1,
                ))
            return results
        except Exception as e:
            print(f"Tavily search error: {e}")
            return []

    def news_search(self, query: str, num_results: int = 10) -> List[dict]:
        try:
            response = self.client.search(
                query=query,
                max_results=num_results,
                search_depth="basic",
                topic="news",
            )
            results = []
            for i, r in enumerate(response.get("results", [])):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                    "date": r.get("published_date", ""),
                    "source": r.get("source", ""),
                    "position": i + 1,
                })
            return results
        except Exception as e:
            print(f"Tavily news search error: {e}")
            return []


def _resolve_search_provider(provider: str = "auto") -> str:
    """Resolve which search provider to use.

    Args:
        provider: 'auto', 'tavily', or 'duckduckgo'

    Returns:
        'tavily' or 'duckduckgo'
    """
    if provider == "tavily":
        return "tavily"
    if provider == "duckduckgo":
        return "duckduckgo"
    # auto – prefer Tavily when API key is available
    if os.environ.get("TAVILY_API_KEY"):
        return "tavily"
    return "duckduckgo"


class AISearch:
    """
    Free AI Web Search - No API Keys!

    Features:
    - DuckDuckGo search (unlimited, free)
    - Tavily search (when TAVILY_API_KEY is set)
    - Full page content fetching
    - Clean text extraction
    - Perfect for AI/LLM tools
    """

    def __init__(self, headless: bool = True, provider: str = "auto"):
        """
        Args:
            headless: Hide browser (True) or show window (False)
            provider: Search provider – 'auto', 'tavily', or 'duckduckgo'
        """
        self.headless = headless
        self._browser = None
        self._page = None
        self._playwright = None
        self._provider_name = _resolve_search_provider(provider)
        self._tavily = None
        if self._provider_name == "tavily":
            api_key = os.environ.get("TAVILY_API_KEY", "")
            if not api_key:
                raise ValueError(
                    "TAVILY_API_KEY environment variable is required when provider='tavily'"
                )
            self._tavily = TavilyBackend(api_key)
    
    def search(self, query: str, num_results: int = 10) -> List[SearchResult]:
        """
        Search the web.

        Uses Tavily when configured, otherwise DuckDuckGo.

        Args:
            query: What to search for
            num_results: How many results (max ~25)

        Returns:
            List of SearchResult objects
        """
        if self._tavily:
            return self._tavily.search(query, num_results)

        try:
            # Try new package name first
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            results = []
            with DDGS() as ddgs:
                for i, r in enumerate(ddgs.text(query, max_results=num_results)):
                    results.append(SearchResult(
                        title=r.get('title', ''),
                        url=r.get('href', r.get('link', '')),
                        snippet=r.get('body', r.get('snippet', '')),
                        position=i + 1
                    ))

            return results

        except ImportError:
            print("Please install: pip install ddgs")
            return []
        except Exception as e:
            print(f"Search error: {e}")
            return []
    
    def news_search(self, query: str, num_results: int = 10) -> List[dict]:
        """
        Search for latest news.

        Uses Tavily when configured, otherwise DuckDuckGo.

        Args:
            query: What to search for
            num_results: How many results (max ~25)

        Returns:
            List of dicts with title, url, snippet, date, source, position
        """
        if self._tavily:
            return self._tavily.news_search(query, num_results)

        try:
            try:
                from ddgs import DDGS
            except ImportError:
                from duckduckgo_search import DDGS

            results = []
            with DDGS() as ddgs:
                for i, r in enumerate(ddgs.news(query, max_results=num_results)):
                    results.append({
                        'title': r.get('title', ''),
                        'url': r.get('url', r.get('link', '')),
                        'snippet': r.get('body', r.get('excerpt', '')),
                        'date': r.get('date', ''),
                        'source': r.get('source', ''),
                        'position': i + 1
                    })

            return results

        except ImportError:
            print("Please install: pip install ddgs")
            return []
        except Exception:
            # DecodeError / network errors — fall back to regular search
            return [asdict(r) for r in self.search(query, num_results)]

    async def _init_browser(self):
        """Start browser for page fetching"""
        if self._browser is None:
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(headless=self.headless)
            context = await self._browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
            )
            self._page = await context.new_page()
    
    async def _close_browser(self):
        """Close browser"""
        if self._browser:
            await self._browser.close()
            await self._playwright.stop()
            self._browser = None
            self._page = None
    
    def fetch_page(self, url: str, timeout: int = 30000) -> PageContent:
        """
        Fetch and extract text from a webpage.
        
        Args:
            url: URL to fetch
            timeout: Max wait time in ms
            
        Returns:
            PageContent with extracted text
        """
        import asyncio
        return asyncio.run(self._fetch_page_async(url, timeout))
    
    async def search_async(self, query: str, num_results: int = 10) -> List[SearchResult]:
        """Async search (wraps sync search for use in async contexts)."""
        return self.search(query, num_results)

    async def fetch_page_async(self, url: str, timeout: int = 30000) -> PageContent:
        """Fetch and extract text from a webpage (async)."""
        return await self._fetch_page_async(url, timeout)

    async def _fetch_page_async(self, url: str, timeout: int = 30000) -> PageContent:
        """Internal async page fetch"""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            if parsed.scheme not in ('http', 'https'):
                return PageContent(url=url, title="Error", text="Only http/https URLs are supported", word_count=0)

            await self._init_browser()
            
            await self._page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            
            title = await self._page.title()
            
            # Remove junk elements
            await self._page.evaluate("""
                () => {
                    const remove = ['script', 'style', 'nav', 'header', 'footer', 
                                   'aside', 'iframe', '.ad', '.ads', '.advertisement',
                                   '#cookie-banner', '.cookie-notice', '.popup'];
                    remove.forEach(sel => {
                        document.querySelectorAll(sel).forEach(el => el.remove());
                    });
                }
            """)
            
            # Extract main content
            text = await self._page.evaluate("""
                () => {
                    const selectors = ['main', 'article', '[role="main"]', 
                                      '.content', '#content', '.post-content',
                                      '.entry-content', '.article-body'];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.innerText.length > 100) {
                            return el.innerText;
                        }
                    }
                    return document.body.innerText;
                }
            """)
            
            text = self._clean_text(text)
            
            return PageContent(
                url=url,
                title=title or "",
                text=text,
                success=True
            )
            
        except Exception as e:
            return PageContent(
                url=url,
                title="",
                text="",
                success=False,
                error=str(e)
            )
    
    def _clean_text(self, text: str) -> str:
        """Clean extracted text"""
        if not text:
            return ""
        
        # Normalize whitespace
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        
        lines = []
        for line in text.split('\n'):
            line = line.strip()
            if len(line) < 2:
                continue
            if line.lower() in ['menu', 'search', 'close', 'share', 'skip to content', 
                               'loading...', 'advertisement']:
                continue
            lines.append(line)
        
        return '\n'.join(lines)
    
    def research(self, query: str, fetch_top: int = 3) -> dict:
        """
        Complete research: search + fetch top results.
        
        Args:
            query: Topic to research
            fetch_top: How many top results to fully fetch
            
        Returns:
            Dict with search results and fetched content
        """
        results = self.search(query)
        
        fetched = []
        for r in results[:fetch_top]:
            page = self.fetch_page(r.url)
            fetched.append({
                "title": r.title,
                "url": r.url,
                "snippet": r.snippet,
                "content": page.text[:5000] if page.success else None,
                "success": page.success,
                "error": page.error
            })
        
        return {
            "query": query,
            "total_results": len(results),
            "all_results": [asdict(r) for r in results],
            "fetched_pages": fetched
        }
    
    def close(self):
        """Clean up browser"""
        if self._browser:
            try:
                import asyncio
                asyncio.run(self._close_browser())
            except Exception:
                pass
            finally:
                self._browser = None
                self._page = None
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        self.close()


# ============================================================
# EASY FUNCTIONS - Just import and use!
# ============================================================

def search(query: str, num_results: int = 10, provider: str = "auto") -> List[dict]:
    """
    Quick search - easiest way to search!

    Uses Tavily when TAVILY_API_KEY is set (or provider='tavily'), otherwise DuckDuckGo.

    Example:
        from ai_search import search
        results = search("Python tutorials")
        for r in results:
            print(r['title'], r['url'])
    """
    ai = AISearch(provider=provider)
    results = ai.search(query, num_results)
    return [asdict(r) for r in results]


def news_search(query: str, num_results: int = 10, provider: str = "auto") -> List[dict]:
    """
    Search for LATEST news - gets recent results!

    Uses Tavily when TAVILY_API_KEY is set (or provider='tavily'), otherwise DuckDuckGo.

    Example:
        from ai_search import news_search
        results = news_search("Anthropic AI updates")
        for r in results:
            print(r['title'], r['date'], r['url'])
    """
    ai = AISearch(provider=provider)
    return ai.news_search(query, num_results)


def fetch(url: str) -> str:
    """
    Quick fetch - get page content!
    
    Example:
        from ai_search import fetch
        content = fetch("https://python.org")
        print(content)
    """
    ai = AISearch()
    try:
        page = ai.fetch_page(url)
        return page.text if page.success else f"Error: {page.error}"
    finally:
        ai.close()


def research(query: str) -> dict:
    """
    Quick research - search + read top pages!
    
    Example:
        from ai_search import research
        info = research("what is quantum computing")
        for page in info['fetched_pages']:
            print(page['title'])
            print(page['content'][:500])
    """
    ai = AISearch()
    try:
        return ai.research(query)
    finally:
        ai.close()


# ============================================================
# FOR AI TOOLS - Copy these function signatures
# ============================================================

def ai_web_search(query: str, num_results: int = 10) -> str:
    """
    AI Tool: Search the web.
    Returns formatted string for AI consumption.
    """
    results = search(query, num_results)
    
    output = f"Web Search Results for: '{query}'\n\n"
    for r in results:
        output += f"[{r['position']}] {r['title']}\n"
        output += f"    URL: {r['url']}\n"
        output += f"    {r['snippet']}\n\n"
    
    if not results:
        output += "No results found.\n"
    
    return output


def ai_fetch_page(url: str) -> str:
    """
    AI Tool: Fetch a webpage.
    Returns formatted string for AI consumption.
    """
    ai = AISearch()
    try:
        page = ai.fetch_page(url)
        if page.success:
            return f"Page: {page.title}\nURL: {url}\n\n{page.text}"
        else:
            return f"Error fetching {url}: {page.error}"
    finally:
        ai.close()


def ai_research(query: str) -> str:
    """
    AI Tool: Research a topic.
    Returns formatted string for AI consumption.
    """
    info = research(query)
    
    output = f"Research Results for: '{query}'\n"
    output += f"Found {info['total_results']} results.\n\n"
    
    for i, page in enumerate(info['fetched_pages'], 1):
        output += "=" * 50 + "\n"
        output += f"SOURCE {i}: {page['title']}\n"
        output += f"URL: {page['url']}\n"
        output += "=" * 50 + "\n"
        
        if page['content']:
            output += page['content'][:3000]
            if len(page['content']) > 3000:
                output += "\n...[truncated]..."
        else:
            output += f"(Could not fetch)\nSnippet: {page['snippet']}"
        
        output += "\n\n"
    
    return output


# ============================================================
# CLI - Run directly to test
# ============================================================

if __name__ == "__main__":
    import sys
    
    print("=" * 60)
    print("🔍 AI Web Search - FREE, No API Keys!")
    print("=" * 60)
    
    if len(sys.argv) > 1:
        query = " ".join(sys.argv[1:])
    else:
        query = input("\nEnter search query: ").strip() or "Python programming"
    
    print(f"\n🔎 Searching for: {query}\n")
    
    ai = AISearch()
    try:
        results = ai.search(query, num_results=5)
        
        if results:
            print(f"✅ Found {len(results)} results:\n")
            for r in results:
                print(f"📌 [{r.position}] {r.title}")
                print(f"   🔗 {r.url}")
                print(f"   📝 {r.snippet[:120]}...")
                print()
            
            # Fetch first result
            print("-" * 60)
            print("📄 Fetching first result for content...\n")
            page = ai.fetch_page(results[0].url)
            
            if page.success:
                print(f"Title: {page.title}")
                print(f"\nContent preview:")
                print(page.text[:800])
                print("...")
            else:
                print(f"❌ Error: {page.error}")
        else:
            print("❌ No results found.")
            
    finally:
        ai.close()
    
    print("\n✅ Done!")
