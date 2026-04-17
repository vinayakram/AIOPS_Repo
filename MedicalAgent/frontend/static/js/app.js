// ── Auth guard ────────────────────────────────────────────────
const token = localStorage.getItem('token');
const username = localStorage.getItem('username') || 'User';

if (!token) {
  window.location.href = '/';
}

document.getElementById('userBadge').textContent = username;

// ── LLM access toggle ─────────────────────────────────────────
let llmEnabled = true;

async function initLLMToggle() {
  try {
    const res = await fetch('/api/admin/llm-access', {
      headers: { Authorization: `Bearer ${token}` },
    });
    if (res.ok) {
      const data = await res.json();
      llmEnabled = data.llm_enabled;
      updateLLMToggleBtn();
    }
  } catch (_) {}
}

function updateLLMToggleBtn() {
  const btn = document.getElementById('llmToggleBtn');
  if (!btn) return;
  if (llmEnabled) {
    btn.textContent = '⚡ LLM: Enabled';
    btn.classList.remove('llm-disabled');
    btn.title = 'Click to disable LLM access (triggers error trace in AIops)';
  } else {
    btn.textContent = '🚫 LLM: Disabled';
    btn.classList.add('llm-disabled');
    btn.title = 'Click to re-enable LLM access';
  }
}

