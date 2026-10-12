'use strict';
const $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const api = async (path, opts = {}) => {
  const o = { headers: { 'X-Kage': '1', 'Content-Type': 'application/json' }, ...opts };
  if (o.body && typeof o.body !== 'string') o.body = JSON.stringify(o.body);
  const r = await fetch(path, o); const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || r.statusText); return d;
};
let S = { workspace: null }, busy = false;

/* ---------- dithered art: a torii at dusk, 1-bit Bayer, drawn in the page ---------- */
function drawArt() {
  const c = $('#art'), W = c.width, H = c.height, g = c.getContext('2d'), img = g.createImageData(W, H);
  const bayer = [0, 32, 8, 40, 2, 34, 10, 42, 48, 16, 56, 24, 50, 18, 58, 26, 12, 44, 4, 36, 14, 46, 6, 38, 60, 28, 52, 20, 62, 30, 54, 22, 3, 35, 11, 43, 1, 33, 9, 41, 51, 19, 59, 27, 49, 17, 57, 25, 15, 47, 7, 39, 13, 45, 5, 37, 63, 31, 55, 23, 61, 29, 53, 21];
  const hz = H * .66, cx = W * .5, sx = cx, sy = H * .42, sr = H * .27;
  const torii = (x, y) => {
    const dx = Math.abs(x - cx); const top = H * .3, pillarW = 7 + (y - top) * .02;
    if (y > top + 10 && y < hz && Math.abs(dx - W * .17) < pillarW) return true;
    const sweep = Math.pow(dx / (W * .3), 2) * 9;
    if (y > top - sweep - 2 && y < top + 9 - sweep && dx < W * .31) return true;
    if (y > top + 12 && y < top + 17 && dx < W * .21) return true;
    if (y > top + 28 && y < top + 35 && dx < W * .205) return true;
    return false;
  };
  let seed = 7; const rnd = () => (seed = (seed * 16807) % 2147483647) / 2147483647;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    let L;
    const water = y > hz, yy = water ? 2 * hz - y : y, xx = water ? x + Math.sin(y * .9 + x * .05) * (2 + (y - hz) * .06) : x;
    const d = Math.hypot(xx - sx, yy - sy);
    L = .1 + .5 * (1 - Math.min(1, yy / hz)) * .6 + Math.max(0, 1 - d / sr) * .95 + Math.max(0, 1 - d / (sr * 2.6)) * .22;
    if (torii(xx, yy)) L = .03;
    if (water) { L *= .62; if (Math.sin(y * 1.7 + rnd() * 3) > .82) L *= .55 }
    if (!water && y > hz - 6) L = Math.min(L, .05 + (hz - y) * .01);
    L *= 1 - Math.pow(Math.hypot((x - cx) / W, (y - H * .5) / H) * 1.25, 2.4);
    L += (rnd() - .5) * .035;
    const on = L * 64 > bayer[(y & 7) * 8 + (x & 7)], i = (y * W + x) * 4;
    const [r, gg, b] = on ? [233, 228, 216] : [8, 8, 7];
    img.data[i] = r; img.data[i + 1] = gg; img.data[i + 2] = b; img.data[i + 3] = 255;
  }
  g.putImageData(img, 0, 0);
}

