"""v1.3: project description, per-file descriptions, view_file, edit_file (diff approval, undo, safety), slash commands."""
import json, os, sys, tempfile, time, unittest
from pathlib import Path
tmp = tempfile.mkdtemp(); HOME = Path(tmp) / 'home'; HOME.mkdir()
os.environ.update(HOME=str(HOME), KAGE_HOME_DIR=tmp, KAGE_VAULT=tmp + '/vault', XDG_CONFIG_HOME=tmp + '/cfg')
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kage import projscan
from kage.actions import ActionLog
from kage.agent import Agent
from kage.errors import AgentError

PNG = bytes.fromhex('89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082')

def build(ws: Path):
    (ws / 'app').mkdir(); (ws / 'tests').mkdir(); (ws / 'node_modules/dep').mkdir(parents=True)
    (ws / 'README.md').write_text('# Tideway\n\nTideway is a small tool that turns tide tables into calendar files you can import anywhere.\n\n## Usage\nrun it\n')
    (ws / 'app/__init__.py').write_text('"""Tideway core package."""\n')
    (ws / 'app/main.py').write_text('"""Command line entry point: reads a tide table and writes an .ics file."""\nimport sys\n\ndef main(argv):\n    name = argv[0]\n    return name\n\nif __name__ == "__main__":\n    main(sys.argv[1:])\n')
    (ws / 'app/util.py').write_text('def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n\ndef clamp(x, lo, hi):\n    return x\n\nclass Table:\n    pass\n')
    (ws / 'app/run.sh').write_text('#!/bin/sh\n# Launches the tideway CLI with the right python\npython3 -m app "$@"\n')
    (ws / 'package.json').write_text(json.dumps({'name': 'x', 'description': 'Web viewer for tide tables', 'scripts': {'dev': 'vite', 'build': 'vite build'}}))
    (ws / 'tests/test_main.py').write_text('import unittest\n')
    (ws / '.env').write_text('TOKEN=supersecret\n'); (ws / 'logo.png').write_bytes(PNG)
    (ws / 'node_modules/dep/index.js').write_text('module.exports = 1\n')

class Base(unittest.TestCase):
    def setUp(self):
        self.cards = []; self.answer = True; self.events = []
        self.ws = Path(tempfile.mkdtemp(dir=HOME)); build(self.ws)
        self.a = Agent(self.ws); self.a.actions = ActionLog(Path(tempfile.mkdtemp()))
        self.a.confirm_handler = lambda m: (self.cards.append(m), self.answer)[1]; self.a._emit = self.events.append
    def ev(self, t): return [e for e in self.events if e.get('t') == t]

class Describe(Base):
    def test_project_card_and_dossier(self):
        out = self.a.tool_describe_project()
        self.assertTrue(out.startswith('OK (exit 0)')); self.assertIn('Tideway is a small tool', out); self.assertIn('Python', out)
        card = self.ev('project')[0]['card']
        self.assertEqual(card['name'], self.ws.name); self.assertIn('Python', card['primary'])
        self.assertNotIn('node_modules', out); self.assertNotIn('supersecret', json.dumps(self.events) + out)       # vendor dirs skipped, secrets never read
        self.assertIn('app/main.py', [k['path'] for k in card['key_files']] + card['entries'])
        self.assertTrue(any(d['name'] == 'app' and 'Tideway core' in d['desc'] for d in card['dirs']))             # folder described from its __init__ docstring
        self.assertEqual(self.a._describe('.')['secrets_skipped'], 1)
    def test_scan_is_fast_and_cached(self):
        r1 = projscan.scan(self.ws, force=True); t = time.time(); r2 = projscan.scan(self.ws)
        self.assertTrue(r2['cached']); self.assertLess(time.time() - t, 0.05); self.assertLess(r1['ms'], 2000)
        (self.ws / 'new.py').write_text('x = 1\n'); self.assertFalse(projscan.scan(self.ws)['cached'])              # a changed tree rescans
    def test_describe_files_one_liners(self):
        out = self.a.tool_describe_files('app'); self.assertIn('Command line entry point', out); self.assertIn('Launches the tideway CLI', out); self.assertIn('defines clamp, Table', out)
        rows = self.ev('files')[0]['rows']; self.assertTrue(all(r['path'].startswith('app/') for r in rows))
        self.assertIn('Web viewer for tide tables', self.a.tool_describe_files('.', 'package.json'))
        self.assertIn('Tideway', self.a.tool_describe_files('.', 'README*'))
        self.assertIn('secret, not read', self.a.tool_describe_files('.', '.env', recursive=True) if False else projscan.describe_file(self.ws / '.env')['desc'])
        self.assertNotIn('supersecret', out)
    def test_front_matter_description(self):
        (self.ws / 'skill.md').write_text('---\nname: demo\ndescription: Use when the Boss wants a demo.\n---\n# Demo\nbody\n')
        self.assertEqual(projscan.describe_file(self.ws / 'skill.md')['desc'], 'Use when the Boss wants a demo.')
    def test_large_python_file_still_gets_its_docstring(self):
        (self.ws / 'huge.py').write_text('"""The big one."""\n' + ''.join(f'def f{i}(x):\n    return x + {i}\n\n' for i in range(4000)))   # >60 KB: must not be parsed truncated
        self.assertEqual(projscan.describe_file(self.ws / 'huge.py')['desc'], 'The big one.')
    def test_describe_files_recursive_pattern_skips_vendor(self):
        out = self.a.tool_describe_files('.', '*.py', recursive=True); self.assertIn('app/main.py', out); self.assertNotIn('node_modules', out)
    def test_outside_workspace_asks(self):
        other = Path(tempfile.mkdtemp(dir=HOME)); (other / 'a.py').write_text('"""other"""\n'); self.answer = False
        with self.assertRaises(AgentError): self.a.tool_describe_project(str(other))
        self.answer = True; self.assertIn('other', self.a.tool_describe_files(str(other)))

