"""
SURF Web UI — local web interface for SURF chat.

Launch from CLI with /web, or run directly: python web_ui.py
"""

import json
import os
import re
import sys
import time
import threading
import uuid
import webbrowser
from pathlib import Path

from flask import Flask, Response, request, jsonify, send_from_directory

# Import SURF internals
from .surf import (
    Config, Provider, SYSTEM_PROMPT, THINKING_INSTRUCTIONS,
    chat_ollama, chat_anthropic, chat_openai,
    is_ollama_running, start_ollama, ensure_model_exists,
    _is_vision_model,
)
from .ai_search import search, news_search, fetch

# ════════════════════════════════════════════════════════════════
# FLASK APP
# ════════════════════════════════════════════════════════════════

app = Flask(__name__, static_folder=None)
app.secret_key = os.urandom(32)

# Project root (one level up from core/)
_ROOT = Path(__file__).resolve().parent.parent

# Global state — single-user local tool
_config = Config()

# Chat history: list of conversations
# Each: {"id": str, "title": str, "created": float, "messages": [...]}
_conversations: list = []
_active_id: str | None = None
_CHAT_FILE = _ROOT / "surf_chats.json"
_MEMORY_FILE = _ROOT / "surf_memory.json"
_KEYS_FILE = _ROOT / "surf_keys.json"

# ── Saved API keys (per-provider) ─────────────────────────────
# {"anthropic": "sk-...", "openai": "sk-...", "openrouter": "sk-...", "custom": "sk-..."}
_saved_keys: dict[str, str] = {}

# ── Global memory (cross-conversation) ────────────────────────
# List of {"fact": str, "source": str, "ts": float}
_global_memory: list[dict] = []

# ── Search cache (query → (results, timestamp)) ──────────────
_search_cache: dict[str, tuple[list, float]] = {}
_CACHE_TTL = 300  # 5 minutes

# ── Aggregate stats ───────────────────────────────────────────
_STATS_FILE = _ROOT / "surf_stats.json"
_agg_stats: dict = {"total_messages": 0, "total_tokens": 0, "total_reasoning_tokens": 0,
                     "total_searches": 0, "total_time_s": 0.0, "requests": []}


