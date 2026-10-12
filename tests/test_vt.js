// Emulator tests: node tests/test_vt.js   (also run by tests/test_extras.py when node is installed)
const assert = require('assert'); const { VT, keySeq } = require('../kage/ui/term.js');
const mk = (r = 5, c = 20) => { const log = { reply: [], title: [], alt: [] }; const v = new VT(r, c, { reply: s => log.reply.push(s), title: t => log.title.push(t), alt: o => log.alt.push(o) }); return [v, log] };
let n = 0; const t = (name, fn) => { fn(); n++ };
t('text + cursor', () => { const [v] = mk(); v.write('hello'); assert.equal(v.lineText(0), 'hello'); assert.equal(v.x, 5) });
t('crlf', () => { const [v] = mk(); v.write('a\r\nb'); assert.equal(v.lineText(0), 'a'); assert.equal(v.lineText(1), 'b') });
t('autowrap', () => { const [v] = mk(5, 10); v.write('x'.repeat(12)); assert.equal(v.lineText(0), 'x'.repeat(10)); assert.equal(v.lineText(1), 'xx') });
t('sgr colours', () => { const [v] = mk(); v.write('\x1b[31mred\x1b[0m ok\x1b[38;5;196mA\x1b[38;2;1;2;3mB'); assert.equal(v.styles[v.grid[0].st[0]].fg, '#e2342a'); assert.equal(v.grid[0].st[4], 0);
  assert.equal(v.styles[v.grid[0].st[6]].fg, 'rgb(255,0,0)'); assert.equal(v.styles[v.grid[0].st[7]].fg, 'rgb(1,2,3)') });
t('cursor addressing', () => { const [v] = mk(); v.write('\x1b[3;5Hx'); assert.equal(v.grid[2].ch[4], 'x'); v.write('\x1b[2Ay'); assert.equal(v.grid[0].ch[5], 'y') });
t('erase line + display', () => { const [v] = mk(); v.write('abcdef\r\x1b[3C\x1b[K'); assert.equal(v.lineText(0), 'abc'); v.write('\x1b[2J\x1b[H'); assert.equal(v.lineText(0), ''); assert.equal(v.x, 0) });
t('scrollback', () => { const [v] = mk(3, 10); v.write('a\r\nb\r\nc\r\nd'); assert.equal(v.hist.length, 1); assert.equal(v.hist[0].ch[0], 'a'); assert.equal(v.lineText(0), 'b'); assert.equal(v.lineText(2), 'd') });
t('alt screen restores main', () => { const [v, l] = mk(); v.write('main\x1b[?1049hALT'); assert.equal(v.alt, true); assert.equal(v.lineText(0), 'ALT'); v.write('\x1b[?1049l'); assert.equal(v.alt, false); assert.equal(v.lineText(0), 'main'); assert.deepEqual(l.alt, [true, false]) });
t('device status report', () => { const [v, l] = mk(); v.write('ab\x1b[6n\x1b[5n\x1b[c'); assert.deepEqual(l.reply, ['\x1b[1;3R', '\x1b[0n', '\x1b[?1;2c']) });
t('delete/insert chars + lines', () => { const [v] = mk(); v.write('abcdef\r\x1b[2P'); assert.equal(v.lineText(0), 'cdef'); v.write('\x1b[2@'); assert.equal(v.lineText(0), '  cdef'.replace(/^ +/, m => m)); v.write('\x1b[H\x1b[L'); assert.equal(v.lineText(0), ''); assert.equal(v.lineText(1).trim(), 'cdef') });
t('scroll region does not touch history', () => { const [v] = mk(5, 10); v.write('1\r\n2\r\n3\r\n4\r\n5\x1b[2;4r\x1b[4;1H\n'); assert.equal(v.hist.length, 0); assert.equal(v.lineText(0), '1'); assert.equal(v.lineText(4), '5'); assert.equal(v.lineText(1), '3') });
t('osc title', () => { const [v, l] = mk(); v.write('\x1b]0;user@host: ~/x\x07rest'); assert.deepEqual(l.title, ['user@host: ~/x']); assert.equal(v.lineText(0), 'rest') });
t('private modes', () => { const [v] = mk(); v.write('\x1b[?1h\x1b[?2004h\x1b[?25l'); assert.equal(v.appCursor, true); assert.equal(v.bracketed, true); assert.equal(v.curVisible, false) });
t('colour change keeps pending wrap', () => { const [v] = mk(5, 5); v.write('abcde\x1b[0mf'); assert.equal(v.lineText(0), 'abcde'); assert.equal(v.lineText(1), 'f') });
t('resize shrink moves rows to history', () => { const [v] = mk(5, 10); v.write('1\r\n2\r\n3\r\n4\r\n5'); v.resize(3, 10); assert.equal(v.rows, 3); assert.equal(v.hist.length, 2); assert.equal(v.lineText(2), '5') });
t('unicode', () => { const [v] = mk(); v.write('é✓😀'); assert.equal(v.lineText(0), 'é✓😀') });
t('keys', () => {
  assert.equal(keySeq({ key: 'Enter' }, false), '\r'); assert.equal(keySeq({ key: 'ArrowUp' }, false), '\x1b[A'); assert.equal(keySeq({ key: 'ArrowUp' }, true), '\x1bOA');
  assert.equal(keySeq({ key: 'c', ctrlKey: true }, false), '\x03'); assert.equal(keySeq({ key: 'x', altKey: true }, false), '\x1bx'); assert.equal(keySeq({ key: 'C', ctrlKey: true, shiftKey: true }, false), null);
  assert.equal(keySeq({ key: 'ArrowLeft', ctrlKey: true }, false), '\x1b[1;5D'); assert.equal(keySeq({ key: 'Backspace' }, false), '\x7f'); assert.equal(keySeq({ key: 'a', metaKey: true }, false), null);
});
t('shell integration report + tailText', () => {
  const log = []; const v = new VT(4, 20, { cmd: (...a) => log.push(a) }); const b = s => Buffer.from(s).toString('base64');
  v.write('line1\r\nboom\r\n\x1b]777;kage;2;7;' + b('make test') + ';' + b('/home/x') + '\x07$ ');
  assert.deepEqual(log, [[2, 7, b('make test'), b('/home/x')]]); assert.equal(v.lineText(0), 'line1'); assert.equal(v.tailText(3), 'line1\nboom\n$');
  v.write('\x1b]777;kage;nope\x07'); assert.equal(log.length, 1);
});
console.log(`vt: ${n} groups ok`);
