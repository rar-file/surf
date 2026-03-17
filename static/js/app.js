/* ═══════════════════════════════════════════════════════════════
   SURF — Application Logic
   Search · Understand · Reason · Fast
   ═══════════════════════════════════════════════════════════════ */

/* ── DOM Helpers ──────────────────────────────────────────── */
const $ = s => document.querySelector(s);
const $$ = s => document.querySelectorAll(s);

/* ── Markdown Config ──────────────────────────────────────── */
marked.setOptions({
  highlight: (code, lang) => {
    if (lang && hljs.getLanguage(lang)) return hljs.highlight(code, { language: lang }).value;
    return hljs.highlightAuto(code).value;
  },
  breaks: true,
  gfm: true,
});

/* ── State ────────────────────────────────────────────────── */
let state = {};
let streaming = false;

/* ── Code Theme ───────────────────────────────────────────── */
const CODE_THEMES = {
  'github-dark-dimmed': 'GitHub Dark Dimmed',
  'dracula':            'Dracula',
  'monokai':            'Monokai',
  'nord':               'Nord',
  'atom-one-dark':      'Atom One Dark',
  'tokyo-night-dark':   'Tokyo Night',
  'base16/solarized-dark': 'Solarized Dark',
  'a11y-dark':          'A11y Dark',
  'github-dark':        'GitHub Dark',
};

function getCodeTheme() { return localStorage.getItem('codeTheme') || 'github-dark-dimmed'; }
function setCodeTheme(theme) {
  localStorage.setItem('codeTheme', theme);
  const link = document.getElementById('hljsThemeLink');
  if (link) link.href = `https://cdn.jsdelivr.net/npm/highlight.js@11/styles/${theme}.min.css`;
  // Re-highlight all existing code blocks
  document.querySelectorAll('.msg-body pre code').forEach(el => {
    hljs.highlightElement(el);
  });
}

/* ═══════════════════════════════════════════════════════════════
   INITIALIZATION & SYNC
   ═══════════════════════════════════════════════════════════════ */

async function init() {
  setCodeTheme(getCodeTheme());
  const r = await fetch('/api/state');
  state = await r.json();
  syncUI();
  loadModels();
  renderConversation();
  // Populate theme dropdown
  const sel = document.getElementById('selCodeTheme');
  if (sel) {
    sel.innerHTML = Object.entries(CODE_THEMES).map(([k,v]) =>
      `<option value="${k}">${v}</option>`).join('');
    sel.value = getCodeTheme();
  }
}

function syncUI() {
  $('#selProvider').value = state.provider;
  $('#inpModel').value = state.model;
  syncToggle('tgSearch', state.web_search);
  syncToggle('tgThink', state.thinking);
  syncToggle('tgStream', state.streaming);
  syncToggle('tgAgent', state.agent_mode);
  syncToggle('tgStats', localStorage.getItem('showStats') === '1');
  updateMemoryCounts();

  // Sync model dropdown value
  const selModel = document.getElementById('selModel');
  if (selModel && state.model) {
    selModel.value = state.model;
    if (selModel.value !== state.model) selModel.value = '__custom';
  }

  // Sync vision model dropdown
  const selVision = document.getElementById('selVisionModel');
  if (selVision) selVision.value = state.vision_model || '';

  const needsKey = ['anthropic', 'openai', 'openrouter', 'custom'].includes(state.provider);
  $('#apiKeyWrap').style.display = needsKey ? '' : 'none';
  const provLabel = {anthropic:'Anthropic',openai:'OpenAI',openrouter:'OpenRouter',custom:'Custom'}[state.provider]||state.provider;
  $('#inpKey').placeholder = `${provLabel} API key...`;
  $('#keyBadge').textContent = state.api_key_set ? `${provLabel} key set` : 'not set';
  $('#keyBadge').className = 'key-badge ' + (state.api_key_set ? 'set' : 'unset');

  $('#modelBadge').textContent = (state.provider && state.model) ? `${state.provider} / ${state.model}` : '';

  const convo = state.conversations?.find(c => c.id === state.active_id);
  $('#topTitle').textContent = convo ? convo.title : 'New chat';
  $('#topPills').innerHTML = `
    <span class="tp ${state.web_search ? 'on' : 'off'}">Search</span>
    <span class="tp ${state.thinking ? 'on' : 'off'}">Think</span>
    <span class="tp ${state.streaming ? 'on' : 'off'}">Stream</span>
    <span class="tp ${state.agent_mode ? 'on' : 'off'}">Agent</span>
    <span class="tp ${localStorage.getItem('showStats') === '1' ? 'on' : 'off'}">Stats</span>
  `;
  renderHistory();
}

function syncToggle(id, val) {
  const el = document.getElementById(id);
  if (val) el.classList.add('on'); else el.classList.remove('on');
}


/* ═══════════════════════════════════════════════════════════════
   CONVERSATION HISTORY
   ═══════════════════════════════════════════════════════════════ */

function renderHistory() {
  const list = $('#historyList');
  const convos = state.conversations || [];
  list.innerHTML = '<div class="sb-history-label">Conversations</div>' +
    (convos.length === 0
      ? '<div style="padding:7px 10px;font-size:0.75rem;color:var(--text-4)">No chats yet</div>'
      : convos.map(c => `
          <div class="history-item ${c.id === state.active_id ? 'active' : ''}" data-id="${c.id}" onclick="switchConvo('${c.id}')">
            <span class="h-icon">&#9679;</span>
            <span class="h-title">${esc(c.title)}</span>
            <span class="h-delete" onclick="event.stopPropagation();deleteConvo('${c.id}')" title="Delete">&times;</span>
          </div>
        `).join(''));
}

function renderConversation() {
  const scroll = $('#chatScroll');
  const msgs = state.messages || [];
  if (msgs.length === 0) { scroll.innerHTML = emptyHTML(); bindPromptCards(); return; }
  scroll.innerHTML = '';
  for (const m of msgs) {
    if (m.role === 'user') { addMsg('user', esc(m.content)); }
    else if (m.role === 'assistant') {
      if (m.reasoning) addFinalizedThinkingCard(m.reasoning);
      // Agent result with screenshots — render special card
      if (m.agent_screenshots && m.agent_screenshots.length > 0) {
        _renderAgentHistoryCard(scroll, m);
      } else {
        const body = addMsg('ai', '');
        body.innerHTML = renderMD(m.content);
        addCopyBtns(body);
        postProcessMediaCards(body);
      }
    }
    else if (m.role === 'search') {
      try { const r = JSON.parse(m.content); if (r.length) addSearchCard(r); } catch(e) {}
    }
  }
  scrollBottom();
}

function _renderAgentHistoryCard(scroll, msg) {
  const card = document.createElement('div');
  card.className = 'agent-card agent-history';
  const shots = msg.agent_screenshots || [];
  const log = msg.agent_log || '';
  // Show the last screenshot as the main image
  const lastShot = shots[shots.length - 1];
  const thumbs = shots.map((s, i) =>
    `<img class="agent-history-thumb ${i === shots.length - 1 ? 'active' : ''}" src="data:image/jpeg;base64,${s.image_b64}" data-idx="${i}" alt="Step ${s.step}">`
  ).join('');
  card.innerHTML = `
    <div class="agent-header">
      <div class="agent-orb" style="animation:none;background:linear-gradient(135deg,#4ade80,#22d3ee)">
        <span class="agent-done-icon">&#10003;</span>
      </div>
      <div class="agent-title">Agent Result</div>
    </div>
    <div class="agent-browser">
      <div class="agent-url-bar"><span class="agent-url-secure" style="color:#4ade80">&#128274;</span><span class="agent-url-text">${esc(lastShot?.url || '')}</span></div>
      <div class="agent-viewport">
        <img class="agent-screenshot agent-history-main" src="data:image/jpeg;base64,${lastShot?.image_b64 || ''}" alt="Agent screenshot">
      </div>
    </div>
    ${shots.length > 1 ? `<div class="agent-history-thumbs">${thumbs}</div>` : ''}
    ${log ? `<div class="agent-log agent-history-log">${log.split('\\n').map(l => `<div class="agent-log-entry agent-log-action"><span class="agent-log-icon">▶</span> <span>${esc(l)}</span></div>`).join('')}</div>` : ''}
    <div class="agent-history-result">${renderMD(msg.content)}</div>
  `;
  // Thumbnail click handler
  card.querySelectorAll('.agent-history-thumb').forEach(thumb => {
    thumb.addEventListener('click', () => {
      const idx = parseInt(thumb.dataset.idx);
      const main = card.querySelector('.agent-history-main');
      if (main && shots[idx]) {
        main.src = 'data:image/jpeg;base64,' + shots[idx].image_b64;
        card.querySelectorAll('.agent-history-thumb').forEach(t => t.classList.remove('active'));
        thumb.classList.add('active');
        const urlText = card.querySelector('.agent-url-text');
        if (urlText) urlText.textContent = shots[idx].url || '';
      }
    });
  });
  scroll.appendChild(card);
}

function emptyHTML() {
  return `
    <div class="empty" id="emptyState">
      <div class="empty-glyph">SURF</div>
      <div class="empty-tagline">Search. Understand. Reason. Fast.</div>
      <div class="empty-grid">
        <div class="prompt-card" data-q="What's the latest news today?">
          <div class="pc-title">Latest news</div>
          <div class="pc-sub">Search the web for today's headlines</div>
        </div>
        <div class="prompt-card" data-q="Explain quantum computing in simple terms">
          <div class="pc-title">Explain like I'm 5</div>
          <div class="pc-sub">Break down quantum computing simply</div>
        </div>
        <div class="prompt-card" data-q="Write a Python quicksort with explanation">
          <div class="pc-title">Write code</div>
          <div class="pc-sub">Python quicksort with explanation</div>
        </div>
        <div class="prompt-card" data-q="Compare the top AI models in 2026">
          <div class="pc-title">Compare AI models</div>
          <div class="pc-sub">Which models lead in 2026?</div>
        </div>
      </div>
    </div>`;
}

