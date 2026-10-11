"""Git and GitHub tools. Every tool here is non-interactive, secret-aware and VERIFIES its own result,
so the model never has to guess whether something worked. Mixed into Agent (needs: path, confirm, cancel, _secret, workspace).
"""
from __future__ import annotations
import re, shlex, shutil, subprocess
from pathlib import Path
from . import shell
from .errors import AgentError, Cancelled

HEAVY = ('node_modules/', '.venv/', 'venv/', '__pycache__/', '.mypy_cache/', '.pytest_cache/', 'target/debug/', 'target/release/', '.gradle/')
REMOTE_RX = re.compile(r'^(https://[\w.-]+/[\w.-]+/[\w.-]+?(\.git)?/?|git@[\w.-]+:[\w.-]+/[\w.-]+?(\.git)?|ssh://git@[\w.-]+(:\d+)?/[\w.-]+/[\w.-]+?(\.git)?|(file://)?/[\w./ +-]+)$')
NAME_RX = re.compile(r'^[A-Za-z0-9._-]{1,100}(/[A-Za-z0-9._-]{1,100})?$')

def web_url(remote: str) -> str:
    """https://github.com/o/r for either https or ssh remotes ('' when not recognisable)."""
    m = re.match(r'^(?:https://(?:[^@/]+@)?|git@|ssh://git@)([\w.-]+)[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?$', remote.strip())
    return f'https://{m.group(1)}/{m.group(2)}/{m.group(3)}' if m else ''

