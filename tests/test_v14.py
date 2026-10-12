"""v1.4: persistent auto-approve (safety rails kept), /auto, web settings, rashin-parity skills, emoji-free UI/prompt."""
import http.client, json, os, re, sys, tempfile, threading, unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock
tmp = tempfile.mkdtemp(); HOME = Path(tmp) / 'home'; HOME.mkdir()
os.environ.update(HOME=str(HOME), KAGE_HOME_DIR=tmp, KAGE_VAULT=tmp + '/vault', XDG_CONFIG_HOME=tmp + '/cfg')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kage import agent as agent_mod, config, skills, web
from kage.actions import ActionLog
from kage.agent import Agent
from kage.errors import AgentError

class Approvals(unittest.TestCase):
    def setUp(self):
        self.cards, self.events = [], []
        self.ws = Path(tempfile.mkdtemp(dir=HOME)); self.a = Agent(self.ws)
        self.a.actions = ActionLog(Path(tempfile.mkdtemp())); self.a._emit = self.events.append
        self.a.confirm_handler = lambda m: (self.cards.append(m), False)[1]          # the Boss says NO to every card
        self.a.auto_approve = False; self.a.trust = False

    def test_default_asks_the_boss(self):
        self.assertFalse(self.a.confirm('Copy a to b?'))
        self.assertEqual(len(self.cards), 1)

    def test_auto_approve_skips_normal_cards(self):
        self.a.auto_approve = True
        self.assertTrue(self.a.confirm('Copy a to b?'))
        self.assertEqual(self.cards, [])
        self.assertEqual([e['t'] for e in self.events], ['auto'])

    def test_auto_approve_covers_file_writes_and_git(self):
        self.a.auto_approve = True
        self.a.tool_write_file('new.txt', 'hi')
        self.assertEqual((self.ws / 'new.txt').read_text(), 'hi')
        self.assertEqual(self.cards, [])

    def test_high_risk_always_asks_even_with_auto_approve(self):
        self.a.auto_approve = True; self.a.trust = True
        self.assertFalse(self.a.confirm('rm -r everything', danger=True))
        self.assertEqual(len(self.cards), 1)
        self.assertTrue(self.cards[0].startswith('HIGH RISK'))
        self.assertNotIn('⚠', self.cards[0])

    def test_folder_scope_grant_always_asks(self):
        self.a.auto_approve = True
        self.assertFalse(self.a.confirm('Allow Kage to look outside the workspace?', scope=True))
        self.assertEqual(len(self.cards), 1)

    def test_session_trust_still_works_like_auto(self):
        self.a.trust = True
        self.assertTrue(self.a.confirm('Commit 2 files'))
        self.assertEqual(self.cards, [])

    def test_auto_approve_persists_across_sessions(self):
        cfg_dir = Path(tmp) / 'cfg-test'
        with mock.patch.object(config, 'CONFIG_DIR', cfg_dir), mock.patch.object(config, 'CONFIG_FILE', cfg_dir / 'config.json'):
            self.a.set_auto_approve(True)
            self.assertTrue(config.load()['auto_approve'])
            fresh = Agent(self.ws)                  # a new session picks the saved setting up
            self.assertTrue(fresh.auto_approve)
            fresh.set_auto_approve(False)
            self.assertFalse(config.load()['auto_approve'])

    def test_slash_auto_toggles_and_reports(self):
        cfg_dir = Path(tmp) / 'cfg-slash'
        with mock.patch.object(config, 'CONFIG_DIR', cfg_dir), mock.patch.object(config, 'CONFIG_FILE', cfg_dir / 'config.json'):
            out = self.a._slash('/auto on', lambda e: None)
            self.assertIn('ON', out); self.assertTrue(self.a.auto_approve)
            out = self.a._slash('/auto off', lambda e: None)
            self.assertIn('off', out.lower()); self.assertFalse(self.a.auto_approve)

    def test_workspace_scope_grant_passes_through_extras(self):
        # extras._grant asks with scope=True, so auto-approve must not grant outside access on its own
        self.a.auto_approve = True
        outside = Path(tmp) / 'elsewhere'; outside.mkdir()
        with self.assertRaises(AgentError):
            self.a._grant(outside)
        self.assertTrue(any('outside the workspace' in c for c in self.cards))

