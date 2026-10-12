"""v1.2.1: action log + undo, morning briefing, shell integration (ask-why), web endpoints."""
import base64, http.client, json, os, re, shutil, subprocess, sys, tempfile, threading, time, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
tmp = tempfile.mkdtemp(); HOME = Path(tmp) / 'home'; HOME.mkdir()
os.environ.update(HOME=str(HOME), KAGE_HOME_DIR=tmp, KAGE_VAULT=tmp + '/vault', XDG_CONFIG_HOME=tmp + '/cfg')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kage import briefing, picker, term, web
from kage.actions import ActionLog
from kage.agent import Agent
from kage.errors import AgentError

def git(*a, cwd): return subprocess.run(['git', '-C', str(cwd), *a], capture_output=True, text=True, check=True).stdout

class Base(unittest.TestCase):
    def setUp(self):
        self.cards = []; self.answer = True; self.ws = Path(tempfile.mkdtemp(dir=HOME))
        self.a = Agent(self.ws); self.a.actions = ActionLog(Path(tempfile.mkdtemp())); self.a.confirm_handler = lambda m: (self.cards.append(m), self.answer)[1]

class Undo(Base):
    def test_write_overwrite_and_create(self):
        self.a.tool_write_file('n.txt', 'one'); self.a.tool_write_file('n.txt', 'two'); self.assertEqual((self.ws / 'n.txt').read_text(), 'two')
        self.assertTrue(self.a.actions.undo().startswith('UNDONE')); self.assertEqual((self.ws / 'n.txt').read_text(), 'one')
        self.a.actions.undo(); self.assertFalse((self.ws / 'n.txt').exists())                      # the create is undone by deleting
        with self.assertRaises(AgentError): self.a.actions.undo()                                  # nothing left to undo
    def test_refuses_when_changed_since(self):
        self.a.tool_write_file('n.txt', 'one'); (self.ws / 'n.txt').write_text('my own edit!')
        with self.assertRaises(AgentError) as c: self.a.actions.undo()
        self.assertIn('changed since', str(c.exception)); self.assertEqual((self.ws / 'n.txt').read_text(), 'my own edit!')
        self.assertTrue(self.a.actions.undo(force=True).startswith('UNDONE'))
    def test_move_copy_extract_archive(self):
        (self.ws / 'a.txt').write_text('x'); self.a.tool_move_file('a.txt', 'b.txt'); self.a.actions.undo(); self.assertTrue((self.ws / 'a.txt').exists() and not (self.ws / 'b.txt').exists())
        self.a.tool_copy_file('a.txt', 'c.txt'); self.a.actions.undo(); self.assertFalse((self.ws / 'c.txt').exists())
        (self.ws / 'd').mkdir(); (self.ws / 'd/f.txt').write_text('f'); self.assertTrue(self.a.tool_create_archive('d').startswith('VERIFIED')); self.a.actions.undo(); self.assertFalse((self.ws / 'd.zip').exists())
        self.a.tool_create_archive('d'); self.a.tool_extract_archive('d.zip', 'out'); self.assertTrue((self.ws / 'out/d/f.txt').is_file())
        (self.ws / 'out/keep.txt').write_text('mine'); self.a.actions.undo()
        self.assertFalse((self.ws / 'out/d').exists()); self.assertTrue((self.ws / 'out/keep.txt').exists())        # only what extraction created is removed
    def test_git_commit_undo(self):
        git('init', '-q', '-b', 'main', cwd=self.ws); git('config', 'user.name', 'T', cwd=self.ws); git('config', 'user.email', 't@e.x', cwd=self.ws)
        (self.ws / 'a.txt').write_text('1'); self.assertTrue(self.a.tool_git_commit('first').startswith('VERIFIED'))
        (self.ws / 'a.txt').write_text('2'); self.a.tool_git_commit('second'); self.assertIn('second', git('log', '--oneline', cwd=self.ws))
        self.a.actions.undo(); self.assertNotIn('second', git('log', '--oneline', cwd=self.ws)); self.assertIn('a.txt', git('status', '--short', cwd=self.ws))   # changes stay
        self.a.actions.undo(); self.assertEqual(subprocess.run(['git', '-C', str(self.ws), 'rev-parse', 'HEAD'], capture_output=True).returncode, 128)        # root commit removed
    def test_tool_log_and_slash(self):
        self.a.tool_write_file('z.txt', 'z'); self.assertIn('[can undo]', self.a.tool_action_log())
        self.answer = False; self.assertTrue(self.a.tool_undo().startswith('Cancelled')); self.assertTrue((self.ws / 'z.txt').exists())
        self.answer = True; self.assertTrue(self.a.tool_undo().startswith('UNDONE')); self.assertIn('[undone]', self.a._slash('/log', lambda e: None))
    def test_commands_logged_not_readonly(self):
        import json as j
        script = iter([j.dumps({'action': 'run_command', 'args': {'command': 'echo hi > f.txt'}}), j.dumps({'action': 'run_command', 'args': {'command': 'ls'}}), j.dumps({'action': 'done', 'message': 'ok'})])
        self.a._chat = lambda *x, **k: (next(script), {}); self.a.run('do it'); rows = self.a.actions.recent()
        self.assertEqual([r['tool'] for r in rows], ['run_command']); self.assertIn('echo hi', rows[0]['summary']); self.assertFalse(rows[0]['undoable'])

