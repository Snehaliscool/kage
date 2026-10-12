'use strict';
/* Kage web terminal. A compact VT100/xterm emulator (no dependencies, local-first) and the client that talks to /api/term/*.
   The emulator class VT is DOM-free so it can be tested in node (tests/test_vt.js). */

const ANSI16 = ['#1a1916', '#e2342a', '#7fbf6a', '#d9a441', '#6aa0e8', '#c678dd', '#56b6c2', '#cfc9b8', '#6b675c', '#ff5f56', '#9fd88a', '#f0c060', '#8ab8ff', '#e09aff', '#7fd6e0', '#f4efe2'];
function color256(n) {
  if (n < 16) return ANSI16[n];
  if (n >= 232) { const v = 8 + (n - 232) * 10; return `rgb(${v},${v},${v})` }
  n -= 16; const L = [0, 95, 135, 175, 215, 255]; return `rgb(${L[Math.floor(n / 36)]},${L[Math.floor(n / 6) % 6]},${L[n % 6]})`;
}
const MAX_HIST = 3000;

class VT {
  constructor(rows, cols, hooks) {
    this.hooks = hooks || {}; this.styles = [{}]; this.styleIdx = new Map([['{}', 0]]); this.state = 0; this.buf = ''; this.osc = '';
    this.hist = []; this.alt = false; this.main = null; this.appCursor = false; this.wrap = true; this.curVisible = true; this.bracketed = false;
    this.rows = rows; this.cols = cols; this.pen = {}; this.penIdx = 0; this.dirty = new Set();
    this.grid = Array.from({ length: rows }, () => this.blankRow()); this.x = 0; this.y = 0; this.pend = false; this.top = 0; this.bot = rows - 1; this.saved = { x: 0, y: 0, pen: {} };
  }
  /* ---- styles + cells */
  intern(p) {
    const k = JSON.stringify(p); let i = this.styleIdx.get(k);
    if (i === undefined) { i = this.styles.push(p) - 1; this.styleIdx.set(k, i) } return i;
  }
  setPen(p) { this.pen = p; this.penIdx = this.intern(p) }
  eraseStyle() { return this.pen.bg !== undefined ? this.intern({ bg: this.pen.bg }) : 0 }
  blankRow() { const st = this.eraseStyle(); return { ch: new Array(this.cols).fill(' '), st: new Array(this.cols).fill(st) } }
  mark(y) { this.dirty.add(y); if (this.hooks.dirty) this.hooks.dirty() }
  markAll() { for (let i = 0; i < this.rows; i++) this.dirty.add(i); if (this.hooks.dirty) this.hooks.dirty() }
  lineText(y) { return this.grid[y].ch.join('').replace(/\s+$/, '') }
  /* ---- scrolling */
  pushHist(row) {
    this.hist.push(row); if (this.hooks.pushHist) this.hooks.pushHist(row);
    if (this.hist.length > MAX_HIST) { this.hist.shift(); if (this.hooks.dropHist) this.hooks.dropHist() }
  }
  scrollUp(n) {
    for (let i = 0; i < n; i++) { const r = this.grid.splice(this.top, 1)[0]; if (this.top === 0 && !this.alt) this.pushHist(r); this.grid.splice(this.bot, 0, this.blankRow()) }
    for (let y = this.top; y <= this.bot; y++) this.dirty.add(y); this.mark(this.top);
  }
  scrollDown(n) {
    for (let i = 0; i < n; i++) { this.grid.splice(this.bot, 1); this.grid.splice(this.top, 0, this.blankRow()) }
    for (let y = this.top; y <= this.bot; y++) this.dirty.add(y); this.mark(this.top);
  }
  lf() { this.pend = false; if (this.y === this.bot) this.scrollUp(1); else if (this.y < this.rows - 1) this.y++ }
  ri() { if (this.y === this.top) this.scrollDown(1); else if (this.y > 0) this.y-- }
  /* ---- input stream */
  write(s) {
    for (const ch of s) {
      const c = ch.codePointAt(0);
      switch (this.state) {
        case 0:
          if (c === 0x1b) this.state = 1; else if (c < 0x20 || c === 0x7f) this.ctrl(c); else this.put(ch); break;
        case 1: this.escape(ch); break;
        case 2:
          if (c >= 0x40 && c <= 0x7e) { this.csi(ch); this.state = 0; this.buf = '' }
          else if (c === 0x1b) { this.state = 1; this.buf = '' }
          else if (c < 0x20) this.ctrl(c); else this.buf += ch;
          break;
        case 3:
          if (c === 0x07) this.endString(); else if (c === 0x1b) this.state = 4; else if (this.osc.length < 4096) this.osc += ch; break;
        case 4: if (ch === '\\') this.endString(); else { this.state = 1; this.osc = ''; this.escape(ch) } break;
        case 5: this.state = 0; break;
      }
    }
  }
  endString() {
    const o = this.osc; this.osc = ''; this.state = 0; const m = /^[02];(.*)$/s.exec(o); if (m && this.hooks.title) this.hooks.title(m[1]);
    const k = /^777;kage;(\d+);(\d+);([A-Za-z0-9+\/=]*);([A-Za-z0-9+\/=]*)$/.exec(o);      // shell integration: exit code, history number, command, folder
    if (k && this.hooks.cmd) this.hooks.cmd(+k[1], +k[2], k[3], k[4]);
  }
  tailText(n) {
    const rows = [...this.hist, ...this.grid].map(r => r.ch.join('').replace(/\s+$/, '')); while (rows.length && !rows[rows.length - 1]) rows.pop(); return rows.slice(-n).join('\n');
  }
  ctrl(c) {
    if (c === 0x08) { if (this.x > 0) this.x--; this.pend = false }
    else if (c === 0x09) { this.x = Math.min(this.cols - 1, ((this.x >> 3) + 1) << 3); this.pend = false }
    else if (c === 0x0a || c === 0x0b || c === 0x0c) this.lf();
    else if (c === 0x0d) { this.x = 0; this.pend = false }
    this.mark(this.y);
  }
  put(ch) {
    if (this.pend) { if (this.wrap) { this.x = 0; this.lf() } this.pend = false }
    const row = this.grid[this.y]; row.ch[this.x] = ch; row.st[this.x] = this.penIdx; this.mark(this.y);
    if (this.x < this.cols - 1) this.x++; else this.pend = this.wrap;
  }
  escape(ch) {
    this.state = 0;
    switch (ch) {
      case '[': this.state = 2; this.buf = ''; break;
      case ']': case 'P': case '_': case '^': case 'X': this.state = 3; this.osc = ch === ']' ? '' : '\u0000'; break;
      case '(': case ')': case '*': case '+': case '-': case '.': case '/': case '#': case '%': this.state = 5; break;
      case '7': this.saved = { x: this.x, y: this.y, pen: this.pen }; break;
      case '8': this.x = Math.min(this.saved.x, this.cols - 1); this.y = Math.min(this.saved.y, this.rows - 1); this.setPen(this.saved.pen); this.pend = false; break;
      case 'D': this.lf(); break;
      case 'E': this.x = 0; this.lf(); break;
      case 'M': this.ri(); break;
      case 'c': this.fullReset(); break;
    }
  }
  fullReset() {
    this.setPen({}); this.x = this.y = 0; this.pend = false; this.top = 0; this.bot = this.rows - 1; this.appCursor = false; this.wrap = true; this.curVisible = true; this.bracketed = false;
    if (this.alt) this.setAlt(false, false); this.grid = Array.from({ length: this.rows }, () => this.blankRow()); this.markAll();
  }
  /* ---- CSI */
  csi(f) {
    const m = /^([?<=>]?)([0-9;:]*)([ -\/]*)$/.exec(this.buf); if (!m) return;
    const priv = m[1], ps = m[2] === '' ? [] : m[2].split(/[;:]/).map(v => v === '' ? 0 : parseInt(v, 10)), P = (i, d) => ps[i] > 0 ? ps[i] : d;
    const clampY = y => Math.max(0, Math.min(this.rows - 1, y)), clampX = x => Math.max(0, Math.min(this.cols - 1, x));
    if ('ABCDEFGHdfae`LMru'.includes(f)) this.pend = false;      // only cursor moves cancel a pending wrap (colour changes do not)
    switch (f) {
      case 'A': this.y = Math.max(this.y >= this.top ? this.top : 0, this.y - P(0, 1)); break;
      case 'B': case 'e': this.y = Math.min(this.y <= this.bot ? this.bot : this.rows - 1, this.y + P(0, 1)); break;
      case 'C': case 'a': this.x = clampX(this.x + P(0, 1)); break;
      case 'D': this.x = clampX(this.x - P(0, 1)); break;
      case 'E': this.x = 0; this.y = clampY(this.y + P(0, 1)); break;
      case 'F': this.x = 0; this.y = clampY(this.y - P(0, 1)); break;
      case 'G': case '`': this.x = clampX(P(0, 1) - 1); break;
      case 'd': this.y = clampY(P(0, 1) - 1); break;
      case 'H': case 'f': this.y = clampY(P(0, 1) - 1); this.x = clampX(P(1, 1) - 1); break;
      case 'J': this.eraseDisplay(ps[0] || 0); break;
      case 'K': this.eraseLine(ps[0] || 0); break;
      case 'L': if (this.y >= this.top && this.y <= this.bot) { const n = Math.min(P(0, 1), this.bot - this.y + 1); for (let i = 0; i < n; i++) { this.grid.splice(this.bot, 1); this.grid.splice(this.y, 0, this.blankRow()) } for (let y = this.y; y <= this.bot; y++) this.dirty.add(y); this.mark(this.y) } this.x = 0; break;
      case 'M': if (this.y >= this.top && this.y <= this.bot) { const n = Math.min(P(0, 1), this.bot - this.y + 1); for (let i = 0; i < n; i++) { this.grid.splice(this.y, 1); this.grid.splice(this.bot, 0, this.blankRow()) } for (let y = this.y; y <= this.bot; y++) this.dirty.add(y); this.mark(this.y) } this.x = 0; break;
      case 'P': { const r = this.grid[this.y], n = Math.min(P(0, 1), this.cols - this.x), st = this.eraseStyle(); r.ch.splice(this.x, n); r.st.splice(this.x, n); for (let i = 0; i < n; i++) { r.ch.push(' '); r.st.push(st) } this.mark(this.y); break }
      case '@': { const r = this.grid[this.y], n = Math.min(P(0, 1), this.cols - this.x), st = this.eraseStyle(); for (let i = 0; i < n; i++) { r.ch.splice(this.x, 0, ' '); r.st.splice(this.x, 0, st); r.ch.pop(); r.st.pop() } this.mark(this.y); break }
      case 'X': { const r = this.grid[this.y], n = Math.min(P(0, 1), this.cols - this.x), st = this.eraseStyle(); for (let i = 0; i < n; i++) { r.ch[this.x + i] = ' '; r.st[this.x + i] = st } this.mark(this.y); break }
      case 'S': this.scrollUp(P(0, 1)); break;
      case 'T': this.scrollDown(P(0, 1)); break;
      case 'm': this.sgr(ps); break;
      case 'r': { const t = P(0, 1) - 1, b = P(1, this.rows) - 1; if (t < b && b < this.rows) { this.top = t; this.bot = b } this.x = 0; this.y = 0; break }
      case 's': if (!priv) this.saved = { x: this.x, y: this.y, pen: this.pen }; break;
      case 'u': if (!priv) { this.x = Math.min(this.saved.x, this.cols - 1); this.y = Math.min(this.saved.y, this.rows - 1); this.setPen(this.saved.pen) } break;
      case 'h': case 'l': if (priv === '?') for (const p of ps) this.mode(p, f === 'h'); break;
      case 'n': if (!priv && this.hooks.reply) { if (ps[0] === 5) this.hooks.reply('\x1b[0n'); else if (ps[0] === 6) this.hooks.reply(`\x1b[${this.y + 1};${this.x + 1}R`) } break;
      case 'c': if (this.hooks.reply) this.hooks.reply(priv === '>' ? '\x1b[>0;276;0c' : priv === '' ? '\x1b[?1;2c' : ''); break;
    }
    this.mark(this.y);
  }
  mode(p, on) {
    if (p === 1) this.appCursor = on; else if (p === 7) this.wrap = on; else if (p === 25) { this.curVisible = on; this.mark(this.y) }
    else if (p === 2004) this.bracketed = on; else if (p === 47 || p === 1047) this.setAlt(on, false); else if (p === 1049) this.setAlt(on, true);
  }
  setAlt(on, saveCursor) {
    if (on === this.alt) return;
    if (on) {
      this.main = { grid: this.grid, x: this.x, y: this.y, top: this.top, bot: this.bot, pen: this.pen, saved: this.saved }; this.alt = true;
      this.grid = Array.from({ length: this.rows }, () => this.blankRow()); this.top = 0; this.bot = this.rows - 1; if (saveCursor) { this.x = 0; this.y = 0 }
    } else {
      const m = this.main; this.alt = false; this.grid = m.grid; this.top = m.top; this.bot = m.bot; this.setPen(m.pen); this.saved = m.saved;
      if (saveCursor) { this.x = Math.min(m.x, this.cols - 1); this.y = Math.min(m.y, this.rows - 1) } this.main = null;
    }
    this.pend = false; if (this.hooks.alt) this.hooks.alt(on); this.markAll();
  }
  eraseLine(mode) {
    const r = this.grid[this.y], st = this.eraseStyle(), a = mode === 1 || mode === 2 ? 0 : this.x, b = mode === 0 || mode === 2 ? this.cols : this.x + 1;
    for (let i = a; i < b; i++) { r.ch[i] = ' '; r.st[i] = st } this.mark(this.y);
  }
  eraseDisplay(mode) {
    if (mode === 0) { this.eraseLine(0); for (let y = this.y + 1; y < this.rows; y++) { this.grid[y] = this.blankRow(); this.dirty.add(y) } }
    else if (mode === 1) { for (let y = 0; y < this.y; y++) { this.grid[y] = this.blankRow(); this.dirty.add(y) } this.eraseLine(1) }
    else { for (let y = 0; y < this.rows; y++) { this.grid[y] = this.blankRow(); this.dirty.add(y) } if (mode === 3 && !this.alt) { this.hist = []; if (this.hooks.clearHist) this.hooks.clearHist() } }
    this.mark(this.y);
  }
  sgr(ps) {
    if (!ps.length) ps = [0]; let p = { ...this.pen };
    for (let i = 0; i < ps.length; i++) {
      const n = ps[i];
      if (n === 0) p = {}; else if (n === 1) p.b = 1; else if (n === 2) p.d = 1; else if (n === 3) p.i = 1; else if (n === 4) p.u = 1; else if (n === 7) p.r = 1; else if (n === 9) p.s = 1;
      else if (n === 22) { delete p.b; delete p.d } else if (n === 23) delete p.i; else if (n === 24) delete p.u; else if (n === 27) delete p.r; else if (n === 29) delete p.s;
      else if (n >= 30 && n <= 37) p.fg = ANSI16[n - 30]; else if (n >= 90 && n <= 97) p.fg = ANSI16[n - 90 + 8]; else if (n === 39) delete p.fg;
      else if (n >= 40 && n <= 47) p.bg = ANSI16[n - 40]; else if (n >= 100 && n <= 107) p.bg = ANSI16[n - 100 + 8]; else if (n === 49) delete p.bg;
      else if (n === 38 || n === 48) {
        const key = n === 38 ? 'fg' : 'bg';
        if (ps[i + 1] === 5) { p[key] = color256(ps[i + 2] | 0); i += 2 }
        else if (ps[i + 1] === 2) { p[key] = `rgb(${ps[i + 2] | 0},${ps[i + 3] | 0},${ps[i + 4] | 0})`; i += 4 }
      }
    }
    this.setPen(p);
  }
  /* ---- size */
  resize(rows, cols) {
    rows = Math.max(2, rows | 0); cols = Math.max(10, cols | 0); if (rows === this.rows && cols === this.cols) return;
    const widen = g => { for (const r of g) { r.ch.length = Math.min(r.ch.length, cols); r.st.length = Math.min(r.st.length, cols); while (r.ch.length < cols) { r.ch.push(' '); r.st.push(0) } } };
    const heighten = (g, isMain) => {
      while (g.length > rows) { if (isMain && this.y >= rows) { this.pushHist(g.shift()); this.y-- } else g.pop() }
      while (g.length < rows) g.push({ ch: new Array(cols).fill(' '), st: new Array(cols).fill(0) });
    };
    if (this.alt) { heighten(this.main.grid, false); widen(this.main.grid); heighten(this.grid, false); widen(this.grid) }
    else { heighten(this.grid, true); widen(this.grid) }
    this.rows = rows; this.cols = cols; this.top = 0; this.bot = rows - 1; this.x = Math.min(this.x, cols - 1); this.y = Math.max(0, Math.min(this.y, rows - 1)); this.pend = false; this.markAll();
  }
}