function bindPromptCards() {
  $$('.prompt-card').forEach(c => {
    c.onclick = () => { $('#chatInput').value = c.dataset.q; sendMessage(); };
  });
}

async function switchConvo(id) {
  await api('/api/conversations/switch', { id });
  renderConversation();
}

async function deleteConvo(id) {
  await api('/api/conversations/delete', { id });
  renderConversation();
}


/* ═══════════════════════════════════════════════════════════════
   API HELPERS
   ═══════════════════════════════════════════════════════════════ */

async function api(path, body) {
  const r = await fetch(path, body
    ? { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify(body) }
    : {});
  state = await r.json();
  syncUI();
  return state;
}

// Vision-capable model keywords
const _VISION_KEYWORDS = ['vision','llava','bakllava','moondream','minicpm-v','cogvlm','fuyu','obsidian','granite-vision','gpt-4o','gpt-4-turbo','gpt-4-vision','claude-3','gemini','gemma3','minicpm','internvl','qwen2-vl','qwen2.5-vl'];
function _isVisionModel(name) {
  const n = name.toLowerCase();
  return _VISION_KEYWORDS.some(k => n.includes(k));
}

let _ollamaModels = []; // cached model list

async function loadModels() {
  const selModel = document.getElementById('selModel');
  const selVision = document.getElementById('selVisionModel');
  const modelInfo = document.getElementById('modelInfo');
  const customRow = document.getElementById('modelCustomRow');

  if (state.provider !== 'ollama') {
    // Non-Ollama: show custom input instead of dropdown
    if (selModel) selModel.style.display = 'none';
    if (customRow) customRow.style.display = 'flex';
    if (modelInfo) modelInfo.textContent = '';
    return;
  }

  if (selModel) selModel.style.display = '';
  if (customRow) customRow.style.display = 'none';

  try {
    const r = await fetch('/api/models');
    const d = await r.json();
    _ollamaModels = d.models || [];
  } catch(e) {
    _ollamaModels = [];
  }

  if (!_ollamaModels.length) {
    if (selModel) selModel.innerHTML = '<option value="">No models found</option>';
    if (selVision) selVision.innerHTML = '<option value="">No models found</option>';
    return;
  }

  // Separate vision and non-vision models
  const visionModels = _ollamaModels.filter(m => _isVisionModel(m.name));
  const textModels = _ollamaModels.filter(m => !_isVisionModel(m.name));

  // Build chat model dropdown
  if (selModel) {
    let html = '';
    if (textModels.length) {
      html += '<optgroup label="Text Models">';
      html += textModels.map(m =>
        `<option value="${m.name}" ${m.name === state.model ? 'selected' : ''}>${m.name} (${m.size_gb}GB)</option>`
      ).join('');
      html += '</optgroup>';
    }
    if (visionModels.length) {
      html += '<optgroup label="\uD83D\uDC41 Vision Models">';
      html += visionModels.map(m =>
        `<option value="${m.name}" ${m.name === state.model ? 'selected' : ''}>${m.name} (${m.size_gb}GB)</option>`
      ).join('');
      html += '</optgroup>';
    }
    html += '<optgroup label="Other"><option value="__custom">Type custom name...</option></optgroup>';
    selModel.innerHTML = html;
    selModel.value = state.model;
    // If current model isn't in list, show custom input
    if (selModel.value !== state.model) {
      selModel.value = '__custom';
      if (customRow) customRow.style.display = 'flex';
    }
  }

  // Build vision model dropdown
  if (selVision) {
    let html = '<option value="">Same as chat model</option>';
    if (visionModels.length) {
      html += '<optgroup label="\uD83D\uDC41 Vision Models (recommended)">';
      html += visionModels.map(m =>
        `<option value="${m.name}" ${m.name === state.vision_model ? 'selected' : ''}>${m.name} (${m.size_gb}GB)</option>`
      ).join('');
      html += '</optgroup>';
    }
    if (textModels.length) {
      html += '<optgroup label="Text Models">';
      html += textModels.map(m =>
        `<option value="${m.name}" ${m.name === state.vision_model ? 'selected' : ''}>${m.name} (${m.size_gb}GB)</option>`
      ).join('');
      html += '</optgroup>';
    }
    selVision.innerHTML = html;
    selVision.value = state.vision_model || '';
  }

  // Show current model info
  if (modelInfo) {
    const cur = _ollamaModels.find(m => m.name === state.model);
    if (cur) {
      const badge = _isVisionModel(cur.name) ? ' \uD83D\uDC41 vision' : '';
      modelInfo.textContent = `${cur.size_gb}GB${badge}`;
    } else {
      modelInfo.textContent = '';
    }
  }
}


/* ═══════════════════════════════════════════════════════════════
   MESSAGE RENDERING
   ═══════════════════════════════════════════════════════════════ */

function addMsg(role, html) {
  const empty = $('#emptyState'); if (empty) empty.remove();
  const isUser = role === 'user';
  const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  const div = document.createElement('div');
  div.className = 'msg';
  const msgIndex = $('#chatScroll').querySelectorAll('.msg').length;
  div.innerHTML = `
    <div class="msg-head">
      <div class="msg-av ${isUser ? 'user' : 'ai'}">${isUser ? 'Y' : 'S'}</div>
      <span class="msg-name">${isUser ? 'You' : 'SURF'}</span>
      <span class="msg-time">${now}</span>
      <button class="msg-branch-btn" title="Branch from here" onclick="branchConversation(${msgIndex})">&#9095;</button>
    </div>
    <div class="msg-body">${html}</div>
  `;
  $('#chatScroll').appendChild(div);
  scrollBottom();
  return div.querySelector('.msg-body');
}

function renderMD(text) {
  const raw = marked.parse(text);
  // Sanitize AI output — do NOT allow onclick/event-handler attributes
  // (our copy buttons are injected via string-replace *after* DOMPurify runs, so they still work)
  const html = DOMPurify.sanitize(raw, { ADD_TAGS: ['button'] });
  return html
    .replace(/<pre><code class="language-(\w+)">/g,
      (_, lang) => `<pre><div class="code-header"><span>${lang}</span><button class="code-copy" onclick="copyCode(this)">Copy</button></div><code class="language-${lang}">`)
    .replace(/<pre><code>/g,
      '<pre><div class="code-header"><span>code</span><button class="code-copy" onclick="copyCode(this)">Copy</button></div><code>');
}

function copyCode(btn) {
  const code = btn.closest('pre').querySelector('code');
  navigator.clipboard.writeText(code.textContent);
  btn.textContent = 'Copied!';
  setTimeout(() => btn.textContent = 'Copy', 1500);
}

function addCopyBtns(container) { /* handled via renderMD injection */ }

/* ── Rich Media Cards (post-process links/images in AI messages) ── */
function postProcessMediaCards(container) {
  if (!container) return;
  // Find bare image URLs and render them inline
  const imgRegex = /(https?:\/\/[^\s<>"']+\.(?:png|jpe?g|gif|webp|svg))(\?[^\s<>"']*)?/gi;
  const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT, null);
  const textsToReplace = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (node.parentElement?.closest('pre, code, a, .media-card')) continue;
    if (imgRegex.test(node.textContent)) textsToReplace.push(node);
    imgRegex.lastIndex = 0;
  }
  textsToReplace.forEach(node => {
    const frag = document.createDocumentFragment();
    let text = node.textContent;
    let match;
    imgRegex.lastIndex = 0;
    let lastIdx = 0;
    while ((match = imgRegex.exec(text)) !== null) {
      if (match.index > lastIdx) frag.appendChild(document.createTextNode(text.slice(lastIdx, match.index)));
      const card = document.createElement('div');
      card.className = 'media-card media-card-image';
      card.innerHTML = `<img src="${esc(match[0])}" alt="Image" loading="lazy" onerror="this.parentElement.remove()">`;
      frag.appendChild(card);
      lastIdx = match.index + match[0].length;
    }
    if (lastIdx < text.length) frag.appendChild(document.createTextNode(text.slice(lastIdx)));
    node.parentNode.replaceChild(frag, node);
  });

  // Turn <a> links (not images) into preview cards
  container.querySelectorAll('a[href]').forEach(a => {
    if (a.closest('pre, code, .media-card, .code-header')) return;
    const url = a.getAttribute('href');
    if (!url || !url.startsWith('http')) return;
    try {
      const domain = new URL(url).hostname;
      const card = document.createElement('span');
      card.className = 'media-card media-card-link';
      card.innerHTML = `<img class="mc-favicon" src="https://www.google.com/s2/favicons?domain=${esc(domain)}&sz=32" alt="" onerror="this.style.display='none'"><span class="mc-text"><span class="mc-title">${a.textContent}</span><span class="mc-domain">${esc(domain)}</span></span>`;
      card.onclick = (e) => { e.preventDefault(); window.open(url, '_blank', 'noopener'); };
      a.replaceWith(card);
    } catch(e) {}
  });
}

/* ── Typing Indicator ─────────────────────────────────────── */
function addTypingIndicator() {
  removeTypingIndicator();
  const el = document.createElement('div');
  el.className = 'msg';
  el.id = 'typingIndicator';
  el.innerHTML = `
    <div class="msg-head">
      <div class="msg-av ai">S</div>
      <span class="msg-name">SURF</span>
    </div>
    <div class="msg-body">
      <div class="typing-dots"><span></span><span></span><span></span></div>
    </div>
  `;
  $('#chatScroll').appendChild(el);
  scrollBottom();
}
function removeTypingIndicator() {
  const el = document.getElementById('typingIndicator');
  if (el) el.remove();
}


/* ═══════════════════════════════════════════════════════════════
   SEARCH CARDS
   ═══════════════════════════════════════════════════════════════ */

