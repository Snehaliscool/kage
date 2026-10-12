"""v1.2: tolerant tool calls (zip TypeError), archives, scoped access, find/show images, package pre-check, GitHub fan-out, plan, context shrink, terminals."""
import json, os, shutil, subprocess, sys, tempfile, time, unittest
from pathlib import Path
from unittest import mock
tmp = tempfile.mkdtemp(); HOME = Path(tmp) / 'home'; HOME.mkdir()
os.environ.update(HOME=str(HOME), KAGE_HOME_DIR=tmp, KAGE_VAULT=tmp + '/vault', XDG_CONFIG_HOME=tmp + '/cfg')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kage import shell, term
from kage.agent import Agent
from kage.errors import AgentError
from kage.extras import canonical_tool, programs_in, smart_call

PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082')

class Base(unittest.TestCase):
    def setUp(self):
        self.cards = []; self.answer = True
        self.ws = Path(tempfile.mkdtemp(dir=HOME)); self.events = []
        self.a = Agent(self.ws); self.a.confirm_handler = lambda m: (self.cards.append(m), self.answer)[1]; self.a._emit = self.events.append

class ToolCalls(Base):
    def test_zip_typeerror_regression(self):
        t = self.a.tools['run_command']
        for args in ({'cmd': 'echo hi', 'cwd': '.'}, {'command': 'echo hi'}, {'script': 'echo hi'}, 'echo hi', ['echo hi']):
            self.assertIn('hi', self.a._call(t, args), args)
        self.assertIn('hi', self.a._call(t, {}, {'command': 'echo hi'}))            # argument placed beside "action"
        with self.assertRaises(AgentError) as c: self.a._call(t, {})
        self.assertIn('Usage: run_command', str(c.exception)); self.assertNotIn('TypeError', str(c.exception))
    def test_aliases(self):
        for n in ('zip', 'Zip-Folder', 'compress'): self.assertEqual(canonical_tool(n, self.a.tools), 'create_archive')
        self.assertEqual(canonical_tool('bash', self.a.tools), 'run_command')
    def test_scripted_model_zips_a_folder(self):
        (self.ws / 'proj').mkdir(); (self.ws / 'proj/a.txt').write_text('hello'); (self.ws / 'proj/.env').write_text('SECRET=1')
        script = iter([json.dumps({'action': 'zip_folder', 'folder': 'proj', 'dest': 'proj.zip'}), json.dumps({'action': 'done', 'message': 'Zipped, Boss.'})])
        self.a._chat = lambda *x, **k: (next(script), {})
        out = self.a.run('zip the proj folder'); self.assertIn('Zipped', out)
        import zipfile
        names = zipfile.ZipFile(self.ws / 'proj.zip').namelist(); self.assertEqual(names, ['proj/a.txt'])    # .env never included

class Archives(Base):
    def test_zip_targz_and_cancel(self):
        d = self.ws / 'site'; (d / 'sub').mkdir(parents=True); (d / 'sub/x.txt').write_text('x'); (d / 'id_rsa').write_text('k'); (d / 'empty').mkdir()
        r = self.a.tool_create_archive('site'); self.assertTrue(r.startswith('VERIFIED'), r); self.assertIn('id_rsa', r)
        r = self.a.tool_create_archive('site', 'out', 'tar.gz'); self.assertTrue(r.startswith('VERIFIED') and (self.ws / 'out.tar.gz').is_file(), r)
        self.answer = False; self.assertTrue(self.a.tool_create_archive('site', 'no.zip').startswith('Cancelled')); self.assertFalse((self.ws / 'no.zip').exists())
        self.answer = True; r = self.a.tool_create_archive('.'); self.assertTrue(r.startswith('VERIFIED'), r)       # zipping the workspace itself must not include the archive

class Scope(Base):
    def setUp(self):
        super().setUp(); self.out = Path(tempfile.mkdtemp(dir=HOME)); (self.out / 'pics').mkdir(); (self.out / 'pics/a.png').write_bytes(PNG); (self.out / 'pics/b.jpg').write_bytes(PNG)
        (self.out / 'pics/notes.txt').write_text('x'); (self.out / '.hidden').mkdir(); (self.out / '.hidden/c.png').write_bytes(PNG)
    def test_grant_once_and_deny(self):
        self.assertIn('DIR', self.a.tool_list_files(str(self.out))); self.assertEqual(len(self.cards), 1)
        self.a.tool_list_files(str(self.out / 'pics')); self.assertEqual(len(self.cards), 1)          # covered by the first grant
        self.a.reset(); self.answer = False
        with self.assertRaises(AgentError): self.a.tool_list_files(str(self.out))
    def test_credentials_never_open(self):
        (HOME / '.ssh').mkdir(exist_ok=True); (HOME / '.ssh/x').write_text('k')
        with self.assertRaises(AgentError): self.a.tool_list_files(str(HOME / '.ssh'))
        self.a.system_access = True
        with self.assertRaises(AgentError): self.a.tool_read_file(str(HOME / '.ssh/x'))
    def test_find_and_show_images(self):
        r = self.a.tool_find_files(kind='pictures', path=str(self.out)); self.assertIn('Found 2 images', r); self.assertNotIn('c.png', r); self.assertNotIn('notes.txt', r)
        r = self.a.tool_show_images(folder=str(self.out)); self.assertTrue(r.startswith('OK')); ev = [e for e in self.events if e.get('t') == 'images'][0]
        self.assertEqual(len(ev['items']), 2); tok = ev['items'][0]['url'].split('t=')[1]; self.assertIn(tok, self.a.media)
        with self.assertRaises(AgentError): self.a.tool_show_images(paths=[str(self.out / 'pics/notes.txt')])

