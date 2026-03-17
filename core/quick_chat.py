#!/usr/bin/env python3
"""
============================================================
⚡ Quick Chat - Simple AI Chat with Web Search
============================================================
Minimal version - easy to understand and customize!

Usage:
    python quick_chat.py                    # Ollama (default)
    python quick_chat.py --claude           # Anthropic Claude
    python quick_chat.py --openai           # OpenAI GPT
    python quick_chat.py --ollama mistral   # Specific Ollama model
============================================================
"""

import os
import sys
import json
import subprocess
import urllib.request
from typing import Generator

# Our web search
from .ai_search import search


# ============================================================
# AI PROVIDERS - Easy to add your own!
# ============================================================

def chat_ollama(messages: list, model: str = "llama3.2") -> Generator[str, None, None]:
    """Chat with Ollama (local)"""
    url = "http://localhost:11434/api/chat"
    
    data = json.dumps({
        "model": model,
        "messages": messages,
        "stream": True
    }).encode()
    
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            for line in response:
                if line:
                    chunk = json.loads(line.decode())
                    if "message" in chunk:
                        yield chunk["message"].get("content", "")
    except Exception as e:
        yield f"\n❌ Ollama error: {e}\n💡 Make sure Ollama is running: ollama serve"


def chat_anthropic(messages: list, model: str = "claude-sonnet-4-20250514") -> Generator[str, None, None]:
    """Chat with Anthropic Claude"""
    try:
        import anthropic
    except ImportError:
        yield "Installing anthropic..."
        subprocess.check_call([sys.executable, "-m", "pip", "install", "anthropic", "-q"])
        import anthropic
    
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        yield "❌ Set ANTHROPIC_API_KEY environment variable"
        return
    
    client = anthropic.Anthropic(api_key=api_key)
    
    # Extract system message
    system = "You are a helpful AI assistant."
    chat_msgs = []
    for m in messages:
        if m["role"] == "system":
            system = m["content"]
        else:
            chat_msgs.append(m)
    
    try:
        with client.messages.stream(
            model=model,
            max_tokens=4096,
            system=system,
            messages=chat_msgs
        ) as stream:
            for text in stream.text_stream:
                yield text
    except Exception as e:
        yield f"\n❌ Anthropic error: {e}"


def chat_openai(messages: list, model: str = "gpt-4o") -> Generator[str, None, None]:
    """Chat with OpenAI"""
    try:
        from openai import OpenAI
    except ImportError:
        yield "Installing openai..."
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
        from openai import OpenAI
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        yield "❌ Set OPENAI_API_KEY environment variable"
        return
    
    client = OpenAI(api_key=api_key)
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True
        )
        for chunk in response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        yield f"\n❌ OpenAI error: {e}"


def chat_custom(messages: list, model: str, base_url: str) -> Generator[str, None, None]:
    """Chat with any OpenAI-compatible API (LM Studio, vLLM, etc.)"""
    try:
        from openai import OpenAI
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "openai", "-q"])
        from openai import OpenAI
    
    client = OpenAI(api_key="not-needed", base_url=base_url)
    
    try:
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True
        )
        for chunk in response:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content
    except Exception as e:
        yield f"\n❌ API error: {e}"


# ============================================================
# CHAT LOOP
# ============================================================

