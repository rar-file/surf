"""
SURF Browser Agent â€” Autonomous web browsing via Playwright + Vision AI.
The agent takes screenshots, sends them to a vision model, and executes
actions (click, scroll, type, navigate) based on the model's decisions.
Non-vision models get structured page text with element coordinates instead.
"""

import base64
import json
import os
import time
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Generator, Callable, Optional


# â”€â”€ Action types the AI can request â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@dataclass
class AgentAction:
    action: str          # navigate, click, type, scroll, done, wait
    selector: str = ""   # CSS selector or description
    value: str = ""      # URL for navigate, text for type, direction for scroll
    x: int = 0           # pixel coordinates for click
    y: int = 0           # pixel coordinates for click
    explanation: str = "" # what the AI thinks it's doing


# Shared JSON enforcement block â€” appended to both prompts
_JSON_FORMAT_BLOCK = """
Your ENTIRE response must be a single JSON object. Nothing else. No markdown. No bullets. No explanation.

Examples of CORRECT responses:
{{"action": "click", "x": 640, "y": 402, "explanation": "Clicking Accept all cookies button"}}
{{"action": "type", "value": "latest news today", "explanation": "Typing search query"}}
{{"action": "navigate", "value": "https://en.wikipedia.org", "explanation": "Going to Wikipedia"}}
{{"action": "scroll", "value": "down", "explanation": "Scrolling to see more results"}}
{{"action": "done", "value": "The capital of France is Paris.", "explanation": "Found the answer"}}

Examples of WRONG responses (DO NOT do these):
- **Step 2: Accept Cookies** Click the button   â† WRONG, not JSON
- ```json {{"action": "click"}} ```                â† WRONG, has code fences
- I'll click the accept button                    â† WRONG, natural language
"""

AGENT_SYSTEM_PROMPT_VISION = """You are a web browser automation bot. You see a screenshot and page elements. Decide the next action.

The screenshot is {width}x{height} pixels. Use precise x,y coordinates for clicks.
""" + _JSON_FORMAT_BLOCK + """
RULES:
- If you see search results, READ them and extract the answer. Use "done" once you have it.
- To search: click the search input field first, then type with the "type" action.
- NEVER click random links (About, Privacy, Terms, etc.) unless the task requires it.
- Click input fields BEFORE typing.
- Type the ACTUAL query, not placeholder text.
- NEVER repeat the same action. Try something different if it didn't work.
- Dismiss cookie banners first.
- Use "done" as soon as you have the answer.
- Step {step}/{max_steps}.

Task: {task}"""

AGENT_SYSTEM_PROMPT_TEXT = """You are a web browser automation bot. You receive page elements with (x,y) coordinates. Decide the next action.
""" + _JSON_FORMAT_BLOCK + """
RULES:
- If you see search results, READ them and extract the answer. Use "done" once you have it.
- To search: click the search input field first, then type with the "type" action.
- NEVER click random links (About, Privacy, Terms, etc.) unless the task requires it.
- Use the EXACT (x,y) coordinates shown next to each element.
- Click input fields BEFORE typing.
- Type the ACTUAL query, not placeholder text.
- NEVER repeat the same action. Try something different if it didn't work.
- Dismiss cookie banners first.
- Use "done" as soon as you have the answer.
- Step {step}/{max_steps}.

Task: {task}"""


