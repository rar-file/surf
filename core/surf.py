#!/usr/bin/env python3
"""
███████╗██╗   ██╗██████╗ ███████╗
██╔════╝██║   ██║██╔══██╗██╔════╝
███████╗██║   ██║██████╔╝█████╗  
╚════██║██║   ██║██╔══██╗██╔══╝  
███████║╚██████╔╝██║  ██║██║     
╚══════╝ ╚═════╝ ╚═╝  ╚═╝╚═╝     

SURF - Search. Understand. Reason. Fast.
A beautiful CLI for any AI with free web search.

Version: 1.0.0
License: MIT
"""

__version__ = "1.0.0"

import os
import sys
import json
import shutil
import subprocess
import time
import argparse
import platform
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Generator
from enum import Enum

# Rich for beautiful terminal UI
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich.box import ROUNDED
    from rich.markdown import Markdown
except ImportError:
    print("Installing rich...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "rich", "-q"])
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    from rich.table import Table
    from rich.box import ROUNDED
    from rich.markdown import Markdown

from prompt_toolkit import PromptSession
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.formatted_text import ANSI

from .ai_search import search, news_search, research, fetch


# ════════════════════════════════════════════════════════════════
# CONFIGURATION
# ════════════════════════════════════════════════════════════════

class Provider(Enum):
    OLLAMA = "ollama"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    OPENROUTER = "openrouter"
    CUSTOM = "custom"


# ═══════════════════════════════════════════════════════════════════════════════
# SYSTEM PROMPTS - What the AI knows about itself
# ═══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = """You are SURF - a fast, direct AI assistant.

RULES YOU MUST FOLLOW:
1. When search results are provided, you MUST use them. Reference specific facts, numbers, and details from the results. Do NOT ignore them or make up your own answer.
2. Cite sources - mention the website name or URL when referencing search results.
3. Use markdown formatting: **bold** for emphasis, bullet lists, `code` blocks.
4. Be concise and direct. Answer the question first, then elaborate if needed.
5. Never refuse a normal question. If you're unsure, say so honestly - don't hallucinate.
6. Never repeat the user's question back to them. Just answer it.
7. If search results are relevant, weave them naturally into your answer.

YOUR CAPABILITIES:
- Web search: real-time results from DuckDuckGo are provided when relevant
- Step-by-step reasoning for complex problems
- Image understanding (when using a vision model)
- Markdown-formatted responses"""

THINKING_INSTRUCTIONS = """REASONING MODE:
Think through this problem step by step before answering.
Wrap ALL your reasoning inside <think> and </think> tags.
After </think>, write ONLY your final answer - clean and direct, no meta-commentary.

You MUST use this format:
<think>
[step-by-step reasoning here]
</think>
[final answer here]

Do NOT put any answer text inside the <think> tags.
Do NOT put any reasoning text outside the <think> tags (except the final answer after </think>)."""


@dataclass
class Config:
    provider: Provider = Provider.OLLAMA
    model: str = "llama3.2"
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    web_search: bool = False
    thinking: bool = True
    streaming: bool = True
    agent_mode: bool = False
    vision_model: str = ""          # optional vision model for agent mode (e.g. "llama3.2-vision")
    messages: List[Dict] = field(default_factory=list)


# ════════════════════════════════════════════════════════════════
# TOKEN COUNTING & CONTEXT LIMITS
# ════════════════════════════════════════════════════════════════

# Approximate context window sizes for common models
MODEL_CONTEXT_LIMITS = {
    # Ollama models
    "llama3.2": 128000, "llama3.1": 128000, "llama3": 8192,
    "mistral": 32768, "mixtral": 32768, "codellama": 16384,
    "phi3": 128000, "phi": 2048, "gemma2": 8192, "gemma": 8192,
    "qwen2.5": 32768, "qwen2": 32768, "deepseek-coder": 16384,
    # Anthropic
    "claude-sonnet-4-20250514": 200000, "claude-3-5-sonnet": 200000,
    "claude-3-opus": 200000, "claude-3-haiku": 200000,
    # OpenAI
    "gpt-4o": 128000, "gpt-4-turbo": 128000, "gpt-4": 8192,
    "gpt-3.5-turbo": 16385, "o1": 200000, "o1-mini": 128000,
    # OpenRouter defaults
    "meta-llama/llama-3.1-8b-instruct:free": 131072,
}

def estimate_tokens(text: str) -> int:
    """Estimate token count (~4 chars per token for English)"""
    return len(text) // 4

def get_context_limit(model: str) -> int:
    """Get context window limit for a model"""
    # Exact match
    if model in MODEL_CONTEXT_LIMITS:
        return MODEL_CONTEXT_LIMITS[model]
    # Check base name (e.g., "llama3.2:1b" -> "llama3.2")
    base = model.split(":")[0].split("-")[0]
    for key, limit in MODEL_CONTEXT_LIMITS.items():
        if key.startswith(base) or base.startswith(key.split("-")[0]):
            return limit
    # Default fallback
    return 8192

def format_tokens(count: int) -> str:
    """Format token count nicely (e.g., 2.3k, 128k)"""
    if count >= 1000:
        if count < 10000:
            formatted = f"{count/1000:.1f}".rstrip('0').rstrip('.')
            return f"{formatted}k"
        return f"{count//1000}k"
    return str(count)


# ════════════════════════════════════════════════════════════════
# OLLAMA AUTO-START
# ════════════════════════════════════════════════════════════════

def is_ollama_running() -> bool:
    """Check if Ollama is running"""
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def start_ollama(console: Console) -> bool:
    """Try to start Ollama automatically"""
    console.print("   [dim]🔄 Starting Ollama...[/]", end="")
    
    try:
        # Check if ollama command exists
        if shutil.which("ollama") is None:
            console.print(" [red]not installed[/]")
            console.print()
            console.print("   [dim]Install Ollama from:[/] [cyan]https://ollama.ai[/]")
            return False
        
        # Start ollama serve in background
        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if platform.system() == "Windows":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True
        subprocess.Popen(["ollama", "serve"], **popen_kwargs)
        
        # Wait for it to be ready
        for i in range(20):  # Wait up to 10 seconds
            time.sleep(0.5)
            if is_ollama_running():
                # Give it a moment to fully initialize
                time.sleep(1)
                console.print(" [green]started![/]")
                return True
        
        console.print(" [yellow]taking too long[/]")
        return False
        
    except Exception as e:
        console.print(f" [red]failed: {e}[/]")
        return False


def ensure_model_exists(model: str, console: Console) -> Optional[str]:
    """Check if model exists. Returns the resolved model name, or None if unavailable."""
    import urllib.request
    
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            model_names = [m["name"] for m in data.get("models", [])]
            
            # Exact match (e.g. "llama3.2:1b")
            if model in model_names:
                return model
            
            # Match with :latest tag (e.g. "llama3.2" -> "llama3.2:latest")
            if f"{model}:latest" in model_names:
                return model
            
            # Partial base-name match (e.g. "llama3.2" matches "llama3.2:1b")
            base = model.split(":")[0]
            matches = [m for m in model_names if m.split(":")[0] == base]
            if matches:
                resolved = matches[0]
                console.print(f"   [dim]Using {resolved} (closest match for '{model}')[/]")
                return resolved
            
            # Model not found
            console.print(f"   [yellow]⚠ Model '{model}' not found[/]")
            if model_names:
                display = [m for m in model_names[:5]]
                console.print(f"   [dim]Available: {', '.join(display)}{'...' if len(model_names) > 5 else ''}[/]")
            console.print()
            
            # Ask to pull
            try:
                response = console.input(f"   [cyan]Pull {model}? [Y/n]:[/] ").strip().lower()
                if response in ["", "y", "yes"]:
                    console.print(f"   [dim]📥 Pulling {model}...[/]")
                    result = subprocess.run(
                        ["ollama", "pull", model],
                        capture_output=False
                    )
                    if result.returncode == 0:
                        return model
            except EOFError:
                pass
            
            return None
            
    except Exception:
        return model  # Assume it exists if we can't check


# ════════════════════════════════════════════════════════════════
# AI CLIENTS
# ════════════════════════════════════════════════════════════════

def _is_vision_model(model: str) -> bool:
    """Check if a model name suggests vision/multimodal capability."""
    m = model.lower()
    vision_keywords = ('vision', 'llava', 'bakllava', 'moondream', 'minicpm-v', 'cogvlm',
                       'fuyu', 'obsidian', 'granite-vision', 'llama-3.2-vision', 'llama3.2-vision',
                       'gpt-4o', 'gpt-4-turbo', 'gpt-4-vision', 'claude-3', 'gemini',
                       'gemma3', 'minicpm', 'internvl', 'qwen2-vl', 'qwen2.5-vl')
    return any(k in m for k in vision_keywords)


def chat_ollama(messages: list, model: str, base_url: str = None) -> Generator[str, None, None]:
    """Chat with Ollama"""
    import urllib.request
    
    url = f"{base_url or 'http://localhost:11434'}/api/chat"
    has_vision = _is_vision_model(model)
    # Format messages — only attach images for vision-capable models
    api_msgs = []
    for m in messages:
        msg = {"role": m["role"], "content": m["content"]}
        if m.get("image"):
            if has_vision:
                msg["images"] = [m["image"]]  # Ollama expects list of base64 strings
            else:
                msg["content"] += "\n\n[User attached an image. Describe what you can help with based on their text message.]"
        api_msgs.append(msg)
    data = json.dumps({"model": model, "messages": api_msgs, "stream": True}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            for line in response:
                if line:
                    chunk = json.loads(line.decode())
                    if "message" in chunk:
                        yield chunk["message"].get("content", "")
    except urllib.error.URLError as e:
        yield "ERROR:CONNECTION"
    except Exception as e:
        yield f"ERROR:{e}"


def chat_anthropic(messages: list, model: str, api_key: str, thinking: bool = True) -> Generator[str, None, None]:
    """Chat with Anthropic Claude"""
    try:
        import anthropic
    except ImportError:
        yield "INSTALL:anthropic"
        subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
        import anthropic
    
    if not api_key:
        yield "ERROR:ANTHROPIC_API_KEY not set\n\nSet with: export ANTHROPIC_API_KEY=your-key\nOr: /key your-key"
        return
    
    client = anthropic.Anthropic(api_key=api_key)
    
    system = ""
    chat_msgs = []
    has_vision = _is_vision_model(model)
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        elif m.get("image") and has_vision:
            # Vision: multipart content with image
            chat_msgs.append({"role": m["role"], "content": [
                {"type": "image", "source": {"type": "base64", "media_type": m.get("image_mime", "image/png"), "data": m["image"]}},
                {"type": "text", "text": m["content"]}
            ]})
        else:
            content = m["content"]
            if m.get("image") and not has_vision:
                content += "\n\n[User attached an image. Describe what you can help with based on their text message.]"
            chat_msgs.append({"role": m["role"], "content": content})
    
    try:
        with client.messages.stream(
            model=model,
            max_tokens=8096,
            system=system,
            messages=chat_msgs
        ) as stream:
            for text in stream.text_stream:
                yield text
    except Exception as e:
        yield f"ERROR:{e}"


def chat_openai(messages: list, model: str, api_key: str, base_url: str = None) -> Generator[str, None, None]:
    """Chat with OpenAI or compatible API"""
    try:
        from openai import OpenAI
    except ImportError:
        yield "INSTALL:openai"
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
        from openai import OpenAI
    
    if not api_key and not base_url:
        yield "ERROR:OPENAI_API_KEY not set\n\nSet with: export OPENAI_API_KEY=your-key"
        return
    
    client = OpenAI(api_key=api_key or "not-needed", base_url=base_url)
    
    # Format messages — only convert image attachments for vision-capable models
    has_vision = _is_vision_model(model)
    api_msgs = []
    for m in messages:
        if m.get("image") and has_vision:
            api_msgs.append({"role": m["role"], "content": [
                {"type": "image_url", "image_url": {"url": f"data:{m.get('image_mime','image/png')};base64,{m['image']}"}},
                {"type": "text", "text": m["content"]}
            ]})
        else:
            content = m["content"]
            if m.get("image") and not has_vision:
                content += "\n\n[User attached an image. Describe what you can help with based on their text message.]"
            api_msgs.append({"role": m["role"], "content": content})
    
    try:
        response = client.chat.completions.create(model=model, messages=api_msgs, stream=True)
        for chunk in response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        yield f"ERROR:{e}"


# ════════════════════════════════════════════════════════════════
# SURF CLI
# ════════════════════════════════════════════════════════════════

# ════════════════════════════════════════════════════════════════
# TERMINAL UNICODE DETECTION
# ════════════════════════════════════════════════════════════════

def _detect_unicode_support() -> bool:
    """
    Return True if the terminal renders Unicode well.
    - Non-Windows: always yes
    - Windows Terminal (any version): WT_SESSION env var is present
    - Windows 11+ (build >= 22000): ships Windows Terminal as default
    """
    if sys.platform != "win32":
        return True
    if os.environ.get("WT_SESSION"):
        return True
    try:
        build = int(platform.version().split(".")[-1])
        if build >= 22000:
            return True
    except Exception:
        pass
    return False


class _UI:
    """UI symbols — fancy Unicode or plain ASCII depending on terminal."""

    def __init__(self, fancy: bool):
        if fancy:
            self.on         = "●"
            self.off        = "○"
            self.you        = "◇"
            self.surf_label = "◆ SURF"
            self.ok         = "✓"
            self.err        = "✗"
            self.warn       = "⚠"
            self.fill       = "─"
            self.box_tl     = "╭─"
            self.box_tr     = "╮"
            self.box_bl     = "╰"
            self.box_br     = "╯"
            self.box_side   = "│"
            self.bar_sep    = "│ "
            self.icons      = {"ollama": "🦙 ", "anthropic": "🔮 ", "openai": "🤖 ", "openrouter": "🌐 ", "custom": "⚡ "}
            self.bye        = "🏄 See you next wave!"
            self.spinner    = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            self.connecting = "🏄 Connecting..."
            self.search_lbl = "🔍 Searching..."
        else:
            # Same Unicode symbols — only emoji and ◇ omitted (Win10 legacy conhost can't render them)
            self.on         = "●"
            self.off        = "○"
            self.you        = ">"
            self.surf_label = "◆ SURF"
            self.ok         = "✓"
            self.err        = "✗"
            self.warn       = "⚠"
            self.fill       = "─"
            self.box_tl     = "╭─"
            self.box_tr     = "╮"
            self.box_bl     = "╰"
            self.box_br     = "╯"
            self.box_side   = "│"
            self.bar_sep    = "│ "
            self.icons      = {"ollama": "", "anthropic": "", "openai": "", "openrouter": "", "custom": ""}
            self.bye        = "See you next wave!"
            self.spinner    = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
            self.connecting = "Connecting..."
            self.search_lbl = "Searching..."


UNICODE = _detect_unicode_support()
UI = _UI(fancy=UNICODE)
_BOX = ROUNDED


# Crashing wave: white foam curling over, spray, then deep blue water
LOGO = """
[bold white]   ░██████╗[/][white]██╗[/][bold white]   [/][white]██╗[/][bold bright_cyan]██████╗ [/][bright_cyan]███████╗[/]  [white]•°[/]
[bold white]   ██╔════╝[/][bright_cyan]██║   ██║[/][cyan]██╔══██╗[/][bold cyan]██╔════╝[/] [white]°∙[/]
[bright_cyan]   ╚█████╗ [/][cyan]██║   ██║[/][bold cyan]██████╔╝[/][blue]█████╗[/][white]° •[/]
[cyan]   ░╚═══██╗[/][bold cyan]██║   ██║[/][blue]██╔══██╗[/][bold blue]██╔══╝[/][bright_cyan]~≈[/]
[bold cyan]   ██████╔╝[/][blue]╚██████╔╝[/][bold blue]██║  ██║[/][dark_blue]██║[/][cyan]~≈∼[/]
[blue]   ╚═════╝ [/][bold blue] ╚═════╝ [/][dark_blue]╚═╝  ╚═╝[/][blue]╚═╝[/][bold blue]≈∼≈[/][cyan]~[/][bright_cyan]~[/]
"""


def format_thinking_response(response: str, console) -> str:
    """Extract and display thinking section, return the main answer"""
    import re
    
    # Check for <think> tags
    think_match = re.search(r'<think>(.*?)</think>', response, re.DOTALL)
    
    if think_match:
        thinking_content = think_match.group(1).strip()
        main_answer = response[think_match.end():].strip()
        
        # Display thinking in a collapsible-style block
        console.print()
        console.print(f"   [dim cyan]╭─ 🧠 Thinking {'─' * 50}[/]")
        for line in thinking_content.split('\n'):
            console.print(f"   [dim cyan]│[/] [dim]{line}[/]")
        console.print(f"   [dim cyan]╰{'─' * 60}[/]")
        console.print()
        
        return main_answer
    
    return response


# ── Slash command definitions for completion ──────────────────
_SLASH_COMMANDS = [
    ("/search",    "Toggle web search on/off"),
    ("/think",     "Toggle thinking mode"),
    ("/stream",    "Toggle live streaming"),
    ("/model",     "Switch model — /model <name>"),
    ("/models",    "List available Ollama models"),
    ("/vision",    "Set vision model — /vision <name>"),
    ("/image",     "Attach image to next message — /image <path>"),
    ("/provider",  "Switch provider — /provider <name>"),
    ("/key",       "Set API key — /key <provider> <key>"),
    ("/url",       "Set custom API URL"),
    ("/summarize", "Summarize the current conversation"),
    ("/research",  "Deep research mode — /research <topic>"),
    ("/new",       "Start a new conversation"),
    ("/clear",     "Clear conversation history"),
    ("/status",    "Show current settings"),
    ("/web",       "Launch browser UI"),
    ("/help",      "Show all commands"),
    ("/quit",      "Exit SURF"),
]


class _SlashCompleter(Completer):
    """Tab-completion for / commands with descriptions."""
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        # Only complete the first word
        if " " in text:
            return
        q = text.lower()
        for cmd, desc in _SLASH_COMMANDS:
            if cmd.startswith(q):
                yield Completion(
                    cmd, start_position=-len(text),
                    display_meta=desc,
                )


class SurfCLI:
    def __init__(self, config: Config):
        self.config = config
        self.console = Console()
        self.running = True
        self._pending_image: Optional[str] = None   # base64 image for next message
        self._pending_image_mime: str = "image/png"  # mime type
        self._prompt = PromptSession(completer=_SlashCompleter(), complete_while_typing=True)
    
    def get_chat_fn(self):
        """Get the chat function for current provider"""
        if self.config.provider == Provider.OLLAMA:
            return lambda msgs: chat_ollama(msgs, self.config.model, self.config.api_base)
        elif self.config.provider == Provider.ANTHROPIC:
            return lambda msgs: chat_anthropic(msgs, self.config.model, self.config.api_key, self.config.thinking)
        elif self.config.provider == Provider.OPENAI:
            return lambda msgs: chat_openai(msgs, self.config.model, self.config.api_key)
        elif self.config.provider == Provider.OPENROUTER:
            return lambda msgs: chat_openai(msgs, self.config.model, self.config.api_key, "https://openrouter.ai/api/v1")
        else:
            return lambda msgs: chat_openai(msgs, self.config.model, self.config.api_key, self.config.api_base)
    
    def print_header(self, clear: bool = False):
        """Print the startup header"""
        if clear:
            self.console.print()
            self.console.rule(style="dim cyan")
            self.console.print()
        self.console.print(LOGO)
        
        # Tagline with wave emoji
        wave = "🌊 " if UNICODE else ""
        self.console.print(f"   [dim]{wave}Search • Understand • Reason • Fast[/]")
        self.console.print()
        
        # Quick start hints in a nice box
        self.console.print(f"   [dim]╭──────────────────────────────────────────────────────╮[/]")
        self.console.print(f"   [dim]│[/] [cyan]/help[/] commands  [dim]│[/] [cyan]/search[/] web  [dim]│[/] [cyan]/research[/] deep dive [dim]│[/]")
        self.console.print(f"   [dim]╰──────────────────────────────────────────────────────╯[/]")
        self.console.print()
    
    def print_status_bar(self):
        """Print the status bar with token count"""
        # Build status items
        items = []
        
        # Search status
        if self.config.web_search:
            items.append(("[green]● Search[/]", True))
        else:
            items.append(("[dim]○ Search[/]", False))
        
        # Think status  
        if self.config.thinking:
            items.append(("[green]● Think[/]", True))
        else:
            items.append(("[dim]○ Think[/]", False))
        
        # Stream status
        if self.config.streaming:
            items.append(("[green]● Live[/]", True))
        else:
            items.append(("[dim]○ Live[/]", False))
        
        # Provider & model
        provider_icon = UI.icons.get(self.config.provider.value, "")
        model_short = self.config.model.split("/")[-1][:20]  # Shorten long model names
        items.append((f"[cyan]{provider_icon}{model_short}[/]", True))
        
        # Calculate token usage
        total_text = "".join(m.get("content", "") for m in self.config.messages)
        used_tokens = estimate_tokens(total_text)
        context_limit = get_context_limit(self.config.model)
        usage_pct = (used_tokens / context_limit) * 100 if context_limit > 0 else 0
        
        # Token bar visualization
        bar_width = 8
        filled = int((usage_pct / 100) * bar_width)
        empty = bar_width - filled
        
        if usage_pct > 80:
            bar_color = "red"
        elif usage_pct > 50:
            bar_color = "yellow"
        else:
            bar_color = "green"
        
        token_bar = f"[{bar_color}]{'█' * filled}[/][dim]{'░' * empty}[/]"
        token_text = f"{format_tokens(used_tokens)}/{format_tokens(context_limit)}"
        items.append((f"{token_bar} [dim]{token_text}[/]", True))

        # Build the status line
        status = Text()
        status.append("   ", style="")
        status.append("╭─", style="dim cyan")
        
        for i, (item, _) in enumerate(items):
            if i > 0:
                status.append(" │ ", style="dim cyan")
            # Parse and append the rich markup
            self.console.print(status, end="")
            status = Text()
            self.console.print(item, end="")
        
        self.console.print(" [dim cyan]─╮[/]")
    
    def check_ollama(self) -> bool:
        """Check Ollama and auto-start if needed"""
        if self.config.provider != Provider.OLLAMA:
            return True
        
        if is_ollama_running():
            resolved = ensure_model_exists(self.config.model, self.console)
            if resolved:
                self.config.model = resolved
                return True
            return False
        
        # Try to auto-start
        if start_ollama(self.console):
            time.sleep(0.5)  # Give it a moment
            resolved = ensure_model_exists(self.config.model, self.console)
            if resolved:
                self.config.model = resolved
                return True
            return False
        
        return False
    
    def handle_command(self, cmd: str) -> bool:
        """Handle slash commands"""
        parts = cmd.strip().split(maxsplit=1)
        command = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        
        if command in ["/quit", "/exit", "/q"]:
            self.console.print()
            self.console.print(f"   [bold cyan]{UI.bye}[/]")
            self.console.print()
            self.running = False
            return False
        
        elif command in ["/help", "/h", "/?"]:
            self.print_help()
        
        elif command in ["/search", "/s"]:
            if arg.lower() in ["on", "1", "true"]:
                self.config.web_search = True
            elif arg.lower() in ["off", "0", "false"]:
                self.config.web_search = False
            else:
                self.config.web_search = not self.config.web_search
            icon = f"[green]{UI.on}[/]" if self.config.web_search else f"[dim]{UI.off}[/]"
            status = "[green]ON[/]" if self.config.web_search else "[dim]OFF[/]"
            self.console.print(f"   {icon} Web Search: {status}")

        elif command in ["/think", "/t"]:
            if arg.lower() in ["on", "1"]:
                self.config.thinking = True
            elif arg.lower() in ["off", "0"]:
                self.config.thinking = False
            else:
                self.config.thinking = not self.config.thinking
            icon = f"[green]{UI.on}[/]" if self.config.thinking else f"[dim]{UI.off}[/]"
            status = "[green]ON[/]" if self.config.thinking else "[dim]OFF[/]"
            self.console.print(f"   {icon} Thinking: {status}")

        elif command in ["/stream", "/live"]:
            if arg.lower() in ["on", "1"]:
                self.config.streaming = True
            elif arg.lower() in ["off", "0"]:
                self.config.streaming = False
            else:
                self.config.streaming = not self.config.streaming
            icon = f"[green]{UI.on}[/]" if self.config.streaming else f"[dim]{UI.off}[/]"
            status = "[green]ON[/]" if self.config.streaming else "[dim]OFF[/]"
            self.console.print(f"   {icon} Live Streaming: {status}")
        
        elif command in ["/model", "/m"]:
            if arg:
                old_model = self.config.model
                if self.config.provider == Provider.OLLAMA:
                    resolved = ensure_model_exists(arg, self.console)
                    if not resolved:
                        return True
                    self.config.model = resolved
                else:
                    self.config.model = arg
                self.console.print(f"   [cyan]{UI.ok}[/] Model: [bold]{self.config.model}[/]")
            else:
                self.console.print(f"   Model: [cyan]{self.config.model}[/]")
                self.console.print("   [dim]Usage: /model <name>[/]")
        
        elif command in ["/provider", "/p"]:
            providers = {"ollama": Provider.OLLAMA, "anthropic": Provider.ANTHROPIC,
                        "openai": Provider.OPENAI, "openrouter": Provider.OPENROUTER, "custom": Provider.CUSTOM}
            if arg.lower() in providers:
                self.config.provider = providers[arg.lower()]
                # Set defaults
                if self.config.provider == Provider.ANTHROPIC:
                    self.config.model = "claude-sonnet-4-20250514"
                    self.config.api_key = self.config.api_key or os.getenv("ANTHROPIC_API_KEY")
                elif self.config.provider == Provider.OPENAI:
                    self.config.model = "gpt-4o"
                    self.config.api_key = self.config.api_key or os.getenv("OPENAI_API_KEY")
                elif self.config.provider == Provider.OPENROUTER:
                    self.config.model = "meta-llama/llama-3.1-8b-instruct:free"
                    self.config.api_key = self.config.api_key or os.getenv("OPENROUTER_API_KEY")
                elif self.config.provider == Provider.OLLAMA:
                    self.config.model = "llama3.2"
                    self.check_ollama()
                self.console.print(f"   [cyan]{UI.ok}[/] Provider: [bold]{arg}[/] [dim]({self.config.model})[/]")
            else:
                self.console.print(f"   Provider: [cyan]{self.config.provider.value}[/]")
                self.console.print("   [dim]Options: ollama, anthropic, openai, openrouter, custom[/]")
        
        elif command in ["/key", "/k"]:
            known_providers = {"anthropic", "openai", "openrouter", "custom"}
            if arg:
                parts_k = arg.split(maxsplit=1)
                if len(parts_k) == 2 and parts_k[0].lower() in known_providers:
                    # /key openrouter sk-abc...
                    target_prov = parts_k[0].lower()
                    key_val = parts_k[1]
                    self.config.api_key = key_val
                    self.console.print(f"   [cyan]{UI.ok}[/] API key saved for [bold]{target_prov}[/]")
                elif len(parts_k) == 1:
                    # Just a key with no provider — reject it
                    self.console.print(f"   [red]{UI.err}[/] Please specify the provider:")
                    self.console.print(f"   [dim]Usage: /key <provider> <key>[/]")
                    self.console.print(f"   [dim]Providers: {', '.join(sorted(known_providers))}[/]")
                else:
                    # First word isn't a known provider
                    self.console.print(f"   [red]{UI.err}[/] Unknown provider: [bold]{parts_k[0]}[/]")
                    self.console.print(f"   [dim]Providers: {', '.join(sorted(known_providers))}[/]")
            else:
                prov = self.config.provider.value
                key_status = f"[green]{UI.on}[/] set" if self.config.api_key else f"[red]{UI.off}[/] not set"
                self.console.print(f"   API key ({prov}): {key_status}")
                self.console.print(f"   [dim]Usage: /key <provider> <key>[/]")
                self.console.print(f"   [dim]Providers: {', '.join(sorted(known_providers))}[/]")
        
        elif command in ["/url", "/u"]:
            if arg:
                self.config.api_base = arg
                self.console.print(f"   [cyan]{UI.ok}[/] URL: {arg}")
            else:
                self.console.print(f"   URL: [cyan]{self.config.api_base or 'default'}[/]")
        
        elif command in ["/clear", "/c"]:
            self.config.messages = []
            self.print_header(clear=True)
            self.console.print(f"   [cyan]{UI.ok}[/] Conversation cleared")

        elif command in ["/new", "/n"]:
            self.config.messages = []
            self.console.print(f"   [cyan]{UI.ok}[/] New conversation")
        
        elif command == "/status":
            self.print_status()
        
        elif command == "/models":
            self.list_models()
        
        elif command in ["/vision", "/vi", "/vmodel"]:
            if arg.lower() in ["off", "none", "clear", ""]:
                self.config.vision_model = ""
                self.console.print(f"   [dim]{UI.off}[/] Vision model cleared")
            elif arg:
                if self.config.provider == Provider.OLLAMA:
                    resolved = ensure_model_exists(arg, self.console)
                    if not resolved:
                        return True
                    self.config.vision_model = resolved
                else:
                    self.config.vision_model = arg
                self.console.print(f"   [cyan]{UI.ok}[/] Vision model: [bold]{self.config.vision_model}[/]")
                self.console.print(f"   [dim]Use /image <path> to attach an image to your next message[/]")
            else:
                if self.config.vision_model:
                    self.console.print(f"   Vision model: [cyan]{self.config.vision_model}[/]")
                    self.console.print("   [dim]Use /vision off to clear, /image <path> to attach[/]")
                else:
                    self.console.print("   [dim]No vision model set. Usage: /vision <model>[/]")
                    self.console.print("   [dim]Example: /vision llama3.2-vision[/]")

        elif command in ["/image", "/img", "/attach"]:
            if not arg:
                if self._pending_image:
                    self.console.print(f"   [cyan]{UI.ok}[/] Image attached — type your message to send it")
                else:
                    self.console.print("   [dim]Usage: /image <path>[/]")
                    self.console.print("   [dim]Example: /image screenshot.png[/]")
                return True
            path = arg.strip().strip('"\'')
            try:
                import base64, mimetypes
                mime = mimetypes.guess_type(path)[0] or "image/png"
                with open(path, "rb") as f:
                    raw = f.read()
                self._pending_image = base64.b64encode(raw).decode("utf-8")
                self._pending_image_mime = mime
                kb = len(raw) // 1024
                self.console.print(f"   [cyan]{UI.ok}[/] Image attached: [dim]{path}[/] [dim]({kb}KB, {mime})[/]")
                # Warn if no vision model set and current model isn't vision
                active_vision = self.config.vision_model or self.config.model
                if not _is_vision_model(active_vision):
                    self.console.print(f"   [yellow]{UI.warn}[/] Current model [bold]{active_vision}[/] may not support images")
                    self.console.print(f"   [dim]Set a vision model with /vision <name>, e.g. /vision llama3.2-vision[/]")
                else:
                    self.console.print(f"   [dim]Will use [bold]{active_vision}[/] for this image[/]")
                self.console.print(f"   [dim]Now type your message to send it with the image[/]")
            except FileNotFoundError:
                self.console.print(f"   [red]{UI.err}[/] File not found: {arg}")
            except Exception as e:
                self.console.print(f"   [red]{UI.err}[/] Could not load image: {e}")
        
        elif command == "/web":
            port = int(arg) if arg.isdigit() else 7777
            self.console.print(f"   [cyan]{UI.ok}[/] Launching web UI on port {port}...")
            try:
                from .web_ui import launch
                launch(config=self.config, port=port, open_browser=True)
            except ImportError:
                self.console.print("   [red]Flask required:[/] pip install flask")
            except Exception as e:
                self.console.print(f"   [red]{UI.err}[/] {e}")
        
        elif command in ["/research", "/r"]:
            if not arg:
                self.console.print(f"   [red]{UI.err}[/] Usage: /research <topic>")
                self.console.print("   [dim]Example: /research quantum computing advances 2026[/]")
                return True
            self.do_research(arg)

        elif command in ["/summarize", "/sum"]:
            msgs = [m for m in self.history if m.get("role") in ("user", "assistant")]
            if len(msgs) < 2:
                self.console.print("   [yellow]⚠[/] Not enough messages to summarize yet")
                return True
            transcript_parts = []
            for m in msgs[-40:]:
                role = "User" if m["role"] == "user" else "Assistant"
                content = m.get("content") or ""
                if isinstance(content, list):
                    content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
                transcript_parts.append(f"{role}: {content[:600]}")
            transcript = "\n\n".join(transcript_parts)
            summary_msgs = [
                {"role": "system", "content": (
                    "You are a precise summarization assistant. Summarize the conversation below.\n"
                    "Format:\n\n"
                    "**Topic:** one sentence\n\n"
                    "**Key Points:**\n- point 1\n- point 2\n- point 3\n\n"
                    "**Outcome:** one or two sentences.\n\nBe concise and direct."
                )},
                {"role": "user", "content": f"Conversation:\n\n{transcript}"},
            ]
            self.console.print("   [dim]Summarizing…[/]")
            try:
                chat_fn = self.get_chat_fn()
                summary = ""
                for chunk in chat_fn(summary_msgs):
                    summary += chunk
                from rich.markdown import Markdown
                from rich.panel import Panel
                self.console.print(Panel(
                    Markdown(summary.strip()),
                    title="[bold cyan]Conversation Summary[/]",
                    border_style="cyan",
                    padding=(1, 2),
                ))
            except Exception as e:
                self.console.print(f"   [red]{UI.err}[/] Summarize failed: {e}")

        else:
            self.console.print(f"   [red]{UI.err}[/] Unknown command: {command}")
            self.console.print("   [dim]Type /help for commands[/]")
        
        return True
    
    def list_models(self):
        """List available Ollama models"""
        if self.config.provider != Provider.OLLAMA:
            self.console.print("   [dim]/models only works with Ollama[/]")
            return
        
        import urllib.request
        try:
            req = urllib.request.Request("http://localhost:11434/api/tags")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode())
                models = data.get("models", [])
                
                if models:
                    self.console.print("   [cyan]Available models:[/]")
                    for m in models:
                        name = m["name"]
                        size = m.get("size", 0) / (1024**3)  # GB
                        marker = f"[green]{UI.on}[/]" if name.startswith(self.config.model) else f"[dim]{UI.off}[/]"
                        self.console.print(f"   {marker} {name} [dim]({size:.1f}GB)[/]")
                else:
                    self.console.print("   [dim]No models found. Pull one with:[/]")
                    self.console.print("   [cyan]ollama pull llama3.2[/]")
        except Exception:
            self.console.print("   [red]Could not list models[/]")
    
    def print_help(self):
        """Print help"""
        ic = UI.icons
        wave = "🌊 " if UNICODE else ""
        surf = "🏄 " if UNICODE else ""
        title = f"[bold magenta]{surf}SURF Help[/]"
        help_text = f"""
[bold blue]━━━ Commands ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]

  [bold yellow]/search[/] [dim]on|off[/]     Toggle web search {wave}
  [bold yellow]/research[/] [dim]<topic>[/]  Deep research mode 🔬
  [bold yellow]/think[/] [dim]on|off[/]      Toggle thinking mode 🧠
  [bold yellow]/stream[/] [dim]on|off[/]     Toggle live streaming ⚡
  