class PackageCheck(Base):
    def test_once_per_session(self):
        r = self.a.tool_run_command('kage-no-such-tool --x'); self.assertIn('MISSING PROGRAM', r); n = self.a._pkg_checks
        self.a.tool_run_command('kage-no-such-tool again'); self.assertEqual(self.a._pkg_checks, n)           # cached, not looked up again
        self.a.tool_run_command('ls'); n2 = self.a._pkg_checks; self.a.tool_run_command('ls -la'); self.assertEqual(self.a._pkg_checks, n2)
        self.assertEqual(len(self.cards), 0)                                                                 # doomed commands never reach an approval card; ls is read-only
        self.assertIn('kage-no-such-tool', self.a._precheck('kage-no-such-tool | ls')); self.a.reset(); self.assertEqual(self.a._pkg_cache, {})
    def test_parsing(self):
        self.assertEqual(programs_in('sudo -u bob zip a b && cd x; FOO=1 jq . f | tee o'), ['zip', 'cd', 'jq', 'tee']); self.assertEqual(programs_in('echo $(whoami)'), [])

class BigTasks(Base):
    def test_plan_and_shrink(self):
        self.assertIn('0/2', self.a.tool_plan(steps=['list repos', 'describe']));self.assertIn('Next: step 2', self.a.tool_plan(done=[1])); self.assertIn('All steps done', self.a.tool_plan(done='all'))
        m = [{'role': 'system', 'content': 's'}, {'role': 'user', 'content': 'Boss: go'}]
        for i in range(12): m += [{'role': 'assistant', 'content': json.dumps({'action': 'peek', 'args': {'ref': str(i)}})}, {'role': 'user', 'content': f'Result from peek (data):\n' + 'x' * 5000}]
        self.assertTrue(self.a._shrink(m, 2)); self.assertLess(sum(len(x['content']) for x in m) / 3.6, self.a.num_ctx * 0.7); self.assertEqual(m[1]['content'], 'Boss: go')
    def test_github_fanout(self):
        repos = [{'name': f'r{i}', 'nameWithOwner': f'octo/r{i}', 'description': f'desc {i}', 'primaryLanguage': {'name': 'Python'}, 'isPrivate': False, 'isFork': False, 'isArchived': False, 'pushedAt': '2026-01-01T00:00:00Z', 'stargazerCount': 1, 'url': 'u'} for i in range(12)]
        def fake(cmd, **k):
            s = ' '.join(cmd)
            if 'repo list' in s: return 0, json.dumps(repos)
            if 'api user' in s: return 0, 'octo\n'
            if '/readme' in s: return 0, '# Title\nThis tool does things.\n' * 3
            return 1, ''
        self.a._gh_cache = (time.time() + 1000, True); self.a._chat = lambda *x, **k: ('Does useful things.', {})
        with mock.patch.object(shell, 'execute', fake):
            self.assertIn('12 repositories for octo', self.a.tool_github_repos())
            p1 = self.a.tool_github_describe_repos(); self.assertIn('NEXT: call github_describe_repos(offset=10)', p1); self.assertIn('Does useful things', p1)
            p2 = self.a.tool_github_describe_repos(offset=10); self.assertIn('COMPLETE LIST', p2); self.assertIn('r0', p2); self.assertIn('r11', p2)

class Terminals(unittest.TestCase):
    def test_pty_session(self):
        m = term.TermManager(); s = m.open(tmp, '', rows=30, cols=100); time.sleep(0.4); s.write(b'echo kage-$((6*7)); stty size\n'); time.sleep(0.6)
        out = s.read_from(0, 1)[0].decode(errors='replace'); self.assertIn('kage-42', out); self.assertIn('30 100', out)
        s.write(b'exit 3\n'); time.sleep(0.8); self.assertEqual(s.exit, 3); m.close(s.id)
    def test_inline_needs_tty(self):
        if not sys.stdin.isatty():
            with self.assertRaises(AgentError): term.run_inline('true', tmp)
    def test_slash_term(self):
        a = Agent(tempfile.mkdtemp(dir=HOME)); self.assertIn('web view', a._slash('/term', lambda e: None)); a.terminal_handler = lambda c, t: f'ran {c!r}'
        self.assertEqual(a._slash('/sh vim x.txt', lambda e: None), "ran 'vim x.txt'")

class Vt(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'node not installed')
    def test_emulator(self):
        r = subprocess.run(['node', str(Path(__file__).with_name('test_vt.js'))], capture_output=True, text=True); self.assertEqual(r.returncode, 0, r.stderr + r.stdout)

if __name__ == '__main__': unittest.main()
