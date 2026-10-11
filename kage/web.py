"""Local-only web server for Kage. Standard library only.

Streams agent progress as NDJSON (tokens, tool steps, approvals) instead of polling.
Hardened for loopback: Host header allow-list (DNS rebinding), same-origin + custom header on POST.
"""
from __future__ import annotations
import json, mimetypes, os, re, threading, time, uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from . import APP, KANJI, VERSION, config, picker, skills
from .agent import Agent, AgentError, Cancelled
from .index import is_secret

UI = Path(__file__).parent / 'ui'
THEME_FILE = config.HOME / '.cache/ryoku/colors.json'

class State:
    agent: Agent = None
    run_lock = threading.Lock()
    pending: dict = {}
    models = {'t': 0, 'v': []}
    index_info = None

S = State()

def confirm(message: str, emit) -> bool:
    aid = uuid.uuid4().hex[:8]; ev = threading.Event(); S.pending[aid] = {'ev': ev, 'ok': False}
    emit({'t': 'approval', 'id': aid, 'message': message})
    deadline = time.time() + 180; ok = False
    while time.time() < deadline:
        if ev.wait(0.4): ok = S.pending[aid]['ok']; break
        if S.agent.cancel.is_set(): break
    S.pending.pop(aid, None); emit({'t': 'approval_done', 'id': aid, 'ok': ok}); return ok

def build_index_info():
    def go():
        try: S.index_info = S.agent.index.stats()
        except Exception as e: S.index_info = {'error': str(e)}
    S.index_info = {'building': True}
    threading.Thread(target=go, daemon=True).start()