/* ---------- markdown (small, safe) ---------- */
const CITE = /(^|[\s(\[`>])((?:[\w.@+-]+\/)*[\w.@+-]+\.[A-Za-z]{1,6}):(\d+)(?:-(\d+))?/g;
const linkify = t => t.replace(CITE, (m, pre, p, a, b) => `${pre}<a class="cite" data-path="${p}" data-line="${a}">${p}:${a}${b ? '-' + b : ''}</a>`);
function inline(s) {
  const codes = []; s = s.replace(/`([^`\n]+)`/g, (m, c) => { codes.push(`<code>${linkify(c)}</code>`); return `\u0000${codes.length - 1}\u0000`; });
  s = linkify(esc(s));
  s = s.replace(/\*\*([^*\n]+)\*\*/g, '<b>$1</b>').replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<i>$2</i>');
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<)]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
  return s.replace(/\u0000(\d+)\u0000/g, (m, i) => codes[+i]);
}
function md(src) {
  const out = []; let list = null, para = [];
  const flush = () => { if (para.length) { out.push('<p>' + inline(para.join(' ')) + '</p>'); para = [] } };
  const closeList = () => { if (list) { out.push(`</${list}>`); list = null } };
  const isRow = l => /^\s*\|.*\|\s*$/.test(l);
  const isSep = l => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(c => c.trim());
  const parts = src.split(/(```[^\n]*\n[\s\S]*?(?:```|$))/g);
  for (const part of parts) {
    const f = part.match(/^```([^\n]*)\n([\s\S]*?)(?:```)?$/);
    if (f) { flush(); closeList(); out.push(`<pre><button class="copy">copy</button><code>${linkify(esc(f[2].replace(/\n$/, '')))}</code></pre>`); continue }
    const lines = part.split('\n');
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i]; let m;
      if (!line.trim()) { flush(); closeList(); continue }
      if (isRow(line) && i + 1 < lines.length && isSep(lines[i + 1])) {
        flush(); closeList();
        const head = cells(line), rows = []; i++;
        while (i + 1 < lines.length && isRow(lines[i + 1])) rows.push(cells(lines[++i]));
        out.push(`<div class="tbl"><table><thead><tr>${head.map(c => `<th>${inline(c)}</th>`).join('')}</tr></thead><tbody>${rows.map(r => `<tr>${head.map((_, j) => `<td>${inline(r[j] || '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div>`);
        continue;
      }
      if ((m = line.match(/^\s*(#{1,6})\s+(.*)/))) { flush(); closeList(); const n = m[1].length; out.push(n === 1 ? `<h2>${inline(m[2])}</h2>` : n === 2 ? `<h3>${inline(m[2])}</h3>` : `<h4>${inline(m[2])}</h4>`) }
      else if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) { flush(); closeList(); out.push('<hr>') }
      else if ((m = line.match(/^\s*>\s?(.*)/))) { flush(); closeList(); out.push(`<blockquote>${inline(m[1])}</blockquote>`) }
      else if ((m = line.match(/^\s*[-*]\s+(.*)/))) { flush(); if (list !== 'ul') { closeList(); out.push('<ul>'); list = 'ul' } out.push(`<li>${inline(m[1])}</li>`) }
      else if ((m = line.match(/^\s*\d+[.)]\s+(.*)/))) { flush(); if (list !== 'ol') { closeList(); out.push('<ol>'); list = 'ol' } out.push(`<li>${inline(m[1])}</li>`) }
      else { closeList(); para.push(line.trim()) }
    }
    flush(); closeList();
  }
  return out.join('');
}

/* ---------- state + header ---------- */
async function loadState() {
  try { S = await api('/api/state') } catch { return }
  $('#ver').textContent = 'v' + (S.version || '');
  $('#wsName').textContent = S.workspace_name || 'choose folder';
  $('#wsPill').title = S.workspace || 'Choose a workspace';
  const sel = $('#modelSel'), models = S.models.length ? S.models : [S.model];
  if (!models.includes(S.model)) models.unshift(S.model);
  if (sel.dataset.sig !== models.join() + S.model) { sel.innerHTML = models.map(m => `<option ${m === S.model ? 'selected' : ''}>${esc(m)}</option>`).join(''); sel.dataset.sig = models.join() + S.model }
  $('#dots').innerHTML = `<span title="Ollama"><i class="${S.ollama ? 'ok' : ''}"></i>ollama</span><span title="Web search: ${S.search.provider}"><i class="${S.search.ok ? 'ok' : ''}"></i>search</span>`;
  const ix = S.index || {}, ixt = ix.building ? 'indexing…' : ix.files != null ? `${ix.files.toLocaleString()} files · ${ix.symbols.toLocaleString()} symbols` : '—';
  $('#spec').innerHTML = [['Model', S.model], ['Workspace', S.workspace || 'not chosen yet'], ['Index', ixt], ['Search', S.search.ok ? S.search.provider : 'offline (run ./start.sh)'], ['Machine', S.machine || 'run /map to scan'], ['Vault', S.vault]]
    .map(([k, v]) => `<div><dt>${k}</dt><dd title="${esc(v)}">${esc(v)}</dd></div>`).join('');
  $('#ixStats').textContent = ix.files != null ? `${ixt} · ${ix.edges.toLocaleString()} links · ${ix.langs.map(l => l.join(' ')).slice(0, 4).join(', ')}` : ixt;
  const auto = $('#autoBtn'); auto.classList.toggle('on', !!S.auto_approve); $('b', auto).textContent = S.auto_approve ? 'ON' : 'OFF';
  document.body.classList.toggle('auto-on', !!S.auto_approve);
  $('#foot-mode').textContent = S.auto_approve ? 'auto-approve on · HIGH RISK and folder access still ask' : 'local · approvals ask first';
  const vit = [[ix.files != null ? ix.files.toLocaleString() : '—', 'files indexed'], [ix.symbols != null ? ix.symbols.toLocaleString() : '—', 'symbols'], [(S.skills || []).length, 'skills'], [S.search.ok ? 'online' : 'off', 'web search']];
  $('#vitals').innerHTML = vit.map(([v, l]) => `<div class="vital"><b>${esc(v)}</b><span>${esc(l)}</span></div>`).join('');
  if (S.index && S.index.building) setTimeout(loadState, 1200);
}
$('#autoBtn').onclick = async () => { try { await api('/api/settings', { method: 'POST', body: { auto_approve: !S.auto_approve } }) } catch { } loadState() };
$('#modelSel').onchange = async e => { await api('/api/model', { method: 'POST', body: { model: e.target.value } }); loadState() };
$$('#tabs button').forEach(b => b.onclick = () => showView(b.dataset.view));
function showView(v) {
  $$('#tabs button').forEach(b => b.classList.toggle('on', b.dataset.view === v)); $$('.view').forEach(x => x.classList.toggle('on', x.id === 'view-' + v));
  if (v === 'index') { if (!$('#ixResults').children.length) runIndex('overview', ''); $('#ixQ').focus() }
  if (v === 'vault') loadVault(); if (v === 'console') $('#input').focus(); if (v === 'terminal' && window.termShow) termShow(); if (v === 'activity') loadActivity();
}
const theme = localStorage.getItem('kage-theme'); if (theme) document.documentElement.dataset.theme = theme;
api('/api/theme').then(t => { if (t.accent) document.documentElement.style.setProperty('--accent', t.accent) }).catch(() => { });

/* ---------- chat ---------- */
const msgs = $('#messages'), input = $('#input'), scroller = $('#scroll');
const stick = () => { if (scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 160) scroller.scrollTop = scroller.scrollHeight };
const SUGG = ['/describe', '/brief', '/term', 'Where is the main entry point?', 'Check my GitHub repos and tell me what they do', 'Show all pictures on my computer', 'Zip this folder'];
$('#chips').innerHTML = SUGG.map(s => `<button class="chip">${esc(s)}</button>`).join('');
$$('.chip').forEach(c => c.onclick = () => send(c.textContent));
let turn = 0;
function addMsg(role, html, cls = '') {
  document.body.classList.add('chatting');
  if (role === 'user') turn++;
  const n = String(turn || 1).padStart(2, '0');
  const d = document.createElement('div'); d.className = `msg ${role} ${cls}`;
  d.innerHTML = `<div class="who"><b>${n}</b> // ${role === 'user' ? 'YOU' : 'KAGE 影'}</div><div class="body">${html}</div>`; msgs.append(d); stick(); return d;
}
const fmtArgs = a => { try { const v = Object.values(a || {}); return v.length ? v.map(x => typeof x === 'string' ? x : JSON.stringify(x)).join(', ').slice(0, 120) : '' } catch { return '' } };
async function send(text) {
  text = (text ?? input.value).trim(); if (!text || busy) return;
  if (!S.workspace) { openPicker(); return }
  busy = true; document.body.classList.add('busy'); input.value = ''; input.style.height = 'auto'; hideSlash(); hist.unshift(text); histI = -1;
  addMsg('user', esc(text)); const box = addMsg('assistant', '<span class="status">thinking…</span>'), body = $('.body', box);
  let answer = '', steps = {}, started = false, cur = body;
  const ensure = () => { if (!started) { body.innerHTML = ''; started = true } };
  try {
    const r = await fetch('/api/chat', { method: 'POST', headers: { 'X-Kage': '1', 'Content-Type': 'application/json' }, body: JSON.stringify({ message: text }) });
    if (!r.ok) { const d = await r.json().catch(() => ({})); throw new Error(d.error || r.statusText) }
    const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
    while (true) {
      const { value, done } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true });
      let i; while ((i = buf.indexOf('\n')) >= 0) { const line = buf.slice(0, i); buf = buf.slice(i + 1); if (line.trim()) handle(JSON.parse(line)) }
    }
  } catch (e) { ensure(); body.insertAdjacentHTML('beforeend', `<p>${esc(e.message)}</p>`); box.classList.add('err') }
  busy = false; document.body.classList.remove('busy'); input.focus(); loadState();
  function handle(ev) {
    if (ev.t === 'status') { if (!started) body.innerHTML = `<span class="status">${esc(ev.m)}</span>` }
    else if (ev.t === 'step') {
      ensure(); const d = document.createElement('details'); d.className = 'step run' + (ev.sub ? ' sub' : '');
      d.innerHTML = `<summary><span class="pill run">RUN</span><span class="tn">${esc(ev.tool)}</span><span class="ta">${esc(fmtArgs(ev.args))}</span><span class="tt"></span></summary><pre></pre>`;
      steps[ev.n + (ev.sub ? 's' : '')] = d; body.append(d); cur = null; stick();
    } else if (ev.t === 'result') {
      const d = steps[ev.n + (ev.sub ? 's' : '')]; if (!d) return; d.classList.remove('run'); d.classList.add(ev.ok === false ? 'bad' : 'ok'); $('.pill', d).textContent = ev.ok === false ? 'FAIL' : 'OK'; $('.pill', d).className = 'pill ' + (ev.ok === false ? 'bad' : 'ok');
      $('.tt', d).textContent = ev.ms + 'ms'; $('pre', d).innerHTML = linkify(esc(ev.text));
      if (['search_code', 'find_symbol', 'impact', 'wip', 'changed', 'overview', 'read_symbol'].includes(ev.tool) && ev.text.length < 1500) d.open = true;
    } else if (ev.t === 'token') {
      ensure(); if (!cur) { cur = document.createElement('div'); cur.className = 'ans cursor'; body.append(cur); answer = '' } answer += ev.d; cur.innerHTML = md(answer); stick();
    } else if (ev.t === 'retract') { if (cur) { cur.remove(); cur = null; answer = '' } }
    else if (ev.t === 'terminal') { ensure(); body.insertAdjacentHTML('beforeend', `<p><button class="chip" data-term="${esc(ev.id)}">Open terminal: ${esc(ev.title)}</button></p>`); cur = null; if (window.termOpen) termOpen(ev.id, ev.title) }
    else if (ev.t === 'images') {
      ensure(); const g = document.createElement('div'); g.className = 'imgs';
      g.innerHTML = ev.items.map(i => `<a class="im" data-full="${esc(i.url)}" data-name="${esc(i.name)}" data-path="${esc(i.path)}" title="${esc(i.path)}"><img loading="lazy" src="${esc(i.url)}&thumb=1" alt="${esc(i.name)}"><span>${esc(i.name)}</span></a>`).join('');
      body.append(g); cur = null; stick();
    }
    else if (ev.t === 'project') { ensure(); body.append(projectCard(ev.card)); cur = null; stick() }
    else if (ev.t === 'files') { ensure(); body.append(filesCard(ev)); cur = null; stick() }
    else if (ev.t === 'file') { ensure(); body.append(fileCard(ev)); cur = null; stick() }
    else if (ev.t === 'diff') { ensure(); body.append(diffCard(ev)); cur = null; stick() }
    else if (ev.t === 'auto') { ensure(); body.insertAdjacentHTML('beforeend', `<div class="autorow"><span class="pill ok">AUTO</span><span class="muted small">${esc(ev.m)}</span></div>`); cur = null; stick() }
    else if (ev.t === 'approval') approvalCard(ev, body);
    else if (ev.t === 'approval_done') { const a = $(`[data-aid="${ev.id}"]`); if (a) { a.classList.add('done'); $('.row', a).innerHTML = `<span class="muted small">${ev.ok ? 'approved' : 'denied'}</span>` } }
    else if (ev.t === 'done') {
      ensure(); if (cur) cur.classList.remove('cursor');
      if (ev.kind === 'cmd') { box.classList.add('cmd'); body.insertAdjacentHTML('beforeend', `<pre><code>${linkify(esc(ev.text))}</code></pre>`) }
      else { if (!cur || ev.error || ev.stopped) { cur = document.createElement('div'); cur.className = 'ans'; body.append(cur) } cur.innerHTML = md(ev.text) }
      if (ev.error) box.classList.add('err');
      const s = ev.stats || {}; $('#foot-stats').textContent = s.ms ? `${(s.ms / 1000).toFixed(1)}s${s.steps ? ' · ' + s.steps + ' tools' : ''}${s.tps ? ' · ' + s.tps + ' tok/s' : ''}` : '';
      stick();
    }
  }
}
/* ---------- project / file cards (v1.3) ---------- */
const nf = n => Number(n || 0).toLocaleString(), LCOL = ['#e2342a', '#d9a441', '#e9e4d8', '#7fbf6a', '#8c8777', '#5a5648'];
const hsz = n => n < 1024 ? n + ' B' : n < 1048576 ? (n / 1024).toFixed(1) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
function projectCard(c) {
  const d = document.createElement('div'), g = c.git || {}; d.className = 'pj';
  const tl = c.langs.reduce((a, l) => a + l.lines, 0) || 1;
  d.innerHTML = `<div class="pj-h"><div><div class="label">${esc(c.name)} // project</div><div class="pj-t">${esc(c.primary.join(' · ') || 'no code detected')}${c.stack.length ? ` <span class="muted">+ ${esc(c.stack.join(', '))}</span>` : ''}</div></div>
    <div class="pj-n"><b>${nf(c.files)}</b> files · <b>${nf(c.lines)}</b> lines<br><span class="muted">${c.ms} ms${c.truncated ? ' · partial scan' : ''}</span></div></div>
    ${c.intro ? `<p class="pj-i">${esc(c.intro)}</p>` : ''}
    ${g.branch ? `<div class="pj-g">⎇ ${esc(g.branch)}${g.dirty ? ` · ${g.dirty} uncommitted` : ' · clean'}${g.last_commit ? ' · ' + esc(g.last_commit) : ''}</div>` : ''}
    ${c.langs.length ? `<div class="pj-bar">${c.langs.map((l, i) => `<i style="flex:${Math.max(l.lines, tl * .01)};background:${LCOL[i % 6]}" title="${esc(l.name)} ${nf(l.lines)} lines"></i>`).join('')}</div>
    <div class="pj-lg">${c.langs.map((l, i) => `<span><i style="background:${LCOL[i % 6]}"></i>${esc(l.name)} ${Math.round(100 * l.lines / tl)}%</span>`).join('')}</div>` : ''}
    <div class="pj-cols">
      ${c.dirs.length ? `<div><div class="label">Folders</div>${c.dirs.map(x => `<a class="pj-r" data-files="${esc(x.name)}" title="list what each file in ${esc(x.name)}/ does"><b>${esc(x.name)}/</b><span class="muted">${nf(x.files)} files${x.lines ? ' · ' + nf(x.lines) + ' lines' : ''}</span><em>${esc(x.desc)}</em></a>`).join('')}</div>` : ''}
      ${c.key_files.length ? `<div><div class="label">Key files</div>${c.key_files.map(x => `<a class="pj-r" data-view="${esc(x.path)}" title="open ${esc(x.path)}"><b>${esc(x.path)}</b><span class="muted">${nf(x.lines)} lines</span><em>${esc(x.desc)}</em></a>`).join('')}</div>` : ''}
    </div>`;
  return d;
}
function filesCard(ev) {
  const d = document.createElement('div'), base = ev.base.split('/').slice(-2).join('/'); d.className = 'pj fl';
  d.innerHTML = `<div class="label">${esc(base)} // ${ev.rows.length}${ev.total > ev.rows.length ? ' of ' + nf(ev.total) : ''} entries</div>` + ev.rows.map(r => r.kind === 'dir'
    ? `<a class="pj-r" data-files="${esc(r.path.replace(/\/$/, ''))}"><b>▸ ${esc(r.path)}</b><span class="muted"></span><em>${esc(r.desc)}</em></a>`
    : `<a class="pj-r" data-view="${esc(r.path)}"><b>${esc(r.path)}</b><span class="muted">${r.lines ? nf(r.lines) + ' lines' : hsz(r.size)}</span><em>${esc(r.desc)}</em></a>`).join('');
  return d;
}
function fileCard(f) {
  const d = document.createElement('div'); d.className = 'fv';
  const code = f.text.split('\n').map((l, i) => `<span class="l" data-n="${f.start + i}">${esc(l) || ' '}</span>`).join('');
  d.innerHTML = `<div class="fv-h"><b>${esc(f.path)}</b><span class="muted">${f.lang ? esc(f.lang) + ' · ' : ''}lines ${nf(f.start)}-${nf(f.end)} of ${nf(f.total)} · ${hsz(f.size)}</span><button class="copy">copy</button></div>
    ${f.desc ? `<div class="fv-d">${esc(f.desc)}</div>` : ''}<pre><code>${code}</code></pre>
    ${f.more ? `<div class="fv-m"><a class="chip" data-view="${esc(f.path)}" data-from="${f.end + 1}">show more from line ${nf(f.end + 1)}</a></div>` : ''}`;
  return d;
}
function diffCard(v) {
  const d = document.createElement('div'); d.className = 'fv df';
  const lines = v.diff.split('\n').filter(l => !l.startsWith('---') && !l.startsWith('+++') && l !== '').map(l => `<span class="${l[0] === '+' ? 'add' : l[0] === '-' ? 'del' : l[0] === '@' ? 'hunk' : ''}">${esc(l)}</span>`).join('');
  d.innerHTML = `<div class="fv-h"><b>${esc(v.path)}</b><span class="add-n">+${v.added}</span><span class="del-n">−${v.removed}</span><span class="muted">edited · /undo reverts it</span></div><pre><code>${lines}</code></pre>`;
  return d;
}
function approvalCard(ev, body) {
  const d = document.createElement('div'); d.className = 'approval'; d.dataset.aid = ev.id;
  d.innerHTML = `<div class="label"><span class="kj">承認</span> Approval required <span class="muted">· Y approves · N or Esc denies</span></div><pre>${esc(ev.message)}</pre><div class="row"><button class="btn" data-y>Approve <kbd>Y</kbd></button><button class="btn ghost" data-n>Deny <kbd>N</kbd></button></div>`;
  const answer = ok => api('/api/approve', { method: 'POST', body: { id: ev.id, approved: ok } }).catch(() => { });
  $('[data-y]', d).onclick = () => answer(true); $('[data-n]', d).onclick = () => answer(false); body.append(d); input.blur(); stick();
}
document.addEventListener('keydown', e => {
  const a = $('.approval:not(.done)'); if (!a || e.ctrlKey || e.metaKey || e.altKey) return;
  const typing = document.activeElement === input && input.value.trim() !== '';   // an empty box never steals the keypress; a half-typed message does
  if (typing) return;
  const k = e.key.toLowerCase(), yes = k === 'y', no = k === 'n' || k === 'escape';
  if (yes || no) { $(yes ? '[data-y]' : '[data-n]', a).click(); e.preventDefault() }
});
msgs.addEventListener('click', e => {
  const tb = e.target.closest('[data-term]'); if (tb) { termOpen(tb.dataset.term, tb.textContent); return }
  const im = e.target.closest('.im'); if (im) { e.preventDefault(); lightbox(im); return }
  const c = e.target.closest('.cite'); if (c) { showView('index'); openPeek(c.dataset.path, +c.dataset.line); return }
  const vw = e.target.closest('[data-view]'); if (vw) { e.preventDefault(); send('/view ' + vw.dataset.view + (vw.dataset.from ? ' ' + vw.dataset.from : '')); return }
  const fl = e.target.closest('[data-files]'); if (fl) { e.preventDefault(); send('/files ' + fl.dataset.files); return }
  const b = e.target.closest('.copy'); if (b) { navigator.clipboard.writeText($('code', b.closest('.fv, pre') || b.parentElement).innerText); b.textContent = 'copied'; setTimeout(() => b.textContent = 'copy', 1200) }
});
$('#send').onclick = () => send(); $('#stop').onclick = () => api('/api/stop', { method: 'POST', body: {} });