async function toggleLLMAccess() {
  const newState = !llmEnabled;
  try {
    const res = await fetch(`/api/admin/llm-access?enabled=${newState}`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}` },
    });
    if (res.ok) {
      const data = await res.json();
      llmEnabled = data.llm_enabled;
      updateLLMToggleBtn();
    }
  } catch (_) {}
}

initLLMToggle();

function logout() {
  localStorage.removeItem('token');
  localStorage.removeItem('username');
  window.location.href = '/';
}

// ── Query input helpers ───────────────────────────────────────
const queryInput = document.getElementById('queryInput');
const sendBtn = document.getElementById('sendBtn');

queryInput.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendQuery();
  }
});

// Auto-resize textarea
queryInput.addEventListener('input', () => {
  queryInput.style.height = 'auto';
  queryInput.style.height = Math.min(queryInput.scrollHeight, 120) + 'px';
});

function fillQuery(btn) {
  queryInput.value = btn.textContent;
  queryInput.focus();
}

// ── Main query function ───────────────────────────────────────
async function sendQuery() {
  const query = queryInput.value.trim();
  if (!query) return;

  const maxArticles = parseInt(document.getElementById('maxArticles').value);
  const topK = parseInt(document.getElementById('topK').value);

  // Hide empty state
  document.getElementById('emptyState')?.remove();

  // Append user message
  appendUserMessage(query);

  // Clear input
  queryInput.value = '';
  queryInput.style.height = 'auto';
  sendBtn.disabled = true;

  // Show loading
  const loadingEl = appendLoading();

  try {
    const res = await fetch('/api/query', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ query, max_articles: maxArticles, top_k: topK }),
    });

    if (res.status === 401) {
      logout();
      return;
    }

    const data = await res.json();
    loadingEl.remove();

    if (!res.ok) {
      appendError(data.detail || 'Query failed');
      return;
    }

    appendAssistantMessage(data);
  } catch (err) {
    loadingEl.remove();
    appendError('Network error: ' + err.message);
  } finally {
    sendBtn.disabled = false;
    queryInput.focus();
  }
}

// ── DOM helpers ───────────────────────────────────────────────
const chatBody = document.getElementById('chatBody');

function scrollBottom() {
  chatBody.scrollTop = chatBody.scrollHeight;
}

function appendUserMessage(text) {
  const wrap = document.createElement('div');
  wrap.className = 'message-wrap';
  wrap.innerHTML = `
    <div class="msg-user">
      <div class="bubble">${escapeHtml(text)}</div>
      <div class="msg-meta">${formatTime(new Date())}</div>
    </div>`;
  chatBody.appendChild(wrap);
  scrollBottom();
}

function appendLoading() {
  const wrap = document.createElement('div');
  wrap.className = 'message-wrap';
  wrap.innerHTML = `
    <div class="msg-assistant">
      <div class="loading-wrap">
        <div class="spinner"></div>
        <div class="loading-text">Searching PubMed &amp; ranking results <span>...</span></div>
      </div>
    </div>`;
  chatBody.appendChild(wrap);
  scrollBottom();
  return wrap;
}

function appendError(msg) {
  const wrap = document.createElement('div');
  wrap.className = 'message-wrap';
  wrap.innerHTML = `
    <div class="msg-assistant">
      <div class="bubble" style="background:#fef2f2;border-color:#fca5a5;color:#dc2626;">
        ⚠️ ${escapeHtml(msg)}
      </div>
    </div>`;
  chatBody.appendChild(wrap);
  scrollBottom();
}

function appendAssistantMessage(data) {
  if ((data.answer || '').trim() === 'Failed to generate answer via Anthropic. Please retry.') {
    return;
  }

  const prBadge = data.pagerank_method === 'citation'
    ? `<span class="pr-badge citation">📊 Citation PageRank</span>`
    : `<span class="pr-badge similarity">📐 Similarity PageRank</span>`;

  const sourcesHtml = buildSourcesHtml(data.sources, data.pagerank_method);

  const wrap = document.createElement('div');
  wrap.className = 'message-wrap';
  wrap.innerHTML = `
    <div class="msg-assistant">
      <div class="bubble">${formatAnswer(data.answer)}</div>
      <div class="msg-meta">
        ${prBadge}
        &nbsp;·&nbsp; ${data.total_fetched} articles fetched · ${data.sources.length} used as context
        &nbsp;·&nbsp; ${formatTime(new Date())}
      </div>
      ${sourcesHtml}
    </div>`;

  chatBody.appendChild(wrap);
  scrollBottom();
}

// ── Sources HTML ──────────────────────────────────────────────
function buildSourcesHtml(sources, prMethod) {
  if (!sources || sources.length === 0) return '';

  const cards = sources.map((s, i) => {
    const authorsStr = s.authors.length > 0
      ? s.authors.slice(0, 3).join(', ') + (s.authors.length > 3 ? ' et al.' : '')
      : 'N/A';

    const scores = s.scores || {};
    const finalPct  = Math.round((scores.final    || 0) * 100);
    const prPct     = Math.round((scores.pagerank  || 0) * 100);
    const simPct    = Math.round((scores.similarity || 0) * 100);

    const abstractId = `abs-${Date.now()}-${i}`;

    return `
      <div class="source-card">
        <div style="display:flex;align-items:flex-start;gap:.5rem;">
          <span class="source-rank">${i + 1}</span>
          <div style="flex:1;min-width:0;">
            <a class="source-title" href="${escapeHtml(s.url)}" target="_blank" rel="noopener">
              ${escapeHtml(s.title || 'Untitled')}
            </a>
            <div class="source-meta">
              ${escapeHtml(authorsStr)} &nbsp;|&nbsp;
              <em>${escapeHtml(s.journal || 'N/A')}</em>
              ${s.year ? `(${s.year})` : ''}
              &nbsp;·&nbsp; PMID: <a href="${escapeHtml(s.url)}" target="_blank" style="color:var(--primary)">${s.pmid}</a>
            </div>
            <div class="score-bars">
              <div class="score-item">
                <div class="score-label"><span>Final Score</span><span>${finalPct}%</span></div>
                <div class="score-bar-bg"><div class="score-bar-fill final" style="width:${finalPct}%"></div></div>
              </div>
              <div class="score-item">
                <div class="score-label"><span>PageRank</span><span>${prPct}%</span></div>
                <div class="score-bar-bg"><div class="score-bar-fill pagerank" style="width:${prPct}%"></div></div>
              </div>
              <div class="score-item">
                <div class="score-label"><span>Similarity</span><span>${simPct}%</span></div>
                <div class="score-bar-bg"><div class="score-bar-fill sim" style="width:${simPct}%"></div></div>
              </div>
            </div>
            ${s.abstract_preview ? `
              <span class="source-abstract-toggle" onclick="toggleAbstract('${abstractId}', this)">
                ▶ Show abstract
              </span>
              <div class="source-abstract" id="${abstractId}">${escapeHtml(s.abstract_preview)}</div>
            ` : ''}
          </div>
        </div>
      </div>`;
  }).join('');

  return `
    <div class="sources-panel" style="margin-top:.75rem;">
      <div class="sources-header" onclick="toggleSources(this)">
        <h4>📚 Retrieved Sources (${sources.length})</h4>
        <span class="sources-toggle">▼ Hide</span>
      </div>
      <div class="sources-list">${cards}</div>
    </div>`;
}

function toggleSources(header) {
  const list = header.nextElementSibling;
  const toggle = header.querySelector('.sources-toggle');
  if (list.style.display === 'none') {
    list.style.display = 'flex';
    toggle.textContent = '▼ Hide';
  } else {
    list.style.display = 'none';
    toggle.textContent = '▶ Show';
  }
}

function toggleAbstract(id, btn) {
  const el = document.getElementById(id);
  if (el.classList.contains('open')) {
    el.classList.remove('open');
    btn.textContent = '▶ Show abstract';
  } else {
    el.classList.add('open');
    btn.textContent = '▲ Hide abstract';
  }
}

// ── Utilities ─────────────────────────────────────────────────
function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function formatTime(d) {
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}

function formatAnswer(text) {
  if (!text) return '';
  // Bold [1], [2]... citation markers
  return escapeHtml(text)
    .replace(/\[(\d+)\]/g, '<strong style="color:var(--primary)">[<sup>$1</sup>]</strong>')
    .replace(/\n/g, '<br>');
}
