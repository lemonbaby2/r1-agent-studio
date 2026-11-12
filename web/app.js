/* ============================================================
   DGX R1 Agent Studio · 前端逻辑
   ============================================================ */
const $ = (id) => document.getElementById(id);
const S = {
  cfg: {}, models: [], route: {}, sessions: [],
  sid: null, busy: false, stop: false, showThink: true, agentMode: true,
  cwd: '', images: [], fsPath: '',
};

/* ---------------- 工具函数 ---------------- */
function esc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
function toast(msg) {
  const t = $('toast'); t.textContent = msg; t.classList.add('show');
  clearTimeout(t._t); t._t = setTimeout(() => t.classList.remove('show'), 2200);
}
function nowStr() {
  const d = new Date();
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}
function human(n) {
  n = Number(n || 0);
  const u = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (n >= 1024 && i < u.length - 1) { n /= 1024; i++; }
  return (i === 0 ? n : n.toFixed(1)) + u[i];
}
function uid() { return 's' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

async function api(path, opt) {
  const r = await fetch(path, opt);
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}

/* ---------------- 极简 Markdown ---------------- */
function md(src) {
  let s = String(src || '');
  const blocks = [];
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, (m, lang, code) => {
    blocks.push(`<pre><code>${esc(code.replace(/\n$/, ''))}</code></pre>`);
    return `\u0000B${blocks.length - 1}\u0000`;
  });
  const lines = s.split('\n');
  let out = [], inList = null, inTable = false;

  const closeList = () => { if (inList) { out.push(`</${inList}>`); inList = null; } };

  for (let raw of lines) {
    const line = raw.replace(/\s+$/, '');
    if (/^\u0000B\d+\u0000$/.test(line.trim())) {
      closeList(); out.push(line.trim()); continue;
    }
    if (!line.trim()) { closeList(); out.push(''); continue; }
    let m;
    if ((m = line.match(/^(#{1,4})\s+(.*)$/))) {
      closeList(); const n = m[1].length;
      out.push(`<h${n}>${inline(m[2])}</h${n}>`); continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(line.trim())) { closeList(); out.push('<hr>'); continue; }
    if ((m = line.match(/^>\s?(.*)$/))) { closeList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`); continue; }
    if ((m = line.match(/^\s*[-*+]\s+(.*)$/))) {
      if (inList !== 'ul') { closeList(); out.push('<ul>'); inList = 'ul'; }
      out.push(`<li>${inline(m[1])}</li>`); continue;
    }
    if ((m = line.match(/^\s*(\d+)[.)]\s+(.*)$/))) {
      if (inList !== 'ol') { closeList(); out.push('<ol>'); inList = 'ol'; }
      out.push(`<li>${inline(m[2])}</li>`); continue;
    }
    if (line.includes('|') && line.trim().startsWith('|')) {
      closeList();
      const cells = line.trim().replace(/^\||\|$/g, '').split('|').map(x => x.trim());
      if (/^[:\-\s|]+$/.test(line.trim().replace(/\|/g, ''))) continue;
      if (!inTable) { out.push('<table>'); inTable = true; out.push('<tr>' + cells.map(c => `<th>${inline(c)}</th>`).join('') + '</tr>'); }
      else out.push('<tr>' + cells.map(c => `<td>${inline(c)}</td>`).join('') + '</tr>');
      continue;
    }
    if (inTable) { out.push('</table>'); inTable = false; }
    closeList();
    out.push(`<p>${inline(line)}</p>`);
  }
  closeList();
  if (inTable) out.push('</table>');
  let html = out.join('\n');
  html = html.replace(/\u0000B(\d+)\u0000/g, (m, i) => blocks[+i]);
  return html;
}
function inline(t) {
  return esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\)]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
}

/* ---------------- 会话 ---------------- */
function currentSession() {
  return S.sessions.find(x => x.id === S.sid) || null;
}
function ensureSession(title) {
  let s = currentSession();
  if (s) return s;
  s = { id: uid(), title: title || '新对话', at: nowStr(), messages: [] };
  S.sessions.unshift(s);
  S.sid = s.id;
  renderSessions(); saveSessions();
  return s;
}
function saveSessions() {
  clearTimeout(saveSessions._t);
  saveSessions._t = setTimeout(() => {
    api('/api/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ sessions: S.sessions.slice(0, 60) }),
    }).catch(() => {});
  }, 400);
}
function renderSessions() {
  const box = $('sess-list');
  $('sess-count').textContent = S.sessions.length;
  if (!S.sessions.length) { box.innerHTML = '<div class="sess-empty">还没有对话</div>'; return; }
  box.innerHTML = S.sessions.map(s => `
    <div class="sess-item ${s.id === S.sid ? 'active' : ''}" data-id="${s.id}">
      <div class="sess-title">${esc(s.title || '新对话')}</div>
      <div class="sess-meta">${esc(s.at || '')} · ${(s.messages || []).length} 条</div>
    </div>`).join('');
  box.querySelectorAll('.sess-item').forEach(el => {
    el.onclick = () => { S.sid = el.dataset.id; renderSessions(); renderChat(); };
  });
}

/* ---------------- 消息渲染 ---------------- */
function thinkNode(text, live) {
  return `<div class="think ${S.showThink ? '' : 'closed'}${live ? ' think-live' : ''}">
    <div class="think-head"><span class="caret">▼</span>
      <span class="th-label">${live ? '正在思考…' : '已思考 · ' + String(text || '').length + ' 字'}</span></div>
    <div class="think-body">${esc(text || '')}</div>
  </div>`;
}
function stepNode(st) {
  const okCls = st.ok === undefined ? 'req' : (st.ok ? 'ok' : 'fail');
  const badge = st.ok === undefined ? '<span class="step-badge run">执行中</span>'
    : (st.ok ? '<span class="step-badge ok">成功</span>' : '<span class="step-badge fail">失败</span>');
  const argStr = argPreview(st.args);
  return `<div class="step ${okCls}">
    <div class="step-head">
      <span class="step-idx">${st.step || ''}</span>
      <span class="step-name">${esc(st.name)}</span>
      ${argStr ? `<span class="step-arg mono">${esc(argStr)}</span>` : ''}
      <span class="step-right">${st.ms != null ? `<span>${st.ms}ms</span>` : ''}${badge}
        <span class="step-caret">▼</span></span>
    </div>
    <div class="step-body">
      ${st.plan ? `<div class="step-plan">${esc(st.plan)}</div>` : ''}
      <pre>${esc(st.output || (st.ok === undefined ? '正在执行…' : '(无输出)'))}</pre>
    </div>
  </div>`;
}
function argPreview(args) {
  if (!args || typeof args !== 'object') return '';
  const k = Object.keys(args)[0];
  if (!k) return '';
  let v = args[k];
  if (typeof v === 'string' && v.length > 90) v = v.slice(0, 90) + '…';
  return v;
}
function traceNode(steps, ms) {
  if (!steps || !steps.length) return '';
  return `<div class="trace">
    <div class="trace-head"><span class="caret">▼</span>
      <span>执行轨迹 · ${steps.length} 步</span>
      <span class="trace-meta">${ms ? (ms / 1000).toFixed(1) + 's' : ''}</span></div>
    <div class="trace-body">${steps.map(stepNode).join('')}</div>
  </div>`;
}
function aiNode(m) {
  return `<div class="msg ai">
    <div class="av ai">R1</div>
    <div class="bubble">
      <div class="ai-head"><b>DGX R1</b>
        ${m.model ? `<span class="chip">${esc(m.model)}</span>` : ''}
        ${m.ms ? `<span class="chip">${(m.ms / 1000).toFixed(1)}s</span>` : ''}
        ${m.route ? `<span class="chip">${esc(m.route)}</span>` : ''}
      </div>
      ${m.think ? thinkNode(m.think, false) : ''}
      ${traceNode(m.steps, m.ms)}
      <div class="answer">${md(m.content || '')}</div>
    </div>
  </div>`;
}
function renderChat() {
  const chat = $('chat');
  const s = currentSession();
  const msgs = (s && s.messages) || [];
  $('empty').hidden = msgs.length > 0 || S.busy;
  chat.innerHTML = msgs.map(m => {
    if (m.role === 'user') {
      return `<div class="msg user"><div class="av me">我</div>
        <div class="bubble">${esc(m.content)}</div></div>`;
    }
    return aiNode(m);
  }).join('');
  bindCollapse(chat);
  if (!S.busy) tlFromHistory();
  // 打开历史会话时停在顶部：先看到"已思考 / 执行轨迹"，而不是直接滑到答案
  if (S.busy) scrollBottom(true);
  else $('chat-wrap').scrollTop = 0;
}
function bindCollapse(root) {
  root.querySelectorAll('.think-head').forEach(h => {
    h.onclick = () => h.parentElement.classList.toggle('closed');
  });
  root.querySelectorAll('.trace-head').forEach(h => {
    h.onclick = () => h.parentElement.classList.toggle('closed');
  });
  root.querySelectorAll('.step-head').forEach(h => {
    h.onclick = () => h.parentElement.classList.toggle('closed');
  });
}
function scrollBottom(force) {
  const w = $('chat-wrap');
  if (force || w.scrollHeight - w.scrollTop - w.clientHeight < 260) w.scrollTop = w.scrollHeight;
}

/* ---------------- 右侧轨迹面板 ---------------- */
function tlClear() {
  $('tl').innerHTML = '<div class="tl-empty">本轮的执行步骤会实时出现在这里。</div>';
}
function tlAdd(st) {
  const box = $('tl');
  if (box.querySelector('.tl-empty')) box.innerHTML = '';
  const el = document.createElement('div');
  el.className = 'tl-item run';
  el.id = 'tl-' + st.step;
  el.innerHTML = `<div class="tl-name">#${st.step} ${esc(st.name)}</div>
    <div class="tl-arg mono">${esc(argPreview(st.args) || '')}</div>
    <div class="tl-meta">执行中…</div>`;
  box.appendChild(el);
}
function tlDone(st) {
  let el = document.getElementById('tl-' + st.step);
  if (!el) { tlAdd(st); el = document.getElementById('tl-' + st.step); }
  el.className = 'tl-item ' + (st.ok ? 'ok' : 'fail');
  el.innerHTML = `<div class="tl-name">#${st.step} ${esc(st.name)}
      <span style="float:right;font-weight:400;font-size:10.5px;color:${st.ok ? '#0f9d58' : '#dc2626'}">
      ${st.ok ? '成功' : '失败'} · ${st.ms ?? 0}ms</span></div>
    <div class="tl-arg mono">${esc(argPreview(st.args) || '')}</div>
    <div class="tl-meta">${esc(st.summary || '')}</div>
    <div class="tl-sum">${esc((st.output || '').slice(0, 1200))}</div>`;
  $('tl').scrollTop = $('tl').scrollHeight;
}
/* 打开历史会话时，把已发生的步骤回放到右侧时间线，
   否则右侧永远显示"还没有执行记录"，看不到当时的代码过程。 */
function tlFromHistory() {
  const s = currentSession();
  const msgs = (s && s.messages) || [];
  let last = null;
  for (let i = msgs.length - 1; i >= 0; i--) {
    if (msgs[i].role === 'assistant') { last = msgs[i]; break; }
  }
  if (!last || !last.steps || !last.steps.length) { tlClear(); return; }
  $('tl').innerHTML = '';
  last.steps.forEach(st => tlDone(st));
}

/* ---------------- 发送 ---------------- */
async function send() {
  if (S.busy) return;
  const input = $('input');
  const text = input.value.trim();
  if (!text && !S.images.length) return;

  const s = ensureSession(text.slice(0, 24));
  if (s.messages.length === 0) s.title = text.slice(0, 26) || '新对话';
  s.messages.push({ role: 'user', content: text || '（图片）' });
  s.at = nowStr();
  renderSessions();
  input.value = ''; input.style.height = 'auto';
  setBusy(true);
  tlClear();

  const history = s.messages.filter(m => m.role === 'user' || m.role === 'assistant')
    .map(m => ({ role: m.role, content: m.content }));
  const images = S.images.slice();
  S.images = []; renderChips();

  // ---- 实时节点 ----
  $('empty').hidden = true;
  const live = document.createElement('div');
  live.className = 'msg ai';
  live.innerHTML = `<div class="av ai">R1</div><div class="bubble">
    <div class="ai-head"><b>DGX R1</b><span class="chip" id="live-model">${esc($('sel-model').value || '')}</span>
      <span class="chip" id="live-ms">运行中</span><span class="chip" id="live-route"></span></div>
    <div class="think think-live" id="live-think" hidden>
      <div class="think-head"><span class="caret">▼</span><span>正在思考…</span></div>
      <div class="think-body" id="live-think-body"></div></div>
    <div class="trace" id="live-trace" hidden>
      <div class="trace-head"><span class="caret">▼</span><span id="live-trace-t">执行轨迹</span>
        <span class="trace-meta" id="live-trace-m"></span></div>
      <div class="trace-body" id="live-trace-body"></div></div>
    <div class="answer" id="live-answer"><span class="typing"><i></i><i></i><i></i></span></div>
  </div>`;
  $('chat').appendChild(live);
  bindCollapse(live);
  scrollBottom(true);

  const elThink = $('live-think'), elThinkBody = $('live-think-body');
  const elTrace = $('live-trace'), elTraceBody = $('live-trace-body');
  const elAnswer = $('live-answer');
  const acc = { think: '', answer: '', steps: [], planOf: null };
  let dirty = false;
  const flusher = setInterval(() => {
    if (!dirty) return;
    dirty = false;
    if (acc.think) elThinkBody.textContent = acc.think;
    if (acc.answer) elAnswer.innerHTML = md(acc.answer);
    scrollBottom(false);
  }, 90);

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        model: $('sel-model').value, messages: history,
        temperature: S.cfg.temperature, max_steps: S.cfg.max_steps,
        cwd: S.cwd, agent: S.agentMode, images,
      }),
    });
    if (!resp.ok) {
      let msg = 'HTTP ' + resp.status;
      try { const j = await resp.json(); if (j.error) msg = j.error; } catch (e) {}
      throw new Error(msg);
    }
    const reader = resp.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const chunk = buf.slice(0, idx); buf = buf.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          let d; try { d = JSON.parse(line.slice(6)); } catch (e) { continue; }
          handle(d);
        }
      }
    }
  } catch (e) {
    acc.answer += `\n\n> ⚠️ ${e.message}`;
    elAnswer.innerHTML = md(acc.answer);
  } finally {
    clearInterval(flusher);
    finish();
  }

  function handle(d) {
    switch (d.type) {
      case 'route':
        $('live-route').textContent = d.label || '';
        $('brand-sub').textContent = (d.label || '') + ' · ' + (d.ms != null ? d.ms + 'ms' : '');
        break;
      case 'step':
        $('status-text').textContent = `第 ${d.n} 轮 · 模型正在推理…`;
        $('live-trace-t').textContent = `执行轨迹 · 第 ${d.n} 轮`;
        break;
      case 'think':
        acc.think += d.text;
        elThink.hidden = false;
        dirty = true;
        break;
      case 'plan':
        $('status-text').textContent = '模型正在规划…';
        break;
      case 'plan_done':
        acc.planOf = (d.text || '').trim();
        break;
      case 'status':
        $('status-text').textContent = d.text || '';
        break;
      case 'tool_start': {
        const st = { step: d.step, name: d.name, args: d.args, plan: (acc.planOf || '').trim() };
        acc.planOf = null;
        acc.steps.push(st);
        elTrace.hidden = false;
        elTraceBody.insertAdjacentHTML('beforeend', stepNode(st));
        bindCollapse(elTraceBody);
        tlAdd(st);
        $('status-text').textContent = `🛠 正在执行 ${d.name} …`;
        scrollBottom(false);
        break;
      }
      case 'tool_result': {
        const st = acc.steps.filter(x => x.name === d.name && x.ok === undefined).pop();
        if (st) {
          st.ok = d.ok; st.summary = d.summary; st.output = d.output; st.ms = d.ms;
          const node = elTraceBody.querySelectorAll('.step')[acc.steps.indexOf(st)];
          if (node) { node.outerHTML = stepNode(st); bindCollapse(elTraceBody); }
          tlDone(st);
        }
        $('status-text').textContent = d.ok ? `✓ ${d.name} 完成` : `✗ ${d.name} 失败`;
        break;
      }
      case 'delta':
        acc.answer += d.text;
        dirty = true;
        break;
      case 'error':
        acc.answer += `\n\n> ⚠️ ${d.message}`;
        dirty = true;
        break;
      case 'end':
        if (!acc.answer && d.answer) acc.answer = d.answer;
        if (d.think && !acc.think) acc.think = d.think;
        if (d.steps && d.steps.length) acc.steps = d.steps;
        if (d.ms) $('live-ms').textContent = (d.ms / 1000).toFixed(1) + 's';
        dirty = true;
        break;
      default: break;
    }
  }

  function finish() {
    const msg = {
      role: 'assistant', content: acc.answer || '（无输出）',
      think: acc.think, steps: acc.steps,
      model: $('sel-model').value,
      ms: acc.steps.reduce((a, b) => a + (b.ms || 0), 0),
      route: $('brand-sub').textContent.split(' · ')[0],
    };
    s.messages.push(msg);
    s.at = nowStr();
    saveSessions();
    renderChat();
    setBusy(false);
    $('status-text').textContent = '就绪';
  }
}

function setBusy(v) {
  S.busy = v;
  $('btn-send').disabled = v;
  $('btn-stop').hidden = !v;
  $('send-hint').textContent = v ? '执行中…' : 'Enter 发送';
  if (v) $('status-text').textContent = '模型正在思考…';
}

/* ---------------- 图片 / 目录 ---------------- */
function renderChips() {
  $('ref-chips').innerHTML = S.images.map((im, i) =>
    `<span class="refchip"><b>图片 ${i + 1}</b><button data-i="${i}">✕</button></span>`).join('')
    + (S.cwd ? `<span class="refchip">📁 <b>${esc(S.cwd)}</b></span>` : '');
  $('ref-chips').querySelectorAll('button').forEach(b => {
    b.onclick = () => { S.images.splice(+b.dataset.i, 1); renderChips(); };
  });
}

/* ---------------- 文件面板 ---------------- */
async function fsLoad(path) {
  try {
    const d = await api('/api/fs?path=' + encodeURIComponent(path || S.cwd));
    if (!d.ok) { toast(d.error || '打不开'); return; }
    S.fsPath = d.path;
    $('fs-path').innerHTML = `<span>${esc(d.path)}</span>
      ${d.parent ? '<button id="fs-up">↑ 上级</button>' : ''}
      <button id="fs-use">设为工作目录</button>`;
    if ($('fs-up')) $('fs-up').onclick = () => fsLoad(d.parent);
    $('fs-use').onclick = async () => {
      S.cwd = d.path; renderChips(); updateCwdText();
      await saveCfg({ cwd: d.path }); toast('工作目录已设为 ' + d.path);
    };
    const kw = ($('ws-search').value || '').toLowerCase();
    const items = d.items.filter(x => !kw || x.name.toLowerCase().includes(kw));
    $('fs-list').innerHTML = items.map(x => `
      <div class="fs-item" data-name="${esc(x.name)}" data-dir="${x.dir ? 1 : 0}">
        <span class="ico">${x.dir ? '📁' : fileIcon(x.name)}</span>
        <span class="nm" title="${esc(x.name)}">${esc(x.name)}</span>
        <span class="sz">${x.dir ? '' : human(x.size)}</span>
      </div>`).join('') || '<div class="sess-empty">空目录</div>';
    $('fs-list').querySelectorAll('.fs-item').forEach(el => {
      el.onclick = () => {
        const p = joinPath(d.path, el.dataset.name);
        if (el.dataset.dir === '1') fsLoad(p); else pvLoad(p);
      };
    });
  } catch (e) { toast('读取失败：' + e.message); }
}
function joinPath(a, b) { return a.replace(/[\\/]+$/, '') + '\\' + b; }
function fileIcon(n) {
  const e = (n.split('.').pop() || '').toLowerCase();
  if (['py', 'js', 'ts', 'json', 'md', 'txt', 'yml', 'yaml', 'css', 'html'].includes(e)) return '📄';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'ico'].includes(e)) return '🖼';
  if (['mp4', 'avi', 'mov', 'mkv'].includes(e)) return '🎬';
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(e)) return '📦';
  return '📃';
}
async function pvLoad(path) {
  switchTab('preview');
  $('pv-head').textContent = path;
  $('pv-body').innerHTML = '<pre style="color:#9ca3af">读取中…</pre>';
  try {
    const d = await api('/api/file?path=' + encodeURIComponent(path));
    if (!d.ok) { $('pv-body').innerHTML = `<pre style="color:#dc2626">${esc(d.error)}</pre>`; return; }
    const lines = d.text.split('\n');
    $('pv-body').innerHTML = '<pre>' + lines.map((l, i) =>
      `<span style="color:#aab2c4">${String(i + 1).padStart(4)}</span>  ${esc(l)}`).join('\n') + '</pre>';
  } catch (e) {
    $('pv-body').innerHTML = `<pre style="color:#dc2626">${esc(e.message)}</pre>`;
  }
}
function switchTab(name) {
  document.querySelectorAll('.ptab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.ptab-body').forEach(b => { b.hidden = b.dataset.tab !== name; });
  $('panel').classList.remove('hide');
  $('btn-panel').classList.add('on');
}

/* ---------------- 模型 / 状态 ---------------- */
function renderModels(models, current) {
  const sel = $('sel-model');
  sel.innerHTML = models.map(m =>
    `<option value="${esc(m.name)}">${esc(m.name)}${m.tags && m.tags.length ? ' · ' + m.tags.join('/') : ''}</option>`).join('');
  if (current && models.some(m => m.name === current)) sel.value = current;
  else if (models.length) sel.value = models[0].name;
  updateTags();
}
function updateTags() {
  const m = S.models.find(x => x.name === $('sel-model').value);
  $('model-tags').innerHTML = m && m.tags
    ? m.tags.map(t => `<span class="mtag ${t === '视觉' ? 'v' : t === '推理' ? 'r' : ''}">${esc(t)}</span>`).join('')
    : '';
}
function updateCwdText() { $('cwd-text').textContent = S.cwd ? '📁 ' + S.cwd : ''; }

async function boot() {
  try {
    const st = await api('/api/state');
    S.cfg = st.config || {};
    S.models = st.models || [];
    S.route = st.route || {};
    S.showThink = S.cfg.show_think !== false;
    S.agentMode = S.cfg.agent !== false;
    S.cwd = S.cfg.cwd || '';
    $('ver-text').textContent = 'v' + (st.version || '1.1.0') + ' · ' + (st.host || '');
    $('brand-sub').textContent = (S.route.label || '未连接') +
      (S.route.ms != null ? ' · ' + S.route.ms + 'ms' : '');
    $('conn-dot').className = 'dot ' + (S.route.base ? 'on' : 'bad');
    $('conn-text').textContent = S.route.base
      ? `${S.route.label} · ${S.route.base.replace(/^https?:\/\//, '')}`
      : '未找到可用链路';
    renderModels(S.models, S.cfg.model);
    updateCwdText();
    renderChips();

    $('btn-agent').classList.toggle('on', S.agentMode);
    $('btn-think').classList.toggle('on', S.showThink);

    const ses = await api('/api/sessions');
    S.sessions = ses.sessions || [];
    if (S.sessions.length) S.sid = S.sessions[0].id;
    renderSessions(); renderChat();
    if (!S.sessions.length) $('empty').hidden = false;

    fillCfg();
  } catch (e) {
    $('conn-text').textContent = '初始化失败：' + e.message;
    $('conn-dot').className = 'dot bad';
  }
}

function fillCfg() {
  $('cfg-base').value = S.cfg.base || '';
  $('cfg-model').innerHTML = S.models.map(m => `<option>${esc(m.name)}</option>`).join('');
  if (S.models.some(m => m.name === S.cfg.model)) $('cfg-model').value = S.cfg.model;
  $('cfg-temp').value = S.cfg.temperature ?? 0.6;
  $('cfg-temp-v').textContent = (+$('cfg-temp').value).toFixed(2);
  $('cfg-steps').value = S.cfg.max_steps ?? 8;
  $('cfg-steps-v').textContent = $('cfg-steps').value;
  $('cfg-note').value = S.cfg.system_note || '';
}

async function saveCfg(patch) {
  const body = Object.assign({}, S.cfg, patch);
  try {
    const r = await api('/api/config', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    S.cfg = r.config || S.cfg;
  } catch (e) { toast('保存失败：' + e.message); }
}

/* ---------------- 事件绑定 ---------------- */
function bind() {
  const input = $('input');
  input.addEventListener('input', () => {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 210) + 'px';
  });
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  $('btn-send').onclick = send;
  $('btn-stop').onclick = async () => {
    S.stop = true;
    await fetch('/api/stop', { method: 'POST' });
    toast('已请求停止');
  };
  $('btn-new').onclick = () => {
    S.sid = null; S.cwd = S.cfg.cwd || S.cwd;
    renderChat(); renderSessions();
    $('empty').hidden = false;
  };
  $('btn-clear').onclick = () => {
    const s = currentSession();
    if (!s) return;
    s.messages = []; renderChat(); renderSessions(); saveSessions();
  };
  $('btn-agent').onclick = () => {
    S.agentMode = !S.agentMode;
    $('btn-agent').classList.toggle('on', S.agentMode);
    saveCfg({ agent: S.agentMode });
    toast(S.agentMode ? '自主执行：开（会真的跑命令/读写文件）' : '自主执行：关（只问答）');
  };
  $('btn-think').onclick = () => {
    S.showThink = !S.showThink;
    $('btn-think').classList.toggle('on', S.showThink);
    saveCfg({ show_think: S.showThink });
    document.querySelectorAll('.think').forEach(t => t.classList.toggle('closed', !S.showThink));
  };
  $('btn-panel').onclick = () => {
    $('panel').classList.toggle('hide');
    $('btn-panel').classList.toggle('on', !$('panel').classList.contains('hide'));
  };
  $('panel-close').onclick = () => {
    $('panel').classList.add('hide'); $('btn-panel').classList.remove('on');
  };
  document.querySelectorAll('.ptab').forEach(t => { t.onclick = () => switchTab(t.dataset.tab); });
  $('nav-files').onclick = () => { switchTab('tree'); fsLoad(S.fsPath || S.cwd); };
  $('nav-trace').onclick = () => switchTab('trace');
  $('nav-selftest').onclick = openSelftest;
  $('nav-cfg').onclick = () => { fillCfg(); $('modal-cfg').hidden = false; };
  $('nav').querySelector('[data-view=chat]').onclick = () => switchTab('trace');

  $('sel-model').onchange = () => { updateTags(); saveCfg({ model: $('sel-model').value }); };

  $('btn-folder').onclick = async () => {
    if (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder) {
      const p = await window.pywebview.api.pick_folder();
      if (p) { S.cwd = p; renderChips(); updateCwdText(); fsLoad(p); await saveCfg({ cwd: p }); }
    } else {
      switchTab('tree'); fsLoad(S.fsPath || S.cwd);
      toast('请在上方面板里选目录');
    }
  };
  $('btn-img').onclick = () => $('file-img').click();
  $('file-img').onchange = async (e) => {
    const fs = Array.from(e.target.files || []);
    for (const f of fs) {
      const b64 = await new Promise((res) => {
        const r = new FileReader();
        r.onload = () => res(r.result);
        r.readAsDataURL(f);
      });
      S.images.push(b64);
    }
    e.target.value = '';
    renderChips();
    if (S.images.length) toast('已附加 ' + S.images.length + ' 张图（切到视觉模型 qwen3-vl 才能识别）');
  };
  $('btn-cmd').onclick = () => {
    input.value = (input.value ? input.value + '\n' : '') +
      '在当前目录执行命令并解释输出：\n```\ndir\n```';
    input.focus();
  };
  $('ws-add').onclick = $('btn-folder').onclick;
  $('ws-search').oninput = () => fsLoad(S.fsPath || S.cwd);

  $('cfg-cancel').onclick = () => { $('modal-cfg').hidden = true; };
  $('cfg-save').onclick = async () => {
    await saveCfg({
      base: $('cfg-base').value.trim(),
      model: $('cfg-model').value,
      temperature: +$('cfg-temp').value,
      max_steps: +$('cfg-steps').value,
      system_note: $('cfg-note').value,
      show_think: S.showThink, agent: S.agentMode,
    });
    $('modal-cfg').hidden = true;
    toast('已保存');
    boot();
  };
  $('cfg-temp').oninput = () => { $('cfg-temp-v').textContent = (+$('cfg-temp').value).toFixed(2); };
  $('cfg-steps').oninput = () => { $('cfg-steps-v').textContent = $('cfg-steps').value; };

  $('st-close').onclick = () => { $('modal-st').hidden = true; };
  $('st-again').onclick = runSelftest;

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      $('modal-cfg').hidden = true; $('modal-st').hidden = true;
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'n') { e.preventDefault(); $('btn-new').click(); }
  });
}

async function openSelftest() {
  $('modal-st').hidden = false;
  runSelftest();
}
async function runSelftest() {
  $('st-list').innerHTML = '<div class="st-item"><span class="st-dot"></span><span class="k">探测中…</span></div>';
  $('st-sub').textContent = '并行探测所有链路 + 一次真实推理，大约 5~20 秒';
  try {
    const r = await api('/api/selftest', { method: 'POST' });
    $('st-list').innerHTML = r.items.map(it => `
      <div class="st-item"><span class="st-dot ${it.ok ? 'ok' : 'bad'}"></span>
        <span class="k">${esc(it.k)}</span><span class="v">${esc(it.v)}</span></div>`).join('');
    $('st-sub').textContent = r.ok ? '✓ 全部通过' : '有项目未通过，见下方明细';
  } catch (e) {
    $('st-list').innerHTML = `<div class="st-item"><span class="st-dot bad"></span>
      <span class="k">自检失败</span><span class="v">${esc(e.message)}</span></div>`;
  }
}

/* ---------------- 启动 ---------------- */
const SUGG = [
  ['看本机磁盘占用', '列出每个盘的剩余空间，找出最大的 5 个文件夹'],
  ['跑一段 Python', '算一下 1 到 1000 里所有质数的和，并把过程写成脚本执行'],
  ['查 DGX 状态', '用命令查看 DGX 上 ollama 有哪些模型、显卡占用多少'],
  ['读我的项目', './your-project 里有几个子目录？最近改动的文件是哪些？'],
];
$('sugg').innerHTML = SUGG.map((s, i) =>
  `<button class="sugg-card" data-i="${i}"><b>${esc(s[0])}</b><span>${esc(s[1])}</span></button>`).join('');

let _started = false;
function start() {
  if (_started) return;
  _started = true;
  bind();
  $('sugg').querySelectorAll('.sugg-card').forEach(b => {
    b.onclick = () => { $('input').value = SUGG[+b.dataset.i][1]; $('input').focus(); };
  });
  boot();
}

if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
else start();