/* activity log + undo */
async function loadActivity() {
  const box = $('#actList'); let r; try { r = await api('/api/actions') } catch (e) { box.textContent = e.message; return }
  box.innerHTML = r.actions.length ? r.actions.map(a => `<div class="arow ${a.undone ? 'undone' : ''}"><span class="aid">#${a.id}</span><span class="atime">${new Date(a.ts * 1000).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</span><span class="atool">${esc(a.tool)}</span><span class="asum" title="${esc(a.summary)}">${esc(a.summary)}</span>${a.undoable ? `<button class="btn ghost" data-undo="${a.id}">Undo</button>` : `<span class="muted small">${a.undone ? 'undone' : 'no undo'}</span>`}</div>`).join('') : '<div class="muted pad">Nothing yet. Actions Kage takes will be listed here.</div>';
  $$('[data-undo]', box).forEach(b => b.onclick = async () => {
    if (!confirm(`Undo action #${b.dataset.undo}?`)) return;
    try { const d = await api('/api/undo', { method: 'POST', body: { id: +b.dataset.undo } }); alert(d.text) } catch (e) { alert(e.message) } loadActivity();
  });
}

/* image lightbox */
function lightbox(a) {
  const d = document.createElement('div'); d.className = 'lb'; d.innerHTML = `<img src="${esc(a.dataset.full)}" alt=""><div class="cap">${esc(a.dataset.path)} <span class="muted">· click or Esc to close</span></div>`;
  const close = () => { d.remove(); document.removeEventListener('keydown', onKey, true) }, onKey = e => { if (e.key === 'Escape') { e.stopPropagation(); close() } };
  d.onclick = close; document.addEventListener('keydown', onKey, true); document.body.append(d);
}

