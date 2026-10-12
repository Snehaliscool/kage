"""Folder selection: places, repo discovery, directory browsing, native/terminal pickers."""
from __future__ import annotations
import os, shutil, subprocess, sys, time
from pathlib import Path
from . import config

SKIP = {'node_modules', '.git', '__pycache__', '.cache', 'target', 'dist', 'build', '.venv', 'venv', '.local', '.cargo', '.npm', '.rustup', 'snap', '.var'}
BLOCKED = {'proc', 'sys', 'dev'}
MARKERS = [('git', '.git'), ('py', 'pyproject.toml'), ('py', 'requirements.txt'), ('node', 'package.json'), ('rust', 'Cargo.toml'),
           ('go', 'go.mod'), ('docker', 'docker-compose.yml'), ('docker', 'compose.yaml'), ('docker', 'Dockerfile'), ('make', 'Makefile'),
           ('qml', 'shell.qml'), ('md', 'README.md')]

def badges(p: Path) -> list:
    out = []
    try:
        names = set(os.listdir(p))
    except OSError:
        return out
    for b, m in MARKERS:
        if m in names and b not in out:
            out.append(b)
    return out

def places() -> list:
    h = config.HOME; out = [{'label': 'Home', 'path': str(h), 'icon': '~'}]
    xdg = {}
    try:
        for line in (h / '.config/user-dirs.dirs').read_text().splitlines():
            if line.startswith('XDG_') and '=' in line:
                k, v = line.split('=', 1); xdg[k.split('_')[1]] = v.strip().strip('"').replace('$HOME', str(h))
    except OSError:
        pass
    for label, key, fb in [('Projects', 'PROJECTS', 'Projects'), ('Documents', 'DOCUMENTS', 'Documents'), ('Downloads', 'DOWNLOAD', 'Downloads'),
                           ('Desktop', 'DESKTOP', 'Desktop'), ('Code', None, 'code'), ('Dev', None, 'dev'), ('Src', None, 'src')]:
        p = Path(xdg.get(key) or h / fb) if key else h / fb
        if p.is_dir() and str(p) not in [x['path'] for x in out]:
            out.append({'label': label, 'path': str(p), 'icon': label[0]})
    cfg_dir = h / '.config'
    if cfg_dir.is_dir(): out.append({'label': '.config', 'path': str(cfg_dir), 'icon': '◦'})
    return out

_repo_cache = {'t': 0, 'v': []}
def repos(limit=40, budget=0.9) -> list:
    """Git repos and project folders near home. Cached, time-boxed so the picker opens instantly."""
    if time.time() - _repo_cache['t'] < 60 and _repo_cache['v']:
        return _repo_cache['v'][:limit]
    h = config.HOME; roots = [h / n for n in ('Projects', 'projects', 'code', 'dev', 'src', 'Documents', 'Desktop', 'Downloads')] + [h]
    found, seen, end = [], set(), time.time() + budget
    for root in roots:
        if not root.is_dir(): continue
        base = len(root.parts)
        for cur, dirs, files in os.walk(root):
            if time.time() > end: break
            depth = len(Path(cur).parts) - base
            dirs[:] = [d for d in dirs if not d.startswith('.') and d not in SKIP] if depth < 3 else []
            if depth and '.git' in os.listdir(cur):
                if cur not in seen:
                    seen.add(cur); found.append({'label': Path(cur).name, 'path': cur, 'badges': badges(Path(cur))})
                dirs[:] = []
            if len(found) >= 120: break
    found.sort(key=lambda r: -os.path.getmtime(r['path']))
    _repo_cache.update(t=time.time(), v=found)
    return found[:limit]

def list_dir(path: str, hidden=False) -> dict:
    p = Path(path or config.HOME).expanduser()
    try: p = p.resolve()
    except OSError: p = config.HOME
    if any(x in BLOCKED for x in p.parts[1:2]): p = config.HOME
    while not p.is_dir() and p != p.parent: p = p.parent
    entries = []
    try:
        for e in sorted(os.scandir(p), key=lambda e: e.name.lower()):
            if not e.is_dir(follow_symlinks=False) and not (e.is_symlink() and os.path.isdir(e.path)): continue
            if not hidden and e.name.startswith('.'): continue
            entries.append({'name': e.name, 'path': e.path, 'badges': badges(Path(e.path)) if len(entries) < 300 else []})
            if len(entries) >= 800: break
    except PermissionError:
        return {'path': str(p), 'parent': str(p.parent) if p != p.parent else None, 'entries': [], 'error': 'Permission denied', 'crumbs': _crumbs(p)}
    return {'path': str(p), 'parent': str(p.parent) if p != p.parent else None, 'entries': entries, 'badges': badges(p), 'crumbs': _crumbs(p)}

def _crumbs(p: Path) -> list:
    out, cur = [], Path(p.anchor)
    out.append({'name': '/', 'path': str(cur)})
    for part in p.parts[1:]:
        cur = cur / part; out.append({'name': part, 'path': str(cur)})
    return out

def valid(path: str):
    try: p = Path(path).expanduser().resolve()
    except (OSError, RuntimeError): return None
    return p if p.is_dir() and not any(x in BLOCKED for x in p.parts[1:2]) else None

def native_pick(start=None):
    """Open the desktop's own folder dialog if one exists. Returns a path string or None."""
    start = str(start or config.HOME) + '/'
    cmds = []
    if shutil.which('zenity'): cmds.append(['zenity', '--file-selection', '--directory', '--title=Kage: choose a workspace', f'--filename={start}'])
    if shutil.which('kdialog'): cmds.append(['kdialog', '--getexistingdirectory', start, '--title', 'Kage: choose a workspace'])
    if shutil.which('yad'): cmds.append(['yad', '--file', '--directory', '--title=Kage: choose a workspace', f'--filename={start}'])
    for c in cmds:
        try:
            r = subprocess.run(c, text=True, capture_output=True, timeout=300)
            if r.returncode == 0 and r.stdout.strip(): return r.stdout.strip()
        except (OSError, subprocess.SubprocessError): continue
    return None

def terminal_pick():
    """fzf (if installed) or a numbered menu. Lists recents, cwd, repos and common places."""
    cands, seen = [], set()
    def add(p):
        p = str(p)
        if p not in seen and Path(p).is_dir(): seen.add(p); cands.append(p)
    add(Path.cwd())
    for r in config.load().get('recents', []): add(r)
    for r in repos(60): add(r['path'])
    for pl in places(): add(pl['path'])
    if not cands: return None
    if shutil.which('fzf') and sys.stdin.isatty():
        try:
            r = subprocess.run(['fzf', '--prompt', '影 workspace> ', '--height', '60%', '--reverse', '--border', '--header', 'Pick a folder · Enter to open · Esc to cancel'],
                               input='\n'.join(cands), text=True, stdout=subprocess.PIPE)
            return r.stdout.strip() or None
        except OSError: pass
    shown = cands[:15]
    print('\nChoose a workspace:')
    for i, c in enumerate(shown, 1): print(f'  {i:>2}. {config.short(c, 70)}')
    try: ans = input('Number, or a path (Enter = 1): ').strip()
    except (EOFError, KeyboardInterrupt): return None
    if not ans: return shown[0]
    if ans.isdigit() and 1 <= int(ans) <= len(shown): return shown[int(ans) - 1]
    return ans if Path(ans).expanduser().is_dir() else None
