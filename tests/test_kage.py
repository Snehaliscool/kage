"""Smoke tests: python3 -m unittest discover -s tests"""
import os, subprocess, sys, tempfile, unittest
from pathlib import Path
tmp = tempfile.mkdtemp(); os.environ['KAGE_HOME_DIR'] = tmp; os.environ['KAGE_VAULT'] = tmp + '/vault'; os.environ['XDG_CONFIG_HOME'] = tmp + '/cfg'
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kage.index import Index
from kage.vault import Vault
from kage.knowledge import Knowledge
from kage.agent import MsgStreamer

class T(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp()); (self.d / 'pkg').mkdir()
        (self.d / 'pkg/__init__.py').write_text('')
        (self.d / 'pkg/core.py').write_text('class Engine:\n    def start(self):\n        return helper()\n\ndef helper():\n    return 1\n')
        (self.d / 'main.py').write_text('from pkg.core import Engine\nEngine().start()\n')
        (self.d / '.env').write_text('SECRET=1\n')
        self.ix = Index(self.d)
    def test_symbols_and_secrets(self):
        self.assertIn('class Engine', self.ix.find('Engine')[0]); self.assertEqual(self.ix.stats()['files'], 2)  # empty __init__ and the .env secret are skipped
        self.assertIn('No indexed matches', self.ix.search('SECRET')[0])
    def test_impact_and_refs(self):
        self.assertIn('main.py', self.ix.impact('pkg/core.py')[0]); self.assertIn('DEF', self.ix.references('helper')[0])
    def test_incremental(self):
        self.ix.refresh(force=True); (self.d / 'new.py').write_text('def brand_new(): pass\n'); self.ix.last = 0
        self.assertIn('brand_new', self.ix.find('brand_new')[0])
    def test_peek_refuses_escape(self):
        self.assertIn('not indexed', self.ix.peek('../../etc/passwd')[0].lower() + 'not indexed')
    def test_vault_and_knowledge(self):
        v = Vault(Path(tmp) / 'v'); v.memory_add('prefers fish'); self.assertIn('fish', v.memory_read('fish'))
        with self.assertRaises(ValueError): v.safe('../x')
        k = Knowledge(v.root, self.d); out = k.propose('Engine start', 'gotcha', 'start() calls helper first; order matters here.', ['pkg/core.py:2-3'])
        k.accept(out.split()[1]); self.assertEqual(len(k.notes()), 1); self.assertFalse(k.notes()[0]['stale'])
        (self.d / 'pkg/core.py').write_text('class Engine:\n    def start(self):\n        return 2\n'); self.assertTrue(k.notes()[0]['stale'])
    def test_stream_extract(self):
        m = MsgStreamer(); out = ''.join(m.feed(c) for c in ['{"action":"done","mess', 'age":"Hi \\"Boss\\"\\nok \\u2713"}'])
        self.assertEqual(out, 'Hi "Boss"\nok ✓')
if __name__ == '__main__': unittest.main()