/* slash menu + history */
const CMDS = [['/search', 'find code by concept'], ['/find', 'locate a symbol'], ['/def', 'read a definition'], ['/outline', "a file's shape"], ['/refs', 'who uses a symbol'], ['/impact', 'blast radius of a change'], ['/callers', 'what imports a file'],
['/peek', 'read path:10-40'], ['/describe', 'scan + describe this project'], ['/files', 'what each file does: /files src *.py'], ['/view', 'show a file: path or path:10-40'], ['/overview', 'map this project'], ['/wip', 'unfinished work'], ['/changed', 'what your edits affect'], ['/doctor', 'structural health'], ['/scout', 'deep read-only research'], ['/remember', 'save a durable fact'],
['/recall', 'read memory'], ['/notes', 'reviewed code notes'], ['/journal', "today's journal"], ['/map', 'rebuild machine maps'], ['/skills', 'list skills'], ['/github', 'check git + GitHub setup'], ['/brief', 'morning briefing'], ['/log', 'what Kage did'], ['/undo', 'reverse the last action'], ['/term', 'interactive terminal tab'], ['/sh', 'interactive shell'], ['/access', 'system|workspace: how far files reach'], ['/trust', 'on|off: auto-approve this session'], ['/auto', 'on|off: auto-approve, saved'], ['/new', 'fresh session'], ['/help', 'all commands']];
const hist = []; let histI = -1, slashSel = 0;
function hideSlash() { $('#slashMenu').classList.remove('on') }
function slashUpdate() {
  const v = input.value; if (!v.startsWith('/') || v.includes(' ')) return hideSlash();
  const m = CMDS.filter(c => c[0].startsWith(v.toLowerCase())); if (!m.length) return hideSlash(); slashSel = Math.min(slashSel, m.length - 1);
  const el = $('#slashMenu'); el.innerHTML = m.map((c, i) => `<div class="${i === slashSel ? 'sel' : ''}" data-c="${c[0]}"><b>${c[0]}</b><span>${c[1]}</span></div>`).join(''); el.classList.add('on');
  $$('div', el).forEach(d => d.onmousedown = e => { e.preventDefault(); input.value = d.dataset.c + ' '; hideSlash(); input.focus() }); return m;
}
input.addEventListener('input', () => { input.style.height = 'auto'; input.style.height = Math.min(input.scrollHeight, 200) + 'px'; slashSel = 0; slashUpdate() });
input.addEventListener('keydown', e => {
  const open = $('#slashMenu').classList.contains('on');
  if (open && (e.key === 'ArrowDown' || e.key === 'ArrowUp')) { e.preventDefault(); slashSel += e.key === 'ArrowDown' ? 1 : -1; const n = $$('#slashMenu div').length; slashSel = (slashSel + n) % n; slashUpdate(); return }
  if (open && (e.key === 'Tab' || (e.key === 'Enter' && !input.value.includes(' ')))) { const sel = $$('#slashMenu div')[slashSel]; if (sel && (e.key === 'Tab' || input.value !== sel.dataset.c)) { e.preventDefault(); input.value = sel.dataset.c + ' '; hideSlash(); return } }
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() }
  else if (e.key === 'Escape') { if (busy) api('/api/stop', { method: 'POST', body: {} }); hideSlash() }
  else if (e.key === 'ArrowUp' && !input.value && hist.length) { histI = Math.min(histI + 1, hist.length - 1); input.value = hist[histI]; e.preventDefault() }
});