class Web(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ws = Path(tempfile.mkdtemp(dir=HOME)); cls.cfg = Path(tempfile.mkdtemp()) / 'cfg'
        cls.patch = [mock.patch.object(config, 'CONFIG_DIR', cls.cfg), mock.patch.object(config, 'CONFIG_FILE', cls.cfg / 'config.json')]
        for p in cls.patch: p.start()
        web.S.agent = Agent(cls.ws); web.S.agent.actions = ActionLog(Path(tempfile.mkdtemp()))
        class Server(ThreadingHTTPServer): daemon_threads = True
        cls.srv = Server(('127.0.0.1', 0), web.Handler); cls.port = cls.srv.server_port
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown(); cls.srv.server_close()
        for p in cls.patch: p.stop()

    def req(self, method, path, body=None):
        c = http.client.HTTPConnection('127.0.0.1', self.port, timeout=5)
        headers = {'X-Kage': '1', 'Content-Type': 'application/json', 'Host': f'127.0.0.1:{self.port}'}
        c.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
        r = c.getresponse(); data = json.loads(r.read() or b'{}'); c.close(); return r.status, data

    def test_state_reports_auto_approve_and_settings_toggles_it(self):
        status, st = self.req('GET', '/api/state'); self.assertEqual(status, 200); self.assertIn('auto_approve', st)
        status, out = self.req('POST', '/api/settings', {'auto_approve': True}); self.assertEqual(status, 200); self.assertTrue(out['auto_approve'])
        self.assertTrue(web.S.agent.auto_approve); self.assertTrue(config.load()['auto_approve'])
        _, st = self.req('GET', '/api/state'); self.assertTrue(st['auto_approve'])
        _, out = self.req('POST', '/api/settings', {'auto_approve': False}); self.assertFalse(out['auto_approve'])

class Skills(unittest.TestCase):
    NAMES = {'code-search', 'change-safety', 'durable-knowledge', 'scout'}

    def test_rashin_skill_set_is_present(self):
        names = {s['name'] for s in skills.all_skills()}
        self.assertTrue(self.NAMES <= names, names)

    def test_code_search_has_routing_and_read_only_rules(self):
        body = skills.get('code-search')
        for needle in ['Routing table', 'overview()', 'search_code', 'references(name)', 'impact(path)', 'peek(', 'Trust the cited results', 'alternate strategy']:
            self.assertIn(needle, body)

    def test_change_safety_covers_before_after_and_resume(self):
        body = skills.get('change-safety')
        for needle in ['Before editing a file', 'After editing', 'Resuming a task', 'impact(path)', 'changed()', 'doctor()', 'wip()']:
            self.assertIn(needle, body)

    def test_durable_knowledge_uses_kinds_that_exist(self):
        from kage.knowledge import KINDS
        body = skills.get('durable-knowledge')
        for kind in KINDS: self.assertIn(kind, body)
        self.assertIn('note_propose', body); self.assertIn('note_accept', body); self.assertIn('notes()', body)

    def test_scout_is_read_only_and_returns_the_schema(self):
        body = skills.get('scout')
        self.assertIn('read-only', body.lower()); self.assertIn('"summary"', body); self.assertIn('"files"', body); self.assertIn('"architecture"', body)

class NoEmojiInProduct(unittest.TestCase):
    # emoji presentation code points (pictographs plus the common symbol-emoji); plain text glyphs such as the close cross are fine
    PAT = re.compile('[\U0001F000-\U0001FAFF\u2601\u2614\u2615\u2648-\u2653\u26A0\u26A1\u26AA\u26AB\u26BD\u26BE\u26C4\u26C5\u26D4\u26EA\u26F2\u26F3\u26F5\u26FA\u26FD'
                     '\u2699\u2705\u270A\u270B\u2728\u274C\u274E\u2753-\u2755\u2757\u2795-\u2797\u27B0\u27BF\u2B50\u2B55]')
    def test_prompt_and_ui_have_no_emoji_glyphs(self):
        root = Path(__file__).resolve().parent.parent / 'kage'
        files = [root / 'agent.py', root / 'projtools.py', root / 'picker.py', root / 'ui' / 'app.js', root / 'ui' / 'index.html']
        for f in files:
            hits = [l for l in f.read_text().splitlines() if self.PAT.search(l) and 'CLAIM = re.compile' not in l]
            self.assertEqual(hits, [], f'{f.name} still has emoji: {hits[:2]}')

    def test_style_rule_is_in_the_prompt(self):
        self.assertIn('No emojis', agent_mod.SYSTEM)
        self.assertIn('answer first', agent_mod.SYSTEM)

if __name__ == '__main__': unittest.main()