function addSearchCard(results) {
  const empty = $('#emptyState'); if (empty) empty.remove();
  const card = document.createElement('div');
  card.className = 'search-card';
  const items = results.map(r => `
    <div class="sc-result">
      <a class="sc-result-link" href="${esc(r.url)}" target="_blank" rel="noopener">${esc(r.title)}</a>
      <div class="sc-result-snippet">${esc(r.snippet || '')}</div>
      <div class="sc-result-meta">
        ${r.source ? '<span>' + esc(r.source) + '</span>' : ''}
        ${r.date ? '<span>' + esc(r.date) + '</span>' : ''}
        <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:200px">${esc(r.url || '')}</span>
      </div>
    </div>
  `).join('');
  card.innerHTML = `
    <div class="sc-header open" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
      <div class="sc-icon">S</div>
      <span class="sc-title">Web Search Results</span>
      <span class="sc-badge">${results.length} found</span>
      <span class="sc-chevron">&#9660;</span>
    </div>
    <div class="sc-body open">${items}</div>
  `;
  $('#chatScroll').appendChild(card);
  scrollBottom();
}

function addSearchLoading(query) {
  const empty = $('#emptyState'); if (empty) empty.remove();
  const el = document.createElement('div');
  el.className = 'search-loading';
  el.id = 'searchLoading';
  el.innerHTML = `
    <div class="search-loading-icon">S</div>
    <div class="sl-text">
      <div class="sl-label">Searching the web...</div>
      <div class="sl-query">"${esc(query)}"</div>
      <div class="sl-bar"><div class="sl-bar-fill"></div></div>
    </div>
  `;
  $('#chatScroll').appendChild(el);
  scrollBottom();
}

function removeSearchLoading() {
  const el = document.getElementById('searchLoading');
  if (el) el.remove();
}


/* ═══════════════════════════════════════════════════════════════
   THINKING CARDS
   ═══════════════════════════════════════════════════════════════ */

function addThinking() {
  const empty = $('#emptyState'); if (empty) empty.remove();
  const el = document.createElement('div');
  el.className = 'thinking-card expanded';
  el.id = 'thinkingCard';
  const t0 = Date.now();
  el.innerHTML = `
    <div class="thinking-header" onclick="toggleThinkingCard()">
      <div class="thinking-orb"><div class="thinking-orb-inner"></div></div>
      <div class="thinking-meta">
        <div class="thinking-label">Thinking...</div>
        <div class="thinking-sub">Click to collapse</div>
      </div>
      <div class="thinking-timer" id="thinkTimer">0.0s</div>
      <div class="thinking-chevron">&#9660;</div>
    </div>
    <div class="thinking-body" id="thinkBody"></div>
  `;
  $('#chatScroll').appendChild(el);
  scrollBottom();
  el._t0 = t0;
  el._interval = setInterval(() => {
    const s = ((Date.now() - t0) / 1000).toFixed(1);
    const timer = document.getElementById('thinkTimer');
    if (timer) timer.textContent = s + 's';
  }, 100);
}

let _thinkBuffer = '';

function appendThinkingToken(text) {
  _thinkBuffer += text;
  const body = document.getElementById('thinkBody');
  if (body) { body.textContent = _thinkBuffer; scrollBottom(); }
}

function finalizeThinking() {
  const el = document.getElementById('thinkingCard');
  if (!el) { _thinkBuffer = ''; return; }
  clearInterval(el._interval);
  // If no real content or thinking took <0.5s, remove silently
  const elapsed = el._t0 ? (Date.now() - el._t0) / 1000 : 0;
  if (!_thinkBuffer.trim() || elapsed < 0.5) {
    el.remove();
    _thinkBuffer = '';
    return;
  }
  const secs = document.getElementById('thinkTimer')?.textContent || '';
  const body = document.getElementById('thinkBody');
  if (body && _thinkBuffer) body.innerHTML = renderMD(_thinkBuffer);
  _thinkBuffer = '';
  el.classList.remove('expanded');
  el.classList.add('done');
  const label = el.querySelector('.thinking-label');
  if (label) label.textContent = `Thought for ${secs}`;
  const sub = el.querySelector('.thinking-sub');
  if (sub) sub.textContent = 'Click to expand';
  const orb = el.querySelector('.thinking-orb');
  if (orb) orb.innerHTML = '<span class="thinking-done-icon">&#10003;</span>';
}

function toggleThinkingCard() {
  const el = document.getElementById('thinkingCard');
  if (el) el.classList.toggle('expanded');
}

function removeThinking() {
  const el = document.getElementById('thinkingCard');
  if (el) { clearInterval(el._interval); el.remove(); }
}

function addFinalizedThinkingCard(reasoning) {
  const el = document.createElement('div');
  el.className = 'thinking-card done';
  el.innerHTML = `
    <div class="thinking-header" onclick="this.parentElement.classList.toggle('expanded')">
      <div class="thinking-orb"><span class="thinking-done-icon">&#10003;</span></div>
      <div class="thinking-meta">
        <div class="thinking-label">Reasoning</div>
        <div class="thinking-sub">Click to expand</div>
      </div>
      <div class="thinking-chevron">&#9660;</div>
    </div>
    <div class="thinking-body">${renderMD(reasoning)}</div>
  `;
  $('#chatScroll').appendChild(el);
}


/* ═══════════════════════════════════════════════════════════════
   PERFORMANCE STATS BAR
   ═══════════════════════════════════════════════════════════════ */

function addStatsBar(container, s) {
  if (!container) return;
  const bar = document.createElement('div');
  bar.className = 'stats-bar';
  const fmt = (label, value, hi) =>
    `<div class="stat"><span class="stat-label">${label}</span><span class="stat-value${hi ? ' stat-hi' : ''}">${value}</span></div>`;
  const parts = [fmt('tokens', s.ans_tokens)];
  if (s.tps) parts.push(fmt('tok/s', s.tps, true));
  if (s.ttft_ms) parts.push(fmt('TTFT', s.ttft_ms + 'ms'));
  parts.push(fmt('time', s.total_s + 's'));
  if (s.reasoning_tokens) parts.push(fmt('think', s.reasoning_tokens));
  if (s.vision_model_used) parts.push(`<div class="stat" style="color:var(--cyan)"><span class="stat-label">&#128247; vision</span><span class="stat-value">${esc(s.vision_model_used)}</span></div>`);
  parts.push(fmt('model', s.model));
  bar.innerHTML = parts.join('');
  container.appendChild(bar);
}

