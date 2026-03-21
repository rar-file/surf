"""
MCP Server - Web Search Tools for Claude Desktop & MCP clients.

Provides web_search, fetch_webpage, and web_research tools.

Setup:
    pip install mcp playwright && playwright install chromium
    python mcp_server.py
"""

import asyncio
import json
import os
from typing import Any

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
    MCP_AVAILABLE = True
except ImportError:
    MCP_AVAILABLE = False
    print("MCP not installed. Install with: pip install mcp")

from .ai_search import AISearch


# Global search instance for reuse
_skill = None


def get_skill() -> AISearch:
    """Get or create the AISearch instance"""
    global _skill
    if _skill is None:
        provider = os.environ.get("SEARCH_PROVIDER", "auto")
        _skill = AISearch(headless=True, provider=provider)
    return _skill


async def web_search_handler(query: str, num_results: int = 10) -> str:
    """Handle web search requests"""
    skill = get_skill()
    results = await skill.search_async(query, num_results)
    
    output = f"🔍 Search Results for: '{query}'\n\n"
    for r in results:
        output += f"[{r.position}] {r.title}\n"
        output += f"    🔗 {r.url}\n"
        output += f"    📝 {r.snippet}\n\n"
    
    if not results:
        output += "No results found."
    
    return output


async def fetch_page_handler(url: str) -> str:
    """Handle page fetch requests"""
    skill = get_skill()
    page = await skill.fetch_page_async(url)
    
    if page.success:
        output = f"📄 Page: {page.title}\n"
        output += f"🔗 URL: {url}\n"
        output += f"{'='*60}\n\n"
        output += page.text
        return output
    else:
        return f"❌ Error fetching page: {page.error}"


async def research_handler(query: str) -> str:
    """Handle research requests (search + fetch top results)"""
    skill = get_skill()
    
    # Search first
    results = await skill.search_async(query, num_results=5)
    
    output = f"📚 Research Results for: '{query}'\n"
    output += f"Found {len(results)} results.\n\n"
    
    # Fetch top 3 pages
    for i, result in enumerate(results[:3], 1):
        output += f"{'='*60}\n"
        output += f"📌 SOURCE {i}: {result.title}\n"
        output += f"🔗 {result.url}\n"
        output += f"{'='*60}\n\n"
        
        page = await skill.fetch_page_async(result.url)
        if page.success:
            # Truncate long content
            content = page.text[:4000]
            if len(page.text) > 4000:
                content += "\n\n...[content truncated]..."
            output += content
        else:
            output += f"(Could not fetch content: {page.error})\n"
            output += f"Snippet: {result.snippet}"
        
        output += "\n\n"
    
    return output


def create_server():
    """Create and configure the MCP server"""
    if not MCP_AVAILABLE:
        raise ImportError("MCP package not installed. Run: pip install mcp")
    
    server = Server("web-search")
    
    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="web_search",
                description="Search the web using DuckDuckGo or Tavily (when TAVILY_API_KEY is set). Returns titles, URLs, and snippets for search results. Use this to find information on any topic.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The search query"
                        },
                        "num_results": {
                            "type": "integer",
                            "description": "Number of results to return (default: 10, max: 30)",
                            "default": 10
                        }
                    },
                    "required": ["query"]
                }
            ),
            Tool(
                name="fetch_webpage",
                description="Fetch and read the full content of a webpage. Extracts clean text from the page, removing navigation, ads, etc.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "url": {
                            "type": "string",
                            "description": "The URL of the webpage to fetch"
                        }
                    },
                    "required": ["url"]
                }
            ),
            Tool(
                name="web_research",
                description="Comprehensive web research: searches for a topic and automatically reads the top 3 results. Use this for in-depth research on any topic.",
                inputSchema={
                    "type": "object", 
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "The topic or question to research"
                        }
                    },
                    "required": ["query"]
                }
            )
        ]
    
    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        try:
            if name == "web_search":
                result = await web_search_handler(
                    arguments["query"],
                    arguments.get("num_results", 10)
                )
            elif name == "fetch_webpage":
                result = await fetch_page_handler(arguments["url"])
            elif name == "web_research":
                result = await research_handler(arguments["query"])
            else:
                result = f"Unknown tool: {name}"
            
            return [TextContent(type="text", text=result)]
            
        except Exception as e:
            return [TextContent(type="text", text=f"Error: {str(e)}")]
    
    return server


async def main():
    """Run the MCP server"""
    if not MCP_AVAILABLE:
        print("=" * 60)
        print("❌ MCP package not installed!")
        print("=" * 60)
        print("\nTo install MCP, run:")
        print("  pip install mcp")
        print("\nThen run this server again.")
        return
    
    print("🚀 Starting Web Search MCP Server...", flush=True)
    server = create_server()
    
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream)


if __name__ == "__main__":
    asyncio.run(main())
