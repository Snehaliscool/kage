"""Git/GitHub tools, shell policy and the anti-hallucination guards. Fully offline: a local bare repo plays GitHub."""
import json, os, subprocess, sys, tempfile, unittest
from pathlib import Path
tmp = tempfile.mkdtemp()
os.environ.update(KAGE_HOME_DIR=tmp, KAGE_VAULT=tmp + '/vault', XDG_CONFIG_HOME=tmp + '/cfg', GIT_CONFIG_GLOBAL=tmp + '/gitconfig', GIT_CONFIG_NOSYSTEM='1', HOME=tmp)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from kage import shell
from kage.agent import Agent

def sh(*a, cwd=None): return subprocess.run(a, cwd=cwd, text=True, capture_output=True, check=True).stdout

class Shell(unittest.TestCase):
    def test_policy(self):
        cases = {'git status': 'readonly', 'git log --oneline -5': 'readonly', 'ls | wc -l': 'normal', 'git status && ls': 'normal', 'git push origin main': 'normal',
                 'git push --force origin main': 'danger', 'rm -rf node_modules': 'danger', 'rm -rf ~': 'deny', 'cat ~/.ssh/id_ed25519': 'deny', 'cat .env': 'deny',
                 'gh auth token': 'deny', 'sudo pacman -Syu': 'danger', 'gh auth login': 'interactive', 'git config --global user.name "A B"': 'normal',
                 'git config --get user.name': 'readonly', 'git branch -D x': 'danger', 'git commit -m "a && b"': 'normal', 'npm install': 'normal', 'env': 'deny'}
        for cmd, want in cases.items(): self.assertEqual(shell.classify(cmd)[0], want, cmd)
    def test_never_hangs_on_stdin_and_times_out(self):
        code, out = shell.execute('cat', use_shell=True, timeout=5); self.assertEqual(code, 0)
        code, out = shell.execute('sleep 20', use_shell=True, timeout=1); self.assertEqual(code, 124); self.assertIn('timed out', out)
    def test_redaction_and_hints(self):
        self.assertNotIn('ghp_', shell.redact('token ghp_' + 'a' * 36)); self.assertIn('***@', shell.redact('https://me:secret@github.com/x/y'))
        self.assertIn('github_login', shell.fmt(128, "fatal: could not read Username for 'https://github.com': terminal prompts disabled"))
        self.assertIn('pull --rebase', shell.fmt(1, '! [rejected] main -> main (fetch first)'))

class Flow(unittest.TestCase):
    def setUp(self):
        self.cards = []; self.answer = True; Path(tmp, 'gitconfig').unlink(missing_ok=True)   # every test starts with a blank global git config
        self.ws = Path(tempfile.mkdtemp()); self.remote = Path(tempfile.mkdtemp()) / 'origin.git'; sh('git', 'init', '-q', '--bare', '-b', 'main', str(self.remote))
        self.a = Agent(self.ws); self.a.confirm_handler = lambda m: (self.cards.append(m), self.answer)[1]
    def test_end_to_end_push(self):
        a, ws = self.a, self.ws
        self.assertIn('Git has no', self._err(lambda: a.tool_git_setup()))           # asks for the missing values, does not pretend
        r = a.tool_git_setup('Kage Boss', 'boss@example.com'); self.assertTrue(r.startswith('VERIFIED'), r); self.assertIn('boss@example.com', r)
        self.assertTrue(a.tool_git_setup().startswith('VERIFIED: git was already'))   # idempotent, no second card
        self.assertIn('not a usable git repository', self._err(lambda: a.tool_git_status()))
        self.assertTrue(a.tool_git('init -b main').startswith('OK'))
        (ws / 'app.py').write_text('print("hi")\n')
        self.assertIn('git_remote', a.tool_github_status())                           # NEXT step names the missing remote
        self.assertTrue(a.tool_git_remote(str(self.remote)).startswith('VERIFIED'))
        r = a.tool_git_commit('first commit'); self.assertTrue(r.startswith('VERIFIED: committed'), r)
        self.assertIn('Nothing to commit', a.tool_git_commit('again'))
        r = a.tool_git_push(); self.assertTrue(r.startswith('VERIFIED: origin/main is now at'), r)
        self.assertEqual(sh('git', 'rev-parse', 'main', cwd=self.remote).strip(), sh('git', 'rev-parse', 'HEAD', cwd=ws).strip())
        self.assertIn('Nothing to push', a.tool_git_push())
    def test_secrets_never_get_committed(self):
        a, ws = self.a, self.ws; a.tool_git_setup('B', 'b@example.com'); a.tool_git('init -b main')
        (ws / '.env').write_text('KEY=1\n'); (ws / 'notes.txt').write_text('token ghp_' + 'a' * 36 + '\n')
        e = self._err(lambda: a.tool_git_commit('oops')); self.assertIn('.env', e); self.assertIn('notes.txt', e)
        self.assertEqual(sh('git', 'diff', '--cached', '--name-only', cwd=ws).strip(), '')   # nothing was staged
        (ws / '.env').unlink(); (ws / 'notes.txt').unlink(); (ws / 'node_modules').mkdir(); (ws / 'node_modules/x.js').write_text('1')
        self.assertIn('.gitignore', self._err(lambda: a.tool_git_commit('deps')))
    def test_denied_approval_means_not_done(self):
        a, ws = self.a, self.ws; a.tool_git_setup('B', 'b@example.com'); a.tool_git('init -b main'); (ws / 'f.txt').write_text('x')
        self.answer = False; self.assertTrue(a.tool_git_commit('x').startswith('Cancelled'))
        self.assertEqual(sh('git', 'status', '--porcelain', cwd=ws).strip(), '?? f.txt')
    def test_rejected_push_gives_actionable_hint(self):
        a, ws = self.a, self.ws; a.tool_git_setup('B', 'b@example.com'); a.tool_git('init -b main'); a.tool_git_remote(str(self.remote))
        (ws / 'a.txt').write_text('1'); a.tool_git_commit('one'); a.tool_git_push()
        other = Path(tempfile.mkdtemp()) / 'o'; sh('git', 'clone', '-q', str(self.remote), str(other)); (other / 'b.txt').write_text('2')
        sh('git', 'add', '.', cwd=other); sh('git', '-c', 'user.name=o', '-c', 'user.email=o@x.io', 'commit', '-qm', 'other', cwd=other); sh('git', 'push', '-q', cwd=other)
        (ws / 'c.txt').write_text('3'); a.tool_git_commit('three'); r = a.tool_git_push()
        self.assertTrue(r.startswith('FAILED'), r); self.assertIn('pull --rebase', r)
    def test_force_push_and_dangerous_commands_are_flagged(self):
        a = self.a; a.tool_git_setup('B', 'b@example.com'); a.tool_git('init -b main'); a.tool_git_remote(str(self.remote)); (self.ws / 'a').write_text('1'); a.tool_git_commit('c')
        a.tool_git_push(force=True); self.assertTrue(self.cards[-1].startswith('HIGH RISK'))
        self.cards.clear(); a.tool_run_command('git reset --hard HEAD'); self.assertTrue(self.cards[-1].startswith('HIGH RISK'))
    def test_run_command_policy(self):
        a = self.a
        self.assertTrue(a.tool_run_command('echo hi').startswith('OK')); self.assertEqual(self.cards, [])      # read-only: no card
        r = a.tool_run_command('echo a && echo b | tr a-z A-Z'); self.assertIn('B', r); self.assertEqual(len(self.cards), 1)  # chaining and pipes now work
        self.assertTrue(a.tool_run_command('false').startswith('FAILED (exit 1)'))
        self.assertIn('Blocked', self._err(lambda: a.tool_run_command('rm -rf ~')))
        self.assertIn('real terminal', self._err(lambda: a.tool_run_command('gh auth login')))
        a.trust = True; self.cards.clear(); a.tool_run_command('echo x > out.txt'); self.assertEqual(self.cards, [])    # trust skips normal cards...
        a.tool_run_command('rm -rf build'); self.assertEqual(len(self.cards), 1)                                      # ...but never HIGH RISK ones
    def _err(self, fn):
        from kage.errors import AgentError
        with self.assertRaises(AgentError) as c: fn()
        return str(c.exception)