class View(Base):
    def test_view_range_and_cite_form(self):
        out = self.a.tool_view_file('app/main.py', 4, 5); self.assertIn('lines 4-5 of 9', out); self.assertIn('    4│ def main(argv):', out)
        e = self.ev('file')[-1]; self.assertEqual((e['start'], e['end'], e['total']), (4, 5, 9)); self.assertEqual(e['text'], 'def main(argv):\n    name = argv[0]')
        self.assertIn('lines 4-5', self.a.tool_view_file('app/main.py:4-5'))                                   # path:range as the index prints it
        self.assertIn('Tideway', self.a.tool_view_file('README.md'))
    def test_large_file_pages(self):
        (self.ws / 'big.txt').write_text('\n'.join(f'line {i}' for i in range(1, 2001)))
        out = self.a.tool_view_file('big.txt'); self.assertIn('lines 1-600 of 2000', out); self.assertIn('start=', out.split('More:')[-1]); self.assertTrue(self.ev('file')[-1]['more'])
        self.assertIn('lines 601-1200', self.a.tool_view_file('big.txt', 601))
    def test_refusals(self):
        for bad in ('.env', 'app', 'missing.py'):
            with self.assertRaises(AgentError): self.a.tool_view_file(bad)
        (self.ws / 'blob.bin').write_bytes(b'\x00\x01\x02' * 100)
        with self.assertRaises(AgentError) as c: self.a.tool_view_file('blob.bin')
        self.assertIn('binary', str(c.exception))
    def test_image_goes_to_preview(self):
        out = self.a.tool_view_file('logo.png'); self.assertIn('image preview', out); self.assertTrue(self.ev('images'))

