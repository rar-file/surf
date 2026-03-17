"""
AI Tool Wrappers - Ready-to-use tool definitions for AI frameworks.
Supports LangChain, OpenAI function calling, and Anthropic tool use.
"""

from typing import Optional
from .ai_search import AISearch, search as quick_search, research


# ============================================================
# 1. SIMPLE FUNCTION TOOLS (Works with any AI)
# ============================================================

def web_search(query: str, num_results: int = 5) -> str:
    """
    Search the web for information.
    
    Args:
        query: The search query
        num_results: Number of results to return
        
    Returns:
        Formatted search results as a string
    """
    results = quick_search(query, num_results)
    
    output = f"Web Search Results for: '{query}'\n\n"
    for r in results:
        output += f"[{r['position']}] {r['title']}\n"
        output += f"    URL: {r['url']}\n"
        output += f"    {r['snippet']}\n\n"
    
    return output


def web_research(query: str) -> str:
    """
    Deep research on a topic - searches and reads top pages.
    
    Args:
        query: The topic to research
        
    Returns:
        Comprehensive research results as a string
    """
    info = research(query)
    
    output = f"Research Results for: '{query}'\n"
    output += f"Found {info['total_results']} total results.\n\n"
    
    for i, page in enumerate(info['fetched_pages'], 1):
        output += f"{'='*60}\n"
        output += f"SOURCE {i}: {page['title']}\n"
        output += f"URL: {page['url']}\n"
        output += f"{'='*60}\n"
        
        if page['full_content']:
            output += page['full_content'][:3000]
            if len(page['full_content']) > 3000:
                output += "\n...[content truncated]..."
        else:
            output += f"(Could not fetch content)\nSnippet: {page['snippet']}"
        
        output += "\n\n"
    
    return output


def fetch_webpage(url: str) -> str:
    """
    Fetch and read the content of a specific webpage.
    
    Args:
        url: The URL to fetch
        
    Returns:
        The webpage content as clean text
    """
    with AISearch() as skill:
        page = skill.fetch_page(url)
        
        if page.success:
            return f"Page: {page.title}\nURL: {url}\n\n{page.text}"
        else:
            return f"Error fetching page: {page.error}"


# ============================================================
# 2. LANGCHAIN TOOLS
# ============================================================

def get_langchain_tools():
    """
    Get tools formatted for LangChain.
    
    Usage:
        from langchain.agents import initialize_agent
        from ai_tools import get_langchain_tools
        
        tools = get_langchain_tools()
        agent = initialize_agent(tools, llm, ...)
    """
    try:
        from langchain.tools import Tool
        
        return [
            Tool(
                name="web_search",
                description="Search the web for current information. Input should be a search query.",
                func=web_search
            ),
            Tool(
                name="web_research", 
                description="Do deep research on a topic by searching and reading multiple web pages. Input should be a topic or question.",
                func=web_research
            ),
            Tool(
                name="fetch_webpage",
                description="Fetch and read the content of a specific URL. Input should be a valid URL.",
                func=fetch_webpage
            )
        ]
    except ImportError:
        raise ImportError("Please install langchain: pip install langchain")


# ============================================================
# 3. OPENAI FUNCTION CALLING FORMAT
# ============================================================

OPENAI_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information on any topic. Returns titles, URLs, and snippets from search results.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query to look up"
                    },
                    "num_results": {
                        "type": "integer",
                        "description": "Number of results to return (default: 5)",
                        "default": 5
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function", 
        "function": {
            "name": "web_research",
            "description": "Do comprehensive research on a topic by searching the web and reading the content of top results. Use this for in-depth questions.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The topic or question to research"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_webpage",
            "description": "Fetch and read the full content of a specific webpage URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL of the webpage to fetch"
                    }
                },
                "required": ["url"]
            }
        }
    }
]


def handle_openai_tool_call(tool_name: str, arguments: dict) -> str:
    """
    Handle a tool call from OpenAI.
    
    Usage with OpenAI:
        response = client.chat.completions.create(
            model="gpt-4",
            messages=messages,
            tools=OPENAI_TOOLS
        )
        
        if response.choices[0].message.tool_calls:
            for tool_call in response.choices[0].message.tool_calls:
                result = handle_openai_tool_call(
                    tool_call.function.name,
                    json.loads(tool_call.function.arguments)
                )
    """
    if tool_name == "web_search":
        return web_search(**arguments)
    elif tool_name == "web_research":
        return web_research(**arguments)
    elif tool_name == "fetch_webpage":
        return fetch_webpage(**arguments)
    else:
        return f"Unknown tool: {tool_name}"


# ============================================================
# 4. ANTHROPIC/CLAUDE TOOL FORMAT
# ============================================================

ANTHROPIC_TOOLS = [
    {
        "name": "web_search",
        "description": "Search the web for information on any topic. Returns titles, URLs, and snippets from search results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up"
                },
                "num_results": {
                    "type": "integer", 
                    "description": "Number of results to return (default: 5)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "web_research",
        "description": "Do comprehensive research on a topic by searching the web and reading the content of top results.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The topic or question to research"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "fetch_webpage",
        "description": "Fetch and read the full content of a specific webpage URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The URL of the webpage to fetch"
                }
            },
            "required": ["url"]
        }
    }
]


# ============================================================
# 5. MCP (Model Context Protocol) SERVER
# ============================================================

def create_mcp_server():
    """
    Create an MCP server with web search tools.
    Run this as a standalone server for Claude Desktop, etc.
    
    See mcp_server.py for the full implementation.
    """
    print("See mcp_server.py for the MCP server implementation!")


# ============================================================
# TEST
# ============================================================

if __name__ == "__main__":
    print("Testing AI Tools...")
    print("=" * 60)
    
    # Test web search
    print("\n📍 Testing web_search():")
    result = web_search("Python programming", num_results=3)
    print(result)
    
    print("\n✅ Tools are working! Ready for AI integration.")
