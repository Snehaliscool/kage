"""Running things safely: classify commands, run them non-interactively, explain failures.

Policy in one paragraph: Kage can run *any* command, but the Boss approves it first. Only a short list of
catastrophic or secret-stealing commands is refused outright; risky ones are flagged HIGH RISK on the approval
card; read-only ones (git status, ls, ...) run without a card. Nothing can hang on a prompt (stdin is closed,
git/gh/ssh are told not to ask), and every failure is turned into a HINT the model can act on. Standard library only.
"""
from __future__ import annotations
import os, re, shlex, shutil, subprocess, time

# --------------------------------------------------------------------------- classification
DENY = [
    (r'\brm\b[^;&|]*\s-[a-zA-Z]*[rR][a-zA-Z]*\b[^;&|]*\s(/|/\*|~/?\*?|\$HOME/?\*?|\$\{HOME\}/?\*?|/home/?\*?|/etc|/usr|/boot|/var|/bin|/lib|/opt)/?(\s|$)', 'it would delete the system or your whole home folder'),
    (r'--no-preserve-root', 'it disables the root-delete safeguard'),
    (r'\bmkfs(\.\w+)?\b|\bwipefs\b|\bsfdisk\b|\bparted\b|\bfdisk\b', 'it would format or repartition a disk'),
    (r'\bdd\b[^;&|]*\bof=/dev/', 'it would overwrite a disk device'),
    (r'>\s*/dev/(sd|nvme|vd|mmcblk)', 'it would overwrite a disk device'),
    (r':\(\)\s*\{\s*:\s*\|\s*:&\s*\}\s*;\s*:', 'fork bomb'),
    (r'\bchmod\s+-R\s+[0-7]*7[0-7]*\s+/(\s|$)|\bchown\s+-R\s+\S+\s+/(\s|$)', 'it would change permissions on the whole system'),
    # secrets stay private: never print keys, tokens or credential files
    (r'\b(cat|less|more|head|tail|bat|xxd|base64|strings|nl|tac|cp|scp|rsync|mv|curl|wget|nc|ncat)\b[^;&|]*(\.ssh/(id_|\S*_key\b)|\.gnupg|\.aws/credentials|\.netrc|\.git-credentials|\.config/gh/hosts\.yml|\.kdbx|\.pem\b)', 'it would read or move private keys/credentials'),
    (r'\b(cat|less|more|head|tail|bat|grep|rg|sed|awk|source)\b[^;&|]*(^|[\s/])\.env(\.\S+)?(\s|$)', 'it would read a .env secrets file'),
    (r'\bgh\s+auth\s+(token|status\b[^;&|]*(-t|--show-token))', 'it would print your GitHub token'),
    (r'\bgit\s+credential\b', 'it would print stored credentials'),
    (r'\bprintenv\b|(^|[;&|]\s*)env\s*($|[;&|])', 'it would dump all environment variables (they often hold tokens)'),
]
DANGER = [
    (r'\b(sudo|doas|pkexec)\b', 'runs with root privileges'),
    (r'\brm\b[^;&|]*\s-[a-zA-Z]*[rRf]', 'deletes files recursively or without asking'),
    (r'\bgit\s+push\b[^;&|]*(--force\b|--force-with-lease|\s-f\b|\s\+\S)', 'force-push can overwrite history on the remote'),
    (r'\bgit\s+reset\s+--hard', 'discards uncommitted work'),
    (r'\bgit\s+clean\b[^;&|]*\s-[a-zA-Z]*f', 'permanently deletes untracked files'),
    (r'\bgit\s+(checkout|restore)\s+(--\s+)?\.(\s|$)', 'discards all uncommitted changes'),
    (r'\bgit\s+branch\s+-D\b|\bgit\s+push\b[^;&|]*--delete', 'deletes a branch'),
    (r'\b(shutdown|reboot|poweroff|halt)\b', 'powers off or restarts the machine'),
    (r'\b(curl|wget)\b[^;&]*\|\s*(sudo\s+)?(ba|z|da)?sh\b', 'pipes a download straight into a shell'),
    (r'\bdd\b', 'low-level copy that can destroy data'),
    (r'\b(chmod|chown)\s+-R\b', 'changes permissions recursively'),
    (r'\b(pacman|yay|paru)\s+-[a-zA-Z]*R', 'removes installed packages'),
    (r'\bsystemctl\s+(?!--user)(start|stop|restart|enable|disable|mask|kill)\b', 'changes system services'),
    (r'\bgh\s+repo\s+(delete|edit)\b|\bgh\s+api\b[^;&|]*-X\s*(DELETE|PUT|PATCH)', 'changes or deletes things on GitHub'),
]
INTERACTIVE = r'^\s*(vim?|nvim|nano|emacs|less|more|top|htop|btop|man|passwd)\b|\bgh\s+auth\s+login\b|\bgit\s+rebase\s+-i\b|\bgit\s+add\s+-p\b|ssh-keygen(?![^;&|]*\s-N\b)'