def _load_keys():
    global _saved_keys
    try:
        if _KEYS_FILE.exists():
            data = json.loads(_KEYS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _saved_keys = data
    except Exception:
        pass


def _save_keys():
    try:
        _KEYS_FILE.write_text(json.dumps(_saved_keys, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _get_key_for_provider(provider_val: str) -> str:
    """Get the best available API key for a provider.
    Priority: saved keys file > environment variable > empty."""
    if _saved_keys.get(provider_val):
        return _saved_keys[provider_val]
    env_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai": "OPENAI_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }
    env_var = env_map.get(provider_val)
    if env_var:
        return os.getenv(env_var, "")
    return ""


def _load_stats():
    global _agg_stats
    try:
        if _STATS_FILE.exists():
            data = json.loads(_STATS_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                _agg_stats = data
    except Exception:
        pass


def _save_stats():
    try:
        _STATS_FILE.write_text(json.dumps(_agg_stats, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _record_stats(stats: dict):
    """Record per-message stats into aggregate."""
    _agg_stats["total_messages"] += 1
    _agg_stats["total_tokens"] += stats.get("ans_tokens", 0)
    _agg_stats["total_reasoning_tokens"] += stats.get("reasoning_tokens", 0)
    _agg_stats["total_time_s"] = round(_agg_stats["total_time_s"] + stats.get("total_s", 0), 2)
    _agg_stats["total_searches"] += 1 if stats.get("searched") else 0
    # Keep last 200 requests for per-request stats
    _agg_stats.setdefault("requests", []).append({
        "tokens": stats.get("ans_tokens", 0),
        "tps": stats.get("tps", 0),
        "time": stats.get("total_s", 0),
        "model": stats.get("model", ""),
        "ts": time.time(),
    })
    _agg_stats["requests"] = _agg_stats["requests"][-200:]
    _save_stats()


def _clean_title(title: str) -> str:
    """Strip markdown formatting junk from auto-generated chat titles."""
    t = title.strip()
    # Remove markdown: bold/italic asterisks, underscores, backticks, hashes, tildes
    t = re.sub(r'[*_`~#>]', '', t)
    # Collapse multiple spaces / leading-trailing cleanup
    t = re.sub(r'\s+', ' ', t).strip()
    # Remove leading hyphens/bullets
    t = re.sub(r'^[-•]+\s*', '', t).strip()
    # Remove wrapping quotes
    t = t.strip('"').strip("'").strip()
    return t


def _extract_text_from_image(b64_data: str) -> str:
    """Try to OCR text from a base64-encoded image. Returns extracted text or empty string."""
    try:
        import base64
        import io
        from PIL import Image
        img_bytes = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(img_bytes))
        # Primary: RapidOCR (pip-only, no system binary needed)
        try:
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()
            import numpy as np
            result, _ = ocr(np.array(img))
            if result:
                text = ' '.join(line[1] for line in result).strip()
                if text:
                    return text
        except Exception:
            pass
        # Fallback: pytesseract (needs Tesseract binary installed)
        try:
            import pytesseract
            text = pytesseract.image_to_string(img).strip()
            if text:
                return text
        except Exception:
            pass
    except Exception:
        pass
    return ""


def _cached_search(query: str, num_results: int = 5, use_news: bool = False) -> list:
    """Return cached results or perform fresh search.
    use_news=True uses DuckDuckGo news API (for time-sensitive queries).
    use_news=False uses general web search (default).
    """
    key = (query.strip().lower(), use_news)
    now = time.time()
    if key in _search_cache:
        results, ts = _search_cache[key]
        if now - ts < _CACHE_TTL:
            return results
    if use_news:
        results = news_search(query, num_results=num_results)
        if not results:
            results = search(query, num_results=num_results)
    else:
        results = search(query, num_results=num_results)
    _search_cache[key] = (results, now)
    # Evict old entries
    stale = [k for k, (_, ts) in _search_cache.items() if now - ts > _CACHE_TTL * 2]
    for k in stale:
        del _search_cache[k]
    return results


# ── Model context window detection (#6) ──────────────────────
_model_ctx_cache: dict[str, int] = {}

def _get_model_context_size(model: str) -> int:
    """Query Ollama for model context window size. Returns token count (default 2048)."""
    if model in _model_ctx_cache:
        return _model_ctx_cache[model]
    try:
        import urllib.request
        url = f"{_config.api_base or 'http://localhost:11434'}/api/show"
        data = json.dumps({"name": model}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            info = json.loads(resp.read().decode())
            # Ollama returns model info with parameters or modelfile
            params = info.get("model_info", {})
            # Try common keys for context length
            for key in params:
                if "context_length" in key.lower():
                    ctx = int(params[key])
                    _model_ctx_cache[model] = ctx
                    return ctx
            # Fallback: parse from modelfile/parameters
            modelfile = info.get("modelfile", "") + info.get("parameters", "")
            import re
            m = re.search(r'num_ctx\s+(\d+)', modelfile)
            if m:
                ctx = int(m.group(1))
                _model_ctx_cache[model] = ctx
                return ctx
    except Exception:
        pass
    _model_ctx_cache[model] = 2048
    return 2048


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


def _trim_messages_to_fit(messages: list, system: str, max_tokens: int) -> list:
    """Trim oldest messages (keeping system + last user) to fit within context window.
    Reserves 30% of context for the model's response."""
    response_reserve = int(max_tokens * 0.3)
    available = max_tokens - response_reserve - _estimate_tokens(system)
    if available <= 0:
        return messages[-1:]  # Just the last message

    # Always keep the last message (current user input)
    result = []
    total = 0
    for msg in reversed(messages):
        msg_tokens = _estimate_tokens(msg.get("content", ""))
        if total + msg_tokens > available and result:
            break
        result.append(msg)
        total += msg_tokens
    result.reverse()
    return result


# ── Search query rewriting (#2) ──────────────────────────────
def _rewrite_search_query(user_input: str, conversation_messages: list, ocr_text: str = "") -> str:
    """Rewrite a verbose user question into a concise search query.
    Uses conversation context for follow-ups and OCR text for image queries.
    Falls back to raw input."""
    import re

    # If user said something vague like "search for it/this" and we have OCR text,
    # use the OCR text directly as the search basis
    vague = re.match(
        r'^(search|look up|find|google|look for|surf)\s*(for\s*)?(it|this|that|the image|the picture|what.s in)?\s*[.!]?\s*$',
        user_input.split('\n')[0].strip(), re.IGNORECASE
    )
    if vague and ocr_text:
        # OCR text IS the search query — take the most meaningful part
        clean_ocr = ocr_text.strip()[:200]
        if len(clean_ocr.split()) <= 8:
            return clean_ocr
        # For longer OCR text, let the model pick the key terms
        try:
            chat_fn = _get_chat_fn()
            msgs = [
                {"role": "system", "content": (
                    "Extract the most important searchable phrase (3-8 words) from this text. "
                    "Output ONLY the search query, nothing else."
                )},
                {"role": "user", "content": clean_ocr},
            ]
            result = ""
            for chunk in chat_fn(msgs):
                result += chunk
                if len(result) > 80:
                    break
            result = result.strip().strip('"').strip("'").split('\n')[0].strip()
            if 3 <= len(result) <= 120:
                return result
        except Exception:
            return clean_ocr.split('.')[0][:80]  # First sentence

    # Short queries without OCR context are already fine
    raw_msg = user_input.split('\n')[0].strip()  # First line only (before any OCR text)
    if len(raw_msg.split()) <= 6 and not ocr_text:
        return user_input

    try:
        chat_fn = _get_chat_fn()
        msgs = [
            {"role": "system", "content": (
                "Convert the user's message into a short web search query (3-8 words). "
                "Output ONLY the search query, nothing else. No quotes, no explanation.\n"
                "If the message contains [Text extracted from attached image:], "
                "use that extracted text as the main search context.\n"
                "Examples:\n"
                "User: 'Can you tell me about what's been going on with the economy lately?'\n"
                "Query: US economy latest news 2026\n"
                "User: 'I need help figuring out the best laptop for programming'\n"
                "Query: best laptop programming 2026\n"
                "User: 'who created it?'\n"
                "Query: who created Rust programming language"
            )},
        ]
        # Add last 2 messages for follow-up context
        recent = [m for m in conversation_messages[-4:] if m.get("role") in ("user", "assistant")]
        for m in recent:
            msgs.append({"role": m["role"], "content": m["content"][:200]})
        msgs.append({"role": "user", "content": user_input})

        result = ""
        for chunk in chat_fn(msgs):
            result += chunk
            if len(result) > 80:
                break
        result = result.strip().strip('"').strip("'").split('\n')[0].strip()
        if 3 <= len(result) <= 120:
            return result
    except Exception:
        pass
    return raw_msg if raw_msg else user_input


# ── Result compression (#3) ──────────────────────────────────
def _compress_search_context(results: list, query: str, model_ctx: int) -> str:
    """Format search results, compressing more aggressively for smaller context windows.
    Preserves all key info (title, snippet, URL, date) but trims intelligently."""
    if not results:
        return ""

    # Budget: use at most ~15% of context for search results
    char_budget = int(model_ctx * 4 * 0.15)  # tokens * 4 chars/token * 15%
    char_budget = max(char_budget, 600)  # minimum useful size

    lines = []
    used = 0
    for r in results:
        title = r.get('title', '')
        snippet = r.get('snippet', '')
        url = r.get('url', '')
        date_str = f" ({r.get('date', '')})" if r.get('date') else ""
        source_str = f" [{r.get('source', '')}]" if r.get('source') else ""

        # For tight budgets, truncate snippets
        if char_budget < 1500 and len(snippet) > 120:
            snippet = snippet[:120] + "..."

        entry = f"- {title}{date_str}{source_str}\n  {snippet}\n  {url}\n"
        if used + len(entry) > char_budget and lines:
            break
        lines.append(entry)
        used += len(entry)

    return f"\n\nWeb search results for '{query}':\n\n" + "\n".join(lines)


# ── Follow-up context (#4) ───────────────────────────────────
def _get_last_search_topic(convo: dict) -> str:
    """Get the topic/query from the last search in this conversation."""
    for msg in reversed(convo.get("messages", [])):
        if msg.get("role") == "search":
            try:
                results = json.loads(msg["content"])
                if results:
                    # Extract topic from first result title
                    return results[0].get("title", "")[:100]
            except Exception:
                pass
    return ""


def _save_memory():
    try:
        _MEMORY_FILE.write_text(json.dumps(_global_memory, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _load_memory():
    global _global_memory
    try:
        if _MEMORY_FILE.exists():
            data = json.loads(_MEMORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _global_memory = data
    except Exception:
        pass


# ── Fact extraction (signal-based, zero LLM cost) ────────────

# Patterns that capture personal / persistent facts
_FACT_PATTERNS = [
    # Name: "my name is X", "I'm X", "call me X"
    (re.compile(r"\b(?:my name is|i'?m called|call me|i go by)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.I),
     lambda m: f"User's name is {m.group(1)}"),
    # Profession: "I'm a developer", "I work as a ..."
    (re.compile(r"\bi(?:'m| am) (?:a |an )?([\w\s]{2,30}?)(?:\.|,|\band\b|$)", re.I),
     None),  # handled specially below
    # Work: "I work at/on/for X"
    (re.compile(r"\bi work (?:at|on|for|with)\s+(.{2,40}?)(?:\.|,|$)", re.I),
     lambda m: f"User works at {m.group(1).strip().rstrip('.,')}"),
    # Location: "I live in X", "I'm from X", "I'm based in X"
    (re.compile(r"\bi(?:'m| am) (?:from|based in|located in|living in)\s+(.{2,40}?)(?:\.|,|$)", re.I),
     lambda m: f"User is from {m.group(1).strip().rstrip('.,')}"),
    (re.compile(r"\bi live in\s+(.{2,40}?)(?:\.|,|$)", re.I),
     lambda m: f"User lives in {m.group(1).strip().rstrip('.,')}"),
    # Preferences: "I like/love/prefer/hate X"
    (re.compile(r"\bi (?:really )?(like|love|prefer|enjoy|hate|dislike)\s+(.{2,50}?)(?:\.|,|$)", re.I),
     lambda m: f"User {m.group(1).lower()}s {m.group(2).strip().rstrip('.,')}"),
    # Uses: "I use X", "I'm using X"
    (re.compile(r"\bi(?:'m| am)? (?:currently )?using\s+(.{2,40}?)(?:\.|,|\s+for|$)", re.I),
     lambda m: f"User uses {m.group(1).strip().rstrip('.,')}"),
    # Age: "I'm 25", "I am 30 years old"
    (re.compile(r"\bi(?:'m| am)\s+(\d{1,3})\s*(?:years?\s*old)?(?:\.|,|$)", re.I),
     lambda m: f"User is {m.group(1)} years old" if 5 <= int(m.group(1)) <= 120 else None),
    # Explicit remember: "remember that ...", "keep in mind ..."
    (re.compile(r"\b(?:remember that|keep in mind(?:\s+that)?|don'?t forget(?:\s+that)?|note that|fyi)\s+(.{5,120}?)(?:\.|!|$)", re.I),
     lambda m: m.group(1).strip().rstrip('.!')),
    # Language/stack: "I code in X", "my stack is X"
    (re.compile(r"\bi (?:code|program|develop) (?:in|with)\s+(.{2,40}?)(?:\.|,|$)", re.I),
     lambda m: f"User codes in {m.group(1).strip().rstrip('.,')}"),
    (re.compile(r"\bmy (?:tech )?stack is\s+(.{2,60}?)(?:\.|$)", re.I),
     lambda m: f"User's tech stack: {m.group(1).strip().rstrip('.,')}"),
]

# Profession keywords to filter "I'm a ..." false positives
_PROFESSION_WORDS = re.compile(
    r"(?:developer|engineer|designer|student|teacher|professor|doctor|nurse|"
    r"artist|writer|manager|analyst|scientist|researcher|consultant|"
    r"architect|founder|freelancer|intern|devops|sysadmin|programmer|"
    r"web\s*dev|dev\b|coder|qa|ux|ui|dba|cto|ceo|pm\b|scrum|"
    r"data\s+scientist|ml\s+engineer|full[\s-]?stack|front[\s-]?end|back[\s-]?end)", re.I
)


def _normalize_fact(fact: str) -> str:
    """Ensure fact is phrased in third person about the user."""
    if fact.lower().startswith("user"):
        return fact
    # Rephrase first person → third person
    fact = re.sub(r"^(?:i'?m|i am)\s+", "User is ", fact, flags=re.I)
    fact = re.sub(r"^i\s+", "User ", fact, flags=re.I)
    fact = re.sub(r"^my\s+", "User's ", fact, flags=re.I)
    if not fact.lower().startswith("user"):
        fact = "User mentioned: " + fact
    return fact


def _extract_facts(text: str) -> list[str]:
    """Pull personal facts from user text. Returns list of fact strings."""
    facts = []
    for pattern, extractor in _FACT_PATTERNS:
        for match in pattern.finditer(text):
            if extractor is None:
                # Profession special case
                captured = match.group(1).strip().rstrip('.,')
                if _PROFESSION_WORDS.search(captured):
                    facts.append(f"User is a {captured}")
                continue
            result = extractor(match)
            if result:
                facts.append(_normalize_fact(result))
    return facts


def _is_duplicate_fact(new_fact: str, existing: list[dict]) -> bool:
    """Check if this fact is already stored (fuzzy)."""
    nf = new_fact.lower().strip()
    for item in existing:
        ef = item["fact"].lower().strip()
        # Exact or near match
        if nf == ef or nf in ef or ef in nf:
            return True
        # Same subject, different value → update (return False so it gets added)
    return False


def _process_memory(user_text: str, convo: dict):
    """Extract facts and store in appropriate memory tier."""
    facts = _extract_facts(user_text)
    if not facts:
        return []

    stored = []
    session_mem = convo.setdefault("session_memory", [])

    for fact in facts:
        # "remember that" / explicit instructions → always global
        is_explicit = any(kw in user_text.lower() for kw in
                         ["remember that", "keep in mind", "don't forget", "note that", "fyi"])
        # Personal identity facts → global
        is_personal = any(kw in fact.lower() for kw in
                          ["name is", "years old", "lives in", "is from", "works",
                           "is a ", "codes in", "stack:"])

        if is_explicit or is_personal:
            if not _is_duplicate_fact(fact, _global_memory):
                entry = {"fact": fact, "source": convo.get("id", "?"), "ts": time.time()}
                _global_memory.append(entry)
                stored.append(("global", fact))
                _save_memory()
        else:
            if not _is_duplicate_fact(fact, [{"fact": f} for f in session_mem]):
                session_mem.append(fact)
                stored.append(("session", fact))
                _save_chats()

    return stored


def _build_memory_context(convo: dict) -> str:
    """Build memory string to inject into system prompt (capped to avoid overloading models)."""
    MAX_MEMORY_CHARS = 1500  # keep memory context tight
    parts = []
    if _global_memory:
        facts = [m["fact"] for m in _global_memory[-30:]]  # newest 30 max
        parts.append("KNOWN FACTS ABOUT THE USER:\n" + "\n".join(f"- {f}" for f in facts))
    session_mem = convo.get("session_memory", [])
    if session_mem:
        parts.append("SESSION CONTEXT:\n" + "\n".join(f"- {f}" for f in session_mem[-20:]))
    result = "\n\n".join(parts)
    if len(result) > MAX_MEMORY_CHARS:
        result = result[:MAX_MEMORY_CHARS] + "\n[...memory truncated]"
    return result


def _save_chats():
    """Persist conversations to disk."""
    try:
        _CHAT_FILE.write_text(json.dumps(_conversations, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass  # best-effort


def _load_chats():
    """Load conversations from disk."""
    global _conversations, _active_id
    try:
        if _CHAT_FILE.exists():
            data = json.loads(_CHAT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, list):
                _conversations = data
                if _conversations:
                    _active_id = _conversations[0]["id"]
                    return
    except Exception:
        pass


def _active_convo() -> dict | None:
    for c in _conversations:
        if c["id"] == _active_id:
            return c
    return None


def _new_conversation(title: str = "New chat") -> dict:
    global _active_id
    convo = {
        "id": str(uuid.uuid4())[:8],
        "title": title,
        "created": time.time(),
        "messages": [],
    }
    _conversations.insert(0, convo)
    _active_id = convo["id"]
    _config.messages = []
    _save_chats()
    return convo


def _build_app_state() -> dict:
    convo = _active_convo()
    return {
        "provider": _config.provider.value,
        "model": _config.model,
        "web_search": _config.web_search,
        "thinking": _config.thinking,
        "streaming": _config.streaming,
        "agent_mode": _config.agent_mode,
        "vision_model": _config.vision_model,
        "api_key_set": bool(_config.api_key),
        "api_base": _config.api_base or "",
        "active_id": _active_id,
        "messages": convo["messages"] if convo else [],
        "memory_count": len(_global_memory),
        "session_memory_count": len(convo.get("session_memory", [])) if convo else 0,
        "conversations": [
            {"id": c["id"], "title": c["title"], "created": c["created"],
             "count": len([m for m in c["messages"] if m["role"] == "user"])}
            for c in _conversations
        ],
    }


# ── Static files ──────────────────────────────────────────────

STATIC_DIR = _ROOT / "static"


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


# ── API routes ────────────────────────────────────────────────

@app.route("/api/state")
def api_state():
    if not _active_id:
        _new_conversation()
    return jsonify(_build_app_state())


@app.route("/api/settings", methods=["POST"])
def api_settings():
    data = request.get_json()

    if "provider" in data:
        try:
            _config.provider = Provider(data["provider"])
        except ValueError:
            return jsonify({"error": "invalid provider"}), 400

        if _config.provider == Provider.ANTHROPIC:
            _config.model = "claude-sonnet-4-20250514"
        elif _config.provider == Provider.OPENAI:
            _config.model = "gpt-4o"
        elif _config.provider == Provider.OPENROUTER:
            _config.model = "meta-llama/llama-3.1-8b-instruct:free"
        elif _config.provider == Provider.OLLAMA:
            _config.model = "llama3.2"
        # Auto-load saved key for this provider
        _config.api_key = _get_key_for_provider(_config.provider.value)

    if "model" in data:
        _config.model = str(data["model"])[:200]  # cap model name length
    if "web_search" in data:
        _config.web_search = bool(data["web_search"])
    if "thinking" in data:
        _config.thinking = bool(data["thinking"])
    if "streaming" in data:
        _config.streaming = bool(data["streaming"])
    if "api_key" in data and data["api_key"]:
        _config.api_key = data["api_key"]
        # Save key for the current provider
        _saved_keys[_config.provider.value] = data["api_key"]
        _save_keys()
    if "api_base" in data:
        raw_base = data["api_base"] or None
        if raw_base and not raw_base.startswith(("http://", "https://")):
            return jsonify({"error": "api_base must be http:// or https://"}), 400
        _config.api_base = raw_base
    if "agent_mode" in data:
        _config.agent_mode = bool(data["agent_mode"])
    if "vision_model" in data:
        _config.vision_model = data["vision_model"] or ""

    # Auto-warmup whenever provider or model changes
    if ("provider" in data or "model" in data) and _config.provider == Provider.OLLAMA:
        _trigger_warmup()

    return jsonify(_build_app_state())


@app.route("/api/models")
def api_models():
    if _config.provider != Provider.OLLAMA:
        return jsonify({"models": [], "note": "Only available for Ollama"})
    import urllib.request
    try:
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            models = []
            for m in data.get("models", []):
                models.append({
                    "name": m["name"],
                    "size_gb": round(m.get("size", 0) / (1024 ** 3), 1),
                    "active": m["name"].startswith(_config.model),
                })
            return jsonify({"models": models})
    except Exception as e:
        return jsonify({"models": [], "error": str(e)})


# ── Agent mode endpoint ───────────────────────────────────────

@app.route("/api/agent", methods=["POST"])
def api_agent():
    """Run the browser agent. SSE stream of events."""
    data = request.get_json()
    task = data.get("task", "").strip()
    if not task:
        return jsonify({"error": "empty task"}), 400
    start_url = data.get("start_url", "")
    try:
        max_steps = min(int(data.get("max_steps", 15)), 30)  # cap at 30
    except (TypeError, ValueError):
        max_steps = 15

    from .browser_agent import run_agent_loop

    # Determine which model to use — prefer dedicated vision model, fall back to main model
    agent_model = _config.vision_model or _config.model
    has_vision = _is_vision_model(agent_model)
    # Build chat function
    if _config.provider == Provider.OLLAMA:
        chat_fn = lambda msgs: chat_ollama(msgs, agent_model, _config.api_base)
    elif _config.provider == Provider.ANTHROPIC:
        chat_fn = lambda msgs: chat_anthropic(msgs, agent_model, _config.api_key, False)
    elif _config.provider in (Provider.OPENAI, Provider.OPENROUTER, Provider.CUSTOM):
        chat_fn = lambda msgs: chat_openai(msgs, agent_model, _config.api_key, _config.api_base)
    else:
        chat_fn = lambda msgs: chat_ollama(msgs, agent_model, _config.api_base)

    # Also save the task as a user message in the conversation
    convo = _active_convo()
    if convo:
        convo["messages"].append({"role": "user", "content": f"[Agent Task] {task}"})
        _save_chats()

    def generate():
        agent_screenshots = []  # collect key screenshots for history
        agent_actions_log = []  # collect action descriptions
        try:
            for event in run_agent_loop(task, chat_fn, agent_model, max_steps, start_url, is_vision=has_vision):
                yield _sse(event)

                # Collect post-action screenshots (marked with save=True) for history
                if event.get("type") == "agent_screenshot" and event.get("save"):
                    # Keep last 5 screenshots to avoid bloating chat storage
                    agent_screenshots.append({
                        "step": event.get("step", 0),
                        "url": event.get("url", ""),
                        "image_b64": event.get("image_b64", ""),
                    })
                    if len(agent_screenshots) > 5:
                        agent_screenshots.pop(0)

                # Collect actions for the log
                if event.get("type") == "agent_action":
                    desc = event.get("explanation", event.get("action", ""))
                    a = event.get("action", "")
                    if a == "click": desc += f" @ ({event.get('x')}, {event.get('y')})"
                    elif a == "navigate": desc += f" → {event.get('value', '')}"
                    elif a == "type": desc += f': "{event.get("value", "")}"'
                    agent_actions_log.append(f"Step {event.get('step')}: {desc}")

                # When agent is done, save the result + screenshots to conversation
                if event.get("type") == "agent_done" and convo:
                    result = event.get("result", "")
                    steps = event.get("steps_taken", "?")
                    log_text = "\n".join(agent_actions_log[-10:]) if agent_actions_log else ""
                    convo["messages"].append({
                        "role": "assistant",
                        "content": f"[Agent Result]\n\n{result}\n\n*Completed in {steps} steps*",
                        "agent_screenshots": agent_screenshots,
                        "agent_log": log_text,
                    })
                    _save_chats()
        except Exception as e:
            yield _sse({"type": "agent_error", "step": 0, "error": str(e)})
            yield _sse({"type": "agent_done", "result": f"Agent failed: {e}", "steps_taken": 0})

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Conversation management ───────────────────────────────────

@app.route("/api/conversations/new", methods=["POST"])
def api_new_conversation():
    _new_conversation()  # _save_chats called inside
    return jsonify(_build_app_state())


@app.route("/api/conversations/switch", methods=["POST"])
def api_switch_conversation():
    global _active_id
    data = request.get_json()
    cid = data.get("id")
    for c in _conversations:
        if c["id"] == cid:
            _active_id = cid
            _config.messages = [m for m in c["messages"] if m["role"] != "search"]
            return jsonify(_build_app_state())
    return jsonify({"error": "not found"}), 404


@app.route("/api/conversations/delete", methods=["POST"])
def api_delete_conversation():
    global _active_id
    data = request.get_json()
    cid = data.get("id")
    _conversations[:] = [c for c in _conversations if c["id"] != cid]
    if _active_id == cid:
        if _conversations:
            _active_id = _conversations[0]["id"]
            _config.messages = [m for m in _conversations[0]["messages"] if m["role"] != "search"]
        else:
            _new_conversation()
    _save_chats()
    return jsonify(_build_app_state())


@app.route("/api/conversations/rename", methods=["POST"])
def api_rename_conversation():
    data = request.get_json()
    cid = data.get("id")
    title = data.get("title", "").strip()[:100]
    if not title:
        return jsonify({"error": "empty title"}), 400
    for c in _conversations:
        if c["id"] == cid:
            c["title"] = title
            _save_chats()
            return jsonify(_build_app_state())
    return jsonify({"error": "not found"}), 404


# ── Memory API ────────────────────────────────────────────────

@app.route("/api/memory")
def api_memory():
    """Return both memory tiers."""
    convo = _active_convo()
    return jsonify({
        "global": _global_memory,
        "session": convo.get("session_memory", []) if convo else [],
    })


@app.route("/api/memory/add", methods=["POST"])
def api_memory_add():
    """Manually add a global memory fact."""
    data = request.get_json()
    fact = _normalize_fact(data.get("fact", "").strip()[:200])
    if not fact:
        return jsonify({"error": "empty"}), 400
    if not _is_duplicate_fact(fact, _global_memory):
        _global_memory.append({"fact": fact, "source": "manual", "ts": time.time()})
        _save_memory()
    return jsonify({"global": _global_memory})


@app.route("/api/memory/delete", methods=["POST"])
def api_memory_delete():
    """Delete a memory fact by index."""
    data = request.get_json()
    tier = data.get("tier", "global")
    idx = data.get("index")
    if tier == "global" and isinstance(idx, int) and 0 <= idx < len(_global_memory):
        _global_memory.pop(idx)
        _save_memory()
    elif tier == "session":
        convo = _active_convo()
        if convo:
            sm = convo.get("session_memory", [])
            if isinstance(idx, int) and 0 <= idx < len(sm):
                sm.pop(idx)
                _save_chats()
    return api_memory()


@app.route("/api/memory/clear", methods=["POST"])
def api_memory_clear():
    """Clear all global memory."""
    _global_memory.clear()
    _save_memory()
    return jsonify({"global": _global_memory})


# ── Model warm-up ─────────────────────────────────────────────

def _trigger_warmup():
    """Fire a background warmup for the current Ollama model."""
    if _config.provider != Provider.OLLAMA:
        return
    import urllib.request as _ur

    def _do_warmup():
        try:
            payload = json.dumps({
                "model": _config.model,
                "prompt": "hi",
                "options": {"num_predict": 1},
            }).encode()
            req = _ur.Request(
                "http://localhost:11434/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            with _ur.urlopen(req, timeout=30):
                pass
        except Exception:
            pass

    threading.Thread(target=_do_warmup, daemon=True).start()


@app.route("/api/warmup", methods=["POST"])
def api_warmup():
    """Background warm-up: send a tiny prompt to pre-load the model."""
    _trigger_warmup()
    return jsonify({"ok": True})


# ── Skill system (SKILL.md-based) ─────────────────────────────

import yaml as _yaml
import re as _skill_re

SKILLS_DIR = _ROOT / "skills"

def _parse_skill_md(path: Path) -> dict | None:
    """Parse a SKILL.md file and return its metadata + body."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    # Parse YAML front-matter between --- fences
    m = _skill_re.match(r"^---\s*\n(.*?)\n---\s*\n(.*)", text, _skill_re.DOTALL)
    if not m:
        return None
    try:
        meta = _yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}
    body = m.group(2).strip()
    return {
        "id": path.parent.name,
        "name": meta.get("name", path.parent.name),
        "description": meta.get("description", ""),
        "icon": meta.get("icon", "📦"),
        "enabled": meta.get("enabled", True),
        "author": meta.get("author", ""),
        "version": str(meta.get("version", "1.0")),
        "body": body,
        "path": str(path),
    }


def _load_all_skills() -> list[dict]:
    """Scan skills/ directory and load every SKILL.md."""
    skills = []
    if not SKILLS_DIR.is_dir():
        return skills
    for child in sorted(SKILLS_DIR.iterdir()):
        if child.is_dir():
            skill_file = child / "SKILL.md"
            if skill_file.exists():
                s = _parse_skill_md(skill_file)
                if s:
                    skills.append(s)
    return skills


# In-memory enabled/disabled state (survives app lifetime, persisted via SKILL.md)
_skill_overrides: dict[str, bool] = {}  # id → enabled

_SKILL_ID_RE = _skill_re.compile(r'^[a-zA-Z0-9_-]+$')


def _get_skills() -> list[dict]:
    """Return skills with runtime enable/disable overrides applied."""
    skills = _load_all_skills()
    for s in skills:
        if s["id"] in _skill_overrides:
            s["enabled"] = _skill_overrides[s["id"]]
    return skills


def _get_active_skill_instructions() -> str:
    """Build a combined instruction string from all enabled skills for the system prompt."""
    skills = _get_skills()
    active = [s for s in skills if s["enabled"]]
    if not active:
        return ""
    parts = ["ACTIVE SKILLS (follow these instructions when relevant):"]
    for s in active:
        parts.append(f"\n═══ {s['name']} ({s['icon']}) ═══")
        parts.append(s["body"])
    return "\n".join(parts)


@app.route("/api/skills")
def api_skills():
    skills = _get_skills()
    return jsonify({"skills": [
        {"id": s["id"], "name": s["name"], "description": s["description"],
         "icon": s["icon"], "enabled": s["enabled"],
         "author": s["author"], "version": s["version"]}
        for s in skills
    ]})


@app.route("/api/skills/<skill_id>/toggle", methods=["POST"])
def api_skill_toggle(skill_id):
    """Enable or disable a skill at runtime."""
    if not _SKILL_ID_RE.match(skill_id):
        return jsonify({"error": "invalid skill id"}), 400
    skills = _load_all_skills()
    found = any(s["id"] == skill_id for s in skills)
    if not found:
        return jsonify({"error": f"Unknown skill: {skill_id}"}), 404
    data = request.get_json()
    enabled = data.get("enabled", True)
    _skill_overrides[skill_id] = bool(enabled)
    # Also persist to SKILL.md front-matter
    skill_file = SKILLS_DIR / skill_id / "SKILL.md"
    if skill_file.exists():
        try:
            text = skill_file.read_text(encoding="utf-8")
            text = _skill_re.sub(
                r"(enabled:\s*)(?:true|false)",
                f"\\g<1>{'true' if enabled else 'false'}",
                text, count=1
            )
            skill_file.write_text(text, encoding="utf-8")
        except Exception:
            pass  # Persist is best-effort
    return jsonify({"id": skill_id, "enabled": bool(enabled)})


@app.route("/api/skills/<skill_id>")
def api_skill_detail(skill_id):
    """Return the full SKILL.md body for a specific skill."""
    if not _SKILL_ID_RE.match(skill_id):
        return jsonify({"error": "invalid skill id"}), 400
    skill_file = SKILLS_DIR / skill_id / "SKILL.md"
    if not skill_file.exists():
        return jsonify({"error": "Skill not found"}), 404
    s = _parse_skill_md(skill_file)
    if not s:
        return jsonify({"error": "Failed to parse skill"}), 500
    if s["id"] in _skill_overrides:
        s["enabled"] = _skill_overrides[s["id"]]
    return jsonify(s)


# ── Conversation branching ────────────────────────────────────

@app.route("/api/conversations/branch", methods=["POST"])
def api_branch_conversation():
    """Fork a conversation at a specific message index, creating a new branch."""
    global _active_id
    data = request.get_json()
    cid = data.get("id", _active_id)
    msg_index = data.get("message_index", 0)

    source = None
    for c in _conversations:
        if c["id"] == cid:
            source = c
            break
    if not source:
        return jsonify({"error": "conversation not found"}), 404

    # Validate message_index
    if msg_index < 0 or msg_index >= len(source["messages"]):
        return jsonify({"error": "invalid message index"}), 400

    # Create branch with messages up to (not including) the specified index
    branch_msgs = [dict(m) for m in source["messages"][:msg_index]]
    branch = {
        "id": str(uuid.uuid4())[:8],
        "title": f"{source['title']} (branch)",
        "created": time.time(),
        "messages": branch_msgs,
        "branched_from": cid,
        "branch_point": msg_index,
    }
    _conversations.insert(0, branch)
    _active_id = branch["id"]
    _config.messages = [m for m in branch_msgs if m["role"] != "search"]
    _save_chats()
    return jsonify(_build_app_state())


# ── Aggregate stats API ──────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    """Return aggregate usage stats with rich breakdowns."""
    total_msgs = _agg_stats.get("total_messages", 0)
    total_tok = _agg_stats.get("total_tokens", 0)
    reqs = _agg_stats.get("requests", [])
    avg_tps = round(sum(r.get("tps", 0) for r in reqs) / len(reqs), 1) if reqs else 0

    # Model usage breakdown
    model_usage: dict[str, dict] = {}
    for r in reqs:
        m = r.get("model", "unknown")
        if m not in model_usage:
            model_usage[m] = {"messages": 0, "tokens": 0, "time": 0.0, "tps_sum": 0.0}
        model_usage[m]["messages"] += 1
        model_usage[m]["tokens"] += r.get("tokens", 0)
        model_usage[m]["time"] = round(model_usage[m]["time"] + r.get("time", 0), 2)
        model_usage[m]["tps_sum"] += r.get("tps", 0)
    model_breakdown = []
    for m, d in sorted(model_usage.items(), key=lambda x: -x[1]["messages"]):
        model_breakdown.append({
            "model": m, "messages": d["messages"], "tokens": d["tokens"],
            "time": round(d["time"], 1),
            "avg_tps": round(d["tps_sum"] / d["messages"], 1) if d["messages"] else 0,
        })

    # Speed trend (last 30 requests)
    speed_trend = [{"tps": r.get("tps", 0), "tokens": r.get("tokens", 0),
                     "time": r.get("time", 0)} for r in reqs[-30:]]

    # Per-conversation stats
    convo_stats = []
    for c in _conversations[:20]:
        user_msgs = sum(1 for m in c.get("messages", []) if m.get("role") == "user")
        ai_msgs = sum(1 for m in c.get("messages", []) if m.get("role") == "assistant")
        convo_stats.append({
            "id": c["id"], "title": c.get("title", "Untitled"),
            "user_msgs": user_msgs, "ai_msgs": ai_msgs,
            "total_msgs": user_msgs + ai_msgs,
            "created": c.get("created", 0),
        })

    # Peak speed
    peak_tps = max((r.get("tps", 0) for r in reqs), default=0)
    # Fastest / slowest response
    times = [r.get("time", 0) for r in reqs if r.get("time", 0) > 0]
    fastest = min(times) if times else 0
    slowest = max(times) if times else 0

    return jsonify({
        "total_messages": total_msgs,
        "total_tokens": total_tok,
        "total_reasoning_tokens": _agg_stats.get("total_reasoning_tokens", 0),
        "total_searches": _agg_stats.get("total_searches", 0),
        "total_time_s": _agg_stats.get("total_time_s", 0),
        "avg_tokens_per_msg": round(total_tok / total_msgs, 1) if total_msgs else 0,
        "avg_tps": avg_tps,
        "peak_tps": peak_tps,
        "fastest_response": fastest,
        "slowest_response": slowest,
        "total_conversations": len(_conversations),
        "model_breakdown": model_breakdown,
        "speed_trend": speed_trend,
        "conversation_stats": convo_stats,
        "recent_requests": reqs[-20:],
    })


# ── Summarize ─────────────────────────────────────────────────

@app.route("/api/conversations/summarize", methods=["POST"])
def api_conversations_summarize():
    """Summarize the active conversation using the current model."""
    convo = _active_convo()
    if not convo:
        return jsonify({"error": "no active conversation"}), 400
    msgs = [m for m in convo.get("messages", []) if m.get("role") in ("user", "assistant")]
    if len(msgs) < 2:
        return jsonify({"error": "not enough messages to summarize (need at least 2)"}), 400

    data = request.get_json() or {}
    save_to_memory = bool(data.get("save_to_memory", False))

    # Build a transcript — cap to last 40 messages to stay within context
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
            "Format your response as:\n\n"
            "**Topic:** one sentence describing what the conversation was about\n\n"
            "**Key Points:**\n"
            "- bullet 1\n- bullet 2\n- bullet 3\n\n"
            "**Outcome:** one or two sentences on what was resolved or concluded.\n\n"
            "Be concise. No filler phrases like 'In this conversation...'. "
            "Respond only with the formatted summary."
        )},
        {"role": "user", "content": f"Conversation:\n\n{transcript}"},
    ]

    try:
        chat_fn = _get_chat_fn()
        summary = ""
        for chunk in chat_fn(summary_msgs):
            summary += chunk
        summary = summary.strip()
        if summary.startswith("ERROR:"):
            return jsonify({"error": summary}), 502

        if save_to_memory and summary:
            title = convo.get("title", "chat")
            fact = _normalize_fact(f"Conversation summary ({title}): {summary[:300]}")
            if not _is_duplicate_fact(fact, _global_memory):
                _global_memory.append({"fact": fact, "source": convo.get("id", "?"), "ts": time.time()})
                _save_memory()

        return jsonify({
            "summary": summary,
            "convo_id": convo["id"],
            "title": convo.get("title", ""),
            "message_count": len(msgs),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Chat SSE ──────────────────────────────────────────────────

@app.route("/api/chat", methods=["POST"])
def api_chat():
    """SSE streaming endpoint for chat."""
    data = request.get_json()
    user_input = data.get("message", "").strip()
    if not user_input:
        return jsonify({"error": "empty message"}), 400

    # Optional image attachment (base64)
    image_b64 = data.get("image")  # raw base64 string, no data: prefix
    image_mime = data.get("image_mime", "image/png")

    # Server-side image size guard (~10 MB decoded ≈ 13.5 MB base64)
    _MAX_IMAGE_B64 = 13_500_000
    if image_b64 and len(image_b64) > _MAX_IMAGE_B64:
        return jsonify({"error": "Image too large (max 10 MB)"}), 413

    convo = _active_convo()
    if not convo:
        convo = _new_conversation()

    # Auto-title from first message
    is_first_message = not convo["messages"]
    if is_first_message:
        convo["title"] = user_input[:60] + ("..." if len(user_input) > 60 else "")

    def _search_mode(query: str) -> tuple[str, bool]:
        """
        Returns (mode, is_news) where mode is NO/HEADLINES/FETCH.
        is_news=True means use DuckDuckGo News API (time-sensitive).
        is_news=False means use general web search (default).
        """
        import re
        q = query.lower().strip()

        # ── Explicit "don't search" directives — HIGHEST PRIORITY ──
        NO_SEARCH = re.compile(
            r'('
            r'don.?t (search|look.?up|google|browse|surf|use.{0,10}(web|internet|search))|'
            r'no (search|web|internet|browsing|googl)|'
            r'without (search|look|brows|web|internet)|'
            r'stop search|skip.{0,5}search|'
            r'just (think|respond|answer|reply|tell me|explain|say|chat|talk)|'
            r'from (your|memory|what you know|your.{0,10}knowledge)|'
            r'(use )?your own (knowledge|memory|brain)|'
            r'no need to (search|look|browse|check)'
            r')',
            re.IGNORECASE
        )
        if NO_SEARCH.search(q):
            return ("NO", False)

        # ── Skip patterns — things that NEVER need search ──
        SKIP = re.compile(
            r'^('
            r'(write|create|generate|make) (me )?(a |an )?(poem|haiku|story|song|joke|limerick|essay|code|function|class|script)|'
            r'(explain|define|describe) .{0,30}(in simple terms|like i.m 5|simply|briefly)|'
            r'(translate|convert) .{0,20}(to|into)|'
            r'(fix|debug|refactor|optimize|review) (this|my|the) (code|function|program|script)|'
            r'(solve|calculate|compute|work out|figure out)|'
            r'hello|hi |hey |thanks|thank you|goodbye|bye|'
            r'(let.s|can you|please) (test|try|play|do) '
            r')'
        )
        if SKIP.search(q):
            return ("NO", False)

        # ── Thinking / reasoning / puzzle requests — no search needed ──
        THINK_ONLY = re.compile(
            r'\b('
            r'think (about|through|step)|reason|reasoning|logic|logical|'
            r'puzzle|riddle|brain.?teaser|thought experiment|'
            r'hypothetical|imagine|suppose|pretend|'
            r'role.?play|act as|you are a'
            r')\b'
        )
        if THINK_ONLY.search(q):
            return ("NO", False)

        # ── Explicit search intent ──
        EXPLICIT_SEARCH = re.compile(
            r'\b('
            r'search (for|about|up|the web|online|google)|'
            r'look up|look for|google|find (me|out|info|information)|'
            r'browse|surf (for|the web)'
            r')\b'
        )
        if EXPLICIT_SEARCH.search(q):
            return ("HEADLINES", False)

        # ── Time-sensitive signals → use NEWS search ──
        TIME_SIGNALS = re.compile(
            r'\b('
            r'today|tonight|yesterday|this (morning|evening|week|month|year)|'
            r'right now|just now|currently|at the moment|'
            r'latest|recent|breaking|just (announced|released|launched|dropped|happened)|'
            r'news|headline|update|report|announcement|'
            r'weather|forecast|temperature|'
            r'price|stock|crypto|bitcoin|market|'
            r'score|standings|results?|winner|'
            r'election|poll|vote|'
            r'what.{0,20}(happening|going on)|'
            r'any(thing)? new\b|'
            r'20\d\d\b'
            r')\b'
        )
        if TIME_SIGNALS.search(q):
            return ("HEADLINES", True)  # News!

        # ── Deep content signals ──
        FETCH_SIGNALS = re.compile(
            r'\b(what did .{2,40} say|according to|full article|read the|quote.{0,10}(from|by)|details? (of|about|on))\b'
            r'|https?://'
        )
        if FETCH_SIGNALS.search(q):
            return ("FETCH", False)

        # ── Factual question patterns → general web search ──
        # Only search for questions that genuinely need external info
        FACTUAL = re.compile(
            r'\b('
            r'who (is|are|was|were) \w{2,}|'
            r'where (is|are|was|were) \w{2,}|'
            r'when (is|are|was|were|did|does|will) \w|'
            r'(best|top|most|cheapest|fastest|newest|popular).{0,20}(for|to|in|of|that)|'
            r'recommend|suggestion|comparison|review|guide|tutorial|'
            r'vs\.?|versus|compared to|'
            r'tell me about \w{2,}|'
            r'does .{0,20} (work|exist|support|have|cost|take)'
            r')\b'
        )
        if FACTUAL.search(q):
            return ("HEADLINES", False)

        # ── Questions ending with ? — only if they look like they need web info ──
        # Very specific factual questions (not rhetorical or reasoning questions)
        if q.rstrip().endswith('?') and len(q) > 30:
            # Skip if it looks like a reasoning/opinion question
            REASONING_Q = re.compile(r'\b(would you|could you|can you|should i|what do you think|in your opinion|how would you)\b')
            if not REASONING_Q.search(q):
                return ("HEADLINES", False)

        # ── Default: do NOT search ──
        # Only search when there's a clear signal above.
        # Generic messages, conversations, and thinking tasks stay local.
        return ("NO", False)

    def _needs_thinking(query: str) -> bool:
        """
        When thinking toggle is ON, think for all substantial messages.
        Only skip truly trivial messages (greetings, one-word replies).
        The user opted into thinking by turning the toggle on.
        """
        import re
        q = query.lower().strip()

        # ── Skip trivial messages that don't need reasoning ──
        TRIVIAL = re.compile(
            r'^(hi|hello|hey|thanks?|thank you|ok|okay|bye|goodbye|'
            r'yes|no|sure|yep|nope|cool|nice|great|good|lol|haha|'
            r'wow|awesome|perfect|got it|understood|agreed|right)'
            r'[.!?\s]{0,5}$'
        )
        if TRIVIAL.match(q):
            return False

        # Everything else gets thinking when the toggle is ON
        return True

    def generate():
        nonlocal user_input
        _t_generate_start = time.time()

        # ── OCR fallback for non-vision models ────────
        ocr_text = ""
        # Effective model: use vision_model if set and image is attached
        effective_model = (_config.vision_model or _config.model) if image_b64 else _config.model
        if image_b64 and not _is_vision_model(effective_model):
            ocr_text = _extract_text_from_image(image_b64)
            if ocr_text:
                user_input = user_input + "\n\n[Text extracted from attached image:]\n" + ocr_text
            else:
                user_input = user_input + "\n\n[User attached an image, but no text could be extracted from it. Answer based on the text message only.]"

        # ── Get model context window (#6) ─────────────
        model_ctx = _get_model_context_size(_config.model)

        # ── search (auto-infer OR forced when toggle is on) ──
        search_context = ""
        search_results = []
        search_query = user_input  # what we actually search for
        raw_query = user_input.split('\n')[0].strip()[:200]
        # When toggle is on, auto-detect from query. When off, no search.
        if _config.web_search:
            mode, is_news = _search_mode(user_input)
        else:
            mode, is_news = "NO", False
        if mode != "NO":
            # (#2) Rewrite query for better search results
            search_query = _rewrite_search_query(user_input, convo.get("messages", []), ocr_text=ocr_text)
            yield _sse({"type": "search_start", "query": search_query, "original": raw_query, "mode": mode})
            try:
                # (#1) Use general search by default, news only for time-sensitive
                results = _cached_search(search_query, num_results=5, use_news=is_news)
                if results:
                    search_results = results
                    # (#3) Compress results based on model context window
                    search_context = _compress_search_context(results, search_query, model_ctx)
                    # Only open full pages when the query needs deep content
                    if mode == "FETCH":
                        page_context = ""
                        # (#6) Limit fetch size for small models
                        fetch_char_limit = min(3000, int(model_ctx * 4 * 0.10))
                        for r in results[:3]:
                            url = r.get('url', '')
                            if not url:
                                continue
                            try:
                                content = fetch(url)
                                if content and not content.startswith("Error:"):
                                    page_context += (
                                        f"\n--- Full content from: {url} ---\n"
                                        f"{content[:fetch_char_limit]}\n"
                                    )
                            except Exception:
                                pass
                        if page_context:
                            search_context += "\n\nFull page content:\n" + page_context
            except Exception as e:
                yield _sse({"type": "search_error", "error": str(e)})
            yield _sse({"type": "search_done", "results": search_results, "count": len(search_results)})

        # ── decide thinking mode ──────────────────────
        should_think = _config.thinking and _needs_thinking(user_input)

        # Send debug context to frontend
        _convo_msgs = [m for m in convo.get("messages", []) if m.get("role") != "search"]
        _tokens_used = sum(_estimate_tokens(m.get("content", "")) for m in _convo_msgs)
        yield _sse({"type": "debug_context", "model": _config.model, "provider": _config.provider.value,
                     "vision_model_used": effective_model if image_b64 and effective_model != _config.model else None,
                     "context_window": model_ctx, "search_mode": mode, "should_think": should_think,
                     "history_msgs": len(_convo_msgs), "tokens_used": _tokens_used})

        # ── build base system prompt ─────────────────
        system = SYSTEM_PROMPT
        skill_ctx = _get_active_skill_instructions()
        if skill_ctx:
            system += "\n\n" + skill_ctx
        memory_ctx = _build_memory_context(convo)
        if memory_ctx:
            system += "\n\n" + memory_ctx
        # (#4) If no search this turn but there was a previous search, carry topic context
        if not search_context and convo.get("messages"):
            last_topic = _get_last_search_topic(convo)
            if last_topic:
                system += f"\n\n[Previous search context: the conversation was about '{last_topic}'. Use this context for follow-up questions.]"
        if search_context:
            system += (
                "\n\nSEARCH RESULTS — YOU MUST USE THESE:\n"
                "The following are real, current web search results. "
                "Base your answer on these results. Cite the source names.\n"
                + search_context
            )

        convo["messages"].append({"role": "user", "content": user_input})
        user_msg = {"role": "user", "content": user_input}
        if image_b64:
            user_msg["image"] = image_b64
            user_msg["image_mime"] = image_mime
        _config.messages.append(user_msg)

        # ── extract & store memory facts ──────────────
        mem_stored = _process_memory(user_input, convo)
        if mem_stored:
            yield _sse({"type": "memory_stored", "facts": [{"tier": t, "fact": f} for t, f in mem_stored]})

        if search_results:
            convo["messages"].append({
                "role": "search",
                "content": json.dumps(search_results),
                "query": search_query,
                "original_query": raw_query if mode != "NO" else "",
                "mode": mode,
            })

        chat_fn = _get_chat_fn(effective_model)
        full = ""
        is_error = False

        # ── perf counters ─────────────────────────────
        t_ans_start: float = 0.0
        t_first_token: float = 0.0
        ans_tokens: int = 0
        reasoning_tokens: int = 0

        # ── PASS 1: reasoning (if thinking) ──────────
        reasoning_text = ""
        if should_think:
            yield _sse({"type": "thinking_start"})

            think_system = (
                "You are a reasoning engine. Analyze the user's question step by step.\n"
                "Be CONCISE — use short bullet points, not long paragraphs.\n"
                "Cover the key reasoning steps, then STOP. Do not over-explain.\n"
                "Output your reasoning process ONLY — no final answer.\n"
                "Do NOT start with phrases like 'Let me think' or 'I'll reason through this'.\n"
                "Just start reasoning directly. Keep it under 300 words."
            )
            if search_context:
                think_system += (
                    "\n\nYou have web search results available — USE them in your reasoning:\n"
                    + search_context
                )

            think_msgs = [{"role": "system", "content": think_system}]
            # (#6) Trim history to fit model context
            trimmed_history = _trim_messages_to_fit(
                _config.messages[:-1], think_system, model_ctx
            )
            think_msgs.extend(trimmed_history)
            think_msgs.append({"role": "user", "content": user_input})

            # Cap reasoning: max tokens and max time to prevent runaway thinking
            MAX_THINK_TOKENS = 800
            MAX_THINK_SECS = 45
            think_start = time.time()

            try:
                t_think_first = 0
                for chunk in chat_fn(think_msgs):
                    # Skip any accidental <think> tags from native-thinking models
                    clean = chunk.replace("<think>", "").replace("</think>", "")
                    if clean:
                        if not t_think_first:
                            t_think_first = time.time()
                        reasoning_text += clean
                        reasoning_tokens += 1
                        yield _sse({"type": "thinking_token", "text": clean})

                    # Enforce limits
                    if reasoning_tokens >= MAX_THINK_TOKENS:
                        reasoning_text += "\n[Reasoning capped]"
                        break
                    if time.time() - think_start > MAX_THINK_SECS:
                        reasoning_text += "\n[Time limit reached]"
                        break
                think_elapsed = time.time() - think_start
            except Exception as e:
                yield _sse({"type": "thinking_token", "text": f"\n[Reasoning error: {e}]"})

            yield _sse({"type": "thinking_done"})

            # Build answer prompt with reasoning as context
            if reasoning_text.strip():
                system += (
                    f"\n\nYou already reasoned through this problem. Here is your reasoning:\n"
                    f"---\n{reasoning_text}\n---\n"
                    f"Now give a clean, direct answer based on your reasoning above.\n"
                    f"Do NOT repeat your reasoning. Do NOT say 'based on my reasoning'.\n"
                    f"Just answer the question directly."
                )

        # ── PASS 2 (or single pass): answer ──────────
        # (#6) Trim messages to fit context window
        trimmed_msgs = _trim_messages_to_fit(_config.messages, system, model_ctx)
        api_msgs = [{"role": "system", "content": system}]
        api_msgs.extend(trimmed_msgs)

        if not should_think:
            # Single pass — still support native <think> tags for models that have them
            if _config.thinking:
                system += "\n\n" + THINKING_INSTRUCTIONS
                trimmed_msgs = _trim_messages_to_fit(_config.messages, system, model_ctx)
                api_msgs = [{"role": "system", "content": system}]
                api_msgs.extend(trimmed_msgs)

        yield _sse({"type": "response_start"})

        t_ans_start = time.time()
        try:
            gen = chat_fn(api_msgs)
            first = next(gen, None)

            if first and first.startswith("ERROR:CONNECTION"):
                is_error = True
                yield _sse({"type": "error", "text": "Cannot connect to Ollama. Is it running?"})
            elif first and first.startswith("ERROR:"):
                is_error = True
                yield _sse({"type": "error", "text": first[6:]})
            elif first and first.startswith("INSTALL:"):
                yield _sse({"type": "status", "text": f"Installing {first[8:]}..."})
                first = next(gen, None)

            if not is_error:
                # Stream all tokens (first may be None or empty — just continue to gen)
                def _all_chunks():
                    if first:
                        yield first
                    for c in gen:
                        yield c

                if should_think:
                    # Two-pass mode: reasoning already shown, just stream clean tokens
                    for chunk in _all_chunks():
                        if not chunk:
                            continue
                        if not t_first_token:
                            t_first_token = time.time()
                        ans_tokens += 1
                        full += chunk
                        yield _sse({"type": "token", "text": chunk})
                else:
                    # Single pass: parse <think> tags for native-thinking models
                    tbuf = ""
                    in_think = False

                    def process():
                        nonlocal tbuf, in_think, full, ans_tokens, t_first_token, reasoning_tokens, reasoning_text
                        out = []
                        while tbuf:
                            if not in_think:
                                i = tbuf.find('<think>')
                                if i == -1:
                                    safe = max(0, len(tbuf) - 6)
                                    if safe:
                                        tok = tbuf[:safe]; tbuf = tbuf[safe:]
                                        if not t_first_token: t_first_token = time.time()
                                        ans_tokens += 1
                                        full += tok
                                        out.append(_sse({"type": "token", "text": tok}))
                                    break
                                before = tbuf[:i]
                                if before:
                                    if not t_first_token: t_first_token = time.time()
                                    ans_tokens += 1
                                    full += before
                                    out.append(_sse({"type": "token", "text": before}))
                                tbuf = tbuf[i+7:]; in_think = True
                            else:
                                i = tbuf.find('</think>')
                                if i == -1:
                                    safe = max(0, len(tbuf) - 8)
                                    if safe:
                                        tok = tbuf[:safe]; tbuf = tbuf[safe:]
                                        reasoning_tokens += 1
                                        reasoning_text += tok
                                        out.append(_sse({"type": "thinking_token", "text": tok}))
                                    break
                                tok = tbuf[:i]
                                if tok:
                                    reasoning_tokens += 1
                                    reasoning_text += tok
                                    out.append(_sse({"type": "thinking_token", "text": tok}))
                                tbuf = tbuf[i+8:]; in_think = False
                                out.append(_sse({"type": "thinking_done"}))
                        return out

                    for chunk in _all_chunks():
                        if not chunk:
                            continue
                        tbuf += chunk
                        for ev in process(): yield ev
                    if tbuf:
                        if in_think:
                            reasoning_tokens += 1
                            yield _sse({"type": "thinking_token", "text": tbuf})
                            yield _sse({"type": "thinking_done"})
                        else:
                            if not t_first_token: t_first_token = time.time()
                            ans_tokens += 1
                            full += tbuf
                            yield _sse({"type": "token", "text": tbuf})

        except Exception as e:
            is_error = True
            yield _sse({"type": "error", "text": str(e)})

        if full and not is_error:
            asst_msg: dict = {"role": "assistant", "content": full}
            if reasoning_text.strip():
                asst_msg["reasoning"] = reasoning_text
            convo["messages"].append(asst_msg)
            _config.messages.append({"role": "assistant", "content": full})
            _save_chats()

        t_end = time.time()
        total_s = round(t_end - t_ans_start, 2) if t_ans_start else 0
        ttft_ms = round((t_first_token - t_ans_start) * 1000) if t_first_token and t_ans_start else 0
        tps = round(ans_tokens / total_s, 1) if total_s > 0 and ans_tokens > 0 else 0
        stats = {
            "ans_tokens": ans_tokens,
            "reasoning_tokens": reasoning_tokens,
            "tps": tps,
            "ttft_ms": ttft_ms,
            "total_s": total_s,
            "model": _config.model,
            "vision_model_used": effective_model if image_b64 and effective_model != _config.model else None,
            "provider": _config.provider.value if hasattr(_config.provider, 'value') else str(_config.provider),
            "searched": bool(search_results),
        }
        _record_stats(stats)
        yield _sse({"type": "done", "full_response": full, "stats": stats})

        # ── Auto-name conversation after first message ────
        if is_first_message and full and not is_error:
            try:
                name_msgs = [
                    {"role": "system", "content": (
                        "Generate a short, descriptive name (2-5 words) for a chat that starts with this exchange. "
                        "Reply with ONLY the name, nothing else. No quotes, no punctuation at the end."
                    )},
                    {"role": "user", "content": f"User: {user_input[:200]}\nAssistant: {full[:300]}"},
                ]
                title_text = ""
                for chunk in chat_fn(name_msgs):
                    title_text += chunk
                    if len(title_text) > 60:
                        break
                title_text = _clean_title(title_text)[:60]
                if title_text:
                    convo["title"] = title_text
                    _save_chats()
                    yield _sse({"type": "title_update", "title": title_text, "convo_id": convo["id"]})
            except Exception:
                pass

        # ── Auto-memory extraction after every response ───
        if full and not is_error:
            try:
                mem_msgs = [
                    {"role": "system", "content": (
                        "You are a memory extraction agent. Extract ONLY personal facts about the USER.\n\n"
                        "STRICT RULES:\n"
                        "- ONLY extract facts the user reveals about THEMSELVES: their name, age, location, job, "
                        "preferences, projects, tools, hobbies, goals.\n"
                        "- Each fact must start with 'User' (e.g. 'User lives in Seattle').\n"
                        "- Do NOT extract facts about topics being discussed (puzzles, logic problems, code problems, "
                        "news, history, science, hypotheticals, games, riddles).\n"
                        "- Do NOT extract facts about characters in puzzles or scenarios (farmers, norwegians, etc).\n"
                        "- Do NOT extract the content of the conversation itself.\n"
                        "- Do NOT extract facts from the assistant's response — only from what the USER said.\n"
                        "- If there is NOTHING personal about the user, reply with exactly: nothing\n"
                        "- Output ONLY the facts or 'nothing'. No explanations.\n\n"
                        "GOOD examples (extract these):\n"
                        "User's name is Charlie\n"
                        "User is a developer based in Seattle\n"
                        "User is building a browser tool called SURF\n"
                        "User prefers dark mode\n\n"
                        "BAD examples (NEVER extract these):\n"
                        "The farmer crosses the river first with the chicken\n"
                        "The Norwegian lives in the first house\n"
                        "The answer to the puzzle is 42\n"
                        "Python is a programming language\n"
                    )},
                    {"role": "user", "content": f"User said: {user_input[:500]}"},
                ]
                mem_text = ""
                for chunk in chat_fn(mem_msgs):
                    mem_text += chunk
                    if len(mem_text) > 300:
                        break
                mem_text = mem_text.strip()
                # Parse response — "nothing" means skip
                if mem_text and mem_text.lower().strip().rstrip('.') != "nothing":
                    auto_facts = []
                    for line in mem_text.splitlines():
                        line = line.strip().lstrip('-•* ').lstrip('0123456789.)')
                        line = line.strip()
                        if len(line) < 8 or len(line) > 200:
                            continue
                        if line.lower() == "nothing":
                            continue
                        # FILTER: must be about the user, not about topics
                        line_lower = line.lower()
                        if not line_lower.startswith("user"):
                            continue
                        # Reject puzzle/topic leakage
                        TOPIC_NOISE = re.compile(
                            r'\b(farmer|norwegian|puzzle|coin|house #|weighing|riddle|'
                            r'chicken|fox|grain|river|balance|scale|clue|scenario)\b', re.I
                        )
                        if TOPIC_NOISE.search(line):
                            continue
                        line = _normalize_fact(line)
                        if not _is_duplicate_fact(line, _global_memory):
                            entry = {"fact": line, "source": convo.get("id", "?"), "ts": time.time()}
                            _global_memory.append(entry)
                            auto_facts.append(("global", line))
                    if auto_facts:
                        _save_memory()
                        yield _sse({"type": "memory_stored", "facts": [{"tier": t, "fact": f} for t, f in auto_facts]})
            except Exception:
                pass

    return Response(generate(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── helpers ───────────────────────────────────────────────────

def _sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


def _get_chat_fn(model: str = None):
    m = model or _config.model
    if _config.provider == Provider.OLLAMA:
        return lambda msgs: chat_ollama(msgs, m, _config.api_base)
    elif _config.provider == Provider.ANTHROPIC:
        return lambda msgs: chat_anthropic(msgs, m, _config.api_key, _config.thinking)
    elif _config.provider == Provider.OPENAI:
        return lambda msgs: chat_openai(msgs, m, _config.api_key)
    elif _config.provider == Provider.OPENROUTER:
        return lambda msgs: chat_openai(msgs, m, _config.api_key, "https://openrouter.ai/api/v1")
    else:
        return lambda msgs: chat_openai(msgs, m, _config.api_key, _config.api_base)


# ════════════════════════════════════════════════════════════════
# TERMINAL STARTUP BANNER
# ════════════════════════════════════════════════════════════════

def _enable_ansi() -> bool:
    """Try to enable ANSI/VT100 escape processing on Windows."""
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
        handle = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        k32.GetConsoleMode(handle, ctypes.byref(mode))
        k32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return True
    except Exception:
        return False


def _print_banner(port: int):
    """Professional startup banner for the terminal."""
    color = _enable_ansi()
    prov = _config.provider.value
    model = _config.model
    W = 42  # inner width between borders

    def _pad(text: str) -> str:
        return text.ljust(W)

    header = [
        "",
        "  SURF  Web UI",
        "  Search · Understand · Reason · Fast",
        "",
    ]

    details = [
        "",
        f"  Local     http://localhost:{port}",
        f"  Network   http://127.0.0.1:{port}",
        "",
        f"  Provider  {prov}",
        f"  Model     {model}",
        "",
    ]

    if color:
        C, R, B, D, Y = "\033[96m", "\033[0m", "\033[1m", "\033[2m", "\033[33m"
        print()
        print(f"  {C}╭{'─' * W}╮{R}")
        for ln in header:
            d = _pad(ln)
            d = d.replace("SURF", f"{B}{C}SURF{R}", 1)
            d = d.replace("Search · Understand · Reason · Fast", f"{D}Search · Understand · Reason · Fast{R}", 1)
            print(f"  {C}│{R}{d}{C}│{R}")
        print(f"  {C}├{'─' * W}┤{R}")
        for ln in details:
            padded = _pad(ln)
            display = padded
            display = display.replace(f"http://localhost:{port}", f"{B}http://localhost:{port}{R}", 1)
            display = display.replace(f"http://127.0.0.1:{port}", f"{D}http://127.0.0.1:{port}{R}", 1)
            if f"Provider  {prov}" in padded:
                display = display.replace(f"Provider  {prov}", f"{D}Provider{R}  {Y}{prov}{R}", 1)
            if f"Model     {model}" in padded:
                display = display.replace(f"Model     {model}", f"{D}Model{R}     {Y}{model}{R}", 1)
            display = display.replace("Local     ", f"{D}Local{R}     ", 1)
            display = display.replace("Network   ", f"{D}Network{R}   ", 1)
            print(f"  {C}│{R}{display}{C}│{R}")
        print(f"  {C}╰{'─' * W}╯{R}")
        print()
        print(f"  {D}Press Ctrl+C to stop the server{R}")
        print()
    else:
        print()
        print(f"  +{'-' * W}+")
        for ln in header:
            print(f"  |{_pad(ln)}|")
        print(f"  +{'-' * W}+")
        for ln in details:
            print(f"  |{_pad(ln)}|")
        print(f"  +{'-' * W}+")
        print()
        print("  Press Ctrl+C to stop the server")
        print()


# ════════════════════════════════════════════════════════════════
# LAUNCHER
# ════════════════════════════════════════════════════════════════

def launch(config: Config = None, port: int = 7777, open_browser: bool = True):
    """Start the web UI server."""
    global _config
    if config:
        _config = config

    # Load saved chats, or create a fresh one
    _load_chats()
    _load_memory()
    _load_stats()
    _load_keys()
    # Auto-load saved key for the current provider
    if not _config.api_key:
        _config.api_key = _get_key_for_provider(_config.provider.value)
    if not _conversations:
        _new_conversation()

    # Suppress Flask/werkzeug startup banner and request logs
    import logging
    import flask.cli
    flask.cli.show_server_banner = lambda *args, **kwargs: None
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _print_banner(port)

    # Auto-warmup the starting model
    _trigger_warmup()

    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(f"http://localhost:{port}")).start()

    app.run(host="127.0.0.1", port=port, debug=False, threaded=True)


if __name__ == "__main__":
    launch()