/* ---------- index view ---------- */
const MODES = [['search', 'search'], ['find', 'symbol'], ['def', 'definition'], ['outline', 'outline'], ['refs', 'references'], ['impact', 'impact'], ['callers', 'callers'], ['overview', 'overview'], ['wip', 'wip'], ['changed', 'changed'], ['doctor', 'doctor']];
let mode = 'search';
$('#ixModes').innerHTML = MODES.map(([k, l]) => `<button data-m="${k}" class="${k === mode ? 'on' : ''}">${l}</button>`).join('');
$$('#ixModes button').forEach(b => b.onclick = () => { mode = b.dataset.m; $$('#ixModes button').forEach(x => x.classList.toggle('on', x === b)); if (['overview', 'wip', 'changed', 'doctor'].includes(mode)) runIndex(mode, ''); else $('#ixQ').focus() });
$('#ixForm').onsubmit = e => { e.preventDefault(); runIndex(mode, $('#ixQ').value.trim()) };
async function runIndex(cmd, q) {
  const box = $('#ixResults'); if (!S.workspace) { box.innerHTML = '<div class="muted pad">Choose a workspace first.</div>'; return }
  box.innerHTML = '<div class="muted small">searching…</div>';
  try {
    const r = await api(`/api/index?cmd=${cmd}&q=${encodeURIComponent(q)}`);
    const head = `<div class="muted small" style="margin-bottom:8px">${r.ms}ms · ${r.items.length} cited</div>`;
    if (['overview', 'doctor', 'def', 'peek'].includes(cmd) || !r.items.length) box.innerHTML = head + `<div class="rtext">${linkify(esc(r.text))}</div>` + (cmd !== 'overview' ? '' : '') + rows(r.items, cmd === 'def');
    else box.innerHTML = head + rows(r.items);
    if (cmd === 'def' && r.items[0]) openPeek(r.items[0].path, r.items[0].line);
  } catch (e) { box.innerHTML = `<div class="muted pad">${esc(e.message)}</div>` }
}
const rows = (items, skip) => skip ? '' : items.map(i => `<div class="row" data-p="${esc(i.path)}" data-l="${i.line}"><div><span class="rp">${esc(i.path)}:${i.line}</span>${i.kind ? `<span class="rk">${esc(i.kind)}</span>` : ''}${i.name && i.kind !== 'file' ? ` <b style="font:12px var(--mono)">${esc(i.name)}</b>` : ''}</div>${i.snippet ? `<div class="rs">${esc(i.snippet)}</div>` : ''}</div>`).join('');
$('#ixResults').addEventListener('click', e => { const r = e.target.closest('.row'); if (r) openPeek(r.dataset.p, +r.dataset.l); const c = e.target.closest('.cite'); if (c) openPeek(c.dataset.path, +c.dataset.line) });
async function openPeek(path, line) {
  $('#peekTitle').textContent = `${path}:${line}`;
  try {
    const r = await api(`/api/peek?path=${encodeURIComponent(path)}&line=${line}`); if (r.error) throw new Error(r.error);
    $('#peek').innerHTML = r.lines.map((t, i) => `<div class="ln ${r.start + i === line ? 'hit' : ''}"><i>${r.start + i}</i><span>${esc(t) || ' '}</span></div>`).join('');
    const hit = $('#peek .hit'); if (hit) hit.scrollIntoView({ block: 'center' });
  } catch (e) { $('#peek').innerHTML = `<div class="muted pad">${esc(e.message)}</div>` }
}