RO_CMDS = {'ls', 'pwd', 'whoami', 'id', 'uname', 'date', 'which', 'type', 'df', 'du', 'free', 'uptime', 'hostname', 'stat', 'file', 'wc',
           'tree', 'realpath', 'basename', 'dirname', 'nproc', 'lscpu', 'lsblk', 'ps', 'echo'}
RO_GIT = {'status', 'log', 'diff', 'show', 'rev-parse', 'ls-files', 'ls-remote', 'describe', 'blame', 'shortlog', 'rev-list', 'cat-file', 'grep',
          'check-ignore', 'name-rev', 'merge-base', 'diff-tree', 'show-ref', 'for-each-ref', 'count-objects', 'version', '--version', 'help'}
RO_GH = {('auth', 'status'), ('repo', 'view'), ('pr', 'list'), ('pr', 'view'), ('pr', 'status'), ('issue', 'list'), ('issue', 'view'), ('--version',), ('repo', 'list'), ('run', 'list')}

def _tokens(command: str):
    lex = shlex.shlex(command, posix=True, punctuation_chars=True); lex.whitespace_split = True
    return list(lex)

def _is_complex(command: str, toks) -> bool:
    return bool(re.search(r'\$\(|`|\$\{|\n', command)) or any(t and all(c in '();<>|&' for c in t) for t in toks)

def _git_sub(toks):
    """-> (subcommand, rest) skipping `-C dir` style global options."""
    i = 1
    while i < len(toks):
        t = toks[i]
        if t == '-C' or t == '--git-dir' or t == '--work-tree': i += 2; continue
        if t.startswith('-') and t != '--version': return None, []   # -c and friends can run code: not read-only
        return t, toks[i + 1:]
    return None, []

def readonly(command: str) -> bool:
    try: toks = _tokens(command)
    except ValueError: return False
    if not toks or _is_complex(command, toks): return False
    head = os.path.basename(toks[0])
    if head in RO_CMDS: return True
    if head == 'git':
        sub, rest = _git_sub(toks)
        if sub in RO_GIT: return True
        flags = [r for r in rest if r.startswith('-')]; args = [r for r in rest if not r.startswith('-')]
        if sub == 'branch': return not args and not (set(flags) - {'-a', '-r', '-v', '-vv', '--list', '--show-current', '--all', '--remotes'})
        if sub == 'remote': return not args or args[0] in {'show', 'get-url'} or (not args and flags == ['-v'])
        if sub == 'config': return bool({'--get', '--get-all', '--list', '-l', '--get-regexp'} & set(flags))
        if sub == 'tag': return not args or '-l' in flags or '--list' in flags
        if sub == 'stash': return bool(args) and args[0] in {'list', 'show'}
        if sub == 'worktree': return bool(args) and args[0] == 'list'
        if sub == 'reflog': return not args or args[0] == 'show'
        return False
    if head == 'gh':
        rest = tuple(t for t in toks[1:] if not t.startswith('-'))[:2] or tuple(toks[1:2])
        return rest in RO_GH or tuple(toks[1:2]) == ('--version',)
    if head == 'ssh': return '-T' in toks and toks[-1] == 'git@github.com' and all(t.startswith('-') or t in ('git@github.com', 'BatchMode=yes') or re.fullmatch(r'\w+=\S+', t) or toks[i - 1] == '-o' for i, t in enumerate(toks) if i)
    return False