/* ── Context Debug Bar (visual display of model/context/decisions) ── */
function updateContextBar(ev) {
  let bar = document.getElementById('contextBar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'contextBar';
    bar.className = 'context-bar';
    const chatArea = $('#chatArea');
    chatArea.parentNode.insertBefore(bar, chatArea);
  }
  const ctxK = ev.context_window >= 1000 ? (ev.context_window / 1000).toFixed(0) + 'k' : ev.context_window;
  const searchIcon = ev.search_mode === 'NO' ? '&#10005;' : '&#10003;';
  const searchClass = ev.search_mode === 'NO' ? 'ctx-off' : 'ctx-on';
  const thinkIcon = ev.should_think ? '&#10003;' : '&#10005;';
  const thinkClass = ev.should_think ? 'ctx-on' : 'ctx-off';

  // Context fill gauge
  const tokensUsed = ev.tokens_used || 0;
  const ctxWindow = ev.context_window || 1;
  const pct = Math.min(100, Math.round((tokensUsed / ctxWindow) * 100));
  const usedK = tokensUsed >= 1000 ? (tokensUsed / 1000).toFixed(1) + 'k' : tokensUsed;
  // Color: green under 50%, yellow 50-80%, red over 80%
  const fillColor = pct < 50 ? '#4ade80' : pct < 80 ? '#facc15' : '#f87171';

  bar.innerHTML = `
    <span class="ctx-item"><span class="ctx-label">Model</span> <span class="ctx-val ctx-hi">${esc(ev.model)}</span></span>
    ${ev.vision_model_used ? `<span class="ctx-sep">&middot;</span><span class="ctx-item"><span class="ctx-label">&#128247; Vision</span> <span class="ctx-val" style="color:var(--cyan)">${esc(ev.vision_model_used)}</span></span>` : ''}
    <span class="ctx-sep">&middot;</span>
    <span class="ctx-item ctx-gauge-wrap">
      <span class="ctx-label">Context</span>
      <span class="ctx-gauge">
        <span class="ctx-gauge-fill" style="width:${pct}%;background:${fillColor}"></span>
      </span>
      <span class="ctx-val">${usedK}/${ctxK} <span style="opacity:0.5">(${pct}%)</span></span>
    </span>
    <span class="ctx-sep">&middot;</span>
    <span class="ctx-item"><span class="ctx-label">Msgs</span> <span class="ctx-val">${ev.history_msgs}</span></span>
    <span class="ctx-sep">&middot;</span>
    <span class="ctx-item"><span class="ctx-label">Search</span> <span class="ctx-val ${searchClass}">${searchIcon} ${ev.search_mode}</span></span>
    <span class="ctx-sep">&middot;</span>
    <span class="ctx-item"><span class="ctx-label">Think</span> <span class="ctx-val ${thinkClass}">${thinkIcon}</span></span>
  `;
}

function scrollBottom() { const a = $('#chatArea'); a.scrollTop = a.scrollHeight; }


/* ═══════════════════════════════════════════════════════════════
   AGENT MODE — Autonomous browser control
   ═══════════════════════════════════════════════════════════════ */

async function sendAgentMessage(task) {
  streaming = true; $('#btnSend').disabled = true;

  // Show user message
  addMsg('user', esc(task));

  // Create agent viewer card in chat
  const agentCard = document.createElement('div');
  agentCard.className = 'agent-card';
  const t0 = Date.now();
  agentCard.innerHTML = `
    <div class="agent-header">
      <div class="agent-orb"><div class="agent-orb-inner"></div></div>
      <div class="agent-title">Agent browsing...</div>
      <div class="agent-live">
        <span class="agent-live-dot"></span> LIVE
      </div>
      <div class="agent-timer">0s</div>
      <div class="agent-step">Step 0</div>
    </div>
    <div class="agent-browser">
      <div class="agent-url-bar">
        <span class="agent-url-secure">&#9679;</span>
        <span class="agent-url-text">Launching browser...</span>
      </div>
      <div class="agent-viewport">
        <img class="agent-screenshot loading" alt="Browser view">
        <div class="agent-shimmer active"></div>
        <div class="agent-click-marker"></div>
        <svg class="agent-cursor" style="display:none;left:0;top:0" viewBox="0 0 24 24" width="20" height="20">
          <path d="M5 3l14 8-6 2-4 6z" fill="#fff" stroke="#000" stroke-width="1.5"/>
        </svg>
        <div class="agent-think-overlay" style="display:none">
          <div class="agent-think-label">&#129504; Thinking...</div>
          <div class="agent-think-text"></div>
        </div>
      </div>
    </div>
    <div class="agent-log"></div>
  `;
  $('#chatScroll').appendChild(agentCard);
  scrollBottom();

  // Helper: find elements inside THIS card only (avoids duplicate-ID issues on 2nd run)
  const $a = (sel) => agentCard.querySelector(sel);

  // Live elapsed timer
  const timerInterval = setInterval(() => {
    const el = $a('.agent-timer');
    if (el) el.textContent = ((Date.now() - t0) / 1000).toFixed(0) + 's';
  }, 1000);

  const agentTimeout = setTimeout(() => {
    if (streaming) {
      streaming = false; $('#btnSend').disabled = false;
      _appendAgentLog(agentCard, 'Timed out after 3 minutes', 'error');
      clearInterval(timerInterval);
    }
  }, 180000);

  try {
    const res = await fetch('/api/agent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task }),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch(e) { continue; }

        switch (ev.type) {
          case 'agent_start':
            _appendAgentLog(agentCard, `Task: ${ev.task} (max ${ev.max_steps} steps)`, 'info');
            break;

          case 'agent_screenshot': {
            // Hide thinking overlay when new screenshot arrives
            const thinkOverlay = $a('.agent-think-overlay');
            if (thinkOverlay) thinkOverlay.style.display = 'none';
            const img = $a('.agent-screenshot');
            const shimmer = $a('.agent-shimmer');
            if (img) {
              // Smooth crossfade: load new image, then reveal
              const newSrc = 'data:image/jpeg;base64,' + ev.image_b64;
              img.onload = () => {
                img.classList.remove('loading');
                if (shimmer) shimmer.classList.remove('active');
              };
              img.classList.add('loading');
              img.src = newSrc;
            }
            const urlBar = $a('.agent-url-bar');
            if (urlBar) {
              const u = ev.url || 'about:blank';
              const isSecure = u.startsWith('https://');
              urlBar.innerHTML = `
                <span class="agent-url-secure" style="color:${isSecure ? '#4ade80' : '#facc15'}">${isSecure ? '&#128274;' : '&#9888;'}</span>
                <span class="agent-url-text">${esc(u)}</span>
              `;
            }
            const stepEl = $a('.agent-step');
            if (stepEl) stepEl.textContent = `Step ${ev.step}`;
            scrollBottom();
            break;
          }

          case 'agent_thinking_start': {
            // Show thinking overlay on the viewport
            const overlay = $a('.agent-think-overlay');
            if (overlay) {
              overlay.style.display = 'flex';
              const txt = overlay.querySelector('.agent-think-text');
              if (txt) txt.textContent = '';
            }
            break;
          }

          case 'agent_thinking_delta': {
            // Stream thinking text live
            const overlay2 = $a('.agent-think-overlay');
            if (overlay2) {
              overlay2.style.display = 'flex';
              const txt = overlay2.querySelector('.agent-think-text');
              if (txt) {
                txt.textContent += ev.delta;
                // Keep only last 250 chars visible
                if (txt.textContent.length > 250) txt.textContent = '...' + txt.textContent.slice(-200);
              }
            }
            break;
          }

          case 'agent_thinking':
            _appendAgentLog(agentCard, ev.text, 'think');
            break;

          case 'agent_action': {
            // Hide thinking overlay — action is happening
            const thinkOv = $a('.agent-think-overlay');
            if (thinkOv) thinkOv.style.display = 'none';
            let desc = ev.explanation || ev.action;
            if (ev.action === 'click') desc += ` @ (${ev.x}, ${ev.y})`;
            if (ev.action === 'navigate') desc += ` → ${ev.value}`;
            if (ev.action === 'type') desc += `: "${ev.value}"`;
            if (ev.action === 'scroll') desc += ` ${ev.value}`;
            _appendAgentLog(agentCard, desc, 'action');

            // Show click marker + move cursor for clicks
            if (ev.action === 'click') {
              _showClickMarker(agentCard, ev.x, ev.y);
              _moveAgentCursor(agentCard, ev.x, ev.y);
            }

            // Show shimmer while waiting for next screenshot
            const shimmer = $a('.agent-shimmer');
            if (shimmer) shimmer.classList.add('active');
            break;
          }

          case 'agent_error':
            _appendAgentLog(agentCard, ev.error, 'error');
            break;

          case 'agent_done': {
            clearInterval(timerInterval);
            const elapsed = ((Date.now() - t0) / 1000).toFixed(1);
            const header = agentCard.querySelector('.agent-title');
            if (header) header.textContent = `Agent done (${ev.steps_taken} steps, ${elapsed}s)`;
            const orb = agentCard.querySelector('.agent-orb');
            if (orb) { orb.innerHTML = '<span class="agent-done-icon">&#10003;</span>'; orb.style.animation = 'none'; }
            const liveEl = $a('.agent-live');
            if (liveEl) liveEl.style.display = 'none';
            const shimmer = $a('.agent-shimmer');
            if (shimmer) shimmer.classList.remove('active');
            const cursor = $a('.agent-cursor');
            if (cursor) cursor.style.display = 'none';
            _appendAgentLog(agentCard, `Result: ${ev.result}`, 'done');

            // Show result as a regular AI message
            const aiBody = addMsg('ai', '');
            aiBody.innerHTML = renderMD(ev.result);
            addCopyBtns(aiBody);
            scrollBottom();
            break;
          }
        }
      }
    }
  } catch (err) {
    _appendAgentLog(agentCard, `Error: ${err.message}`, 'error');
    console.error('[SURF] Agent error:', err);
  }
  clearTimeout(agentTimeout);
  clearInterval(timerInterval);
  streaming = false; $('#btnSend').disabled = false; $('#chatInput').focus();
  fetch('/api/state').then(r => r.json()).then(s => { state = s; syncUI(); });
}

function _appendAgentLog(card, text, type) {
  const log = card.querySelector('.agent-log');
  if (!log) return;
  const entry = document.createElement('div');
  entry.className = `agent-log-entry agent-log-${type || 'info'}`;
  const icon = { info: '●', action: '▶', think: '◆', error: '✕', done: '✓' }[type] || '●';
  // Truncate thinking output more aggressively
  const maxLen = type === 'think' ? 120 : 200;
  const truncated = text.length > maxLen ? text.substring(0, maxLen) + '...' : text;
  entry.innerHTML = `<span class="agent-log-icon">${icon}</span> <span>${esc(truncated)}</span>`;
  log.appendChild(entry);
  log.scrollTop = log.scrollHeight;
}

function _showClickMarker(card, x, y) {
  const marker = card.querySelector('.agent-click-marker');
  if (!marker) return;
  const img = card.querySelector('.agent-screenshot');
  if (!img || !img.naturalWidth) return;
  const scaleX = img.clientWidth / img.naturalWidth;
  const scaleY = img.clientHeight / img.naturalHeight;
  marker.style.left = (x * scaleX) + 'px';
  marker.style.top = (y * scaleY) + 'px';
  marker.classList.add('active');
  setTimeout(() => marker.classList.remove('active'), 1200);
}

function _moveAgentCursor(card, x, y) {
  const cursor = card.querySelector('.agent-cursor');
  const img = card.querySelector('.agent-screenshot');
  if (!cursor || !img || !img.naturalWidth) return;
  const scaleX = img.clientWidth / img.naturalWidth;
  const scaleY = img.clientHeight / img.naturalHeight;
  cursor.style.display = 'block';
  cursor.style.left = (x * scaleX) + 'px';
  cursor.style.top = (y * scaleY) + 'px';
}


/* ═══════════════════════════════════════════════════════════════
   IMAGE UPLOAD
   ═══════════════════════════════════════════════════════════════ */

let _pendingImage = null; // { base64: '...', mime: 'image/png', name: 'photo.png' }

function handleImageUpload(input) {
  const file = input.files && input.files[0];
  if (!file) return;
  if (!file.type.startsWith('image/')) return;
  if (file.size > 10 * 1024 * 1024) { alert('Image must be under 10 MB'); return; }

  const reader = new FileReader();
  reader.onload = () => {
    const dataUrl = reader.result;
    const base64 = dataUrl.split(',')[1];
    _pendingImage = { base64, mime: file.type, name: file.name, dataUrl };
    showImagePreview();
  };
  reader.readAsDataURL(file);
  input.value = ''; // reset so same file can be re-selected
}

function showImagePreview() {
  const strip = document.getElementById('imgPreviewStrip');
  if (!_pendingImage) { strip.style.display = 'none'; strip.innerHTML = ''; return; }
  strip.style.display = 'flex';
  strip.innerHTML = `
    <div class="img-preview-item">
      <img class="img-preview-thumb" src="${_pendingImage.dataUrl}" alt="preview">
      <button class="img-preview-remove" onclick="clearPendingImage()" title="Remove">&times;</button>
    </div>
  `;
}

function clearPendingImage() {
  _pendingImage = null;
  const strip = document.getElementById('imgPreviewStrip');
  strip.style.display = 'none'; strip.innerHTML = '';
}

// Paste image from clipboard
document.addEventListener('paste', (e) => {
  const items = e.clipboardData && e.clipboardData.items;
  if (!items) return;
  for (const item of items) {
    if (item.type.startsWith('image/')) {
      e.preventDefault();
      const file = item.getAsFile();
      if (file) {
        const reader = new FileReader();
        reader.onload = () => {
          const dataUrl = reader.result;
          const base64 = dataUrl.split(',')[1];
          _pendingImage = { base64, mime: file.type, name: 'pasted-image', dataUrl };
          showImagePreview();
        };
        reader.readAsDataURL(file);
      }
      break;
    }
  }
});

// Drag and drop on input area
document.addEventListener('DOMContentLoaded', () => {
  const inputArea = document.querySelector('.input-area');
  if (!inputArea) return;
  inputArea.addEventListener('dragover', (e) => { e.preventDefault(); inputArea.style.borderColor = 'var(--accent)'; });
  inputArea.addEventListener('dragleave', () => { inputArea.style.borderColor = ''; });
  inputArea.addEventListener('drop', (e) => {
    e.preventDefault(); inputArea.style.borderColor = '';
    const file = e.dataTransfer.files && e.dataTransfer.files[0];
    if (file && file.type.startsWith('image/')) {
      const reader = new FileReader();
      reader.onload = () => {
        const dataUrl = reader.result;
        const base64 = dataUrl.split(',')[1];
        _pendingImage = { base64, mime: file.type, name: file.name, dataUrl };
        showImagePreview();
      };
      reader.readAsDataURL(file);
    }
  });
});


/* ═══════════════════════════════════════════════════════════════
   SEND MESSAGE (SSE Streaming)
   ═══════════════════════════════════════════════════════════════ */

async function sendMessage() {
  const inp = $('#chatInput');
  const text = inp.value.trim();
  if (!text || streaming) return;
  // Don't send bare slash commands as messages
  if (text.startsWith('/') && SLASH_COMMANDS.some(c => c.cmd === text.split(' ')[0])) {
    _renderSlashMenu(text);
    if (_slashFiltered.length) _selectSlash();
    return;
  }
  inp.value = ''; autoResize();

  // If agent mode is on, route to agent handler
  if (state.agent_mode) {
    return sendAgentMessage(text);
  }

  streaming = true; $('#btnSend').disabled = true;

  // Grab pending image
  const image = _pendingImage;
  clearPendingImage();

  // Show user message with image thumbnail if present
  let userHtml = esc(text);
  if (image) {
    userHtml = `<img class="chat-img-thumb" src="${image.dataUrl}" alt="uploaded image">` + userHtml;
  }
  addMsg('user', userHtml);
  addTypingIndicator();

  let aiBody = null, response = '', searchShown = false,
      thinkingHasContent = false, thinkingFinalized = false;
  _thinkBuffer = '';

  // Safety timeout: if streaming hangs for 120s, force-unlock the button
  const _streamTimeout = setTimeout(() => {
    if (streaming) {
      console.warn('[SURF] Stream timeout — force unlocking UI');
      streaming = false; $('#btnSend').disabled = false;
      removeTypingIndicator(); removeThinking(); removeSearchLoading();
      if (!aiBody) { aiBody = addMsg('ai', ''); }
      if (!response) aiBody.innerHTML = '<span style="color:var(--red)">Response timed out. Check the terminal for errors.</span>';
    }
  }, 120000);

  try {
    const payload = { message: text };
    if (image) { payload.image = image.base64; payload.image_mime = image.mime; }
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';

    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      const lines = buf.split('\n');
      buf = lines.pop();

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        let ev;
        try { ev = JSON.parse(line.slice(6)); } catch(e) { continue; }

        switch (ev.type) {
          case 'debug_context':
            updateContextBar(ev);
            console.log('[SURF] Context:', ev);
            break;
          case 'status':
            removeTypingIndicator();
            addMsg('system', `<span style="color:var(--text-4);font-size:0.8rem">${esc(ev.text)}</span>`);
            break;
          case 'search_start':
            removeTypingIndicator();
            addSearchLoading(ev.query);
            break;
          case 'search_done':
            removeSearchLoading();
            if (ev.results && ev.results.length) { addSearchCard(ev.results); searchShown = true; }
            break;
          case 'search_error':
            removeSearchLoading();
            break;
          case 'memory_stored':
            if (ev.facts && ev.facts.length) showMemoryToast(ev.facts);
            break;
          case 'thinking_start':
            removeTypingIndicator();
            // Don't show thinking card yet — wait for first thinking_token
            break;
          case 'response_start':
            removeTypingIndicator();
            break;
          case 'thinking_token':
            thinkingHasContent = true;
            if (!document.getElementById('thinkingCard')) {
              removeTypingIndicator();
              // If AI message already started, insert thinking card BEFORE it
              if (aiBody) {
                const msgDiv = aiBody.closest('.msg');
                const card = document.createElement('div');
                card.className = 'thinking-card expanded';
                card.id = 'thinkingCard';
                const t0 = Date.now();
                card.innerHTML = `
                  <div class="thinking-header" onclick="toggleThinkingCard()">
                    <div class="thinking-orb"><div class="thinking-orb-inner"></div></div>
                    <div class="thinking-meta">
                      <div class="thinking-label">Thinking...</div>
                      <div class="thinking-sub">Click to collapse</div>
                    </div>
                    <div class="thinking-timer" id="thinkTimer">0.0s</div>
                    <div class="thinking-chevron">&#9660;</div>
                  </div>
                  <div class="thinking-body" id="thinkBody"></div>
                `;
                card._t0 = t0;
                card._interval = setInterval(() => {
                  const s = ((Date.now() - t0) / 1000).toFixed(1);
                  const timer = document.getElementById('thinkTimer');
                  if (timer) timer.textContent = s + 's';
                }, 100);
                msgDiv.parentNode.insertBefore(card, msgDiv);
              } else {
                addThinking();
              }
            }
            appendThinkingToken(ev.text);
            break;
          case 'thinking_done':
            finalizeThinking();
            thinkingFinalized = true;
            break;
          case 'token':
            if (!aiBody) {
              removeTypingIndicator();
              if (thinkingHasContent && !thinkingFinalized) finalizeThinking();
              else if (!thinkingHasContent) removeThinking();
              aiBody = addMsg('ai', '');
            }
            response += ev.text;
            aiBody.innerHTML = renderMD(response);
            scrollBottom();
            break;
          case 'error':
            removeTypingIndicator(); removeThinking(); removeSearchLoading();
            if (!aiBody) aiBody = addMsg('ai', '');
            aiBody.innerHTML = `<span style="color:var(--red)">${esc(ev.text)}</span>`;
            break;
          case 'done':
            removeTypingIndicator();
            if (thinkingHasContent && !thinkingFinalized) finalizeThinking();
            else if (!thinkingHasContent) removeThinking();
            if (aiBody && response) { aiBody.innerHTML = renderMD(response); addCopyBtns(aiBody); postProcessMediaCards(aiBody); }
            if (ev.stats && localStorage.getItem('showStats') === '1') addStatsBar(aiBody?.parentElement, ev.stats);
            fetch('/api/state').then(r => r.json()).then(s => { state = s; syncUI(); });
            break;
          case 'title_update':
            if (ev.title) {
              const c = state.conversations?.find(c => c.id === ev.convo_id);
              if (c) c.title = ev.title;
              syncUI();
            }
            break;
        }
      }
    }
  } catch (err) {
    removeTypingIndicator(); removeThinking(); removeSearchLoading();
    if (!aiBody) aiBody = addMsg('ai', '');
    aiBody.innerHTML = `<span style="color:var(--red)">${esc(err.message)}</span>`;
    console.error('[SURF] Stream error:', err);
  }
  clearTimeout(_streamTimeout);
  streaming = false; $('#btnSend').disabled = false; $('#chatInput').focus();
}


/* ═══════════════════════════════════════════════════════════════
   SIDEBAR MEMORY
   ═══════════════════════════════════════════════════════════════ */

function updateMemoryCounts() {
  $('#memGlobalCount').textContent = state.memory_count || 0;
  $('#memSessionCount').textContent = state.session_memory_count || 0;
  loadMemoryLists();
}

async function loadMemoryLists() {
  try {
    const r = await fetch('/api/memory');
    const mem = await r.json();
    const gl = $('#memGlobalList');
    if (mem.global && mem.global.length) {
      gl.innerHTML = mem.global.map((m, i) =>
        `<div class="mem-item"><span class="mem-text">${esc(m.fact)}</span><span class="mem-del" onclick="deleteMemory('global',${i})">&times;</span></div>`
      ).join('');
    } else {
      gl.innerHTML = '<div style="padding:3px 6px;font-size:0.65rem;color:var(--text-4)">No global memories yet</div>';
    }
    const sl = $('#memSessionList');
    if (mem.session && mem.session.length) {
      sl.innerHTML = mem.session.map((f, i) =>
        `<div class="mem-item"><span class="mem-text">${esc(f)}</span><span class="mem-del" onclick="deleteMemory('session',${i})">&times;</span></div>`
      ).join('');
    } else {
      sl.innerHTML = '<div style="padding:3px 6px;font-size:0.65rem;color:var(--text-4)">No session memories</div>';
    }
  } catch(e) {}
}

async function addGlobalMemory() {
  const inp = $('#memAddInput');
  const fact = inp.value.trim();
  if (!fact) return;
  inp.value = '';
  await fetch('/api/memory/add', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({fact}) });
  const r = await fetch('/api/state'); state = await r.json(); syncUI();
}

async function deleteMemory(tier, index) {
  await fetch('/api/memory/delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({tier, index}) });
  const r = await fetch('/api/state'); state = await r.json(); syncUI();
}

function showMemoryToast(facts) {
  const toast = document.getElementById('memToast');
  if (!toast) return;
  const labels = facts.map(f => `${f.tier === 'global' ? 'Global' : 'Session'}: ${f.fact}`).join(' | ');
  toast.querySelector('.toast-text').textContent = 'Remembered: ' + labels;
  toast.classList.add('show');
  setTimeout(() => toast.classList.remove('show'), 3000);
}


/* ═══════════════════════════════════════════════════════════════
   STATS MODAL
   ═══════════════════════════════════════════════════════════════ */

let _statsData = null;
let _statsTab = 'overview';

function switchStatsTab(tab) {
  _statsTab = tab;
  $$('.stats-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
  renderStatsTab();
}

async function openStatsModal() {
  openModal('statsModal');
  const body = $('#statsModalBody');
  body.innerHTML = '<div class="stats-empty">Loading statistics...</div>';

  try {
    const r = await fetch('/api/stats');
    _statsData = await r.json();
    _statsTab = 'overview';
    $$('.stats-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === 'overview'));
    renderStatsTab();
  } catch(e) {
    body.innerHTML = '<div class="stats-empty">Failed to load statistics</div>';
  }
}

function renderStatsTab() {
  const body = $('#statsModalBody');
  if (!_statsData) return;
  const s = _statsData;

  switch (_statsTab) {
    case 'overview': body.innerHTML = renderStatsOverview(s); break;
    case 'models': body.innerHTML = renderStatsModels(s); break;
    case 'speed': body.innerHTML = renderStatsSpeed(s); break;
    case 'conversations': body.innerHTML = renderStatsConvos(s); break;
    case 'history': body.innerHTML = renderStatsHistory(s); break;
  }
}

function renderStatsOverview(s) {
  const avgResp = (s.total_time_s / Math.max(s.total_messages, 1)).toFixed(1);
  return `
    <div class="stats-hero">
      <div class="stats-hero-card c-accent">
        <div class="shc-value">${s.total_messages.toLocaleString()}</div>
        <div class="shc-label">Total Messages</div>
      </div>
      <div class="stats-hero-card c-green">
        <div class="shc-value">${s.total_tokens.toLocaleString()}</div>
        <div class="shc-label">Total Tokens</div>
        <div class="shc-sub">${s.avg_tokens_per_msg} avg/msg</div>
      </div>
      <div class="stats-hero-card c-cyan">
        <div class="shc-value">${s.total_reasoning_tokens.toLocaleString()}</div>
        <div class="shc-label">Think Tokens</div>
      </div>
      <div class="stats-hero-card c-yellow">
        <div class="shc-value">${s.total_searches}</div>
        <div class="shc-label">Web Searches</div>
      </div>
    </div>
    <div class="stats-secondary">
      <div class="stats-sec-card">
        <div class="ssc-icon i-speed">&#9889;</div>
        <div class="ssc-text">
          <div class="ssc-value">${s.avg_tps} t/s</div>
          <div class="ssc-label">Average Speed</div>
        </div>
      </div>
      <div class="stats-sec-card">
        <div class="ssc-icon i-time">&#9202;</div>
        <div class="ssc-text">
          <div class="ssc-value">${s.total_time_s}s</div>
          <div class="ssc-label">Total Gen Time</div>
        </div>
      </div>
      <div class="stats-sec-card">
        <div class="ssc-icon i-think">&#129504;</div>
        <div class="ssc-text">
          <div class="ssc-value">${avgResp}s</div>
          <div class="ssc-label">Avg Response Time</div>
        </div>
      </div>
      <div class="stats-sec-card">
        <div class="ssc-icon i-search">&#128269;</div>
        <div class="ssc-text">
          <div class="ssc-value">${s.total_conversations || 0}</div>
          <div class="ssc-label">Total Conversations</div>
        </div>
      </div>
    </div>
    <div class="stats-records">
      <div class="stats-record-card">
        <div class="src-label">Peak Speed</div>
        <div class="src-value accent">${s.peak_tps} t/s</div>
      </div>
      <div class="stats-record-card">
        <div class="src-label">Fastest Response</div>
        <div class="src-value green">${s.fastest_response}s</div>
      </div>
      <div class="stats-record-card">
        <div class="src-label">Slowest Response</div>
        <div class="src-value yellow">${s.slowest_response}s</div>
      </div>
      <div class="stats-record-card">
        <div class="src-label">Avg Tokens/Msg</div>
        <div class="src-value cyan">${s.avg_tokens_per_msg}</div>
      </div>
    </div>`;
}

function renderStatsModels(s) {
  const models = s.model_breakdown || [];
  if (!models.length) return '<div class="stats-empty">No model data yet. Start chatting!</div>';

  const maxMsgs = Math.max(...models.map(m => m.messages), 1);
  const maxTok = Math.max(...models.map(m => m.tokens), 1);

  return `
    <div class="stats-models-grid">
      ${models.map((m, i) => `
        <div class="stats-model-card">
          <div class="smc-rank">#${i + 1}</div>
          <div class="smc-body">
            <div class="smc-name">${esc(m.model)}</div>
            <div class="smc-metrics">
              <div class="smc-metric">
                <span class="smc-metric-label">Messages</span>
                <span class="smc-metric-val">${m.messages}</span>
              </div>
              <div class="smc-metric">
                <span class="smc-metric-label">Tokens</span>
                <span class="smc-metric-val">${m.tokens.toLocaleString()}</span>
              </div>
              <div class="smc-metric">
                <span class="smc-metric-label">Avg Speed</span>
                <span class="smc-metric-val accent">${m.avg_tps} t/s</span>
              </div>
              <div class="smc-metric">
                <span class="smc-metric-label">Total Time</span>
                <span class="smc-metric-val">${m.time}s</span>
              </div>
            </div>
            <div class="smc-bar-row">
              <span class="smc-bar-label">Usage</span>
              <div class="smc-bar"><div class="smc-bar-fill" style="width:${(m.messages / maxMsgs * 100).toFixed(0)}%"></div></div>
            </div>
          </div>
        </div>
      `).join('')}
    </div>`;
}

function renderStatsSpeed(s) {
  const trend = s.speed_trend || [];
  if (!trend.length) return '<div class="stats-empty">No speed data yet. Start chatting!</div>';

  const maxTps = Math.max(...trend.map(t => t.tps), 1);
  const avgTps = trend.reduce((a, t) => a + t.tps, 0) / trend.length;

  return `
    <div class="stats-speed-summary">
      <div class="speed-stat">
        <div class="speed-stat-value accent">${s.avg_tps} t/s</div>
        <div class="speed-stat-label">Average</div>
      </div>
      <div class="speed-stat">
        <div class="speed-stat-value green">${s.peak_tps} t/s</div>
        <div class="speed-stat-label">Peak</div>
      </div>
      <div class="speed-stat">
        <div class="speed-stat-value cyan">${s.fastest_response}s</div>
        <div class="speed-stat-label">Fastest</div>
      </div>
      <div class="speed-stat">
        <div class="speed-stat-value yellow">${s.slowest_response}s</div>
        <div class="speed-stat-label">Slowest</div>
      </div>
    </div>
    <div class="stats-section-head">Speed Trend <span class="stats-section-sub">Last ${trend.length} requests</span></div>
    <div class="speed-chart">
      ${trend.map((t, i) => `
        <div class="speed-bar-wrap" title="${t.tps} t/s | ${t.tokens} tokens | ${t.time}s">
          <div class="speed-bar" style="height:${Math.max((t.tps / maxTps * 100), 4).toFixed(0)}%"></div>
          <div class="speed-bar-label">${t.tps}</div>
        </div>
      `).join('')}
    </div>
    <div class="speed-chart-axis">
      <span>Oldest</span>
      <span class="speed-avg-line">Avg: ${avgTps.toFixed(1)} t/s</span>
      <span>Latest</span>
    </div>`;
}

function renderStatsConvos(s) {
  const convos = s.conversation_stats || [];
  if (!convos.length) return '<div class="stats-empty">No conversations yet. Start chatting!</div>';

  return `
    <div class="stats-section-head">Conversations <span class="stats-section-sub">${convos.length} total</span></div>
    <div class="stats-convo-list">
      ${convos.map(c => {
        const date = c.created ? new Date(c.created * 1000).toLocaleDateString([], {month:'short', day:'numeric', hour:'2-digit', minute:'2-digit'}) : '';
        return `
          <div class="stats-convo-row" onclick="closeModal('statsModal');switchConvo('${c.id}')">
            <div class="scr-dot"></div>
            <div class="scr-body">
              <div class="scr-title">${esc(c.title)}</div>
              <div class="scr-meta">
                <span>${c.user_msgs} sent</span>
                <span>${c.ai_msgs} received</span>
                <span>${date}</span>
              </div>
            </div>
            <div class="scr-count">${c.total_msgs}</div>
          </div>`;
      }).join('')}
    </div>`;
}

function renderStatsHistory(s) {
  const recent = (s.recent_requests || []).slice().reverse();
  if (!recent.length) return '<div class="stats-empty">No request history yet. Start chatting!</div>';

  return `
    <div class="stats-section-head">Recent Requests <span class="stats-section-sub">Last ${recent.length}</span></div>
    <table class="stats-table">
      <thead><tr><th>Model</th><th>Tokens</th><th>Speed</th><th>Time</th></tr></thead>
      <tbody>
        ${recent.map(r => `
          <tr>
            <td class="st-model">${esc(r.model)}</td>
            <td>${r.tokens}</td>
            <td class="st-accent">${r.tps} t/s</td>
            <td>${r.time}s</td>
          </tr>
        `).join('')}
      </tbody>
    </table>`;
}


/* ═══════════════════════════════════════════════════════════════
   MEMORY MODAL
   ═══════════════════════════════════════════════════════════════ */

let _mmTab = 'global';

async function openMemoryModal() {
  openModal('memoryModal');
  await renderMemoryModal();
}

async function renderMemoryModal() {
  try {
    const r = await fetch('/api/memory');
    const mem = await r.json();
    const gl = mem.global || [];
    const sl = mem.session || [];
    const body = $('#memoryModalBody');
    const isGlobal = _mmTab === 'global';
    const items = isGlobal ? gl : sl;

    body.innerHTML = `
      <div class="mm-info">
        <div class="mm-info-dot"></div>
        <span>Memory is auto-extracted from conversations and persists across sessions</span>
      </div>

      <div class="mm-tabs">
        <button class="mm-tab ${isGlobal ? 'active' : ''}" onclick="_mmTab='global';renderMemoryModal()">
          Global <span class="mm-tab-count">${gl.length}</span>
        </button>
        <button class="mm-tab ${!isGlobal ? 'active' : ''}" onclick="_mmTab='session';renderMemoryModal()">
          Session <span class="mm-tab-count">${sl.length}</span>
        </button>
      </div>

      <div class="mm-actions">
        ${isGlobal ? '<button class="mm-action-btn danger" onclick="clearAllMemory()">Clear all global</button>' : ''}
      </div>

      <div class="mm-list">
        ${items.length
          ? items.map((item, i) => {
              const fact = isGlobal ? item.fact : item;
              const source = isGlobal && item.source ? item.source : '';
              return `
                <div class="mm-fact">
                  <div class="mm-fact-dot ${isGlobal ? 'global' : 'session'}"></div>
                  <div class="mm-fact-body">
                    <div class="mm-fact-text">${esc(fact)}</div>
                    <div class="mm-fact-meta">
                      <span class="mm-fact-tag ${source === 'manual' ? 'manual' : 'auto'}">${source === 'manual' ? 'Manual' : 'Auto'}</span>
                    </div>
                  </div>
                  <button class="mm-fact-del" onclick="deleteMemoryModal('${_mmTab}',${i})">&times;</button>
                </div>`;
            }).join('')
          : `<div class="mm-empty-state">
              <div class="mm-empty-icon">${isGlobal ? '&#128161;' : '&#128172;'}</div>
              <div class="mm-empty-text">No ${_mmTab} memories yet</div>
              <div class="mm-empty-sub">${isGlobal ? 'Memories are auto-extracted or manually added' : 'Session facts are detected during conversation'}</div>
            </div>`
        }
      </div>

      ${isGlobal ? `
        <div class="mm-add-bar">
          <input class="mm-add-input" id="mmAddInput" placeholder="Add a fact about yourself..." spellcheck="false"
            onkeydown="if(event.key==='Enter')addMemoryModal()">
          <button class="mm-add-btn" onclick="addMemoryModal()">Add</button>
        </div>
      ` : ''}
    `;
  } catch(e) {
    $('#memoryModalBody').innerHTML = '<div class="stats-empty">Failed to load memory</div>';
  }
}

async function addMemoryModal() {
  const inp = document.getElementById('mmAddInput');
  if (!inp) return;
  const fact = inp.value.trim();
  if (!fact) return;
  inp.value = '';
  await fetch('/api/memory/add', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({fact}) });
  const r2 = await fetch('/api/state'); state = await r2.json(); syncUI();
  renderMemoryModal();
}

async function deleteMemoryModal(tier, index) {
  await fetch('/api/memory/delete', { method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({tier, index}) });
  const r2 = await fetch('/api/state'); state = await r2.json(); syncUI();
  renderMemoryModal();
}

async function clearAllMemory() {
  if (!confirm('Clear all global memory?')) return;
  await fetch('/api/memory/clear', { method: 'POST' });
  const r2 = await fetch('/api/state'); state = await r2.json(); syncUI();
  renderMemoryModal();
}


/* ═══════════════════════════════════════════════════════════════
   CONVERSATION BRANCHING
   ═══════════════════════════════════════════════════════════════ */

async function branchConversation(msgIndex) {
  try {
    const res = await fetch('/api/conversations/branch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: state.active_id, message_index: msgIndex }),
    });
    state = await res.json();
    syncUI();
    renderConversation();
  } catch(e) { console.error('Branch failed:', e); }
}


/* ═══════════════════════════════════════════════════════════════
   SUMMARIZE
   ═══════════════════════════════════════════════════════════════ */

let _saveToMemory = false;

function openSummarizeModal() {
  const msgs = (state.conversation?.messages || []).filter(m => m.role === 'user' || m.role === 'assistant');
  const title = state.conversation?.title || 'this conversation';
  const subtitle = document.getElementById('summarizeSubtitle');
  if (subtitle) subtitle.textContent = `"${title}" · ${msgs.length} messages`;

  const body = document.getElementById('summarizeBody');
  _saveToMemory = false;
  body.innerHTML = `
    <div class="summarize-prompt">
      <p style="font-size:.78rem;color:var(--text-2);margin-bottom:14px">
        AI will read your conversation and produce a structured summary.
      </p>
      <div class="summarize-actions">
        <button class="btn-primary" id="btnDoSummarize" onclick="doSummarize()">✨ Generate Summary</button>
        <label style="display:flex;align-items:center;gap:7px;cursor:pointer">
          <div class="tg-switch" id="tgSaveToMemory"></div>
          <span style="font-size:.72rem;color:var(--text-3)">Save to memory</span>
        </label>
      </div>
    </div>`;

  syncToggle('tgSaveToMemory', false);
  const tg = document.getElementById('tgSaveToMemory');
  if (tg) tg.addEventListener('click', () => {
    _saveToMemory = !_saveToMemory;
    syncToggle('tgSaveToMemory', _saveToMemory);
  });

  openModal('summarizeModal');
}

async function doSummarize() {
  const body = document.getElementById('summarizeBody');
  const btn  = document.getElementById('btnDoSummarize');
  if (!body) return;
  if (btn) btn.disabled = true;

  body.innerHTML = `
    <div style="text-align:center;padding:36px 20px;color:var(--text-3)">
      <div class="typing-dots"><span></span><span></span><span></span></div>
      <div style="margin-top:14px;font-size:.75rem">Generating summary…</div>
    </div>`;

  try {
    const r = await fetch('/api/conversations/summarize', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ save_to_memory: _saveToMemory }),
    });
    const data = await r.json();

    if (!r.ok || data.error) {
      const errEl = document.createElement('div');
      errEl.className = 'summarize-error';
      errEl.textContent = '⚠ ' + (data.error || 'Failed to generate summary');
      body.innerHTML = '';
      body.appendChild(errEl);
      return;
    }

    // Safely render markdown summary
    const raw = data.summary || '';
    const rendered = DOMPurify.sanitize(marked.parse(raw), {
      ALLOWED_TAGS: ['p','br','strong','em','b','i','ul','ol','li','h1','h2','h3','h4','code','pre'],
      ALLOWED_ATTR: [],
    });

    body.innerHTML = `
      <div class="summarize-result">
        <div class="summarize-content">${rendered}</div>
        <div class="summarize-footer">
          <button class="btn-secondary" onclick="doSummarize()">↻ Regenerate</button>
          <button class="btn-secondary" id="btnCopySummary">⎘ Copy</button>
          ${_saveToMemory ? '<span style="color:var(--green);font-size:.72rem">✓ Saved to memory</span>' : ''}
        </div>
      </div>`;
    body.dataset.rawSummary = raw;

    const copyBtn = document.getElementById('btnCopySummary');
    if (copyBtn) copyBtn.addEventListener('click', () => {
      navigator.clipboard.writeText(body.dataset.rawSummary || '').then(() => {
        copyBtn.textContent = '✓ Copied!';
        setTimeout(() => { copyBtn.textContent = '⎘ Copy'; }, 2000);
      });
    });

    if (_saveToMemory) updateMemoryCounts();
  } catch(err) {
    const errEl = document.createElement('div');
    errEl.className = 'summarize-error';
    errEl.textContent = '⚠ ' + String(err);
    body.innerHTML = '';
    body.appendChild(errEl);
  }
}


/* ═══════════════════════════════════════════════════════════════
   SKILLS SYSTEM
   ═══════════════════════════════════════════════════════════════ */

async function openSkillsModal() {
  openModal('skillsModal');
  const body = $('#skillsModalBody');
  body.innerHTML = '<div class="stats-empty">Loading skills...</div>';
  try {
    const r = await fetch('/api/skills');
    const d = await r.json();
    const skills = d.skills || [];
    renderSkillGrid(body, skills);
  } catch(e) {
    body.innerHTML = '<div class="stats-empty">Failed to load skills</div>';
  }
}

function renderSkillGrid(container, skills) {
  container.innerHTML = `
    <div class="skill-grid">
      ${skills.map(s => `
        <div class="skill-card ${s.enabled ? '' : 'disabled'}" data-skill="${esc(s.id)}">
          <div class="skill-icon">${esc(s.icon)}</div>
          <div class="skill-info">
            <div class="skill-name">${esc(s.name)}</div>
            <div class="skill-desc">${esc(s.description)}</div>
            <div class="skill-meta">v${esc(s.version)}${s.author ? ' · by ' + esc(s.author) : ''}</div>
          </div>
          <label class="skill-toggle" title="${s.enabled ? 'Disable' : 'Enable'} skill">
            <input type="checkbox" ${s.enabled ? 'checked' : ''} onchange="toggleSkill('${esc(s.id)}', this.checked)">
            <span class="slider"></span>
          </label>
        </div>
      `).join('')}
    </div>
    <div id="skillDetailArea"></div>
  `;
  // Click card (not toggle) to view details
  container.querySelectorAll('.skill-card').forEach(card => {
    card.addEventListener('click', (e) => {
      if (e.target.closest('.skill-toggle')) return;
      viewSkillDetail(card.dataset.skill);
    });
  });
}

async function toggleSkill(skillId, enabled) {
  try {
    await fetch(`/api/skills/${encodeURIComponent(skillId)}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    });
    // Update card visual state
    const card = document.querySelector(`.skill-card[data-skill="${skillId}"]`);
    if (card) card.classList.toggle('disabled', !enabled);
  } catch(e) { /* silent */ }
}