/* ---------- vault view ---------- */
let vfPath = null;
async function loadVault() {
  const v = await api('/api/vault'); $('#vaultRoot').textContent = v.root; let last = '';
  $('#vaultTree').innerHTML = v.tree.map(f => { const d = f.path.includes('/') ? f.path.split('/').slice(0, -1).join('/') : 'vault'; const h = d !== last ? `<div class="d">${esc(d)}</div>` : ''; last = d;
    return h + `<div class="f ${f.path === vfPath ? 'on' : ''}" data-p="${esc(f.path)}">${esc(f.path.split('/').pop())}${f.generated ? '<em>generated</em>' : ''}</div>` }).join('');
  $$('#vaultTree .f').forEach(e => e.onclick = () => openVault(e.dataset.p, v.tree.find(t => t.path === e.dataset.p)));
  if (!vfPath) openVault('memory/MEMORY.md', { generated: false });
}
async function openVault(p, meta) {
  vfPath = p; const r = await api('/api/vault/file?path=' + encodeURIComponent(p)); $('#vfTitle').textContent = p.toUpperCase();
  const ed = $('#vfEdit'); ed.hidden = false; ed.value = r.content; const gen = meta && meta.generated;
  $('#vfNote').textContent = gen ? 'Contains generated sections: rewritten on every map rebuild. Edit only outside the markers.' : 'Yours to edit.'; $('#vfSave').hidden = false;
  $$('#vaultTree .f').forEach(e => e.classList.toggle('on', e.dataset.p === p));
}
$('#vfSave').onclick = async () => { try { await api('/api/vault/file', { method: 'POST', body: { path: vfPath, content: $('#vfEdit').value } }); $('#vfNote').textContent = 'Saved.' } catch (e) { $('#vfNote').textContent = e.message } };
$('#vReindex').onclick = async e => { e.target.textContent = 'scanning…'; try { const r = await api('/api/vault/reindex', { method: 'POST', body: {} }); e.target.textContent = `Rebuilt in ${r.ms}ms`; loadVault(); loadState() } catch (x) { e.target.textContent = x.message } setTimeout(() => e.target.textContent = 'Rebuild machine maps', 2500) };
$('#memForm').onsubmit = async e => { e.preventDefault(); const n = $('#memQ').value.trim(); if (!n) return; await api('/api/vault/memory', { method: 'POST', body: { note: n } }); $('#memQ').value = ''; if (vfPath === 'memory/MEMORY.md') openVault(vfPath, {}) };

