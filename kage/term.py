"""Interactive terminal sessions. Standard library only (pty, termios, select).

Two front ends share this module:
  * the web view: `TermManager` owns real PTY sessions (bash, vim, htop, `gh auth login`...). The browser streams their
    output and sends keystrokes back (see web.py /api/term/* and ui/term.js).
  * the terminal agent: `run_inline` hands the user's own terminal to the child process until it exits.

A session keeps a ring buffer of its output with absolute offsets, so a browser tab can reconnect and replay.
"""
from __future__ import annotations
from pathlib import Path
import atexit, fcntl, os, pty, select, shutil, signal, struct, subprocess, termios, threading, time, uuid
from . import config
from .errors import AgentError

MAX_BUF = 262_144        # bytes of scrollback kept per session for reconnects
MAX_SESSIONS = 8
MAX_INPUT = 65_536

def default_shell() -> str:
    sh = os.environ.get('SHELL') or ''
    return sh if sh and os.path.exists(sh) else (shutil.which('bash') or '/bin/sh')

def term_env() -> dict:
    e = dict(os.environ)
    e.update({'TERM': 'xterm-256color', 'COLORTERM': 'truecolor', 'KAGE_TERMINAL': '1'})
    for k in ('LINES', 'COLUMNS'): e.pop(k, None)
    return e

BASH_RC = r'''[ -f ~/.bashrc ] && . ~/.bashrc
__kage_report() {
  local c=$? n h
  read -r n h <<<"$(builtin history 1 2>/dev/null)"
  printf '\033]777;kage;%s;%s;%s;%s\007' "$c" "${n:-0}" "$(printf %s "$h" | base64 | tr -d '\n')" "$(printf %s "$PWD" | base64 | tr -d '\n')"
  return $c
}
if [[ "$(declare -p PROMPT_COMMAND 2>/dev/null)" == "declare -a"* ]]; then PROMPT_COMMAND=(__kage_report "${PROMPT_COMMAND[@]}"); else PROMPT_COMMAND="__kage_report${PROMPT_COMMAND:+;$PROMPT_COMMAND}"; fi
'''
ZSH_RC = r'''ZDOTDIR="${KAGE_ORIG_ZDOTDIR:-$HOME}"; [ -f "$ZDOTDIR/.zshrc" ] && . "$ZDOTDIR/.zshrc"
__kage_cmd=''; __kage_n=0
__kage_preexec() { __kage_cmd="$1"; (( __kage_n++ )); }
__kage_precmd() { local c=$?; if [[ -n $__kage_cmd ]]; then printf '\033]777;kage;%s;%s;%s;%s\007' "$c" "$__kage_n" "$(printf %s "$__kage_cmd" | base64 | tr -d '\n')" "$(printf %s "$PWD" | base64 | tr -d '\n')"; fi; __kage_cmd=''; return $c; }
precmd_functions=(__kage_precmd $precmd_functions); preexec_functions+=(__kage_preexec)
'''

def _write(p: Path, text: str):
    try:
        if not p.exists() or p.read_text() != text: p.parent.mkdir(parents=True, exist_ok=True); p.write_text(text)
    except OSError: return False
    return True

def _argv(command: str, integrate=False):
    """-> (argv, extra_env). With `integrate`, bash and zsh shells report every command's exit code to the web terminal (OSC 777),
    which is what powers the \"Ask Kage why\" button. Nothing in your own rc files is changed."""
    command = (command or '').strip()
    if command: return ['bash', '-c', command], {}
    sh = default_shell(); name = os.path.basename(sh); d = config.DATA_DIR / 'shell'
    if integrate and name == 'bash' and _write(d / 'bashrc', BASH_RC): return [sh, '--rcfile', str(d / 'bashrc'), '-i'], {}
    if integrate and name == 'zsh' and _write(d / 'zsh/.zshrc', ZSH_RC) and _write(d / 'zsh/.zshenv', '[ -f "${KAGE_ORIG_ZDOTDIR:-$HOME}/.zshenv" ] && . "${KAGE_ORIG_ZDOTDIR:-$HOME}/.zshenv"\n'):
        return [sh, '-i'], {'ZDOTDIR': str(d / 'zsh'), 'KAGE_ORIG_ZDOTDIR': os.environ.get('ZDOTDIR') or str(config.HOME)}
    return [sh, '-i'], {}

def _winsize(fd, rows, cols):
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack('HHHH', max(2, min(int(rows), 500)), max(10, min(int(cols), 1000)), 0, 0))

def _ctty():
    """Runs in the child after setsid + dup2: make the PTY its controlling terminal (job control, Ctrl+C, vim need this)."""
    try: fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    except OSError: pass