async function viewSkillDetail(skillId) {
  const area = document.getElementById('skillDetailArea');
  if (!area) return;
  area.innerHTML = '<div class="stats-empty">Loading...</div>';
  try {
    const r = await fetch(`/api/skills/${encodeURIComponent(skillId)}`);
    const s = await r.json();
    if (s.error) { area.innerHTML = ''; return; }
    area.innerHTML = `
      <div class="skill-detail">
        <div class="skill-detail-header">
          <button class="skill-back-btn" onclick="document.getElementById('skillDetailArea').innerHTML=''">&larr; Back</button>
          <span class="skill-detail-title">${esc(s.icon)} ${esc(s.name)}</span>
        </div>
        <div class="skill-detail-body">${typeof marked !== 'undefined' ? marked.parse(s.body) : esc(s.body).replace(/\\n/g, '<br>')}</div>
      </div>
    `;
  } catch(e) {
    area.innerHTML = '';
  }
}


/* ═══════════════════════════════════════════════════════════════
   UTILITIES
   ═══════════════════════════════════════════════════════════════ */

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}

function autoResize() {
  const i = $('#chatInput'); i.style.height = 'auto';
  const h = Math.min(i.scrollHeight, 180);
  i.style.height = h + 'px';
  i.style.overflowY = i.scrollHeight > 180 ? 'auto' : 'hidden';
}