/* ---------------------------------------------------------------- keys */
function keySeq(e, appCursor) {
  const k = e.key; if (e.metaKey || k === 'Dead' || k === 'Process' || k === 'Unidentified') return null;
  const mod = 1 + (e.shiftKey ? 1 : 0) + (e.altKey ? 2 : 0) + (e.ctrlKey ? 4 : 0);
  const arrows = { ArrowUp: 'A', ArrowDown: 'B', ArrowRight: 'C', ArrowLeft: 'D', Home: 'H', End: 'F' };
  if (arrows[k]) { const c = arrows[k]; return mod > 1 ? `\x1b[1;${mod}${c}` : appCursor ? `\x1bO${c}` : `\x1b[${c}` }
  const tilde = { Insert: 2, Delete: 3, PageUp: 5, PageDown: 6 }; if (tilde[k]) return mod > 1 ? `\x1b[${tilde[k]};${mod}~` : `\x1b[${tilde[k]}~`;
  const fk = { F1: 'OP', F2: 'OQ', F3: 'OR', F4: 'OS', F5: '[15~', F6: '[17~', F7: '[18~', F8: '[19~', F9: '[20~', F10: '[21~', F11: '[23~', F12: '[24~' }; if (fk[k]) return '\x1b' + fk[k];
  if (k === 'Enter') return '\r'; if (k === 'Tab') return e.shiftKey ? '\x1b[Z' : '\t'; if (k === 'Escape') return '\x1b';
  if (k === 'Backspace') return e.ctrlKey ? '\x08' : e.altKey ? '\x1b\x7f' : '\x7f';
  if (e.ctrlKey && !e.altKey && k.length === 1) {
    if (e.shiftKey && 'cvCV'.includes(k)) return null;                         // copy / paste stay with the browser
    if (k.toLowerCase() === 'c' && typeof getSelection === 'function' && String(getSelection()).length) return null;   // Ctrl+C copies when text is selected
    const c = k.toLowerCase(); if (c >= 'a' && c <= 'z') return String.fromCharCode(c.charCodeAt(0) - 96);
    const map = { '[': 27, '\\': 28, ']': 29, '^': 30, '_': 31, '@': 0, ' ': 0, '?': 127, '2': 0, '3': 27, '4': 28, '5': 29, '6': 30, '7': 31, '8': 127 }; return c in map ? String.fromCharCode(map[c]) : null;
  }
  if ([...k].length === 1) return (e.altKey ? '\x1b' : '') + k;
  return null;
}

