"""The vault: durable memory, a daily journal and generated machine maps (the rashin idea).

Layout (~/.local/share/kage/vault):
  AGENTS.md  CLAUDE.md→AGENTS.md   rules any coding agent should read first
  system.md desktop.md packages.md user.md habits.md   generated, safe to regenerate
  memory/MEMORY.md                  durable notes the agent keeps across sessions
  journal/YYYY-MM-DD.md             one file per day
  knowledge/<workspace>/            reviewed notes anchored to code (see knowledge.py)
Generated files only change between the kage:generated markers; prose outside is kept.
"""
from __future__ import annotations
import datetime as dt, json, os, re, shutil, subprocess, threading, time
from pathlib import Path
from . import config

BEGIN, END = '<!-- kage:generated:begin -->', '<!-- kage:generated:end -->'
AGENTS_MD = """# Kage system vault

Shared knowledge base for every coding agent on this machine. Read it before
exploring the filesystem or guessing where things live.

## The one rule

Read `desktop.md` before searching the filesystem. It maps every subsystem to
its config path, the binary that owns it and how to reload it.

## What is here

- `system.md` generated: hardware, kernel, GPU, disks, displays.
- `desktop.md` generated: the config map (paths, owners, reload commands).
- `packages.md` generated: installed package counts, the explicit set, updates.
- `user.md` generated: where this user's config diverges from the baseline.
- `habits.md` generated: this user's directories, tool stack and shell rhythms.
- `memory/` durable notes agents keep across sessions (`MEMORY.md`).
- `journal/` dated notes, one file per day named `YYYY-MM-DD.md`.
- `knowledge/` reviewed decisions, concepts and gotchas anchored to real code.

## Rules

- Generated files are overwritten between the `kage:generated` markers on every
  reindex. Write anything durable to `memory/` or `journal/` instead.
- Changes listed in `user.md` are the user's own; never revert them unasked.
- You may add prose outside the generated markers; it is kept.
- This file is yours to extend. `CLAUDE.md` is a symlink to it.
"""
FILES = ['AGENTS.md', 'system.md', 'desktop.md', 'packages.md', 'user.md', 'habits.md']

def sh(cmd, timeout=4):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return (r.stdout or '').strip() if r.returncode == 0 else ''
    except (OSError, subprocess.SubprocessError):
        return ''