[bold cyan]━━━ Model & Provider ━━━━━━━━━━━━━━━━━━━━━━━━[/]

  [bold yellow]/model[/] [dim]<name>[/]      Switch model
  [bold yellow]/models[/]             List Ollama models
  [bold yellow]/provider[/] [dim]<name>[/]   Switch provider
  [bold yellow]/key[/] [dim]<prov> <key>[/]  Set API key
  [bold yellow]/url[/] [dim]<url>[/]         Set custom API URL

[bold green]━━━ Session ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]

  [bold yellow]/clear[/]              Clear conversation
  [bold yellow]/new[/]                Start fresh
  [bold yellow]/status[/]             Show current settings
  [bold yellow]/web[/]                Launch browser UI
  [bold yellow]/quit[/]               Exit

[bold magenta]━━━ Providers ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]

  {ic['ollama']}[bold]ollama[/]      Local AI (free, private)
  {ic['anthropic']}[bold]anthropic[/]   Claude (smartest)
  {ic['openai']}[bold]openai[/]      GPT-4o, o1
  {ic['openrouter']}[bold]openrouter[/]  100+ models
  {ic['custom']}[bold]custom[/]      Any OpenAI-compatible

[dim]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]
[dim]Tip: Start with[/] [cyan]--search[/] [dim]for web access[/]
[dim]Tip: Press[/] [cyan]Ctrl+C[/] [dim]to interrupt responses[/]"""
        self.console.print(Panel(help_text.strip(), border_style="magenta", box=_BOX, title=title, padding=(1, 2)))
    
    def print_status(self):
        """Print current status"""
        surf_icon = "🏄 " if UNICODE else ""
        title = f"[bold]{surf_icon}Status[/]"
        table = Table(show_header=False, box=_BOX, border_style="cyan", title=title)
        table.add_column("", style="dim", width=12)
        table.add_column("", style="cyan")

        icon = UI.icons.get(self.config.provider.value, "")
        table.add_row("Provider", f"{icon}{self.config.provider.value}")
        table.add_row("Model", self.config.model)
        vm = self.config.vision_model
        if vm:
            table.add_row("Vision", f"[cyan]{vm}[/]")
        else:
            table.add_row("Vision", f"[dim]not set — /vision <model>[/]")
        table.add_row("URL", self.config.api_base or "default")
        if self._pending_image:
            table.add_row("Image", "[green]📎 attached[/]")
        table.add_row("API Key", f"[green]{UI.on} set[/]" if self.config.api_key else f"[red]{UI.off} not set[/]")
        table.add_row("Search", f"[green]{UI.on} ON[/]" if self.config.web_search else f"[dim]{UI.off} OFF[/]")
        table.add_row("Think", f"[green]{UI.on} ON[/]" if self.config.thinking else f"[dim]{UI.off} OFF[/]")
        table.add_row("Live", f"[green]{UI.on} ON[/]" if self.config.streaming else f"[dim]{UI.off} OFF[/]")
        table.add_row("Messages", f"{len(self.config.messages)}")
        
        # Token usage
        total_text = "".join(m.get("content", "") for m in self.config.messages)
        used_tokens = estimate_tokens(total_text)
        context_limit = get_context_limit(self.config.model)
        usage_pct = (used_tokens / context_limit) * 100 if context_limit > 0 else 0
        token_color = "red" if usage_pct > 80 else ("yellow" if usage_pct > 50 else "green")
        table.add_row("Tokens", f"[{token_color}]{format_tokens(used_tokens)}[/] / {format_tokens(context_limit)} ({usage_pct:.0f}%)")

        self.console.print(table)
    
    def _get_chat_fn_for(self, model: str):
        """Get a chat function for a specific model (used for vision model override)."""
        if self.config.provider == Provider.OLLAMA:
            return lambda msgs: chat_ollama(msgs, model, self.config.api_base)
        elif self.config.provider == Provider.ANTHROPIC:
            return lambda msgs: chat_anthropic(msgs, model, self.config.api_key, self.config.thinking)
        elif self.config.provider == Provider.OPENAI:
            return lambda msgs: chat_openai(msgs, model, self.config.api_key)
        elif self.config.provider == Provider.OPENROUTER:
            return lambda msgs: chat_openai(msgs, model, self.config.api_key, "https://openrouter.ai/api/v1")
        else:
            return lambda msgs: chat_openai(msgs, model, self.config.api_key, self.config.api_base)

    def do_search(self, query: str) -> str:
        """Perform web search - uses NEWS search for latest results"""
        self.console.print(f"   [dim]{UI.search_lbl}[/]", end="")
        
        try:
            # Try news search first for latest results
            results = news_search(query, num_results=5)
            
            # Fallback to regular search if no news found
            if not results:
                results = search(query, num_results=5)
            
            if results:
                self.console.print(f" [dim]found {len(results)} results[/]")
                context = f"\n\nLatest web search results for '{query}' (as of March 2026):\n\n"
                for r in results:
                    date_str = f" ({r.get('date', '')})" if r.get('date') else ""
                    source_str = f" - {r.get('source', '')}" if r.get('source') else ""
                    context += f"• {r['title']}{date_str}{source_str}\n  {r.get('snippet', '')}\n  URL: {r.get('url', '')}\n\n"
                return context
            else:
                self.console.print(" [dim]no results[/]")
                return ""
        except Exception as e:
            self.console.print(f" [dim]error: {e}[/]")
            return ""
    
    def do_research(self, topic: str):
        """Deep research mode - multi-query search with page fetching and AI summarization"""
        self.console.print()
        self.console.print(f"   [bold magenta]🔬 Research Mode[/] — [cyan]{topic}[/]")
        self.console.print()
        
        # Generate multiple search angles
        search_angles = [
            topic,
            f"{topic} latest news 2026",
            f"{topic} explained",
        ]
        
        all_results = []
        fetched_content = []
        
        # Phase 1: Multi-angle search
        self.console.print(f"   [dim]Phase 1: Searching multiple angles...[/]")
        for i, query in enumerate(search_angles, 1):
            self.console.print(f"   [dim]  [{i}/{len(search_angles)}] {query[:50]}...[/]" if len(query) > 50 else f"   [dim]  [{i}/{len(search_angles)}] {query}[/]")
            try:
                results = search(query, num_results=3)
                for r in results:
                    # Avoid duplicates by URL
                    if not any(existing.get('url') == r.get('url') for existing in all_results):
                        all_results.append(r)
            except Exception:
                pass
        
        self.console.print(f"   [green]✓[/] Found {len(all_results)} unique sources")
        self.console.print()
        
        # Phase 2: Fetch top pages
        self.console.print(f"   [dim]Phase 2: Reading top sources...[/]")
        pages_to_fetch = min(3, len(all_results))
        for i, result in enumerate(all_results[:pages_to_fetch], 1):
            url = result.get('url', '')
            title = result.get('title', 'Unknown')[:40]
            self.console.print(f"   [dim]  [{i}/{pages_to_fetch}] {title}...[/]")
            try:
                content = fetch(url)
                if content and not content.startswith("Error:"):
                    fetched_content.append({
                        'title': result.get('title', ''),
                        'url': url,
                        'content': content[:4000]  # Limit content size
                    })
            except Exception:
                pass
        
        self.console.print(f"   [green]✓[/] Read {len(fetched_content)} pages")
        self.console.print()
        
        # Phase 3: Build research context and ask AI to summarize
        self.console.print(f"   [dim]Phase 3: Synthesizing research...[/]")
        self.console.print()
        
        research_context = f"\\n\\n=== DEEP RESEARCH RESULTS FOR: {topic} ===\\n\\n"
        
        # Add search snippets
        research_context += "--- SEARCH RESULTS ---\\n"
        for r in all_results[:8]:
            research_context += f"• {r.get('title', '')}\\n  {r.get('snippet', '')}\\n  Source: {r.get('url', '')}\\n\\n"
        
        # Add fetched page content
        if fetched_content:
            research_context += "\\n--- FULL PAGE CONTENT ---\\n"
            for page in fetched_content:
                research_context += f"\\n[SOURCE: {page['title']}]\\n{page['url']}\\n{'-'*40}\\n{page['content']}\\n\\n"
        
        research_context += "=== END RESEARCH ===\\n"
        
        # Create a research prompt
        research_prompt = f"""Based on the research above, provide a comprehensive summary of: {topic}