/* ---------------------------------------------------------------- DOM view + session */
if (typeof document !== 'undefined') {
  const esc2 = s => s.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
  const bytesToB64 = s => { const b = new TextEncoder().encode(s); let o = ''; for (let i = 0; i < b.length; i += 8192) o += String.fromCharCode(...b.subarray(i, i + 8192)); return btoa(o) };
  const sessions = new Map(); let activeId = null, loaded = false;
  const bar = () => $('#termTabs'), host = () => $('#termHost');

  class TermView {
    constructor(info) {
      this.id = info.id; this.title = info.title || 'Terminal'; this.off = 0; this.exited = false; this.closed = false; this.q = ''; this.sending = false; this.lastCur = -1; this.raf = 0; this.dec = new TextDecoder();
      this.el = document.createElement('div'); this.el.className = 'tv'; this.el.tabIndex = 0; this.el.hidden = true;
      this.el.innerHTML = '<div class="tm" aria-hidden="true">XXXXXXXXXXXXXXXXXXXX</div><div class="th"></div><div class="ts"></div>';
      host().append(this.el); this.hist = $('.th', this.el); this.scr = $('.ts', this.el); this.tm = $('.tm', this.el);
      this.measure(); this.vt = new VT(24, 80, {
        dirty: () => this.schedule(), reply: s => this.send(s), title: t => { this.title = t.slice(0, 40) || this.title; this.tab() },
        pushHist: r => { const d = document.createElement('div'); d.className = 'r'; d.innerHTML = this.rowHtml(r, -1) || ' '; this.hist.append(d) },
        dropHist: () => this.hist.firstChild && this.hist.firstChild.remove(), clearHist: () => { this.hist.innerHTML = '' },
        alt: on => { this.el.classList.toggle('alt', on); this.el.scrollTop = 0 },
        cmd: (code, n, b64, cwd) => this.onCmd(code, n, b64, cwd),
      });
      this.rowEls = []; this.buildRows();
      this.el.addEventListener('keydown', e => this.key(e));
      this.el.addEventListener('paste', e => { const t = (e.clipboardData || window.clipboardData).getData('text'); if (t) { this.send(this.vt.bracketed ? `\x1b[200~${t}\x1b[201~` : t.replace(/\r?\n/g, '\r')); e.preventDefault() } });
      this.el.addEventListener('mousedown', () => setTimeout(() => { if (!String(getSelection())) this.el.focus() }, 0));
      this.ro = new ResizeObserver(() => { clearTimeout(this.rt); this.rt = setTimeout(() => this.fit(), 80) }); this.ro.observe(host());
      this.connect();
    }
    onCmd(code, n, b64, cwd) {
      const dec = s => { try { return new TextDecoder().decode(Uint8Array.from(atob(s), c => c.charCodeAt(0))) } catch { return '' } };
      if (!n || n === this.lastN) { return }                                          // empty Enter or the rc file loading
      this.lastN = n;
      if (code === 0 || code === 130 || code === 148) this.fail = null;                // success, Ctrl+C, Ctrl+Z: nothing to explain
      else this.fail = { code, text: dec(b64), cwd: dec(cwd), out: this.vt.tailText(60) };
      if (activeId === this.id) renderFail();
    }
    measure() { const r = this.tm.getBoundingClientRect(); this.cw = r.width / 20 || 8; this.chh = r.height || 17; return [this.cw, this.chh] }
    buildRows() { this.scr.innerHTML = ''; this.rowEls = []; for (let i = 0; i < this.vt.rows; i++) { const d = document.createElement('div'); d.className = 'r'; this.scr.append(d); this.rowEls.push(d) } this.vt.markAll() }
    fit() {
      if (this.el.hidden || !this.el.clientHeight) return; this.measure();
      const cs = getComputedStyle(this.el), w = this.el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 14, h = this.el.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
      const cols = Math.max(20, Math.floor(w / this.cw)), rows = Math.max(4, Math.floor(h / this.chh));
      if (cols === this.vt.cols && rows === this.vt.rows) return;
      this.vt.resize(rows, cols); this.buildRows(); this.scr.style.height = rows * this.chh + 'px';
      api('/api/term/resize', { method: 'POST', body: { id: this.id, rows, cols } }).catch(() => { });
    }
    css(i) {
      const s = this.vt.styles[i]; if (!i) return ''; let fg = s.fg, bg = s.bg;
      if (s.r) { const t = fg; fg = bg || 'var(--tbg)'; bg = t || 'var(--tfg)' }
      return (fg ? `color:${fg};` : '') + (bg ? `background:${bg};` : '') + (s.b ? 'font-weight:700;' : '') + (s.d ? 'opacity:.6;' : '') + (s.i ? 'font-style:italic;' : '') + (s.u || s.s ? `text-decoration:${s.u ? 'underline ' : ''}${s.s ? 'line-through' : ''};` : '');
    }
    rowHtml(row, cx) {
      let out = '', run = '', cur = -2, n = row.ch.length;
      while (n > 0 && row.ch[n - 1] === ' ' && !row.st[n - 1] && n - 1 !== cx) n--;
      const flush = () => { if (!run) return; const c = cur >= 0 ? this.css(cur) : ''; out += cur === -1 ? `<span class="cur">${esc2(run)}</span>` : c ? `<span style="${c}">${esc2(run)}</span>` : esc2(run); run = '' };
      for (let i = 0; i < n; i++) { const st = i === cx ? -1 : row.st[i]; if (st !== cur) { flush(); cur = st } run += row.ch[i] }
      flush(); return out;
    }
    schedule() { if (!this.raf) this.raf = requestAnimationFrame(() => this.paint()) }
    paint() {
      this.raf = 0; const vt = this.vt, tv = this.el, stick = tv.scrollHeight - tv.scrollTop - tv.clientHeight < this.chh * 2;
      if (this.lastCur >= 0) vt.dirty.add(this.lastCur); vt.dirty.add(vt.y); this.lastCur = vt.y;
      for (const y of vt.dirty) { const el = this.rowEls[y]; if (el) el.innerHTML = this.rowHtml(vt.grid[y], y === vt.y && vt.curVisible && !this.exited ? vt.x : -2) || ' ' }
      vt.dirty.clear(); if (stick) tv.scrollTop = tv.scrollHeight;
    }
    key(e) {
      if (this.exited) { if (e.key === 'Enter') { e.preventDefault(); closeTab(this.id) } return }
      const s = keySeq(e, this.vt.appCursor); if (s === null) return; e.preventDefault(); e.stopPropagation(); this.send(s);
    }
    send(s) { this.q += s; if (!this.sending) this.flush() }
    async flush() {
      this.sending = true;
      while (this.q) { const c = this.q.slice(0, 4000); this.q = this.q.slice(4000); try { await api('/api/term/input', { method: 'POST', body: { id: this.id, d: bytesToB64(c) } }) } catch { } }
      this.sending = false;
    }
    async connect() {
      if (this.closed || this.exited) return; this.ac = new AbortController();
      try {
        const r = await fetch(`/api/term/stream?id=${this.id}&from=${this.off}`, { signal: this.ac.signal }); if (!r.ok) throw new Error('gone');
        const rd = r.body.getReader(), dec = new TextDecoder(); let buf = '';
        while (true) {
          const { value, done } = await rd.read(); if (done) break; buf += dec.decode(value, { stream: true });
          let i; while ((i = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, i); buf = buf.slice(i + 1); if (!line.trim()) continue;
            const m = JSON.parse(line);
            if (m.d !== undefined) { const bin = atob(m.d), u = new Uint8Array(bin.length); for (let k = 0; k < bin.length; k++) u[k] = bin.charCodeAt(k); this.vt.write(this.dec.decode(u, { stream: true })); this.off = m.o }
            else if (m.exit !== undefined) { this.finish(m.exit); return }
          }
        }
      } catch (e) { if (this.closed) return; if (String(e.message) === 'gone') { this.finish(null); return } }
      setTimeout(() => this.connect(), 1000);
    }
    finish(code) {
      this.exited = true; this.vt.write(`\r\n\x1b[2m[process exited${code === null ? '' : ' with code ' + code} - press Enter to close this tab]\x1b[0m\r\n`); this.vt.curVisible = false; this.paint(); this.tab();
    }
    show(on) { this.el.hidden = !on; if (on) { this.fit(); this.el.focus(); this.el.scrollTop = this.el.scrollHeight } }
    tab() { const b = $(`[data-tid="${this.id}"] .tt`, bar()); if (b) b.textContent = (this.exited ? '× ' : '') + this.title }
    destroy() { this.closed = true; if (this.ac) this.ac.abort(); this.ro.disconnect(); this.el.remove() }
  }

  function renderTabs() {
    bar().innerHTML = [...sessions.values()].map(s => `<button data-tid="${s.id}" class="ttab ${s.id === activeId ? 'on' : ''}"><span class="tt">${esc(s.title)}</span><i class="tx" title="Close">✕</i></button>`).join('') + '<button class="ttab add" id="termAdd" title="New terminal">+</button>';
    $$('.ttab[data-tid]', bar()).forEach(b => b.onclick = e => { if (e.target.classList.contains('tx')) closeTab(b.dataset.tid); else activate(b.dataset.tid) });
    $('#termAdd').onclick = () => termNew();
  }
  function renderFail() {
    const b = $('#termFail'), s = sessions.get(activeId), f = s && s.fail;
    b.hidden = !f; if (f) b.textContent = `Ask Kage why (exit ${f.code})`; b.title = f ? f.text : '';
  }
  $('#termFail').onclick = () => {
    const s = sessions.get(activeId), f = s && s.fail; if (!f) return; s.fail = null; renderFail();
    const msg = `My command failed in the terminal (exit ${f.code}).\nCommand: ${f.text}\nFolder: ${f.cwd || 'unknown'}\nOutput (last lines):\n\`\`\`\n${f.out.slice(-4500)}\n\`\`\`\nWork out why it failed and fix it.`;
    showView('console'); if (busy) { input.value = msg; input.focus() } else send(msg);
  };
  function activate(id) { activeId = id; for (const s of sessions.values()) s.show(s.id === id); renderTabs(); renderFail() }
  function attach(info) { if (sessions.has(info.id)) return sessions.get(info.id); const v = new TermView(info); sessions.set(info.id, v); return v }
  async function closeTab(id) {
    const s = sessions.get(id); if (!s) return; s.destroy(); sessions.delete(id); api('/api/term/close', { method: 'POST', body: { id } }).catch(() => { });
    if (activeId === id) activeId = [...sessions.keys()].pop() || null; if (activeId) activate(activeId); else renderTabs();
  }
  window.termNew = async () => {
    try { const info = await api('/api/term/open', { method: 'POST', body: { rows: 24, cols: 80 } }); attach(info); activate(info.id) } catch (e) { alert(e.message) }
  };
  window.termOpen = (id, title) => { attach({ id, title }); activeId = id; showView('terminal') };      // opened by the agent
  window.termShow = async () => {
    if (!loaded) { loaded = true; try { (await api('/api/term/list')).terminals.forEach(attach) } catch { } }
    if (!sessions.size) await termNew(); else activate(activeId && sessions.has(activeId) ? activeId : [...sessions.keys()][0]);
  };
}
if (typeof module !== 'undefined') module.exports = { VT, keySeq, color256 };