function openModal(id) { document.getElementById(id).classList.add('open'); }
function closeModal(id) { document.getElementById(id).classList.remove('open'); }

function warmupModel() {
  fetch('/api/warmup', { method: 'POST' }).catch(() => {});
}


/* ═══════════════════════════════════════════════════════════════
   EVENT LISTENERS
   ═══════════════════════════════════════════════════════════════ */

/* ── Slash Command Menu ────────────────────────────────────── */
const SLASH_COMMANDS = [
  { cmd: '/search',   icon: '🔍', cat: 'toggle', desc: 'Toggle web search',    action: () => { const v = !state.web_search; api('/api/settings', { web_search: v }); return `Web search ${v ? 'enabled' : 'disabled'}`; } },
  { cmd: '/think',    icon: '🧠', cat: 'toggle', desc: 'Toggle thinking mode', action: () => { const v = !state.thinking;   api('/api/settings', { thinking: v });   return `Thinking ${v ? 'enabled' : 'disabled'}`; } },
  { cmd: '/stream',   icon: '⚡', cat: 'toggle', desc: 'Toggle streaming',     action: () => { const v = !state.streaming;  api('/api/settings', { streaming: v });  return `Streaming ${v ? 'enabled' : 'disabled'}`; } },
  { cmd: '/agent',    icon: '🤖', cat: 'toggle', desc: 'Toggle agent mode',    action: () => { const v = !state.agent_mode; api('/api/settings', { agent_mode: v }); return `Agent mode ${v ? 'enabled' : 'disabled'}`; } },
  { cmd: '/new',      icon: '➕', cat: 'action', desc: 'New conversation',      action: () => { api('/api/conversations/new', {}); renderConversation(); return 'New conversation'; } },
  { cmd: '/clear',    icon: '🗑️', cat: 'action', desc: 'Clear all memory',     action: () => { api('/api/memory/clear', {}); return 'Memory cleared'; } },
  { cmd: '/memory',   icon: '📝', cat: 'nav',    desc: 'Open memory panel',     action: () => { openModal('memoryModal'); return null; } },
  { cmd: '/stats',    icon: '📊', cat: 'nav',    desc: 'Open stats dashboard',  action: () => { openModal('statsModal'); return null; } },
  { cmd: '/summarize',icon: '📄', cat: 'nav',    desc: 'Summarize conversation',action: () => { openSummarizeModal(); return null; } },
  { cmd: '/model',    icon: '🔧', cat: 'nav',    desc: 'Focus model selector',  action: () => { $('#sidebar').classList.add('open'); $('#overlay').classList.add('active'); document.getElementById('selModel')?.focus(); return null; } },
  { cmd: '/keys',     icon: '🔑', cat: 'nav',    desc: 'Open API key settings', action: () => { $('#sidebar').classList.add('open'); $('#overlay').classList.add('active'); document.getElementById('inpKey')?.focus(); return null; } },
];