class TermSession:
    def __init__(self, command: str, cwd, rows=24, cols=80, title='Terminal'):
        self.id = uuid.uuid4().hex[:12]; self.title = title or 'Terminal'; self.command = command or ''; self.cwd = str(cwd)
        self.buf = bytearray(); self.base = 0; self.cond = threading.Condition(); self.exit = None
        self.created = time.time(); self.active = time.time(); self._wlock = threading.Lock()
        master, slave = pty.openpty()
        try:
            _winsize(master, rows, cols)
            argv, extra = _argv(command, integrate=True)
            self.proc = subprocess.Popen(argv, cwd=cwd, env={**term_env(), **extra}, stdin=slave, stdout=slave, stderr=slave,
                                         start_new_session=True, close_fds=True, preexec_fn=_ctty)
        except (OSError, ValueError) as e:
            os.close(master); raise AgentError(f'Could not start the terminal: {e}')
        finally: os.close(slave)
        self.master = master
        threading.Thread(target=self._reader, daemon=True).start()

    # ---- output
    @property
    def total(self) -> int: return self.base + len(self.buf)
    @property
    def alive(self) -> bool: return self.exit is None

    def _push(self, data: bytes):
        with self.cond:
            self.buf += data
            if len(self.buf) > MAX_BUF:
                cut = len(self.buf) - MAX_BUF; del self.buf[:cut]; self.base += cut
            self.cond.notify_all()

    def _reader(self):
        fd = self.master
        while True:
            try: ready, _, _ = select.select([fd], [], [], 0.4)
            except (OSError, ValueError): break
            if ready:
                try: data = os.read(fd, 65536)
                except OSError: break            # EIO: the child side closed
                if not data: break
                self._push(data)
            elif self.proc.poll() is not None:
                # the shell is gone; drain whatever is left, then finish (background children may still hold the tty)
                try:
                    while select.select([fd], [], [], 0.05)[0]:
                        d = os.read(fd, 65536)
                        if not d: break
                        self._push(d)
                except OSError: pass
                break
        code = self.proc.wait()
        with self.cond:
            self.exit = code if code >= 0 else 128 - code      # killed by signal N -> 128+N
            self.cond.notify_all()
        try: os.close(fd)
        except OSError: pass

    def read_from(self, offset: int, timeout: float = 15.0):
        """-> (bytes, new_offset, exit_code_or_None). Blocks up to `timeout` for news."""
        deadline = time.time() + timeout
        with self.cond:
            while True:
                if offset < self.base: offset = self.base
                if offset < self.total: data = bytes(self.buf[offset - self.base:]); return data, self.total, self.exit
                if self.exit is not None: return b'', offset, self.exit
                left = deadline - time.time()
                if left <= 0: return b'', offset, None
                self.cond.wait(left)

    # ---- input
    def write(self, data: bytes):
        if not self.alive: raise AgentError('That terminal has exited.')
        self.active = time.time()
        with self._wlock:
            view = memoryview(data[:MAX_INPUT])
            while view:
                try: n = os.write(self.master, view)
                except OSError as e: raise AgentError(f'Terminal write failed: {e}')
                view = view[n:]

    def resize(self, rows, cols):
        if self.alive:
            try: _winsize(self.master, rows, cols)
            except OSError: pass

    def close(self):
        if self.exit is None:
            for sig, wait in ((signal.SIGHUP, 0.6), (signal.SIGKILL, 0)):
                try: os.killpg(self.proc.pid, sig)
                except OSError: break
                if wait:
                    end = time.time() + wait
                    while time.time() < end and self.exit is None: time.sleep(0.05)
                if self.exit is not None: break

    def info(self) -> dict:
        return {'id': self.id, 'title': self.title, 'alive': self.alive, 'exit': self.exit, 'command': self.command}

class TermManager:
    def __init__(self):
        self.sessions: dict[str, TermSession] = {}; self.lock = threading.Lock()
        atexit.register(self.close_all)

    def open(self, cwd, command='', title='', rows=24, cols=80) -> TermSession:
        with self.lock:
            self._reap()
            if sum(1 for s in self.sessions.values() if s.alive) >= MAX_SESSIONS: raise AgentError(f'Too many open terminals (max {MAX_SESSIONS}). Close one first.')
            s = TermSession(command, cwd, rows, cols, title or ('Shell' if not command else command.split()[0][:24]))
            self.sessions[s.id] = s; return s

    def get(self, sid: str) -> TermSession:
        s = self.sessions.get(str(sid))
        if not s: raise AgentError('No such terminal.')
        return s

    def close(self, sid: str):
        s = self.sessions.get(str(sid))
        if s: s.close()
        with self.lock: self.sessions.pop(str(sid), None)

    def list(self) -> list:
        with self.lock: self._reap(); return [s.info() for s in self.sessions.values()]

    def _reap(self):
        now = time.time()
        for sid, s in list(self.sessions.items()):
            if not s.alive and now - s.active > 1800 and now - s.created > 1800: self.sessions.pop(sid, None)

    def close_all(self):
        for s in list(self.sessions.values()):
            try: s.close()
            except Exception: pass

def run_inline(command: str, cwd) -> int:
    """Give the user's own terminal to a child (shell when `command` is empty) and wait. Ctrl+C reaches the child, not Kage."""
    import sys
    if not sys.stdin.isatty(): raise AgentError('There is no interactive terminal attached, so an interactive program cannot run here.')
    try: p = subprocess.Popen(_argv(command)[0], cwd=cwd, env=term_env())
    except OSError as e: raise AgentError(f'Could not start: {e}')
    while True:
        try: return p.wait()
        except KeyboardInterrupt: continue      # the child received the same Ctrl+C from the tty