class BrowserAgent:
    """Controls a Playwright browser and coordinates with a vision AI model."""

    def __init__(self):
        self._playwright = None
        self._browser = None
        self._page = None
        self._width = 1280
        self._height = 900

    def start(self) -> bool:
        """Launch the browser. Returns True on success."""
        try:
            from playwright.sync_api import sync_playwright
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-gpu", "--disable-blink-features=AutomationControlled"]
            )
            self._page = self._browser.new_page(
                viewport={"width": self._width, "height": self._height},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
            self._page.set_default_timeout(15000)
            return True
        except Exception:
            return False

    def stop(self):
        """Close browser and clean up."""
        try:
            if self._page:
                self._page.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        except Exception:
            pass
        self._page = None
        self._browser = None
        self._playwright = None

    def screenshot_b64(self) -> str:
        """Take a JPEG screenshot and return as base64 string (smaller than PNG)."""
        if not self._page:
            return ""
        try:
            # Use JPEG for smaller payloads â€” critical for SSE streaming
            raw = self._page.screenshot(type="jpeg", quality=65, full_page=False)
            return base64.b64encode(raw).decode("utf-8")
        except Exception:
            return ""

    def navigate(self, url: str) -> bool:
        """Navigate to a URL."""
        if not self._page:
            return False
        try:
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            self._page.goto(url, wait_until="domcontentloaded", timeout=20000)
            self._page.wait_for_timeout(500)  # let page settle
            return True
        except Exception:
            return False

    def click(self, x: int, y: int) -> bool:
        """Click at pixel coordinates."""
        if not self._page:
            return False
        try:
            self._page.mouse.click(x, y)
            self._page.wait_for_timeout(400)
            return True
        except Exception:
            return False

    def type_text(self, text: str) -> bool:
        """Type text into the currently focused element."""
        if not self._page:
            return False
        try:
            self._page.keyboard.type(text, delay=15)
            return True
        except Exception:
            return False

    def press_key(self, key: str) -> bool:
        """Press a keyboard key (Enter, Tab, etc)."""
        if not self._page:
            return False
        try:
            self._page.keyboard.press(key)
            self._page.wait_for_timeout(300)
            return True
        except Exception:
            return False

    def scroll(self, direction: str = "down", amount: int = 400) -> bool:
        """Scroll the page up or down."""
        if not self._page:
            return False
        try:
            delta = amount if direction == "down" else -amount
            self._page.mouse.wheel(0, delta)
            self._page.wait_for_timeout(300)
            return True
        except Exception:
            return False

    def dismiss_consent(self) -> bool:
        """Auto-dismiss cookie/privacy consent banners (Google, generic).
        Returns True if a consent element was found and clicked."""
        if not self._page:
            return False
        # Common consent button texts across sites
        consent_texts = [
            "Accept all", "I agree", "Accept All", "Agree",
            "Accept cookies", "Accept all cookies",
            "Alles accepteren", "Tout accepter", "Alle akzeptieren",
            "Got it", "OK", "Consent",
        ]
        for text in consent_texts:
            try:
                btn = self._page.get_by_role("button", name=text, exact=False)
                if btn.count() > 0:
                    btn.first.click(timeout=3000)
                    self._page.wait_for_timeout(1500)
                    return True
            except Exception:
                continue
        # Also try common link-style consent (Google sometimes uses a div)
        try:
            for sel in ["button#L2AGLb", "[aria-label='Accept all']", "form[action*='consent'] button"]:
                el = self._page.locator(sel)
                if el.count() > 0:
                    el.first.click(timeout=3000)
                    self._page.wait_for_timeout(1500)
                    return True
        except Exception:
            pass
        return False

    def get_page_info(self) -> dict:
        """Get basic page info."""
        if not self._page:
            return {}
        try:
            return {
                "url": self._page.url,
                "title": self._page.title(),
            }
        except Exception:
            return {}

    def get_page_text(self, max_chars: int = 6000) -> str:
        """Extract structured page elements with coordinates for non-vision models."""
        if not self._page:
            return ""
        try:
            text = self._page.evaluate("""() => {
                const seen = new Set();
                const items = [];
                
                // Helper: deduplicate by rounding coords to 10px grid
                function key(tag, cx, cy) { return `${tag}_${Math.round(cx/10)}_${Math.round(cy/10)}`; }
                
                // 1. Interactive elements first (most important)
                const interactive = document.querySelectorAll(
                    'a[href], button, input, textarea, select, [role="button"], [role="link"], [onclick], [tabindex]'
                );
                for (const el of interactive) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 5 || rect.height < 5) continue;
                    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
                    const tag = el.tagName.toLowerCase();
                    const cx = Math.round(rect.x + rect.width / 2);
                    const cy = Math.round(rect.y + rect.height / 2);
                    const k = key(tag, cx, cy);
                    if (seen.has(k)) continue;
                    seen.add(k);
                    
                    const label = (el.innerText || el.textContent || '').trim().split('\\n')[0].slice(0, 80);
                    const ariaLabel = el.getAttribute('aria-label') || '';
                    const title = el.getAttribute('title') || '';
                    const name = label || ariaLabel || title || el.name || '';
                    if (!name && tag !== 'input' && tag !== 'textarea') continue;
                    
                    if (tag === 'input' || tag === 'textarea') {
                        const t = el.type || 'text';
                        const ph = el.placeholder || '';
                        const val = el.value || '';
                        items.push(`[INPUT type=${t} placeholder="${ph}" value="${val}" at (${cx},${cy})]`);
                    } else if (tag === 'select') {
                        const opt = el.options ? el.options[el.selectedIndex]?.text || '' : '';
                        items.push(`[SELECT "${name}" selected="${opt}" at (${cx},${cy})]`);
                    } else if (tag === 'a') {
                        items.push(`[LINK "${name}" at (${cx},${cy})]`);
                    } else {
                        items.push(`[BUTTON "${name}" at (${cx},${cy})]`);
                    }
                }
                
                // 2. Content elements (headings, paragraphs, list items)
                const content = document.querySelectorAll('h1, h2, h3, h4, h5, p, li, td, th, figcaption');
                for (const el of content) {
                    const rect = el.getBoundingClientRect();
                    if (rect.width < 5 || rect.height < 5) continue;
                    if (rect.bottom < 0 || rect.top > window.innerHeight) continue;
                    const tag = el.tagName.toLowerCase();
                    const text = (el.innerText || '').trim().split('\\n')[0].slice(0, 150);
                    if (!text || text.length < 2) continue;
                    const cx = Math.round(rect.x + rect.width / 2);
                    const cy = Math.round(rect.y + rect.height / 2);
                    const k = key(tag, cx, cy);
                    if (seen.has(k)) continue;
                    seen.add(k);
                    
                    if (/^h[1-5]$/.test(tag)) items.push(`[HEADING] ${text}`);
                    else items.push(`[TEXT] ${text}`);
                }
                
                return items.join('\\n');
            }""")
            return text[:max_chars] if text else "(empty page â€” no visible elements found)"
        except Exception:
            try:
                fallback = self._page.inner_text("body")
                return fallback[:max_chars] if fallback else "(empty page)"
            except Exception:
                return "(could not read page)"

    @property
    def is_running(self) -> bool:
        return self._page is not None


def parse_agent_action(text: str) -> Optional[AgentAction]:
    """Parse an action from the AI model's response.
    Tries JSON first, falls back to natural language extraction."""
    text = text.strip()

    # Strip markdown code fences if present
    fence_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', text, re.DOTALL)
    if fence_match:
        text = fence_match.group(1)

    # Try to find JSON in the response (allow nested braces too)
    json_match = re.search(r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}', text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group())
            action_raw = data.get("action", "")
            # Handle nested action: {"action": {"type": "click", "value": "..."}}
            if isinstance(action_raw, dict):
                data = {**data, **action_raw}
                action_raw = action_raw.get("type", action_raw.get("action", ""))
            action = str(action_raw).lower().strip()
            if action in ("navigate", "click", "type", "scroll", "done", "wait"):
                return AgentAction(
                    action=action,
                    selector=str(data.get("selector", data.get("xpath", ""))),
                    value=str(data.get("value", data.get("url", data.get("text", "")))),
                    x=int(data.get("x", 0)),
                    y=int(data.get("y", 0)),
                    explanation=str(data.get("explanation", "")),
                )
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # â”€â”€ Natural language fallback â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    # If the model wrote prose instead of JSON, try to extract intent
    lower = text.lower()

    # Detect "click" intent with coordinates
    click_match = re.search(r'click.*?\(\s*(\d+)\s*,\s*(\d+)\s*\)', lower)
    if not click_match:
        click_match = re.search(r'click.*?at\s*\(?(\d+)\s*,\s*(\d+)\s*\)?', lower)
    if not click_match:
        click_match = re.search(r'x\s*[:=]\s*(\d+).*?y\s*[:=]\s*(\d+)', lower)
    if click_match:
        return AgentAction(
            action="click",
            x=int(click_match.group(1)),
            y=int(click_match.group(2)),
            explanation=text[:200],
        )

    # Detect "click" + button name â€” match against known page elements later
    click_name = re.search(r'click\s+(?:the\s+|on\s+)?["\']?([^"\'.]+?)["\']?\s*(?:button|link|tab)', lower)
    if click_name:
        return AgentAction(
            action="click_by_name",
            value=click_name.group(1).strip(),
            explanation=text[:200],
        )

    # Detect navigate intent
    nav_match = re.search(r'(?:navigate|go|open|visit).*?(https?://\S+)', lower)
    if nav_match:
        return AgentAction(action="navigate", value=nav_match.group(1), explanation=text[:200])

    # Detect type intent
    type_match = re.search(r'(?:type|enter|input|search for)\s+["\'](.+?)["\']', lower)
    if type_match:
        return AgentAction(action="type", value=type_match.group(1), explanation=text[:200])

    # Detect scroll intent
    if re.search(r'scroll\s*(down|up)', lower):
        direction = "up" if "up" in lower else "down"
        return AgentAction(action="scroll", value=direction, explanation=text[:200])

    # Detect done/complete intent
    if re.search(r'(?:task\s+)?(?:complete|done|finish|found the answer)', lower):
        return AgentAction(action="done", value=text[:500], explanation="Task completed")

    # Detect accept/dismiss cookie banners specifically
    if re.search(r'(?:accept|dismiss|agree|consent).*(?:cookie|all|banner)', lower):
        return AgentAction(
            action="click_by_name",
            value="accept all",
            explanation=text[:200],
        )

    return None


def _resolve_click_by_name(name: str, page_text: str) -> Optional[AgentAction]:
    """Resolve a click_by_name action to actual coordinates by fuzzy-matching against page elements."""
    if not name or not page_text:
        return None
    name_lower = name.lower().strip()
    # Parse elements like: [BUTTON "Accept all" at (640,402)] or [LINK "Gmail" at (50,23)]
    elements = re.findall(r'\[(BUTTON|LINK|INPUT|SELECT)\s+"([^"]+)"\s+at\s+\((\d+),(\d+)\)\]', page_text)
    
    best_match = None
    best_score = 0
    for tag, label, x, y in elements:
        label_lower = label.lower()
        # Exact match
        if name_lower == label_lower:
            return AgentAction(action="click", x=int(x), y=int(y), explanation=f"Clicking '{label}'")
        # Containment match
        if name_lower in label_lower or label_lower in name_lower:
            score = len(name_lower) / max(len(label_lower), 1)
            if score > best_score:
                best_score = score
                best_match = (label, int(x), int(y))
        # Word overlap
        name_words = set(name_lower.split())
        label_words = set(label_lower.split())
        overlap = len(name_words & label_words)
        if overlap > 0:
            score = overlap / max(len(name_words), 1) * 0.8
            if score > best_score:
                best_score = score
                best_match = (label, int(x), int(y))
    
    if best_match and best_score > 0.3:
        return AgentAction(action="click", x=best_match[1], y=best_match[2],
                          explanation=f"Clicking '{best_match[0]}'")
    return None


def _save_screenshot(screenshot_b64: str, step: int, task: str) -> str:
    """Save a screenshot to disk. Returns the file path."""
    save_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent_screenshots")
    os.makedirs(save_dir, exist_ok=True)
    # Sanitize task for filename
    safe_task = re.sub(r'[^a-zA-Z0-9_-]', '_', task[:30]).strip('_')
    ts = time.strftime("%Y%m%d_%H%M%S")
    filename = f"{ts}_{safe_task}_step{step:02d}.jpg"
    filepath = os.path.join(save_dir, filename)
    try:
        with open(filepath, "wb") as f:
            f.write(base64.b64decode(screenshot_b64))
        return filepath
    except Exception:
        return ""


def _extract_search_query(task: str) -> str:
    """If the task looks like a search request, extract the query.
    Returns the search query string, or '' if this isn't a search task."""
    lower = task.lower().strip()
    # Explicit search patterns
    patterns = [
        r'(?:search\s+(?:for|about|up)?|google|look\s+up|find\s+(?:out|me)?|what\s+is|what\s+are|who\s+is|who\s+are|when\s+(?:is|was|did)|where\s+is|how\s+(?:to|many|much|do|does|did|is)|why\s+(?:is|do|does|did|are))\s+["\']?(.+?)["\']?$',
    ]
    for pat in patterns:
        m = re.search(pat, lower, re.IGNORECASE)
        if m:
            return m.group(1).strip().rstrip('?.')
    # If no URL and looks like a question/query (not a specific site instruction)
    if not re.search(r'https?://|go to|open|navigate|visit|click', lower):
        # Short enough to be a search query (not a complex multi-step instruction)
        if len(task.split()) <= 12:
            return task.strip().rstrip('?.')
    return ''


def run_agent_loop(
    task: str,
    chat_fn: Callable,
    model: str,
    max_steps: int = 15,
    start_url: str = "",
    is_vision: bool = True,
) -> Generator[dict, None, None]:
    """
    Run the agent loop. Yields SSE-compatible event dicts.
    Uses a COMPACT action log (not full conversation history) so the model's
    context window doesn't overflow — critical for small local models.
    """
    agent = BrowserAgent()

    yield {"type": "agent_start", "task": task, "max_steps": max_steps}

    if not agent.start():
        yield {"type": "agent_error", "step": 0, "error": "Failed to launch browser. Is Playwright installed? Run: playwright install chromium"}
        return

    try:
        # Smart start: detect search intent and jump straight to results
        search_query = _extract_search_query(task)
        if start_url:
            url = start_url
        elif search_query:
            url = "https://www.google.com/search?q=" + urllib.parse.quote_plus(search_query)

        else:
            url = "https://www.google.com"

        if not agent.navigate(url):
            yield {"type": "agent_error", "step": 0, "error": f"Failed to navigate to {url}"}
            return

        # Auto-dismiss consent/cookie banners before the agent even starts
        agent.dismiss_consent()

        # Compact action log — just one-liner summaries, NOT full page text
        action_log = []
        recent_actions = []
        consecutive_failures = 0

        for step in range(1, max_steps + 1):
            # 1. Take screenshot
            screenshot = agent.screenshot_b64()
            if not screenshot:
                yield {"type": "agent_error", "step": step, "error": "Failed to capture screenshot"}
                break

            page_info = agent.get_page_info()
            _save_screenshot(screenshot, step, task)

            yield {
                "type": "agent_screenshot",
                "image_b64": screenshot,
                "step": step,
                "url": page_info.get("url", ""),
                "title": page_info.get("title", ""),
            }

            # 2. Get page elements (capped small for context budget)
            page_text = agent.get_page_text(max_chars=2000)

            # 3. Build compact action log summary (last 5 only)
            log_summary = ""
            if action_log:
                log_summary = "Actions so far:\n" + "\n".join(action_log[-5:]) + "\n\n"

            # 4. Stuck detection
            stuck_warning = ""
            if len(recent_actions) >= 3 and all(a == recent_actions[-1] for a in recent_actions[-3:]):
                stuck_warning = "WARNING: Same action repeated 3x. Do something DIFFERENT.\n\n"

            # 5. Build messages — ALWAYS just system + ONE user message (no history accumulation)
            if is_vision:
                system = AGENT_SYSTEM_PROMPT_VISION.format(
                    width=agent._width, height=agent._height,
                    task=task, step=step, max_steps=max_steps
                )
                user_msg = f"{stuck_warning}{log_summary}URL: {page_info.get('url', '')}\nTitle: {page_info.get('title', '')}\n\nKey page elements:\n{page_text[:1500]}\n\nRespond with ONE JSON action."
                msgs = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg, "image": screenshot, "image_mime": "image/jpeg"},
                ]
            else:
                system = AGENT_SYSTEM_PROMPT_TEXT.format(
                    task=task, step=step, max_steps=max_steps
                )
                user_msg = f"{stuck_warning}{log_summary}URL: {page_info.get('url', '')}\nTitle: {page_info.get('title', '')}\n\nPage elements:\n{page_text}\n\nRespond with ONE JSON action."
                msgs = [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_msg},
                ]

            # 6. Call the model — STREAM thinking live to frontend
            response_text = ""
            yield {"type": "agent_thinking_start", "step": step}
            try:
                for chunk in chat_fn(msgs):
                    if chunk and chunk.startswith("ERROR:"):
                        yield {"type": "agent_error", "step": step, "error": chunk}
                        break
                    response_text += chunk
                    # Stream thinking chunks live
                    if chunk:
                        yield {"type": "agent_thinking_delta", "step": step, "delta": chunk}
                    if len(response_text) > 800:
                        break
            except Exception as e:
                yield {"type": "agent_error", "step": step, "error": f"Model error: {e}"}
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    yield {"type": "agent_done", "result": "Agent stopped — too many model errors.", "steps_taken": step}
                    return
                continue

            if not response_text.strip():
                yield {"type": "agent_error", "step": step, "error": "Empty response from model"}
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    yield {"type": "agent_done", "result": "Agent stopped — model returning empty responses.", "steps_taken": step}
                    return
                continue

            # 7. Detect gibberish — if response is mostly non-meaningful chars, bail
            clean = response_text.strip()
            alpha_count = sum(1 for c in clean if c.isalnum() or c in '{}":,._-/ ')
            if len(clean) > 30 and alpha_count / len(clean) < 0.5:

                yield {"type": "agent_error", "step": step, "error": "Model returned garbled text — retrying"}
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    yield {"type": "agent_done", "result": "Agent stopped — model producing garbled output. Try a larger or different model.", "steps_taken": step}
                    return
                continue

            yield {"type": "agent_thinking", "step": step, "text": clean[:300]}

            # 8. Parse action
            action = parse_agent_action(response_text)

            if action and action.action == "click_by_name":
                resolved = _resolve_click_by_name(action.value, page_text)
                action = resolved  # None if not found

            # Fix clicks at (0,0) — model gave a name/URL instead of coords
            if action and action.action == "click" and action.x == 0 and action.y == 0:
                # If value looks like a URL, rewrite as navigate
                if action.value and re.match(r'https?://', action.value):
                    action = AgentAction(action="navigate", value=action.value, explanation=action.explanation)
                # If value or selector has text, try to resolve by name
                elif action.value or action.selector:
                    name = action.value or action.selector
                    # Strip HTML/CSS selectors — if it looks like a selector, extract text
                    clean_name = re.sub(r'[#.\[\]@:()=\'">/{}]', ' ', name).strip()
                    clean_name = re.sub(r'\s+', ' ', clean_name)
                    if clean_name and len(clean_name) > 1:
                        resolved = _resolve_click_by_name(clean_name, page_text)
                        if resolved:
                            action = resolved
                        else:
                            if agent.dismiss_consent():
                                action_log.append(f"Step {step}: Auto-dismissed consent banner")
                                continue
                            action = None  # will trigger retry
                    else:
                        action = None

            if not action:
                # ONE simple retry — minimal context, no history stacking
                retry_msgs = [
                    {"role": "system", "content": "Respond with ONLY a JSON object. Nothing else."},
                    {"role": "user", "content": f'Page elements:\n{page_text[:800]}\n\nTask: {task}\n\nExample: {{"action": "click", "x": 640, "y": 400, "explanation": "Clicking button"}}'},
                ]
                retry_text = ""
                try:
                    for chunk in chat_fn(retry_msgs):
                        if chunk and chunk.startswith("ERROR:"):
                            break
                        retry_text += chunk
                        if len(retry_text) > 500:
                            break
                except Exception:
                    pass

                if retry_text.strip():
                    action = parse_agent_action(retry_text)
                    if action and action.action == "click_by_name":
                        resolved = _resolve_click_by_name(action.value, page_text)
                        action = resolved
                    if action:
                        yield {"type": "agent_thinking", "step": step, "text": f"(retry) {retry_text.strip()[:200]}"}

            if not action:
                yield {"type": "agent_error", "step": step, "error": "Could not parse action"}
                action_log.append(f"Step {step}: FAILED — could not parse response")
                consecutive_failures += 1
                if consecutive_failures >= 3:
                    yield {"type": "agent_done", "result": "Agent stopped — model can't produce valid actions. Try a different or larger model.", "steps_taken": step}
                    return
                continue

            # Success — reset failure counter
            consecutive_failures = 0

            # Add COMPACT entry to action log
            if action.action == "click":
                action_log.append(f"Step {step}: Clicked ({action.x},{action.y}) — {action.explanation[:50]}")
            elif action.action == "type":
                action_log.append(f"Step {step}: Typed \"{action.value[:30]}\"")
            elif action.action == "navigate":
                action_log.append(f"Step {step}: Navigated to {action.value[:50]}")
            elif action.action == "scroll":
                action_log.append(f"Step {step}: Scrolled {action.value}")
            else:
                action_log.append(f"Step {step}: {action.action}")

            recent_actions.append(f"{action.action}_{action.x}_{action.y}_{action.value}")

            yield {
                "type": "agent_action", "step": step,
                "action": action.action, "explanation": action.explanation,
                "value": action.value, "x": action.x, "y": action.y,
            }

            # 9. Execute action
            if action.action == "done":
                yield {"type": "agent_done", "result": action.value, "steps_taken": step}
                return
            elif action.action == "navigate":
                if action.value:
                    agent.navigate(action.value)
                    agent.dismiss_consent()  # auto-handle consent on new pages
            elif action.action == "click":
                if action.x == 0 and action.y == 0:
                    action_log.append(f"Step {step}: Skipped click(0,0) — bad coords")
                    continue
                agent.click(action.x, action.y)
                agent.dismiss_consent()
            elif action.action == "type":
                if action.value:
                    agent.type_text(action.value)
                    agent.press_key("Enter")
            elif action.action == "scroll":
                direction = action.value if action.value in ("up", "down") else "down"
                agent.scroll(direction)
            elif action.action == "wait":
                time.sleep(2)

            # Post-action screenshot (fast)
            time.sleep(0.3)
            post_shot = agent.screenshot_b64()
            if post_shot:
                post_info = agent.get_page_info()
                _save_screenshot(post_shot, step, task + "_post")
                yield {
                    "type": "agent_screenshot",
                    "image_b64": post_shot,
                    "step": step,
                    "url": post_info.get("url", ""),
                    "title": post_info.get("title", ""),
                    "save": True,
                }

        yield {"type": "agent_done", "result": "Reached maximum steps without completing the task.", "steps_taken": max_steps}

    finally:
        agent.stop()