class Vault:
    def __init__(self, root: Path | None = None):
        self.root = Path(root or config.VAULT_DIR)
        self._lock = threading.Lock()
        self.ensure()

    # ------------------------------------------------------------ layout
    def ensure(self):
        for d in ('memory', 'journal', 'knowledge'): (self.root / d).mkdir(parents=True, exist_ok=True)
        a = self.root / 'AGENTS.md'
        if not a.exists(): a.write_text(AGENTS_MD)
        c = self.root / 'CLAUDE.md'
        if not c.exists() and not c.is_symlink():
            try: c.symlink_to('AGENTS.md')
            except OSError: shutil.copy(a, c)
        m = self.root / 'memory' / 'MEMORY.md'
        if not m.exists(): m.write_text('# Memory\n\nDurable notes Kage keeps across sessions. One bullet per fact.\n\n')

    def safe(self, rel: str) -> Path:
        p = (self.root / rel).resolve()
        if p != self.root and self.root.resolve() not in p.parents: raise ValueError('Path escapes the vault.')
        return p

    def tree(self) -> list:
        out = []
        for base, dirs, files in os.walk(self.root):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for f in sorted(files):
                if f.startswith('.') or f == 'CLAUDE.md': continue
                p = Path(base) / f; rel = p.relative_to(self.root).as_posix()
                try: txt = p.read_text(errors='replace')[:400000]
                except OSError: continue
                out.append({'path': rel, 'size': p.stat().st_size, 'mtime': int(p.stat().st_mtime), 'generated': BEGIN in txt})
        out.sort(key=lambda x: (x['path'].count('/') > 0, x['path'] if '/' not in x['path'] else '', -x['mtime'] if x['path'].startswith('journal/') else 0, x['path']))
        return out

    def read(self, rel: str, limit=200000) -> str:
        p = self.safe(rel)
        if not p.is_file(): raise FileNotFoundError(rel)
        return p.read_text(errors='replace')[:limit]

    def write(self, rel: str, content: str):
        p = self.safe(rel)
        if p.suffix not in ('.md', '.txt'): raise ValueError('Only .md or .txt files can be written in the vault.')
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)

    # ------------------------------------------------------------ memory + journal
    def memory_add(self, note: str) -> str:
        note = ' '.join(note.split())[:500]
        if not note: raise ValueError('Empty note.')
        m = self.root / 'memory' / 'MEMORY.md'
        cur = m.read_text()
        if note.lower() in cur.lower(): return 'Already remembered.'
        with self._lock, open(m, 'a') as f: f.write(f'- {note}  <!-- {dt.date.today()} -->\n')
        return f'Remembered: {note}'

    def memory_read(self, query: str = '', limit=40) -> str:
        lines = [re.sub(r'\s*<!--.*?-->', '', l) for l in (self.root / 'memory' / 'MEMORY.md').read_text().splitlines() if l.startswith('- ')]
        if query:
            ws = [w for w in re.findall(r'\w{3,}', query.lower())]
            lines = [l for l in lines if any(w in l.lower() for w in ws)]
        return '\n'.join(lines[-limit:]) or '(nothing remembered yet)'

    def journal_add(self, text: str, workspace: str = '') -> None:
        text = ' '.join(text.split())[:400]
        if not text: return
        f = self.root / 'journal' / f'{dt.date.today()}.md'
        with self._lock:
            if not f.exists(): f.write_text(f'# {dt.date.today():%A, %d %B %Y}\n\n')
            with open(f, 'a') as h: h.write(f"- {dt.datetime.now():%H:%M}{f' [{workspace}]' if workspace else ''} {text}\n")

    def journal_tail(self, n=5) -> list:
        f = self.root / 'journal' / f'{dt.date.today()}.md'
        return [l[2:] for l in f.read_text().splitlines() if l.startswith('- ')][-n:] if f.exists() else []

    # ------------------------------------------------------------ generated files
    def _write_gen(self, name: str, title: str, intro: str, body: str):
        p = self.root / name; block = f'{BEGIN}\n{body.strip()}\n{END}\n'
        if p.exists():
            cur = p.read_text()
            if BEGIN in cur and END in cur:
                a, b = cur.index(BEGIN), cur.index(END) + len(END)
                p.write_text(cur[:a] + block.rstrip('\n') + cur[b:]); return
        p.write_text(f'# {title}\n\n{intro}\n\n{block}')

    def _system(self) -> tuple:
        osr = {}
        try:
            for l in Path('/etc/os-release').read_text().splitlines():
                if '=' in l: k, v = l.split('=', 1); osr[k] = v.strip('"')
        except OSError: pass
        cpu = ''
        try: cpu = re.search(r'model name\s*:\s*(.+)', Path('/proc/cpuinfo').read_text()).group(1).strip()
        except (OSError, AttributeError): pass
        mem = ''
        try: mem = f"{int(re.search(r'MemTotal:\s+(\d+)', Path('/proc/meminfo').read_text()).group(1)) / 1048576:.1f} GiB"
        except (OSError, AttributeError): pass
        gpu = '; '.join(re.sub(r'^\S+\s+\S+\s+', '', l).strip()[:90] for l in sh(['lspci']).splitlines() if re.search(r'VGA|3D|Display', l)) if shutil.which('lspci') else ''
        disks = sh(['lsblk', '-dno', 'NAME,SIZE,TYPE,MODEL']) if shutil.which('lsblk') else ''
        disp = ''
        if shutil.which('hyprctl'):
            try: disp = '\n'.join(f"- {m['name']}: {m['width']}x{m['height']}@{m['refreshRate']:.0f}Hz scale {m['scale']}" for m in json.loads(sh(['hyprctl', 'monitors', '-j'])))
            except (ValueError, KeyError): pass
        models = sh(['ollama', 'list']) if shutil.which('ollama') else ''
        lines = [f"- OS: {osr.get('PRETTY_NAME', 'Linux')}", f"- Kernel: {os.uname().release} ({os.uname().machine})", f'- CPU: {cpu} ({os.cpu_count()} threads)', f'- RAM: {mem}']
        if gpu: lines.append(f'- GPU: {gpu}')
        lines.append(f"- Session: {os.environ.get('XDG_SESSION_TYPE', '?')} / {os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
        body = '## Hardware\n\n' + '\n'.join(lines)
        if disks: body += '\n\n## Disks\n\n```\n' + disks + '\n```'
        if disp: body += '\n\n## Displays\n\n' + disp
        if models: body += '\n\n## Ollama models\n\n```\n' + '\n'.join(models.splitlines()[:15]) + '\n```'
        return body, f"{osr.get('PRETTY_NAME', 'Linux')} · {cpu.split('@')[0].strip()} · {mem}" + (f' · {gpu.split(";")[0]}' if gpu else '')

    def _desktop(self) -> str:
        h = config.HOME; cfg = h / '.config'
        rows = [('Hyprland (compositor)', '~/.config/hypr/', 'Hyprland', 'hyprctl reload', 'hypr'), ('Quickshell (shell UI)', '~/.config/quickshell/', 'quickshell', 'ryoku reload' if shutil.which('ryoku') else 'restart quickshell', 'quickshell'),
                ('Waybar', '~/.config/waybar/', 'waybar', 'pkill -SIGUSR2 waybar', 'waybar'), ('Terminal (kitty)', '~/.config/kitty/', 'kitty', 'relaunch kitty', 'kitty'),
                ('Terminal (alacritty)', '~/.config/alacritty/', 'alacritty', 'automatic', 'alacritty'), ('Fish', '~/.config/fish/', 'fish', 'open a new shell', 'fish'),
                ('Starship prompt', '~/.config/starship.toml', 'starship', 'open a new shell', None), ('Neovim', '~/.config/nvim/', 'nvim', 'relaunch nvim', 'nvim'),
                ('Yazi', '~/.config/yazi/', 'yazi', 'relaunch yazi', 'yazi'), ('Ryoku hub state', '~/.config/ryoku/', 'ryoku-hub', 'written by Ryoku Settings', 'ryoku'),
                ('Theme palette', '~/.cache/ryoku/colors.json', 'matugen', 'ryogami wallpaper repaint', None), ('Fastfetch', '~/.config/fastfetch/', 'fastfetch', 'none', 'fastfetch'),
                ('Kage', '~/.config/kage/', 'kage', 'restart kage', 'kage')]
        t = ['| Subsystem | Config path | Owner | Reload |', '|---|---|---|---|']
        for label, path, owner, reload, d in rows:
            p = Path(path.replace('~', str(h)))
            if p.exists(): t.append(f'| {label} | {path} | {owner} | {reload} |')
        body = '## Subsystem map\n\nOnly subsystems found on this machine are listed.\n\n' + '\n'.join(t)
        if shutil.which('ryoku') or (cfg / 'ryoku').exists():
            vers = [l for l in sh(['pacman', '-Q']).splitlines() if re.match(r'(ryoku|prowl)', l)] if shutil.which('pacman') else []
            body += ('\n\n## Acting on this desktop\n\nThis is a Ryoku machine. Change the desktop through its commands (`ryoku`, `ryoku-shell`, `ryoku-hub`, `ryogami`, `hyprctl`), '
                     'never by editing shipped files.' + ('\n\n## Ryoku packages\n\n' + '\n'.join(f'- {v}' for v in vers) if vers else ''))
        return body

    def _packages(self) -> str:
        if not shutil.which('pacman'): return 'pacman is not available on this machine.'
        allp = sh(['pacman', '-Qq'], 10).splitlines(); exp = sh(['pacman', '-Qeq'], 10).splitlines(); aur = sh(['pacman', '-Qmq'], 10).splitlines()
        upd = sh(['checkupdates'], 8).splitlines() if shutil.which('checkupdates') else None
        body = f'## Counts\n\n- Installed: {len(allp)}\n- Explicitly installed: {len(exp)}\n- Foreign (AUR/local): {len(aur)}' + (f'\n- Updates pending: {len(upd)}' if upd is not None else '')
        if exp: body += '\n\n## Explicit set\n\n' + ', '.join(exp[:150]) + (' …' if len(exp) > 150 else '')
        if aur: body += '\n\n## Foreign\n\n' + ', '.join(aur[:60])
        return body

    def _user(self) -> str:
        h = config.HOME
        known = ['~/.config/hypr/user.lua', '~/.config/hypr/monitors_user.lua', '~/.config/hypr/hyprland.conf', '~/.config/kitty/kitty.conf', '~/.config/fish/config.fish', '~/.bashrc', '~/.zshrc', '~/.config/nvim/init.lua', '~/.config/starship.toml']
        have = [k for k in known if Path(k.replace('~', str(h))).exists()]
        return 'Files that are the user\'s own. Respect these when editing; they are choices, not defaults.\n\n' + ('\n'.join(f'- `{k}`' for k in have) or '- (none detected)')

    def _habits(self) -> tuple:
        h = config.HOME; env = os.environ
        body = ['## Environment\n', f"- Shell: {Path(env.get('SHELL', 'sh')).name} · Terminal: {env.get('TERM_PROGRAM') or env.get('TERMINAL') or env.get('TERM', '?')} · Editor: {env.get('EDITOR') or env.get('VISUAL') or '?'}"]
        homes = sorted(p.name for p in h.iterdir() if p.is_dir() and not p.name.startswith('.'))[:30] if h.is_dir() else []
        if homes: body.append('- Home directories: ' + ', '.join(f'~/{d}' for d in homes))
        stack = [t for t in ('eza', 'fd', 'rg', 'bat', 'fzf', 'zoxide', 'btop', 'yazi', 'lazygit', 'nvim', 'git', 'docker', 'podman', 'ollama', 'node', 'python3', 'cargo', 'go', 'uv') if shutil.which(t)]
        body += ['\n## Stack\n', '- Available: ' + ', '.join(stack)]
        if 'rg' in stack: body.append('- prefer rg over grep')
        if 'fd' in stack: body.append('- prefer fd over find')
        if 'zoxide' in stack: body.append('- `cd` may be zoxide: it jumps by fragment')
        hist = []
        if config.load().get('habits_history'):
            for f, rx in ((h / '.local/share/fish/fish_history', r'^- cmd: (.+)'), (h / '.bash_history', r'^(.+)'), (h / '.zsh_history', r'^(?:: \d+:\d+;)?(.+)')):
                try:
                    from collections import Counter
                    c = Counter()
                    for l in f.read_text(errors='replace').splitlines()[-4000:]:
                        m = re.match(rx, l)
                        if m and m.group(1).strip():
                            w = m.group(1).split()
                            if w and not w[0].startswith(('#', '-')): c[' '.join(w[:2]) if w[0] in ('git', 'docker', 'cargo', 'npm', 'pacman', 'systemctl') and len(w) > 1 else w[0]] += 1
                    hist = c.most_common(15)
                    if hist: break
                except OSError: continue
        if hist: body += ['\n## Shell rhythms\n', 'Top commands from shell history (counts only; nothing leaves this machine):\n'] + [f'- {c} ({n})' for c, n in hist]
        return '\n'.join(body), ', '.join(stack[:10])

    def reindex(self) -> dict:
        """Regenerate every machine map. Each part is isolated so a missing tool never breaks the rest."""
        self.ensure(); t0 = time.time(); state = {}; errs = []
        def part(name, fn):
            try: return fn()
            except Exception as e: errs.append(f'{name}: {type(e).__name__}: {e}'); return None
        r = part('system', self._system)
        if r: self._write_gen('system.md', 'System', 'Generated hardware and OS facts.', r[0]); state['machine'] = r[1]
        r = part('desktop', self._desktop)
        if r: self._write_gen('desktop.md', 'Desktop map', 'Where every subsystem lives and how to reload it. Read before searching the filesystem.', r)
        r = part('packages', self._packages)
        if r: self._write_gen('packages.md', 'Packages', 'Installed software.', r)
        r = part('user', self._user)
        if r: self._write_gen('user.md', 'User-owned files', 'Where this machine diverges from the baseline.', r)
        r = part('habits', self._habits)
        if r: self._write_gen('habits.md', 'Habits', 'How this user works. Prefer these names and tools.', r[0]); state['stack'] = r[1]
        state['at'] = time.time()
        (self.root / '.state.json').write_text(json.dumps(state))
        return {'ms': int((time.time() - t0) * 1000), 'errors': errs, 'files': len(FILES)}

    def state(self) -> dict:
        try: return json.loads((self.root / '.state.json').read_text())
        except (OSError, ValueError): return {}

    def ensure_fresh(self, max_age=6 * 3600):
        if time.time() - self.state().get('at', 0) > max_age:
            threading.Thread(target=self.reindex, daemon=True).start()

    def brief(self, workspace: str = '') -> str:
        st = self.state(); out = []
        if st.get('machine'): out.append('Machine: ' + st['machine'])
        mem = self.memory_read(limit=8)
        if not mem.startswith('('): out.append('Remembered:\n' + mem)
        j = self.journal_tail(3)
        if j: out.append('Earlier today:\n' + '\n'.join('- ' + x for x in j))
        return '\n'.join(out)[:1600]