def theme_colors():
    try: c = json.loads(THEME_FILE.read_text())
    except (OSError, ValueError): return {}
    def vivid(h):
        try: r, g, b = (int(h.lstrip('#')[i:i + 2], 16) for i in (0, 2, 4))
        except (ValueError, AttributeError): return None
        m = max(r, g, b)
        if m < 120 and m > 0: k = 190 / m; r, g, b = (min(255, int(x * k)) for x in (r, g, b))
        return f'#{r:02x}{g:02x}{b:02x}'
    return {'accent': vivid(c.get('color4') or c.get('color1')), 'accent2': vivid(c.get('color5') or c.get('color3'))}

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Kage/' + VERSION
    def log_message(self, fmt, *args): pass

    # ---------------------------------------------------------- helpers
    def _host_ok(self):
        h = (self.headers.get('Host') or '').lower(); p = self.server.server_port
        return h in (f'127.0.0.1:{p}', f'localhost:{p}', f'[::1]:{p}')
    def _json(self, status, obj, extra=None):
        data = json.dumps(obj).encode()
        self.send_response(status); self.send_header('Content-Type', 'application/json; charset=utf-8'); self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store'); self.send_header('X-Content-Type-Options', 'nosniff')
        for k, v in (extra or {}).items(): self.send_header(k, v)
        self.end_headers(); self.wfile.write(data)
    def _body(self):
        try: size = int(self.headers.get('Content-Length', '0'))
        except ValueError: size = 0
        if size < 1 or size > 400_000: raise ValueError('Request body must be between 1 and 400000 bytes.')
        return json.loads(self.rfile.read(size))
    def _static(self, rel):
        p = (UI / rel).resolve()
        if UI.resolve() not in p.parents or not p.is_file(): return self._json(404, {'error': 'Not found'})
        data = p.read_bytes(); st = p.stat(); etag = f'"{st.st_mtime_ns:x}-{st.st_size:x}"'
        if self.headers.get('If-None-Match') == etag:
            self.send_response(304); self.send_header('ETag', etag); self.send_header('Content-Length', '0'); self.end_headers(); return
        ctype = mimetypes.guess_type(p.name)[0] or 'application/octet-stream'
        if p.suffix in ('.js', '.css', '.html', '.svg'): ctype += '; charset=utf-8'
        self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(data))); self.send_header('ETag', etag)
        self.send_header('Cache-Control', 'max-age=86400' if p.suffix in ('.woff2',) else 'no-cache'); self.send_header('X-Content-Type-Options', 'nosniff')
        if p.name == 'index.html':
            self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
            self.send_header('X-Frame-Options', 'DENY')
        self.end_headers(); self.wfile.write(data)

    # ---------------------------------------------------------- GET
    def do_GET(self):
        if not self._host_ok(): return self._json(403, {'error': 'Bad Host header.'})
        u = urlparse(self.path); path = u.path; q = {k: v[0] for k, v in parse_qs(u.query).items()}
        a = S.agent
        if path in ('/', '/index.html'): return self._static('index.html')
        if path.startswith('/static/'): return self._static(path[len('/static/'):])
        if path == '/favicon.ico': return self._static('favicon.svg')
        if path == '/api/health': return self._json(200, {'ok': True, 'app': APP})
        if path == '/api/state':
            if time.time() - S.models['t'] > 10: S.models.update(t=time.time(), v=a.ollama_models())
            cfg = config.load()
            return self._json(200, {'app': APP, 'kanji': KANJI, 'version': VERSION, 'workspace': str(a.workspace) if a.workspace else None, 'workspace_name': a.workspace.name if a.workspace else None,
                                    'model': a.model, 'models': S.models['v'], 'ollama': bool(S.models['v']), 'search': a.search_status(), 'index': S.index_info if a.workspace else None,
                                    'vault': str(a.vault.root), 'machine': a.vault.state().get('machine', ''), 'recents': [r for r in cfg.get('recents', []) if os.path.isdir(r)],
                                    'last': cfg.get('workspace') if os.path.isdir(cfg.get('workspace') or '') else None, 'system_access': a.system_access, 'busy': S.run_lock.locked(),
                                    'native_picker': bool(__import__('shutil').which('zenity') or __import__('shutil').which('kdialog') or __import__('shutil').which('yad')), 'skills': [s['name'] for s in skills.all_skills()]})
        if path == '/api/theme': return self._json(200, theme_colors())
        if path == '/api/places': return self._json(200, {'places': picker.places(), 'repos': picker.repos(30), 'recents': [{'label': Path(r).name, 'path': r} for r in config.load().get('recents', []) if os.path.isdir(r)]})
        if path == '/api/fs': return self._json(200, picker.list_dir(q.get('path', ''), q.get('hidden') == '1'))
        if path == '/api/index':
            if not a.index: return self._json(409, {'error': 'No workspace selected.'})
            return self._json(200, a.index.run(q.get('cmd', ''), q.get('q', '')))
        if path == '/api/peek':
            if not a.index: return self._json(409, {'error': 'No workspace selected.'})
            return self._json(200, self._peek(q.get('path', ''), int(q.get('line', '1') or 1)))
        if path == '/api/vault':
            return self._json(200, {'root': str(a.vault.root), 'tree': a.vault.tree(), 'state': a.vault.state(), 'memory': a.vault.memory_read(limit=60)})
        if path == '/api/vault/file':
            try: return self._json(200, {'path': q.get('path'), 'content': a.vault.read(q.get('path', ''))})
            except (ValueError, OSError) as e: return self._json(404, {'error': str(e)})
        if path == '/api/skills': return self._json(200, {'skills': skills.all_skills()})
        if path == '/api/notes':
            return self._json(200, {'notes': a.knowledge.notes() if a.knowledge else [], 'lint': a.knowledge.lint() if a.knowledge else ''})
        self._json(404, {'error': 'Not found'})

    def _peek(self, rel, line):
        ix = self.server_agent().index; r = ix.resolve_path(rel)
        if not r: return {'error': f'Not indexed: {rel}'}
        full = (ix.root / r[1]).resolve()
        if is_secret(full) or ix.root not in full.parents: return {'error': 'Refusing to show this file.'}
        lines = full.read_text(errors='replace').split('\n'); a = max(1, line - 25); b = min(len(lines), line + 60)
        return {'path': r[1], 'start': a, 'end': b, 'total': len(lines), 'line': line, 'lines': lines[a - 1:b]}
    def server_agent(self): return S.agent

    # ---------------------------------------------------------- POST
    def do_POST(self):
        if not self._host_ok(): return self._json(403, {'error': 'Bad Host header.'})
        origin = self.headers.get('Origin'); p = self.server.server_port
        if (origin and origin not in (f'http://127.0.0.1:{p}', f'http://localhost:{p}')) or self.headers.get('X-Kage') != '1':
            return self._json(403, {'error': 'Cross-origin requests are not allowed.'})
        path = urlparse(self.path).path; a = S.agent
        try: data = self._body() if path != '/api/pick' and path != '/api/stop' and path != '/api/reset' and path != '/api/vault/reindex' else {}
        except Exception as e: return self._json(400, {'error': str(e)})
        if path == '/api/chat': return self._chat(str(data.get('message', '')).strip())
        if path == '/api/approve':
            item = S.pending.get(str(data.get('id', '')))
            if not item: return self._json(409, {'error': 'No action is waiting for approval.'})
            item['ok'] = bool(data.get('approved')); item['ev'].set(); return self._json(200, {'ok': True})
        if path == '/api/stop':
            a.stop()
            for it in list(S.pending.values()): it['ev'].set()
            return self._json(200, {'ok': True})
        if path == '/api/reset':
            if S.run_lock.locked(): return self._json(409, {'error': 'Busy.'})
            a.reset(); return self._json(200, {'ok': True})
        if path == '/api/workspace':
            if S.run_lock.locked(): return self._json(409, {'error': 'Finish or stop the current task first.'})
            d = picker.valid(str(data.get('path', '')))
            if not d: return self._json(400, {'error': 'That is not a folder I can open.'})
            try: a.set_workspace(d)
            except AgentError as e: return self._json(400, {'error': str(e)})
            build_index_info(); a.warm(); return self._json(200, {'ok': True, 'workspace': str(d)})
        if path == '/api/model':
            m = str(data.get('model', '')).strip()
            if not re.fullmatch(r'[\w.:/@-]{1,80}', m): return self._json(400, {'error': 'Invalid model name.'})
            a.set_model(m); return self._json(200, {'ok': True})
        if path == '/api/pick':
            return self._json(200, {'path': picker.native_pick(str(a.workspace or config.HOME))})
        if path == '/api/vault/file':
            try: a.vault.write(str(data.get('path', '')), str(data.get('content', ''))); return self._json(200, {'ok': True})
            except (ValueError, OSError) as e: return self._json(400, {'error': str(e)})
        if path == '/api/vault/reindex': return self._json(200, a.vault.reindex())
        if path == '/api/vault/memory':
            try: return self._json(200, {'text': a.vault.memory_add(str(data.get('note', '')))})
            except ValueError as e: return self._json(400, {'error': str(e)})
        if path == '/api/index/reindex':
            if not a.index: return self._json(409, {'error': 'No workspace selected.'})
            a.index.refresh(force=True); S.index_info = a.index.stats(); return self._json(200, S.index_info)
        self._json(404, {'error': 'Not found'})

    def _chat(self, message):
        a = S.agent
        if not message or len(message) > 12000: return self._json(400, {'error': 'Message must be between 1 and 12000 characters.'})
        if not a.workspace: return self._json(409, {'error': 'Choose a workspace folder first.'})
        if not S.run_lock.acquire(blocking=False): return self._json(429, {'error': 'Kage is already working on something. Stop it or wait.'})
        self.send_response(200); self.send_header('Content-Type', 'application/x-ndjson; charset=utf-8'); self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Accel-Buffering', 'no'); self.send_header('Connection', 'close'); self.end_headers(); self.close_connection = True
        alive = [True]
        def emit(ev):
            if not alive[0]: return
            try: self.wfile.write((json.dumps(ev) + '\n').encode()); self.wfile.flush()
            except OSError: alive[0] = False; a.stop()
        a.confirm_handler = lambda m: confirm(m, emit)
        try: a.run(message, emit)
        except Exception as e: emit({'t': 'done', 'text': f'Internal error: {type(e).__name__}: {e}', 'error': True, 'stats': {}})
        finally: S.run_lock.release()

def serve(host='127.0.0.1', port=3600, workspace=None, model=None, url=None, max_steps=20, system_access=False, open_browser=False):
    if host not in ('127.0.0.1', 'localhost', '::1'): raise SystemExit('For safety Kage only serves on loopback. Use --host 127.0.0.1.')
    a = Agent(None, model, url, max_steps, system_access); S.agent = a
    if workspace: a.set_workspace(workspace); build_index_info()
    a.warm()
    class Server(ThreadingHTTPServer): daemon_threads = True; allow_reuse_address = True
    try: srv = Server((host, port), Handler)
    except OSError as e: raise SystemExit(f'Cannot bind {host}:{port}: {e}. If Rashin owns port 3600 stop it first, or use --port 3601.')
    url_ = f'http://127.0.0.1:{port}/'
    print(f'{KANJI} {APP} {VERSION} · {url_}\n   model {a.model} · workspace {a.workspace or "(choose in the browser)"} · vault {a.vault.root}\n   local only · Ctrl+C to stop')
    if open_browser:
        import webbrowser; threading.Timer(0.6, lambda: webbrowser.open(url_)).start()
    try: srv.serve_forever()
    except KeyboardInterrupt: print('\nStopping Kage…')
    finally: srv.server_close()