def classify(command: str):
    """-> (level, reason) where level is deny | interactive | danger | readonly | normal."""
    for pat, why in DENY:
        if re.search(pat, command): return 'deny', why
    if re.search(INTERACTIVE, command): return 'interactive', 'This program is interactive'
    for pat, why in DANGER:
        if re.search(pat, command): return 'danger', why
    if readonly(command): return 'readonly', ''
    return 'normal', ''

# --------------------------------------------------------------------------- output hygiene
SECRET_RX = re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)|\bgh[pousr]_[A-Za-z0-9]{30,}|\bgithub_pat_[A-Za-z0-9_]{40,}|\bAKIA[0-9A-Z]{16}\b|\bxox[baprs]-[A-Za-z0-9-]{10,}|\bsk-[A-Za-z0-9]{32,}')
URL_CREDS = re.compile(r'(://)[^/@\s]+:[^/@\s]*@|(://)[A-Za-z0-9_\-]{20,}@')

def redact(text: str) -> str:
    return URL_CREDS.sub(lambda m: (m.group(1) or m.group(2)) + '***@', SECRET_RX.sub('[redacted secret]', text))

def trim(text: str, limit: int = 6800, head: int = 1500) -> str:
    if len(text) <= limit: return text
    return text[:head] + f'\n…({len(text) - limit} characters trimmed)…\n' + text[-(limit - head):]

HINTS = [
    (r'Please tell me who you are|unable to auto-detect email|empty ident name', 'Git has no identity. Call git_setup(name, email).'),
    (r'could not read Username|could not read Password|Authentication failed|terminal prompts disabled|Support for password authentication was removed|invalid credentials|HTTP 401|HTTP 403|requires authentication', 'No working GitHub credentials for HTTPS. Call github_login() (gh auth login + setup-git), or switch the remote to SSH. Never ask the Boss to paste a token.'),
    (r'Permission denied \(publickey\)|Host key verification failed|no such identity|sign_and_send_pubkey', 'SSH key is missing, not added to GitHub, or not loaded in ssh-agent. Call github_login(method="ssh"), or use gh/HTTPS instead.'),
    (r'GH007|email privacy|exposes? your (private )?email', 'GitHub blocked the push because the commit email is private. Use the <id>+<user>@users.noreply.github.com address via git_setup, then amend the commit author, or untick "Block command line pushes that expose my email" on GitHub.'),
    (r'protected branch|GH006|GH013|Changes must be made through a pull request', 'That branch is protected. Push to a new branch (git checkout -b name) and open a pull request.'),
    (r'non-fast-forward|fetch first|\(fetch first\)|Updates were rejected', 'The remote has commits you do not have. Run git(args="pull --rebase"), resolve any conflicts, then push again. Do not force-push unless the Boss asks.'),
    (r'src refspec .* does not match any|does not have any commits yet|unknown revision or path', 'There are no commits on this branch yet. Call git_commit first.'),
    (r'has no upstream branch', 'Push with set_upstream (git_push does this by default).'),
    (r'GH001|exceeds GitHub.s file size limit|Large files detected', 'A file is over GitHub\'s 100 MB limit. Remove it from the commit/history or track it with Git LFS.'),
    (r'not a git repository', 'This folder is not a git repo. Run git(args="init -b main") first.'),
    (r'dubious ownership', 'Git distrusts this folder\'s owner. Fix with git(args="config --global --add safe.directory <that path>").'),
    (r'Repository not found|repository .* not found|does not appear to be a git repository', 'Remote repo not found: check owner/name spelling and that the signed-in account can access it (private repos need auth). github_create_repo can create it.'),
    (r'refusing to merge unrelated histories', 'Local and remote histories are unrelated. Ask the Boss before using --allow-unrelated-histories.'),
    (r'a password is required|a terminal is required to read the password|no tty present', 'This needs a password Kage cannot type. Use open_terminal(command) so the Boss enters it privately.'),
    (r'command not found|No such file or directory: .(git|gh|ssh)', 'That program is not installed. Check with which_command, then pacman_search / pacman_install (GitHub CLI package: github-cli).'),
    (r'Timed out|timed out|Connection timed out|Could not resolve host|Network is unreachable', 'Network problem or timeout. Check connectivity, then retry once.'),
]
def hints(output: str) -> str:
    return ' '.join(dict.fromkeys(h for pat, h in HINTS if re.search(pat, output, re.I)))