let _slashIdx = -1;
let _slashFiltered = [];

function _renderSlashMenu(filter) {
  const menu = $('#slashMenu');
  const q = filter.toLowerCase();
  _slashFiltered = SLASH_COMMANDS.filter(c => c.cmd.includes(q) || c.desc.toLowerCase().includes(q));
  if (!_slashFiltered.length) {
    menu.innerHTML = '<div class="slash-empty">No matching commands</div>';
    menu.classList.add('open');
    _slashIdx = -1;
    return;
  }
  _slashIdx = 0;
  const cats = { toggle: 'Toggles', action: 'Actions', nav: 'Navigation' };
  let html = '';
  let lastCat = '';
  _slashFiltered.forEach((c, i) => {
    if (c.cat !== lastCat) { lastCat = c.cat; html += `<div class="slash-menu-header">${cats[c.cat] || c.cat}</div>`; }
    const hint = c.cat === 'toggle' ? (state[{'/search':'web_search','/think':'thinking','/stream':'streaming','/agent':'agent_mode'}[c.cmd]] ? 'ON' : 'OFF') : '';
    html += `<div class="slash-item${i === 0 ? ' active' : ''}" data-idx="${i}">
      <div class="slash-icon ${c.cat}">${c.icon}</div>
      <div class="slash-text"><div class="slash-name">${c.cmd}</div><div class="slash-desc">${c.desc}</div></div>
      ${hint ? `<span class="slash-hint">${hint}</span>` : ''}
    </div>`;
  });
  menu.innerHTML = html;
  menu.classList.add('open');
  menu.querySelectorAll('.slash-item').forEach(el => {
    el.addEventListener('mouseenter', () => {
      _slashIdx = parseInt(el.dataset.idx);
      _highlightSlash();
    });
    el.addEventListener('click', () => { _selectSlash(); });
  });
}

