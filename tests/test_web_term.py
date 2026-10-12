"""Live HTTP test: PTY terminal streaming, resize, close; image serving only for registered tokens; host/header guards."""
import base64, http.client, json, os, sys, tempfile, threading, time, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
tmp = tempfile.mkdtemp(); HOME = Path(tmp) / 'home'; HOME.mkdir()
os.environ.update(HOME=str(HOME), KAGE_HOME_DIR=tmp, KAGE_VAULT=tmp + '/vault', XDG_CONFIG_HOME=tmp + '/cfg')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kage import web
from kage.agent import Agent
PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082')

class Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(dir=HOME)); web.S.agent = Agent(cls.ws)
        class Srv(ThreadingHTTPServer): daemon_threads = True
        cls.srv = Srv(('127.0.0.1', 0), web.Handler); cls.port = cls.srv.server_port; threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown(); web.S.terms.close_all()
    def req(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10); h = {'X-Kage': '1', 'Content-Type': 'application/json', **(headers or {})}
        c.request(method, path, json.dumps(body) if body is not None else None, h); r = c.getresponse(); d = r.read(); c.close(); return r, d
    def post(self, path, body): r, d = self.req('POST', path, body); return r.status, json.loads(d)
    def test_terminal_roundtrip(self):
        st, info = self.post('/api/term/open', {'rows': 24, 'cols': 80}); self.assertEqual(st, 200); sid = info['id']
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10); c.request('GET', f'/api/term/stream?id={sid}&from=0'); r = c.getresponse(); self.assertEqual(r.status, 200)
        time.sleep(0.4); enc = lambda s: base64.b64encode(s.encode()).decode()
        self.assertEqual(self.post('/api/term/resize', {'id': sid, 'rows': 40, 'cols': 120})[0], 200)
        self.assertEqual(self.post('/api/term/input', {'id': sid, 'd': enc('echo web-$((20+22)); stty size\n')})[0], 200)
        got, end = '', time.time() + 8
        while time.time() < end and ('web-42' not in got or '40 120' not in got):
            line = r.readline()
            if line.strip(): m = json.loads(line); got += base64.b64decode(m['d']).decode(errors='replace') if 'd' in m else ''
        self.assertIn('web-42', got); self.assertIn('40 120', got)
        self.post('/api/term/input', {'id': sid, 'd': enc('exit 5\n')}); code = None; end = time.time() + 8
        while time.time() < end and code is None:
            line = r.readline()
            if line.strip():
                m = json.loads(line); code = m.get('exit', code)
        self.assertEqual(code, 5); c.close()
        r2, d = self.req('GET', '/api/term/list'); self.assertTrue(any(t['id'] == sid for t in json.loads(d)['terminals']))
        self.assertEqual(self.post('/api/term/close', {'id': sid})[0], 200); self.assertEqual(self.req('GET', f'/api/term/stream?id={sid}')[0].status, 404)
    def test_guards(self):
        self.assertEqual(self.post('/api/term/input', {'id': 'nope', 'd': ''})[0], 400)
        r, _ = self.req('POST', '/api/term/open', {}, {'X-Kage': '0'}); self.assertEqual(r.status, 403)
        r, _ = self.req('GET', '/api/term/list', None, {'Host': 'evil.example'}); self.assertEqual(r.status, 403)
        st, info = self.post('/api/term/open', {}); self.assertEqual(self.post('/api/term/input', {'id': info['id'], 'd': '***'})[0], 400); self.post('/api/term/close', {'id': info['id']})
    def test_media_only_registered(self):
        f = self.ws / 'p.png'; f.write_bytes(PNG); (self.ws / 'secret.txt').write_text('no'); a = web.S.agent
        a.media['tok1'] = str(f); a.media['tok2'] = str(self.ws / 'secret.txt')
        r, d = self.req('GET', '/api/media?t=tok1'); self.assertEqual((r.status, d[:4]), (200, b'\x89PNG')); self.assertIn('sandbox', r.getheader('Content-Security-Policy'))
        r, d = self.req('GET', '/api/media?t=tok1&thumb=1'); self.assertEqual(r.status, 200)
        self.assertEqual(self.req('GET', '/api/media?t=tok2')[0].status, 404)      # registered but not an image
        self.assertEqual(self.req('GET', '/api/media?t=../../etc/passwd')[0].status, 404)

if __name__ == '__main__': unittest.main()