def fmt(code: int, out: str) -> str:
    out = trim(redact((out or '').strip())) or '(no output)'
    head = 'OK (exit 0)' if code == 0 else 'TIMED OUT' if code == 124 else f'FAILED (exit {code})'
    h = hints(out) if code else ''
    return f'{head}\n{out}' + (f'\nHINT: {h}' if h else '')

# --------------------------------------------------------------------------- execution
def clean_env(extra=None) -> dict:
    """Environment in which nothing waits for a human: no credential prompts, no pagers, no editors."""
    e = dict(os.environ)
    e.update({'GIT_TERMINAL_PROMPT': '0', 'GCM_INTERACTIVE': 'never', 'GIT_PAGER': 'cat', 'PAGER': 'cat', 'GH_PAGER': 'cat', 'GH_PROMPT_DISABLED': '1',
              'GH_NO_UPDATE_NOTIFIER': '1', 'GIT_EDITOR': 'true', 'EDITOR': 'true', 'NO_COLOR': '1', 'DEBIAN_FRONTEND': 'noninteractive'})
    e.setdefault('GIT_SSH_COMMAND', 'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=15')
    if extra: e.update(extra)
    return e

def execute(cmd, cwd=None, timeout=120, on_poll=None, use_shell=False, env=None):
    """-> (returncode, merged output). stdin is closed; the whole process group is killed on timeout or cancel.
    `on_poll` is called about twice a second and may raise (the agent uses it to honour Stop)."""
    argv = ['bash', '-c', cmd] if use_shell else list(cmd)
    try: p = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace',
                              env=env or clean_env(), start_new_session=True)
    except FileNotFoundError: return 127, f'command not found: {argv[0]}'
    except OSError as e: return 126, f'cannot run {argv[0]}: {e}'
    deadline = time.time() + timeout
    def kill():
        try: os.killpg(p.pid, 9)
        except OSError: pass
        try: return p.communicate(timeout=5)[0] or ''
        except Exception: return ''
    while True:
        try:
            out, _ = p.communicate(timeout=0.5); return p.returncode, out or ''
        except subprocess.TimeoutExpired:
            if time.time() > deadline: return 124, kill() + f'\n[timed out after {timeout}s and was killed]'
            if on_poll:
                try: on_poll()
                except BaseException: kill(); raise

# --------------------------------------------------------------------------- real terminal for interactive steps
TERMS = {'kitty': ['kitty', '--title', '{t}', 'bash', '-c'], 'foot': ['foot', '--title', '{t}', 'bash', '-c'], 'alacritty': ['alacritty', '--title', '{t}', '-e', 'bash', '-c'],
         'wezterm': ['wezterm', 'start', '--', 'bash', '-c'], 'ghostty': ['ghostty', '-e', 'bash', '-c'], 'gnome-terminal': ['gnome-terminal', '--title={t}', '--', 'bash', '-c'],
         'konsole': ['konsole', '-e', 'bash', '-c'], 'xfce4-terminal': ['xfce4-terminal', '--title={t}', '-x', 'bash', '-c'], 'xterm': ['xterm', '-T', '{t}', '-e', 'bash', '-c'],
         'x-terminal-emulator': ['x-terminal-emulator', '-e', 'bash', '-c']}

def terminal_argv(script: str, title: str = 'Kage'):
    wrapped = script + '\nstatus=$?\necho\n[ $status -eq 0 ] && echo "[Kage] finished OK." || echo "[Kage] exited with status $status."\nread -rp "Press Enter to close this window… "'
    pref = os.path.basename(os.environ.get('TERMINAL', '') or '')
    for name in ([pref] if pref else []) + list(TERMS):
        if shutil.which(name):
            tpl = TERMS.get(name, [name, '-e', 'bash', '-c'])
            return [a.replace('{t}', title) for a in tpl] + [wrapped], name
    return None, None