class GitMixin:
    # ------------------------------------------------------------------ plumbing
    def _poll(self):
        if self.cancel.is_set(): raise Cancelled()

    def _git(self, args, cwd, timeout=60):
        """-> (exit code, output with trailing newline removed). No shell involved."""
        code, out = shell.execute(['git', *args], cwd=cwd, timeout=timeout, on_poll=self._poll)
        return code, shell.redact(out).rstrip('\n')

    def _repo(self, path='.'):
        d = self.path(path)
        if not d.is_dir(): raise AgentError(f'Not a folder: {d}')
        code, out = self._git(['rev-parse', '--show-toplevel'], d)
        if code:
            raise AgentError(f'{d} is not a usable git repository. ' + (shell.hints(out) or 'Run git(args="init -b main") first.') + f'\n{out[:300]}')
        return d, Path(out.strip())

    def _identity(self, d):
        return self._git(['config', 'user.name'], d)[1].strip(), self._git(['config', 'user.email'], d)[1].strip()

    def _gcfg(self, key, cwd):
        return self._git(['config', '--global', '--get', key], cwd)[1].strip()

    def _gh_logged_in(self, d):
        if not shutil.which('gh'): return False, 'gh is not installed'
        code, out = shell.execute(['gh', 'auth', 'status', '-h', 'github.com'], cwd=d, timeout=25, on_poll=self._poll)
        return code == 0, shell.redact(out).strip()

    # ------------------------------------------------------------------ read-only
    def tool_git_status(self, path='.'):
        d, top = self._repo(path)
        st = self._git(['status', '-sb'], d)[1]
        last = self._git(['log', '-1', '--format=%h %s (%cr)'], d)
        remotes = self._git(['remote', '-v'], d)[1]
        n, e = self._identity(d)
        return (f'Repo: {top}\n{st}\nLast commit: {last[1] if last[0] == 0 else "(none yet)"}\nRemotes:\n{remotes or "(none)"}\n'
                f'Identity: {n or "MISSING"} <{e or "MISSING"}>')

    def tool_github_status(self, path='.'):
        """One call that answers: can this machine push to GitHub, and what is still missing?"""
        d = self.path(path); lines, todo = [], []
        lines.append(self._git(['--version'], d)[1] or 'git: not installed (pacman_install git)')
        n, e = self._identity(d)
        lines.append(f'identity: {n or "MISSING"} <{e or "MISSING"}>')
        if not (n and e): todo.append('git_setup(name, email)')
        logged, gh_out = self._gh_logged_in(d)
        acct = re.search(r'account (\S+)', gh_out)
        lines.append('gh CLI: ' + (f'logged in as {acct.group(1)}' if logged and acct else 'logged in' if logged else 'not installed (Arch package: github-cli)' if 'not installed' in gh_out else 'installed but NOT logged in'))
        ssh_ok = False
        if shutil.which('ssh'):
            c, o = shell.execute(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'StrictHostKeyChecking=accept-new', 'git@github.com'], cwd=d, timeout=20, on_poll=self._poll)
            m = re.search(r'Hi ([\w-]+)!', o); ssh_ok = bool(m)
            lines.append('ssh to github.com: ' + (f'authenticated as {m.group(1)}' if m else 'no working key (' + (o.strip().splitlines() or ['no output'])[-1][:90] + ')'))
        helpers = self._git(['config', '--get-all', 'credential.helper'], d)[1].split() + self._git(['config', '--get-all', 'credential.https://github.com.helper'], d)[1].split()
        lines.append('https credential helper: ' + (', '.join(dict.fromkeys(helpers)) if helpers else 'none'))
        https_ok = logged or bool(helpers)
        code, top = self._git(['rev-parse', '--show-toplevel'], d)
        if code: lines.append('workspace: not a git repo yet'); todo.append('git(args="init -b main")')
        else:
            branch = self._git(['symbolic-ref', '--short', 'HEAD'], d)[1] or '(detached)'
            remote = self._git(['remote', '-v'], d)[1]
            dirty = len([x for x in self._git(['status', '--porcelain'], d)[1].splitlines() if x.strip()])
            has_commit = self._git(['rev-parse', '--verify', '-q', 'HEAD'], d)[0] == 0
            lines.append(f'repo: {top} on branch {branch}; {dirty} uncommitted file(s); commits: {"yes" if has_commit else "none yet"}')
            lines.append('remotes: ' + (remote.replace('\n', ' | ') if remote else 'none'))
            ahead = ''
            c, ab = self._git(['rev-list', '--left-right', '--count', '@{u}...HEAD'], d)
            if c == 0 and ab.split()[:2]: behind, ahead = ab.split()[:2]; lines.append(f'vs upstream: {ahead} ahead, {behind} behind')
            uses_ssh = bool(re.search(r'git@|ssh://', remote)); uses_https = 'https://' in remote
            if not remote: todo.append('git_remote(url) if the repo already exists on GitHub, else github_create_repo(name)')
            elif uses_ssh and not ssh_ok: todo.append('github_login(method="ssh") or switch the remote to https')
            elif uses_https and not https_ok: todo.append('github_login()')
            if dirty: todo.append('git_commit(message)')
            if has_commit and remote and (ahead not in ('', '0') or c != 0): todo.append('git_push()')
        lines.append('')
        lines.append('NEXT: ' + ' -> '.join(todo) if todo else 'READY: everything is set; git_push() will work.')
        return '\n'.join(lines)

    # ------------------------------------------------------------------ setup
    def tool_git_setup(self, name='', email='', default_branch='main'):
        cwd = self.workspace or Path.home()
        name, email = str(name or '').strip(), str(email or '').strip()
        cur_n, cur_e = self._gcfg('user.name', cwd), self._gcfg('user.email', cwd)
        name, email = name or cur_n, email or cur_e
        missing = [x for x, v in (('name', name), ('email', email)) if not v]
        if missing:
            raise AgentError('Git has no ' + ' and '.join(missing) + ' yet and none was given. Ask the Boss ONE short question for their ' + ' and '.join(missing) +
                             ' (their GitHub no-reply address is fine for the email), then call git_setup(name, email) again.')
        if '\n' in name or len(name) > 100: raise AgentError('That name does not look valid.')
        if not re.fullmatch(r'[^@\s]+@[^@\s]+\.[^@\s]+', email): raise AgentError('That email address does not look valid.')
        if not re.fullmatch(r'[A-Za-z0-9._/-]{1,60}', str(default_branch or 'main')): raise AgentError('Invalid default branch name.')
        want = {'user.name': name, 'user.email': email, 'init.defaultBranch': default_branch or 'main', 'push.autoSetupRemote': 'true'}
        cur = {k: self._gcfg(k, cwd) for k in want}
        todo = {k: v for k, v in want.items() if cur[k] != v}
        summary = '\n'.join(f'  {k} = {self._gcfg(k, cwd)}' for k in want)
        if not todo: return f'VERIFIED: git was already configured; nothing changed.\n{summary}'
        if not self.confirm('Set global git config (~/.gitconfig):\n' + '\n'.join(f'  {k}: {cur[k] or "(unset)"} -> {v}' for k, v in todo.items()), trustable=True):
            return 'Cancelled. Nothing was changed.'
        for k, v in todo.items():
            code, out = self._git(['config', '--global', k, v], cwd)
            if code: raise AgentError(f'git config {k} failed: {out}')
        wrong = [k for k in want if self._gcfg(k, cwd) != want[k]]
        summary = '\n'.join(f'  {k} = {self._gcfg(k, cwd)}' for k in want)
        return (f'NOT VERIFIED: these keys did not stick: {", ".join(wrong)}\n{summary}' if wrong else f'VERIFIED: global git config now reads back as:\n{summary}')

    def tool_open_terminal(self, command, title='Kage'):
        """Open a real terminal window for anything interactive (sign-in, passwords, editors)."""
        command = str(command or '').strip()
        if not command or len(command) > 4000: raise AgentError('Provide the command or script to run in the terminal.')
        level, why = shell.classify(command)
        if level == 'deny': raise AgentError(f'Blocked: {why}.')
        argv, term = shell.terminal_argv(command, str(title)[:60])
        if not argv: raise AgentError('No terminal emulator found. Set $TERMINAL or install kitty, foot, alacritty or xterm.')
        if not self.confirm(f'Open a {term} window and run:\n$ {command}', danger=level == 'danger'): return 'Cancelled. Nothing was opened.'
        try: subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        except OSError as e: raise AgentError(f'Could not start {term}: {e}')
        return (f'OPENED a {term} window. I cannot see or control it. The Boss must finish the steps there. '
                'Tell the Boss what to do, then STOP and wait. Do not claim success: after the Boss says they are done, verify with the matching status tool.')

    def tool_github_login(self, method='gh'):
        method = str(method or 'gh').lower()
        if method not in ('gh', 'ssh'): raise AgentError('method must be "gh" (browser sign-in, easiest) or "ssh".')
        if method == 'gh':
            if not shutil.which('gh'): raise AgentError('GitHub CLI is not installed. Call pacman_install(["github-cli"]) first, or use github_login(method="ssh").')
            script = 'gh auth login -h github.com -p https -w && gh auth setup-git -h github.com'
            note = ('The window will show a one-time code and open github.com in the browser; the Boss pastes the code and approves. '
                    'It then wires git to use that login, so HTTPS pushes work.')
        else:
            script = ('set -e\nKEY="$HOME/.ssh/id_ed25519"\nmkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"\n'
                      '[ -f "$KEY" ] || ssh-keygen -t ed25519 -C "$(git config --global user.email || echo kage)" -f "$KEY"\n'
                      'eval "$(ssh-agent -s)" >/dev/null 2>&1 || true\nssh-add "$KEY" || true\n'
                      'echo; echo "Public key (safe to share):"; cat "$KEY.pub"; echo\n'
                      'if command -v gh >/dev/null 2>&1; then gh auth login -h github.com -p ssh -w; '
                      'else echo "Add the key at https://github.com/settings/ssh/new"; xdg-open https://github.com/settings/ssh/new >/dev/null 2>&1 || true; fi')
            note = ('It creates ~/.ssh/id_ed25519 if missing and uploads it to GitHub. Tip for the Boss: leave the passphrase empty or load the key into an ssh-agent '
                    'that Kage can see, otherwise pushes cannot unlock it.')
        res = self.tool_open_terminal(script, title='Kage · GitHub sign-in')
        return res + '\n' + note + '\nWhen the Boss says they are done, call github_status() and report what it shows.' if res.startswith('OPENED') else res

    def tool_git_remote(self, url, name='origin', path='.'):
        d, top = self._repo(path); url = str(url or '').strip(); name = str(name or 'origin').strip()
        if re.search(r'://[^/@\s]*:[^/@\s]*@', url) or re.search(r'://(ghp_|github_pat_)', url): raise AgentError('Never put a token or password in a remote URL. Use github_login() instead, then a plain URL.')
        if not REMOTE_RX.match(url): raise AgentError('Expected https://github.com/OWNER/REPO(.git), git@github.com:OWNER/REPO(.git) or a local path.')
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,40}', name): raise AgentError('Invalid remote name.')
        c, cur = self._git(['remote', 'get-url', name], d)
        if c == 0 and cur.strip() == url: msg = f'Remote {name} already points to {url}.'
        else:
            verb = 'set-url' if c == 0 else 'add'
            if not self.confirm(f'{"Change" if c == 0 else "Add"} remote {name} in {top}:\n' + (f'  {shell.redact(cur)} ->\n' if c == 0 else '') + f'  {url}', trustable=True): return 'Cancelled. Nothing was changed.'
            c2, out = self._git(['remote', verb, name, url], d)
            if c2: raise AgentError(out)
            msg = f'Remote {name} set to {url}.'
        got = self._git(['remote', 'get-url', name], d)[1].strip()
        c3, o3 = self._git(['ls-remote', name], d, timeout=60)
        if got != url: return f'NOT VERIFIED: remote reads back as {got!r}.'
        return f'VERIFIED: {msg} ' + ('GitHub answered: the repo is reachable with your credentials.' if c3 == 0 else 'But it is NOT reachable yet.\n' + shell.fmt(c3, o3))

    def tool_github_create_repo(self, name, private=True, description='', path='.'):
        d, top = self._repo(path); name = str(name or '').strip(); private = str(private).lower() not in ('false', '0', 'no', 'public')
        if not NAME_RX.match(name): raise AgentError('Repo name may contain letters, digits, . _ - (optionally OWNER/NAME).')
        if self._git(['remote', 'get-url', 'origin'], d)[0] == 0: raise AgentError('This repo already has a remote "origin". Use git_remote to change it or git_push to push.')
        logged, out = self._gh_logged_in(d)
        if not logged: raise AgentError('Not signed in to GitHub CLI. Call github_login() first.' if shutil.which('gh') else 'GitHub CLI is not installed: pacman_install(["github-cli"]).')
        msg = f'Create a {"PRIVATE" if private else "PUBLIC (anyone can see it)"} GitHub repository "{name}" from {top} and add it as remote "origin". Nothing is pushed yet.'
        if not self.confirm(msg, danger=not private): return 'Cancelled. Nothing was created.'
        args = ['gh', 'repo', 'create', name, '--private' if private else '--public', '--source', str(top), '--remote', 'origin'] + (['--description', str(description)[:300]] if description else [])
        code, o = shell.execute(args, cwd=d, timeout=90, on_poll=self._poll)
        if code: return shell.fmt(code, o)
        got = self._git(['remote', 'get-url', 'origin'], d)[1].strip()
        return f'VERIFIED: created {web_url(got) or name} and set it as origin. Next: git_commit (if needed) and git_push().' if got else 'NOT VERIFIED: gh reported success but no origin remote exists.\n' + o

    # ------------------------------------------------------------------ commit / push
    def _changes(self, d):
        """-> list of (xy, path) from `status --porcelain -z`."""
        code, out = shell.execute(['git', 'status', '--porcelain', '-z', '-uall'], cwd=d, timeout=60, on_poll=self._poll)
        if code: raise AgentError(out[:300])
        ents, res, i = out.split('\0'), [], 0
        while i < len(ents):
            e = ents[i]; i += 1
            if len(e) < 4: continue
            xy, p = e[:2], e[3:]
            if xy[0] in 'RC': i += 1
            res.append((xy, p))
        return res

    def _scan(self, top, paths):
        secret, heavy = [], []
        for p in paths:
            f = top / p
            if any(h in p + ('/' if not p.endswith('/') else '') for h in HEAVY) or '/node_modules/' in '/' + p: heavy.append(p); continue
            if self._secret(f): secret.append(p); continue
            try:
                if f.is_file() and f.stat().st_size < 400_000 and shell.SECRET_RX.search(f.read_text(errors='ignore')): secret.append(p)
            except OSError: pass
        return secret, heavy

    def tool_git_commit(self, message, add_all=True, path='.'):
        d, top = self._repo(path); message = str(message or '').strip()
        if not message or len(message) > 500: raise AgentError('Provide a commit message (1-500 characters).')
        n, e = self._identity(d)
        if not (n and e): raise AgentError('Git has no identity. Ask the Boss for name and email, then call git_setup(name, email).')
        add_all = str(add_all).lower() not in ('false', '0', 'no')
        changes = self._changes(d)
        if not changes: return 'Nothing to commit: the working tree is clean. (OK, not an error.)'
        files = [p for xy, p in changes] if add_all else [p for xy, p in changes if xy[0] not in ' ?']
        if not files: raise AgentError('Nothing is staged. Use add_all=true to commit everything, or stage files with git(args="add <file>").')
        secret, heavy = self._scan(top, [p for p in files if (top / p).exists()])
        if secret: raise AgentError('Refusing to commit files that look like secrets: ' + ', '.join(secret[:8]) + '. Add them to .gitignore (git(args="rm --cached <file>") if already tracked) or tell the Boss. Nothing was staged.')
        if heavy: raise AgentError('These look like dependencies or caches and should not be committed: ' + ', '.join(heavy[:6]) + '. Add them to .gitignore first (write_file), then retry.')
        listing = '\n'.join(f'  {xy} {p}' for xy, p in changes[:25]) + (f'\n  …and {len(changes) - 25} more' if len(changes) > 25 else '')
        if not self.confirm(f'Commit {len(files)} file(s) in {top}\nMessage: {message}\nAs: {n} <{e}>\n{listing}', trustable=True): return 'Cancelled. Nothing was committed.'
        before = self._git(['rev-parse', '-q', '--verify', 'HEAD'], d)[1]
        if add_all:
            c, o = self._git(['add', '-A'], d)
            if c: return shell.fmt(c, o)
        c, o = self._git(['commit', '-m', message], d, timeout=120)
        if c: return shell.fmt(c, o)
        after = self._git(['rev-parse', '-q', '--verify', 'HEAD'], d)[1]
        if not after or after == before: return 'NOT VERIFIED: git reported success but HEAD did not move.\n' + o
        subj = self._git(['log', '-1', '--format=%h %s'], d)[1]
        left = len(self._changes(d))
        return f'VERIFIED: committed {subj} ({len(files)} file(s)). Uncommitted files left: {left}.'

    def tool_git_push(self, remote='origin', branch='', force=False, path='.'):
        d, top = self._repo(path); remote = str(remote or 'origin').strip(); force = str(force).lower() in ('true', '1', 'yes')
        if not re.fullmatch(r'[A-Za-z0-9._-]{1,40}', remote): raise AgentError('Invalid remote name.')
        c, url = self._git(['remote', 'get-url', remote], d)
        if c: raise AgentError(f'No remote named "{remote}". Call git_remote(url) if the repo exists on GitHub, or github_create_repo(name) to make one.')
        branch = str(branch or '').strip() or self._git(['symbolic-ref', '--short', 'HEAD'], d)[1].strip()
        if not branch: raise AgentError('HEAD is detached; check out a branch first.')
        if not re.fullmatch(r'[A-Za-z0-9._/-]{1,100}', branch): raise AgentError('Invalid branch name.')
        head = self._git(['rev-parse', '-q', '--verify', 'HEAD'], d)[1].strip()
        if not head: raise AgentError('There are no commits yet. Call git_commit(message) first.')
        dirty = len(self._changes(d))
        has_up = self._git(['rev-parse', '--abbrev-ref', f'{branch}@{{u}}'], d)[0] == 0
        c, log = self._git(['log', '--oneline', '-n', '8'] + ([f'{branch}@{{u}}..{branch}'] if has_up else [branch]), d)
        if has_up and c == 0 and not log.strip() and not force:
            return f'Nothing to push: {remote}/{branch} already has every commit. (OK, not an error.)'
        card = f'Push branch "{branch}" to {remote} ({shell.redact(url)})' + (' with --force-with-lease' if force else '') + f'\nCommits going out:\n{log or "(none listed)"}'
        if dirty: card += f'\nNote: {dirty} uncommitted file(s) will NOT be pushed.'
        if not self.confirm(card, danger=force, trustable=not force): return 'Cancelled. Nothing was pushed.'
        args = ['push'] + (['--force-with-lease'] if force else []) + ['--set-upstream', remote, branch]
        code, out = self._git(args, d, timeout=300)
        if code: return shell.fmt(code, out)
        c, ls = self._git(['ls-remote', remote, f'refs/heads/{branch}'], d)
        remote_sha = ls.split()[0] if c == 0 and ls.split() else ''
        link = web_url(url)
        if remote_sha == head: return f'VERIFIED: {remote}/{branch} is now at {head[:7]}' + (f' ({link}/tree/{branch})' if link else '') + '.'
        return f'NOT VERIFIED: push reported success but {remote}/{branch} is at {remote_sha[:7] or "(unknown)"}, local is {head[:7]}.\n{shell.fmt(code, out)}'

    # ------------------------------------------------------------------ everything else in git
    def tool_git(self, args, path='.', timeout=120):
        """Any other git command (no shell). Read-only ones run without a card."""
        toks = shlex.split(args) if isinstance(args, str) else [str(a) for a in (args or [])]
        if toks and toks[0] == 'git': toks = toks[1:]
        if not toks: raise AgentError('Provide git arguments, e.g. "pull --rebase" or "checkout -b feature".')
        sub = next((t for t in toks if not t.startswith('-') and t not in ('-C',)), '')
        if sub == 'push': raise AgentError('Use git_push(): it checks the remote and verifies the result.')
        if sub == 'commit': raise AgentError('Use git_commit(message): it scans for secrets and verifies the result. (For --amend use run_command.)')
        if sub == 'credential': raise AgentError('Blocked: it would print stored credentials.')
        d = self.path(path)
        if not d.is_dir(): raise AgentError(f'Not a folder: {d}')
        line = 'git ' + shlex.join(toks); level, why = shell.classify(line)
        if level == 'deny': raise AgentError(f'Blocked: {why}.')
        if level != 'readonly' and not self.confirm(f'git in {d}:\n$ {line}' + (f'\n({why})' if why else ''), danger=level == 'danger', trustable=True): return 'Cancelled. Nothing was run.'
        code, out = shell.execute(['git', *toks], cwd=d, timeout=max(5, min(int(timeout), 900)), on_poll=self._poll)
        return shell.fmt(code, out)