function _highlightSlash() {
  const items = $('#slashMenu').querySelectorAll('.slash-item');
  items.forEach((el, i) => el.classList.toggle('active', i === _slashIdx));
  items[_slashIdx]?.scrollIntoView({ block: 'nearest' });
}

function _selectSlash() {
  const cmd = _slashFiltered[_slashIdx];
  if (!cmd) return;
  _closeSlashMenu();
  const inp = $('#chatInput');
  inp.value = ''; autoResize();
  const msg = cmd.action();
  if (msg) {
    // Flash a toast so user sees the result
    const t = document.createElement('div'); t.className = 'toast';
    t.textContent = msg; document.body.appendChild(t);
    requestAnimationFrame(() => t.classList.add('show'));
    setTimeout(() => { t.classList.remove('show'); setTimeout(() => t.remove(), 300); }, 2000);
  }
  inp.focus();
}

function _closeSlashMenu() {
  $('#slashMenu').classList.remove('open');
  _slashIdx = -1; _slashFiltered = [];
}

function _isSlashOpen() { return $('#slashMenu').classList.contains('open'); }

$('#chatInput').addEventListener('keydown', e => {
  if (_isSlashOpen()) {
    if (e.key === 'ArrowDown')  { e.preventDefault(); _slashIdx = Math.min(_slashIdx + 1, _slashFiltered.length - 1); _highlightSlash(); return; }
    if (e.key === 'ArrowUp')    { e.preventDefault(); _slashIdx = Math.max(_slashIdx - 1, 0); _highlightSlash(); return; }
    if (e.key === 'Enter' || e.key === 'Tab') { e.preventDefault(); _selectSlash(); return; }
    if (e.key === 'Escape')     { e.preventDefault(); _closeSlashMenu(); return; }
  }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
$('#chatInput').addEventListener('input', () => {
  autoResize();
  const val = $('#chatInput').value;
  if (val.startsWith('/')) {
    _renderSlashMenu(val);
  } else {
    _closeSlashMenu();
  }
});
$('#btnSend').addEventListener('click', sendMessage);

// Close slash menu when clicking outside
document.addEventListener('click', e => {
  if (_isSlashOpen() && !e.target.closest('.slash-menu') && !e.target.closest('#chatInput')) _closeSlashMenu();
});

$('#btnNew').addEventListener('click', async () => {
  await api('/api/conversations/new', {});
  renderConversation();
});

$$('.tg-switch').forEach(el => {
  el.addEventListener('click', () => {
    const key = el.dataset.key;
    if (el.id === 'tgStats') {
      const next = !el.classList.contains('on');
      localStorage.setItem('showStats', next ? '1' : '0');
      syncUI();
      return;
    }
    const val = !el.classList.contains('on');
    api('/api/settings', { [key]: val });
  });
});

$('#selProvider').addEventListener('change', e => {
  api('/api/settings', { provider: e.target.value }).then(() => { loadModels(); warmupModel(); });
});

// Chat model dropdown
const _selModel = document.getElementById('selModel');
if (_selModel) _selModel.addEventListener('change', e => {
  const v = e.target.value;
  const customRow = document.getElementById('modelCustomRow');
  if (v === '__custom') {
    if (customRow) customRow.style.display = 'flex';
    document.getElementById('inpModel')?.focus();
    return;
  }
  if (customRow) customRow.style.display = 'none';
  if (v) api('/api/settings', { model: v }).then(() => { loadModels(); warmupModel(); });
});

// Custom model input (fallback for typed names)
$('#btnModel').addEventListener('click', () => {
  const m = $('#inpModel').value.trim();
  if (m) api('/api/settings', { model: m }).then(() => { loadModels(); warmupModel(); });
});
$('#inpModel').addEventListener('keydown', e => {
  if (e.key === 'Enter') {
    const m = e.target.value.trim();
    if (m) api('/api/settings', { model: m }).then(() => { loadModels(); warmupModel(); });
  }
});

$('#inpKey').addEventListener('change', e => {
  const k = e.target.value.trim();
  if (k) api('/api/settings', { api_key: k });
});

// Vision model dropdown
const _selVM = document.getElementById('selVisionModel');
if (_selVM) _selVM.addEventListener('change', e => {
  api('/api/settings', { vision_model: e.target.value });
});
$('#btnMenu').addEventListener('click', () => {
  $('#sidebar').classList.toggle('open');
  $('#overlay').classList.toggle('active');
});
$('#overlay').addEventListener('click', () => {
  $('#sidebar').classList.remove('open');
  $('#overlay').classList.remove('active');
});

$('#memAddInput').addEventListener('keydown', e => { if (e.key === 'Enter') addGlobalMemory(); });

// Code theme selector
const _selTheme = document.getElementById('selCodeTheme');
if (_selTheme) _selTheme.addEventListener('change', e => setCodeTheme(e.target.value));

// Keyboard shortcut: Escape to close modals
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.open').forEach(m => m.classList.remove('open'));
  }
});

/* ── Boot ─────────────────────────────────────────────────── */
init();