class Guards(unittest.TestCase):
    def setUp(self):
        self.ws = Path(tempfile.mkdtemp()); self.a = Agent(self.ws); self.a.confirm_handler = lambda m: True; self.events = []
    def script(self, *replies):
        it = iter(replies); self.sent = []
        def fake(messages, emit, use_format=True): self.sent.append(messages[-1]['content']); return json.dumps(next(it)), {}
        self.a._chat = fake
    def test_false_claim_is_retracted_and_corrected(self):
        self.script({'action': 'done', 'message': "I've configured git for you, Boss."}, {'action': 'done', 'message': 'Sorry Boss, I have not run anything yet.'})
        out = self.a.run('configure git', self.events.append)
        self.assertIn('no tool in this turn succeeded', self.sent[1]); self.assertIn({'t': 'retract'}, self.events); self.assertIn('not run anything', out)
    def test_persistent_liar_gets_replaced_with_the_truth(self):
        claim = {'action': 'done', 'message': "I've pushed everything."}; self.script(claim, claim)
        out = self.a.run('push my code', self.events.append); self.assertIn("haven't actually done that", out)
    def test_claim_is_fine_when_a_tool_really_ran(self):
        self.script({'action': 'run_command', 'args': {'command': 'git init -b main'}}, {'action': 'done', 'message': "I've initialized the repo, Boss."})
        out = self.a.run('init a repo', self.events.append); self.assertIn('initialized', out); self.assertNotIn({'t': 'retract'}, self.events)
    def test_no_permission_questions_in_chat(self):
        self.script({'action': 'ask', 'message': 'Would you like me to configure git now?'}, {'action': 'run_command', 'args': {'command': 'git --version'}}, {'action': 'done', 'message': 'Git is installed.'})
        self.a.run('configure git', self.events.append); self.assertIn('approval card', self.sent[1]); self.assertIn('Result from run_command', self.sent[2])
    def test_never_asks_for_tokens(self):
        self.script({'action': 'ask', 'message': 'Please paste your GitHub personal access token.'}, {'action': 'github_status', 'args': {}}, {'action': 'done', 'message': 'Checked.'})
        self.a.run('push to github', self.events.append); self.assertIn('github_login', self.sent[1])
    def test_legit_questions_pass_through(self):
        self.script({'action': 'ask', 'message': 'What name and email should I use for git?'})
        self.assertIn('What name and email', self.a.run('configure git', self.events.append))
if __name__ == '__main__': unittest.main()