class Edit(Base):
    def test_edit_shows_diff_verifies_and_undoes(self):
        r = self.a.tool_edit_file('app/main.py', 'name = argv[0]', 'name = argv[0].strip()')
        self.assertIn('VERIFIED', r); self.assertIn('+1 -1', r)
        self.assertIn('argv[0].strip()', (self.ws / 'app/main.py').read_text()); self.assertIn('-    name = argv[0]', self.cards[0]); self.assertIn('+    name = argv[0].strip()', self.cards[0])
        d = self.ev('diff')[0]; self.assertEqual((d['added'], d['removed']), (1, 1))
        self.assertTrue(self.a.actions.undo().startswith('UNDONE')); self.assertIn('name = argv[0]\n', (self.ws / 'app/main.py').read_text())
    def test_denied_changes_nothing(self):
        before = (self.ws / 'app/main.py').read_text(); self.answer = False
        self.assertTrue(self.a.tool_edit_file('app/main.py', 'return name', 'return None').startswith('Cancelled')); self.assertEqual((self.ws / 'app/main.py').read_text(), before)
        self.assertEqual(self.a.actions.recent(), [])
    def test_not_found_gives_hint_and_ambiguous_gives_lines(self):
        with self.assertRaises(AgentError) as c: self.a.tool_edit_file('app/main.py', 'def main(args):', 'def main(a):')
        self.assertIn('not found', str(c.exception)); self.assertIn('def main(argv):', str(c.exception))             # closest line offered
        with self.assertRaises(AgentError) as c: self.a.tool_edit_file('app/util.py', 'def clamp(x, lo, hi):', 'def c(x):')
        self.assertIn('appears 2 times', str(c.exception)); self.assertIn('lines 1, 4', str(c.exception)); self.assertEqual(self.cards, [])
        self.assertIn('VERIFIED', self.a.tool_edit_file('app/util.py', 'def clamp(x, lo, hi):', 'def clip(x, lo, hi):', replace_all=True)); self.assertEqual((self.ws / 'app/util.py').read_text().count('clip'), 2)
    def test_whitespace_forgiving_match(self):
        r = self.a.tool_edit_file('app/main.py', 'def main(argv):\nname = argv[0]\n', 'def main(argv):\n    name = argv[0] or "x"\n')       # model lost the indentation
        self.assertIn('ignoring whitespace', r); text = (self.ws / 'app/main.py').read_text(); self.assertIn('name = argv[0] or "x"', text); self.assertNotIn('\n\n\n', text)
    def test_crlf_and_mode_preserved(self):
        p = self.ws / 'win.txt'; p.write_bytes(b'one\r\ntwo\r\nthree\r\n'); os.chmod(p, 0o750)
        self.assertIn('VERIFIED', self.a.tool_edit_file('win.txt', 'two', 'TWO')); self.assertEqual(p.read_bytes(), b'one\r\nTWO\r\nthree\r\n'); self.assertEqual(p.stat().st_mode & 0o777, 0o750)
    def test_syntax_error_is_flagged_not_hidden(self):
        r = self.a.tool_edit_file('app/main.py', 'def main(argv):', 'def main(argv)'); self.assertIn('WARNING', r); self.assertIn('syntax error', r); self.assertIn('syntax error', self.cards[0])
    def test_guards(self):
        for args in (('.env', 'TOKEN', 'X'), ('missing.py', 'a', 'b'), ('app/main.py', '', 'x'), ('app/main.py', 'sys', 'sys')):
            with self.assertRaises(AgentError): self.a.tool_edit_file(*args)
        self.assertIn('supersecret', (self.ws / '.env').read_text()); (self.ws / 'latin.txt').write_bytes(b'caf\xe9 au lait')
        with self.assertRaises(AgentError) as c: self.a.tool_edit_file('latin.txt', 'au', 'AU')
        self.assertIn('UTF-8', str(c.exception))
    def test_sloppy_model_arguments(self):
        t = self.a.tools['edit_file']
        r = self.a._call(t, {'file': 'app/util.py', 'old_str': 'class Table:', 'new_str': 'class Grid:'}); self.assertIn('VERIFIED', r)
        r = self.a._call(t, {'path': 'app/util.py', 'search': 'class Grid:', 'replacement': 'class Table:'}); self.assertIn('VERIFIED', r)

class Loop(Base):
    def run_script(self, *steps, task='go'):
        script = iter(json.dumps(s) for s in steps); self.a._chat = lambda *x, **k: (next(script), {}); return self.a.run(task, self.events.append)
    def test_describe_then_answer(self):
        out = self.run_script({'action': 'describe', 'path': '.'}, {'action': 'done', 'message': 'Tideway turns tide tables into calendars, Boss.'}, task='what is this project?')
        self.assertIn('Tideway', out); self.assertEqual(self.ev('project')[0]['card']['name'], self.ws.name)
    def test_view_edit_flow_counts_as_acting(self):
        out = self.run_script({'action': 'view', 'path': 'app/util.py'}, {'action': 'edit', 'path': 'app/util.py', 'old': 'class Table:', 'new': 'class Grid:'},
                              {'action': 'done', 'message': 'I have successfully renamed the class, Boss.'}, task='rename Table to Grid')
        self.assertIn('renamed', out); self.assertIn('class Grid:', (self.ws / 'app/util.py').read_text()); self.assertEqual(len(self.ev('file')), 1)
    def test_claim_without_edit_is_retracted(self):
        out = self.run_script({'action': 'done', 'message': 'I have successfully edited the file.'}, {'action': 'done', 'message': 'I have successfully edited the file.'}, task='edit it')
        self.assertIn("haven't actually done that", out)

class Slash(Base):
    def test_describe_files_view(self):
        r = self.a.run('/describe', self.events.append); self.assertIn('files', r); self.assertEqual(len(self.ev('project')), 1)
        r = self.a._slash('/files app', self.events.append); self.assertIn('entries in', r); self.assertTrue(self.ev('files'))
        r = self.a._slash('/files *.py', self.events.append); self.assertIn('entries in', r)
        r = self.a._slash('/view app/main.py:1-3', self.events.append); self.assertEqual(r, 'app/main.py lines 1-3 of 9.')
        r = self.a._slash('/view app/main.py 4 6', self.events.append); self.assertIn('lines 4-6', r)
        self.assertIn('Usage', self.a._slash('/view', self.events.append))
    def test_help_lists_new_commands(self):
        h = self.a._slash('/help', self.events.append); self.assertTrue(all(c in h for c in ('/describe', '/files', '/view')))

if __name__ == '__main__': unittest.main()