/* ---------- workspace picker ---------- */
let pk = { path: '', sel: '', hidden: false, items: [] };
async function openPicker() {
  $('#picker').hidden = false; $('#pkClose').style.visibility = S.workspace ? 'visible' : 'hidden'; $('#pkNative').hidden = !S.native_picker;
  const p = await api('/api/places'); const grp = (t, a) => a.length ? `<div class="g">${t}</div>` + a.join('') : '';
  const btn = (ic, l, path, sm = '') => `<button data-p="${esc(path)}"><span class="ib">${ic}</span>${esc(l)}<small>${esc(sm)}</small></button>`;
  $('#pkSide').innerHTML = grp('Resume', S.last ? [btn('↻', S.last.split('/').pop(), S.last, 'last')] : []) + grp('Recent', p.recents.slice(0, 6).map(r => btn('◷', r.label, r.path))) +
    grp('Places', p.places.map(x => btn(x.icon, x.label, x.path))) + grp('Projects', p.repos.slice(0, 14).map(r => btn('⎇', r.label, r.path, r.badges.slice(0, 1).join())));
  $$('#pkSide button').forEach(b => b.ondblclick = () => choose(b.dataset.p));
  $$('#pkSide button').forEach(b => b.onclick = () => browse(b.dataset.p));
  browse(S.workspace || S.last || ''); $('#pkFilter').value = ''; $('#pkFilter').focus();
}
async function browse(path) {
  const d = await api(`/api/fs?path=${encodeURIComponent(path)}&hidden=${pk.hidden ? 1 : 0}`); pk.path = d.path; pk.sel = d.path; pk.items = d.entries;
  $('#pkCrumbs').innerHTML = d.crumbs.map((c, i) => `${i ? '<span>/</span>' : ''}<button data-p="${esc(c.path)}">${esc(c.name)}</button>`).join(''); $$('#pkCrumbs button').forEach(b => b.onclick = () => browse(b.dataset.p));
  renderList(); updateSel(d.badges || []);
}
function renderList() {
  const f = $('#pkFilter').value.toLowerCase(), it = pk.items.filter(e => e.name.toLowerCase().includes(f));
  $('#pkList').innerHTML = (pk.path !== '/' ? `<div class="it" data-up><span class="fi">↰</span>..</div>` : '') + it.map(e => `<div class="it ${e.path === pk.sel ? 'sel' : ''}" data-p="${esc(e.path)}"><span class="fi">▸</span>${esc(e.name)}${e.badges.map(b => `<span class="bdg ${b}">${b}</span>`).join('')}</div>`).join('') || '<div class="muted pad">No sub-folders.</div>';
  $$('#pkList .it').forEach(el => { el.onclick = () => { if (el.dataset.up) return browse(pk.path.replace(/\/[^/]+\/?$/, '') || '/'); pk.sel = el.dataset.p; $$('#pkList .it').forEach(x => x.classList.toggle('sel', x === el)); updateSel() }; el.ondblclick = () => browse(el.dataset.p || pk.path.replace(/\/[^/]+\/?$/, '') || '/') });
}
function updateSel(badges) { $('#pkSel').textContent = pk.sel || '—'; $('#pkSel').title = pk.sel }
async function choose(path) {
  try { await api('/api/workspace', { method: 'POST', body: { path } }); $('#picker').hidden = true; msgs.innerHTML = ''; document.body.classList.remove('chatting'); await loadState(); input.focus() } catch (e) { alert(e.message) }
}
$('#pkOpen').onclick = () => choose(pk.sel || pk.path);
$('#pkFilter').oninput = renderList; $('#pkHidden').onchange = e => { pk.hidden = e.target.checked; browse(pk.path) };
$('#pkClose').onclick = () => $('#picker').hidden = true; $('#wsPill').onclick = openPicker;
$('#pkNative').onclick = async () => { const r = await api('/api/pick', { method: 'POST', body: {} }); if (r.path) choose(r.path) };
$('#picker').addEventListener('keydown', e => { if (e.key === 'Enter' && document.activeElement.id !== 'pkFilter') choose(pk.sel || pk.path); if (e.key === 'Enter' && document.activeElement.id === 'pkFilter') { const f = $('#pkList .it:not([data-up])'); if (f) browse(f.dataset.p) } });
document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('#picker').hidden && S.workspace) $('#picker').hidden = true;
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'o') { e.preventDefault(); openPicker() }
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); showView('index') }
});

/* ---------- boot ---------- */
drawArt();
loadState().then(() => { if (!S.workspace) openPicker(); else input.focus() });
setInterval(() => { if (!busy) loadState() }, 15000);