Structure your response as:
1. **Overview** — What is this topic about?
2. **Key Findings** — The most important facts and recent developments
3. **Details** — Deeper insights from the sources
4. **Sources** — List the most relevant sources used

Be thorough but concise. Cite specific sources when making claims."""

        # Use the existing chat flow with research context
        system = SYSTEM_PROMPT + research_context
        if self.config.thinking:
            system += "\\n\\n" + THINKING_INSTRUCTIONS
        
        messages = [{"role": "system", "content": system}]
        messages.append({"role": "user", "content": research_prompt})
        
        # Stream response
        chat_fn = self.get_chat_fn()
        full_response = ""
        
        try:
            with self.console.status(f"   [dim]{UI.connecting}[/]", spinner="dots"):
                response_gen = chat_fn(messages)
                first_chunk = next(response_gen, None)
            
            if first_chunk and not first_chunk.startswith("ERROR:"):
                full_response = first_chunk
                
                if self.config.streaming:
                    # Live streaming
                    fill_w = max(0, self.console.width - 18)
                    self.console.print(f"   {UI.box_tl} [bold magenta]🔬 Research Summary[/] {UI.fill}" + UI.fill * fill_w + UI.box_tr)
                    self.console.print(f"   {UI.box_side}")
                    self.console.print(f"   {UI.box_side}  ", end="")
                    self.console.print(first_chunk, end="", highlight=False, markup=False)
                    
                    for chunk in response_gen:
                        full_response += chunk
                        if "\\n" in chunk:
                            lines = chunk.split("\\n")
                            for i, line in enumerate(lines):
                                if i > 0:
                                    self.console.print()
                                    self.console.print(f"   {UI.box_side}  ", end="")
                                self.console.print(line, end="", highlight=False, markup=False)
                        else:
                            self.console.print(chunk, end="", highlight=False, markup=False)
                    
                    self.console.print()
                    self.console.print(f"   {UI.box_side}")
                    self.console.print("   " + UI.box_bl + UI.fill * (self.console.width - 5) + UI.box_br)
                else:
                    # Panel mode
                    for chunk in response_gen:
                        full_response += chunk
                    
                    md = Markdown(full_response, code_theme="monokai")
                    panel = Panel(
                        md,
                        border_style="magenta",
                        box=_BOX,
                        padding=(1, 2),
                        title="[bold magenta]🔬 Research Summary[/]",
                        title_align="left",
                        expand=True,
                        width=min(self.console.width - 4, 120)
                    )
                    self.console.print(panel)
            else:
                self.console.print(f"   [red]{UI.err}[/] Could not generate summary")
                
        except KeyboardInterrupt:
            self.console.print("\\n   [dim](interrupted)[/]")
        
        # Save to conversation history
        if full_response:
            self.config.messages.append({"role": "user", "content": f"/research {topic}"})
            self.config.messages.append({"role": "assistant", "content": full_response})
    
    def chat(self, user_input: str, image_b64: str = None, image_mime: str = "image/png"):
        """Process chat message"""
        # Consume any pending image
        if image_b64 is None and self._pending_image:
            image_b64 = self._pending_image
            image_mime = self._pending_image_mime
            self._pending_image = None
            self._pending_image_mime = "image/png"

        # If image present, pick the right model
        effective_model = self.config.model
        if image_b64:
            if self.config.vision_model:
                effective_model = self.config.vision_model
            elif not _is_vision_model(self.config.model):
                self.console.print(f"   [yellow]⚠[/] No vision model set — image may be ignored. Use /vision <model>")

        # Web search if enabled
        search_context = ""
        if self.config.web_search:
            search_context = self.do_search(user_input)
        
        # Build system prompt using the global constants
        system = SYSTEM_PROMPT
        if self.config.thinking:
            system += "\n\n" + THINKING_INSTRUCTIONS
        if search_context:
            system += "\n\nSEARCH RESULTS (use these to answer):" + search_context
        
        messages = [{'role': 'system', 'content': system}]
        messages.extend(self.config.messages)
        user_msg: dict = {'role': 'user', 'content': user_input}
        if image_b64:
            user_msg['image'] = image_b64
            user_msg['image_mime'] = image_mime
        messages.append(user_msg)

        # Save user message (without image in history to keep context lean)
        self.config.messages.append({'role': 'user', 'content': user_input})
        
        self.console.print()
        
        # Stream response — use vision model when image is present
        if effective_model != self.config.model:
            chat_fn = self._get_chat_fn_for(effective_model)
        else:
            chat_fn = self.get_chat_fn()
        full_response = ""
        is_error = False
        
        try:
            with self.console.status(f"   [dim]{UI.connecting}[/]", spinner="dots"):
                response_gen = chat_fn(messages)
                first_chunk = next(response_gen, None)
            
            if first_chunk:
                # Handle special responses
                if first_chunk.startswith("ERROR:CONNECTION"):
                    is_error = True
                    self.console.print(f"   [bold red]{UI.err} Cannot connect to Ollama[/]")
                    self.console.print()
                    if start_ollama(self.console):
                        # Retry with multiple attempts — model may still be loading
                        for attempt in range(3):
                            self.console.print(f"   [dim]Retrying ({attempt + 1}/3)...[/]")
                            time.sleep(2)
                            response_gen = chat_fn(messages)
                            first_chunk = next(response_gen, None)
                            if first_chunk and not first_chunk.startswith("ERROR:"):
                                is_error = False
                                break
                        if is_error:
                            self.console.print("   [red]Could not connect after retries.[/]")
                            self.console.print("   [dim]Check that Ollama is running: ollama serve[/]")
                            self.config.messages.pop()  # Remove the saved user message
                            return
                    else:
                        self.console.print("   [dim]Start manually: ollama serve[/]")
                        self.config.messages.pop()  # Remove the saved user message
                        return
                elif first_chunk.startswith("ERROR:"):
                    is_error = True
                    error_msg = first_chunk[6:]
                    self.console.print(f"   [bold red]✗[/] {error_msg}")
                    return
                elif first_chunk.startswith("INSTALL:"):
                    self.console.print(f"   [dim]Installing {first_chunk[8:]}...[/]")
                    first_chunk = next(response_gen, "")
                
                if not is_error and first_chunk:
                    full_response = first_chunk
                    
                    if self.config.streaming:
                        # LIVE STREAMING MODE with real-time thinking detection
                        import time as _time
                        start_time = _time.time()
                        
                        self.console.print()
                        
                        # State: 'init', 'thinking', 'answer'
                        state = 'init'
                        buffer = first_chunk
                        displayed_chars = 0
                        
                        def start_thinking_box():
                            self.console.print(f"   [bold cyan]🧠 Thinking...[/]")
                            self.console.print(f"   [dim cyan]╭{'─' * 58}[/]")
                            self.console.print(f"   [dim cyan]│[/] ", end="")
                        
                        def end_thinking_box():
                            self.console.print()
                            self.console.print(f"   [dim cyan]╰{'─' * 58}[/]")
                            self.console.print()
                        
                        def start_answer_box():
                            surf_icon = "🏄 " if UNICODE else ""
                            self.console.print(f"   [bold magenta]{surf_icon}SURF[/]")
                            self.console.print(f"   [dim magenta]╭{'─' * 60}[/]")
                            self.console.print(f"   [dim magenta]│[/] ", end="")
                        
                        def end_answer_box():
                            self.console.print()
                            self.console.print(f"   [dim magenta]╰{'─' * 60}[/]")
                        
                        def stream_char(char, is_thinking):
                            """Stream a single character"""
                            if char == '\n':
                                self.console.print()
                                if is_thinking:
                                    self.console.print(f"   [dim cyan]│[/] ", end="")
                                else:
                                    self.console.print(f"   [dim magenta]│[/] ", end="")
                            else:
                                if is_thinking:
                                    self.console.print(f"[dim italic]{char}[/]", end="", highlight=False)
                                else:
                                    self.console.print(char, end="", highlight=False, markup=False)
                        
                        def process_buffer():
                            """Process buffer and stream output"""
                            nonlocal state, buffer
                            
                            while buffer:
                                if state == 'init':
                                    # Look for <think> tag
                                    if '<think>' in buffer:
                                        idx = buffer.index('<think>')
                                        # Content before <think> (rare)
                                        before = buffer[:idx]
                                        if before.strip():
                                            start_answer_box()
                                            for c in before:
                                                stream_char(c, False)
                                            end_answer_box()
                                        # Start thinking
                                        start_thinking_box()
                                        state = 'thinking'
                                        buffer = buffer[idx + 7:]  # Skip <think>
                                    elif len(buffer) >= 8:
                                        # Enough to know no <think> tag is coming at start
                                        start_answer_box()
                                        state = 'answer'
                                        # Output what we have and continue
                                        for c in buffer:
                                            stream_char(c, False)
                                        buffer = ""
                                        return
                                    else:
                                        # Need more data
                                        return
                                
                                elif state == 'thinking':
                                    if '</think>' in buffer:
                                        idx = buffer.index('</think>')
                                        # Output thinking content
                                        for c in buffer[:idx]:
                                            stream_char(c, True)
                                        end_thinking_box()
                                        buffer = buffer[idx + 8:]  # Skip </think>
                                        # Always transition to waiting_answer - more content may come
                                        state = 'waiting_answer'
                                        # If there's already content, start answer box
                                        if buffer.strip():
                                            start_answer_box()
                                            state = 'answer'
                                            for c in buffer:
                                                stream_char(c, False)
                                            buffer = ""
                                        return
                                    else:
                                        # Keep last 9 chars in buffer (</think> is 8)
                                        safe_len = max(0, len(buffer) - 9)
                                        if safe_len > 0:
                                            for c in buffer[:safe_len]:
                                                stream_char(c, True)
                                            buffer = buffer[safe_len:]
                                        return
                                
                                elif state == 'waiting_answer':
                                    # After thinking, waiting for answer content
                                    if buffer.strip():
                                        start_answer_box()
                                        state = 'answer'
                                        for c in buffer:
                                            stream_char(c, False)
                                        buffer = ""
                                    return
                                
                                elif state == 'answer':
                                    # Just stream everything
                                    for c in buffer:
                                        stream_char(c, False)
                                    buffer = ""
                                    return
                                
                                elif state == 'done':
                                    buffer = ""
                                    return
                        
                        # Process first chunk
                        process_buffer()
                        
                        # Stream remaining chunks
                        for chunk in response_gen:
                            full_response += chunk
                            buffer += chunk
                            process_buffer()
                        
                        # Flush any remaining buffer
                        if buffer:
                            if state == 'thinking':
                                for c in buffer:
                                    stream_char(c, True)
                            elif state in ('answer', 'waiting_answer'):
                                if state == 'waiting_answer' and buffer.strip():
                                    start_answer_box()
                                    state = 'answer'
                                if state == 'answer':
                                    for c in buffer:
                                        stream_char(c, False)
                            elif state == 'init':
                                # Never got <think>, output as answer
                                start_answer_box()
                                for c in buffer:
                                    stream_char(c, False)
                                state = 'answer'
                        
                        # Close any open boxes
                        if state == 'thinking':
                            end_thinking_box()
                        elif state == 'answer':
                            end_answer_box()
                        
                        # Performance stats
                        elapsed = _time.time() - start_time
                        tokens_approx = len(full_response.split())
                        tps = tokens_approx / elapsed if elapsed > 0 else 0
                        self.console.print()
                        self.console.print(f"   [dim]⚡ {tokens_approx} tokens in {elapsed:.1f}s ({tps:.1f} tok/s)[/]")
                        
                        # Clean up full_response by removing think tags for storage
                        import re
                        clean_response = re.sub(r'<think>.*?</think>', '', full_response, flags=re.DOTALL).strip()
                        if clean_response:
                            full_response = clean_response
                    else:
                        # PANEL MODE - animated thinking, then render
                        import sys
                        spinner_frames = UI.spinner
                        frame_idx = 0

                        # Collect with animated spinner
                        sys.stdout.write(f"   {spinner_frames[0]} Thinking...")
                        sys.stdout.flush()

                        for chunk in response_gen:
                            full_response += chunk
                            frame_idx = (frame_idx + 1) % len(spinner_frames)
                            sys.stdout.write(f"\r   {spinner_frames[frame_idx]} Thinking...")
                            sys.stdout.flush()
                        
                        # Clear spinner line completely
                        sys.stdout.write("\r                    \r")
                        sys.stdout.flush()
                        
                        # Process thinking tags if present
                        display_response = full_response
                        if '<think>' in full_response and '</think>' in full_response:
                            display_response = format_thinking_response(full_response, self.console)
                        
                        # Render response in adaptive panel
                        try:
                            md = Markdown(display_response, code_theme="monokai")
                            surf_icon = "🏄 " if UNICODE else ""
                            panel = Panel(
                                md,
                                border_style="magenta",
                                box=_BOX,
                                padding=(1, 2),
                                title=f"[bold magenta]{surf_icon}SURF[/]",
                                title_align="left",
                                expand=True,
                                width=min(self.console.width - 4, 120)
                            )
                            self.console.print(panel)
                        except Exception:
                            # Fallback to plain text
                            self.console.print()
                            for line in full_response.split("\n"):
                                self.console.print(f"   {line}")
            
        except KeyboardInterrupt:
            self.console.print("\n   [dim](interrupted)[/]")
        
        # Save response
        if full_response and not is_error:
            self.config.messages.append({"role": "assistant", "content": full_response})
    
    def run(self):
        """Main loop"""
        self.print_header()
        
        # Check Ollama on startup
        if self.config.provider == Provider.OLLAMA:
            if not self.check_ollama():
                self.console.print(f"   [yellow]{UI.warn} Ollama not ready. Use /provider to switch.[/]")
        
        while self.running:
            try:
                self.console.print()
                self.print_status_bar()
                self.console.print()
                
                if self._pending_image:
                    self.console.print("   [dim]📎 Image attached — type your message[/]")
                
                # User prompt with nice styling
                you_icon = "💬 " if UNICODE else "> "
                self.console.print(f"   [bold green]{you_icon}You[/]")
                try:
                    user_input = self._prompt.prompt(ANSI("   \x1b[2m╰─\x1b[0m "))
                except EOFError:
                    self.console.print()
                    self.console.print(f"   [bold cyan]{UI.bye}[/]")
                    self.console.print()
                    break
                
                if not user_input.strip():
                    continue
                
                if user_input.startswith("/"):
                    self.handle_command(user_input)
                else:
                    self.chat(user_input)
                
            except KeyboardInterrupt:
                self.console.print("\n   [dim]Type /quit to exit[/]")


# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="🏄 SURF - Search. Understand. Reason. Fast.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  surf                              # Ollama (auto-starts)
  surf -p anthropic                 # Claude
  surf -p openai -m gpt-4o          # GPT-4o
  surf -p openrouter                # OpenRouter (100s of models)
  surf -m mistral --search          # Mistral + web search
  surf --search                     # Enable web search
        """
    )
    
    parser.add_argument("-p", "--provider",
                        choices=["ollama", "anthropic", "openai", "openrouter", "custom"],
                        default="ollama", help="AI provider")
    parser.add_argument("-m", "--model", help="Model name")
    parser.add_argument("-k", "--key", help="API key")
    parser.add_argument("-u", "--url", help="Custom API URL")
    parser.add_argument("--search", "-s", action="store_true", help="Enable web search")
    parser.add_argument("--no-think", action="store_true", help="Disable thinking")
    
    args = parser.parse_args()
    
    # Build config
    config = Config()
    config.provider = Provider(args.provider)
    
    # Set defaults based on provider
    if config.provider == Provider.ANTHROPIC:
        config.model = args.model or "claude-sonnet-4-20250514"
        config.api_key = args.key or os.getenv("ANTHROPIC_API_KEY")
    elif config.provider == Provider.OPENAI:
        config.model = args.model or "gpt-4o"
        config.api_key = args.key or os.getenv("OPENAI_API_KEY")
    elif config.provider == Provider.OPENROUTER:
        config.model = args.model or "meta-llama/llama-3.1-8b-instruct:free"
        config.api_key = args.key or os.getenv("OPENROUTER_API_KEY")
    else:
        config.model = args.model or "llama3.2"
        if args.key:
            config.api_key = args.key
    
    if args.url:
        config.api_base = args.url
    if args.search:
        config.web_search = True
    if args.no_think:
        config.thinking = False
    
    # Run
    cli = SurfCLI(config)
    cli.run()


if __name__ == "__main__":
    main()