def print_colored(text: str, color: str = "white"):
    """Simple colored output"""
    colors = {
        "red": "\033[91m",
        "green": "\033[92m",
        "yellow": "\033[93m",
        "blue": "\033[94m",
        "cyan": "\033[96m",
        "white": "\033[97m",
        "dim": "\033[2m",
        "reset": "\033[0m"
    }
    print(f"{colors.get(color, '')}{text}{colors['reset']}", end="")


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Quick AI Chat with Web Search")
    parser.add_argument("--ollama", nargs="?", const="llama3.2", metavar="MODEL",
                        help="Use Ollama (default model: llama3.2)")
    parser.add_argument("--claude", nargs="?", const="claude-sonnet-4-20250514", metavar="MODEL",
                        help="Use Anthropic Claude")
    parser.add_argument("--openai", nargs="?", const="gpt-4o", metavar="MODEL",
                        help="Use OpenAI GPT")
    parser.add_argument("--custom", nargs=2, metavar=("URL", "MODEL"),
                        help="Use custom OpenAI-compatible API")
    parser.add_argument("--search", "-s", action="store_true",
                        help="Enable web search")
    
    args = parser.parse_args()
    
    # Determine provider
    if args.claude:
        provider = "anthropic"
        model = args.claude
        chat_fn = lambda msgs: chat_anthropic(msgs, model)
    elif args.openai:
        provider = "openai"
        model = args.openai
        chat_fn = lambda msgs: chat_openai(msgs, model)
    elif args.custom:
        provider = "custom"
        base_url, model = args.custom
        chat_fn = lambda msgs: chat_custom(msgs, model, base_url)
    else:
        provider = "ollama"
        model = args.ollama or "llama3.2"
        chat_fn = lambda msgs: chat_ollama(msgs, model)
    
    web_search = args.search
    
    # Print header
    print("\n" + "="*60)
    print_colored("  ⚡ Quick Chat", "cyan")
    print(f" - {provider}:{model}")
    print("="*60)
    print_colored(f"  Web Search: ", "dim")
    print_colored("ON\n" if web_search else "OFF\n", "green" if web_search else "yellow")
    print_colored("  Commands: ", "dim")
    print("/search, /think, /clear, /quit\n")
    print("="*60 + "\n")
    
    # Chat state
    messages = []
    system_prompt = "You are a helpful AI assistant. When given web search results, use them to provide accurate, up-to-date information."
    thinking = True
    
    while True:
        try:
            # Get input
            print_colored("\nYou: ", "green")
            user_input = input().strip()
            
            if not user_input:
                continue
            
            # Commands
            if user_input.startswith("/"):
                cmd = user_input.lower().split()
                
                if cmd[0] in ["/quit", "/q", "/exit"]:
                    print_colored("👋 Bye!\n", "yellow")
                    break
                
                elif cmd[0] == "/search":
                    if len(cmd) > 1 and cmd[1] in ["on", "off"]:
                        web_search = cmd[1] == "on"
                    else:
                        web_search = not web_search
                    print_colored(f"🔍 Web search: {'ON' if web_search else 'OFF'}\n", "cyan")
                    continue
                
                elif cmd[0] == "/think":
                    if len(cmd) > 1 and cmd[1] in ["on", "off"]:
                        thinking = cmd[1] == "on"
                    else:
                        thinking = not thinking
                    print_colored(f"🧠 Thinking: {'ON' if thinking else 'OFF'}\n", "cyan")
                    continue
                
                elif cmd[0] == "/clear":
                    messages = []
                    print_colored("✓ Conversation cleared\n", "cyan")
                    continue
                
                elif cmd[0] == "/help":
                    print_colored("/search - toggle web search\n", "dim")
                    print_colored("/think  - toggle thinking\n", "dim")
                    print_colored("/clear  - clear history\n", "dim")
                    print_colored("/quit   - exit\n", "dim")
                    continue
                
                else:
                    print_colored(f"Unknown command: {cmd[0]}\n", "red")
                    continue
            
            # Web search if enabled
            search_context = ""
            if web_search:
                print_colored("🔍 Searching...", "dim")
                try:
                    results = search(user_input, num_results=5)
                    if results:
                        search_context = "\n\nWeb search results:\n"
                        for r in results:
                            search_context += f"• {r['title']}: {r['snippet']}\n  URL: {r['url']}\n"
                        print_colored(f" found {len(results)} results\n", "dim")
                    else:
                        print_colored(" no results\n", "dim")
                except Exception as e:
                    print_colored(f" error: {e}\n", "red")
            
            # Build messages
            full_system = system_prompt + search_context
            api_messages = [{"role": "system", "content": full_system}]
            api_messages.extend(messages)
            api_messages.append({"role": "user", "content": user_input})
            
            # Get response
            print_colored("\nAssistant: ", "cyan")
            
            full_response = ""
            for chunk in chat_fn(api_messages):
                print(chunk, end="", flush=True)
                full_response += chunk
            print()  # Newline
            
            # Save to history
            messages.append({"role": "user", "content": user_input})
            messages.append({"role": "assistant", "content": full_response})
            
        except KeyboardInterrupt:
            print_colored("\n(interrupted)\n", "dim")
        except EOFError:
            print_colored("\n👋 Bye!\n", "yellow")
            break


if __name__ == "__main__":
    main()