class Brief(unittest.TestCase):
    def test_report(self):
        from kage import config; proj = config.HOME / 'Projects'; proj.mkdir(exist_ok=True)
        for name in ('dirty-repo', 'clean-repo'):
            d = proj / name; shutil.rmtree(d, ignore_errors=True); d.mkdir(); git('init', '-q', '-b', 'main', cwd=d); git('config', 'user.name', 'T', cwd=d); git('config', 'user.email', 't@e.x', cwd=d)
            (d / 'a').write_text('1'); git('add', '-A', cwd=d); git('commit', '-qm', 'init', cwd=d)
        (proj / 'dirty-repo/b').write_text('2'); picker._repo_cache['t'] = 0
        out = briefing.build(None); self.assertIn('Boss', out); self.assertIn('SYSTEM', out); self.assertRegex(out, r'dirty-repo\s+1 uncommitted'); self.assertIn('commit and push dirty-repo', out)
        self.assertIn('read-only', out)
        self.assertNotRegex(out, r'clean-repo\s+\d+ uncommitted')
    def test_slash_and_tool(self):
        a = Agent(Path(tempfile.mkdtemp(dir=HOME))); self.assertIn('SYSTEM', a._slash('/brief', lambda e: None)); self.assertIn('SYSTEM', a.tool_briefing())

class ShellIntegration(unittest.TestCase):
    def test_bash_reports_exit_codes(self):
        old = os.environ.get('SHELL'); os.environ['SHELL'] = shutil.which('bash')
        try:
            s = term.TermManager().open(tmp, ''); time.sleep(0.6); s.write(b'false\n'); time.sleep(0.5); s.write(b'echo hi\n'); time.sleep(0.5); s.write(b'\n'); time.sleep(0.4)
        finally: os.environ['SHELL'] = old or ''
        d = s.read_from(0, 1)[0].decode(errors='replace'); rep = [(int(m[1]), int(m[2]), base64.b64decode(m[3]).decode()) for m in re.finditer(r'\x1b\]777;kage;(\d+);(\d+);([^;\x07]*);[^\x07]*\x07', d)]
        self.assertTrue(any(c == 1 and t == 'false' for c, n, t in rep), rep); self.assertTrue(any(c == 0 and t == 'echo hi' for c, n, t in rep), rep)
        n_false = [n for c, n, t in rep if t == 'false'][0]; self.assertEqual([n for c, n, t in rep if t == 'false'].count(n_false), 1); s.close()      # an empty Enter repeats the last history number (the client dedupes on it)

class WebApi(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(dir=HOME)); web.S.agent = Agent(cls.ws); web.S.agent.actions = ActionLog(Path(tempfile.mkdtemp()))
        class Srv(ThreadingHTTPServer): daemon_threads = True
        cls.srv = Srv(('127.0.0.1', 0), web.Handler); cls.port = cls.srv.server_port; threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
    @classmethod
    def tearDownClass(cls): cls.srv.shutdown()
    def req(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=10); c.request(method, path, json.dumps(body) if body is not None else None, {'X-Kage': '1', 'Content-Type': 'application/json'})
        r = c.getresponse(); d = r.read(); c.close(); return r.status, json.loads(d)
    def test_actions_and_undo(self):
        a = web.S.agent; a.confirm_handler = lambda m: True; a.tool_write_file('w.txt', 'hi')
        st, d = self.req('GET', '/api/actions'); self.assertEqual(st, 200); self.assertTrue(d['actions'][0]['undoable'])
        st, d = self.req('POST', '/api/undo', {'id': d['actions'][0]['id']}); self.assertEqual(st, 200); self.assertIn('UNDONE', d['text']); self.assertFalse((self.ws / 'w.txt').exists())
        self.assertEqual(self.req('POST', '/api/undo', {'id': 999})[0], 400)

if __name__ == '__main__': unittest.main()
