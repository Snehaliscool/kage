"""Project scanner: scan a folder fast, describe it and its files without a model round trip. Standard library only.

`scan(root)` walks the folder once (time-boxed, skipping build/vendor dirs and secrets), counts languages and lines, finds manifests,
entry points and the README, and reads one line of intent from every interesting file (module docstring, leading comment,
markdown heading, <title>, package.json description...). Results are cached by the folder's newest mtime, so asking again is free.
`render(report)` is the plain-text dossier the model (or the terminal) reads; the web UI gets the same report as a card.
"""
from __future__ import annotations
import ast, json, os, re, threading, time, warnings
from pathlib import Path
from .index import IGNORE_DIRS, LANG, NAME_LANG, SKIP_NAMES, is_secret

warnings.filterwarnings('ignore', category=SyntaxWarning)   # parsing other people's code for its docstring must never print their warnings
MAX_FILES = 30_000
MAX_READ = 400_000        # never read more than this from one file when counting lines / describing it
BUDGET = 6.0              # seconds a scan may take before it returns what it has
MANIFESTS = {'package.json': 'Node/JS', 'pyproject.toml': 'Python', 'requirements.txt': 'Python', 'setup.py': 'Python', 'Cargo.toml': 'Rust', 'go.mod': 'Go',
             'pubspec.yaml': 'Dart/Flutter', 'pom.xml': 'Java/Maven', 'build.gradle': 'Java/Gradle', 'Gemfile': 'Ruby', 'composer.json': 'PHP', 'CMakeLists.txt': 'C/C++ (CMake)',
             'Makefile': 'make', 'PKGBUILD': 'Arch package', 'Dockerfile': 'Docker', 'Containerfile': 'Docker', 'docker-compose.yml': 'Docker Compose', 'docker-compose.yaml': 'Docker Compose',
             'compose.yml': 'Docker Compose', 'compose.yaml': 'Docker Compose', 'flake.nix': 'Nix', 'deno.json': 'Deno', 'tsconfig.json': 'TypeScript', 'qmldir': 'QML module'}
ENTRY_NAMES = ['main.py', '__main__.py', 'app.py', 'server.py', 'cli.py', 'manage.py', 'index.js', 'main.js', 'server.js', 'app.js', 'index.ts', 'main.ts', 'main.go', 'main.rs', 'lib.rs',
               'main.c', 'main.cpp', 'Main.java', 'shell.qml', 'main.qml', 'index.html', 'init.lua', 'start.sh', 'run.sh', 'install.sh', 'kage.sh']
README_NAMES = ['README.md', 'readme.md', 'README.rst', 'README.txt', 'README', 'Readme.md']
DOC_EXT = {'.md', '.rst', '.txt'}
BINARY_EXT = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico', '.svg', '.avif', '.pdf', '.zip', '.gz', '.xz', '.bz2', '.7z', '.rar', '.tar', '.woff', '.woff2', '.ttf', '.otf',
              '.mp3', '.mp4', '.mkv', '.webm', '.wav', '.flac', '.ogg', '.so', '.o', '.a', '.pyc', '.exe', '.dll', '.bin', '.db', '.sqlite', '.sqlite3', '.class', '.jar'}
LANG_LABEL = {'python': 'Python', 'js': 'JavaScript', 'ts': 'TypeScript', 'qml': 'QML', 'lua': 'Lua', 'sh': 'Shell', 'go': 'Go', 'rust': 'Rust', 'c': 'C/C++', 'java': 'Java', 'kotlin': 'Kotlin',
              'ruby': 'Ruby', 'php': 'PHP', 'csharp': 'C#', 'md': 'Markdown', 'toml': 'TOML', 'json': 'JSON', 'yaml': 'YAML', 'conf': 'Config', 'css': 'CSS', 'html': 'HTML', 'nix': 'Nix',
              'text': 'Text', 'sql': 'SQL', 'docker': 'Docker', 'make': 'Make'}
CODE_LANGS = set(LANG_LABEL) - {'md', 'toml', 'json', 'yaml', 'conf', 'text'}
_cache: dict = {}
_lock = threading.Lock()

def lang_of(p: Path) -> str: return LANG.get(p.suffix.lower()) or NAME_LANG.get(p.name) or ''

def _read(p: Path, limit=MAX_READ) -> str | None:
    """Text of a file, or None when it is binary / unreadable / a secret."""
    if is_secret(p) or p.suffix.lower() in BINARY_EXT: return None
    try:
        with open(p, 'rb') as f: raw = f.read(limit)
    except OSError: return None
    if b'\0' in raw[:4096]: return None
    return raw.decode('utf-8', 'replace')

def _clean(s: str, n=140) -> str:
    s = re.sub(r'\s+', ' ', s or '').strip(' #*/-=_"\'`\t')
    return (s[:n - 1] + '…') if len(s) > n else s

# ------------------------------------------------------------------------------------------ one-line descriptions
def describe_text(p: Path, text: str) -> str:
    """One line saying what a file is for, from its own words. Cheap heuristics, no model."""
    name, ext = p.name, p.suffix.lower()
    try:
        if ext == '.py':
            try: tree = ast.parse(text)
            except (SyntaxError, ValueError): tree = None
            if tree:
                doc = ast.get_docstring(tree)
                if doc: return _clean(doc.strip().splitlines()[0])
                top = list(dict.fromkeys(n.name for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and not n.name.startswith('_')))
                if top: return 'defines ' + ', '.join(top[:5]) + (f' (+{len(top) - 5} more)' if len(top) > 5 else '')
        if name == 'package.json':
            d = json.loads(text); bits = [d.get('description') or '', ('scripts: ' + ', '.join(list(d.get('scripts', {}))[:5])) if d.get('scripts') else '']
            return _clean('; '.join(b for b in bits if b))
        if ext in ('.md', '.mdx') and text.startswith('---\n'):                     # front matter (skills, docs sites): its description is the best one-liner
            fm = re.match(r'---\n(.*?)\n---', text, re.S)
            if fm:
                kv = {k.strip().lower(): v.strip().strip('"\'') for k, sep, v in (l.partition(':') for l in fm.group(1).splitlines()) if sep}
                if kv.get('description') or kv.get('title'): return _clean(kv.get('description') or kv['title'])
        if ext in ('.md', '.rst') or name.upper().startswith('README'):
            h = re.search(r'^\s*#{1,3}\s+(.+)$', text, re.M)
            if h: return _clean(h.group(1))
            return _clean(next((l for l in text.splitlines() if l.strip() and not set(l.strip()) <= set('=-~#')), ''))
        if ext in ('.html', '.htm'):
            t = re.search(r'(?is)<title[^>]*>(.*?)</title>', text)
            if t: return 'page: ' + _clean(t.group(1))
        if ext == '.toml' or name in ('pyproject.toml', 'Cargo.toml'):
            m = re.search(r'(?m)^description\s*=\s*"([^"]+)"', text)
            if m: return _clean(m.group(1))
        if ext in ('.yml', '.yaml'):
            svc = re.findall(r'^  ([\w.-]+):\s*$', text, re.M)
            if 'services:' in text and svc: return 'services: ' + ', '.join(svc[:8])
        if name in ('Dockerfile', 'Containerfile'):
            m = re.search(r'(?im)^from\s+(\S+)', text)
            return f'container image based on {m.group(1)}' if m else 'container build'
        if name == 'Makefile':
            tg = re.findall(r'(?m)^([A-Za-z][\w.-]*):(?!=)', text)
            return 'targets: ' + ', '.join(tg[:8]) if tg else 'make rules'
        # leading comment block in c-like, shell, lua, css, qml...
        lines = [l for l in text.splitlines()[:25]]
        if lines and lines[0].startswith('#!'): lines = lines[1:]
        buf = []
        for l in lines:
            s = l.strip()
            if not s:
                if buf: break
                continue
            m = re.match(r'^(?://+|#+|--+|;+|\*+|/\*+|<!--)\s*(.*?)(?:\*/|-->)?\s*$', s)
            if m is None: break
            if m.group(1).strip(' -=*#/'): buf.append(m.group(1))
            if len(' '.join(buf)) > 120: break
        if buf: return _clean(' '.join(buf))
        sym = [x for x in re.findall(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(?:function\*?|class|func|fn|def)\s+([A-Za-z_$][\w$]*)', text, re.M) if len(x) > 2]
        if sym: return 'defines ' + ', '.join(list(dict.fromkeys(sym))[:6]) + (f' (+{len(set(sym)) - 6} more)' if len(set(sym)) > 6 else '')
    except (ValueError, TypeError, KeyError, AttributeError): pass
    return ''

NAME_HINTS = {'__main__.py': 'package entry point (python -m)', '__init__.py': 'package marker', 'install.sh': 'install script', 'start.sh': 'start script', 'stop.sh': 'stop script',
              'run.sh': 'run script', 'build.sh': 'build script', 'test.sh': 'test runner script', 'deploy.sh': 'deploy script', 'setup.py': 'Python packaging script',
              '.gitignore': 'git ignore rules', 'LICENSE': 'license', 'LICENSE.md': 'license', 'CHANGELOG.md': 'change log', '.env.example': 'example environment settings',
              'conftest.py': 'pytest fixtures', 'favicon.svg': 'site icon', 'style.css': 'stylesheet', 'requirements.txt': 'Python dependencies'}
def _kind(p: Path) -> str:
    n, e = p.name, p.suffix.lower()
    if n in NAME_HINTS: return NAME_HINTS[n]
    if e == '.sh': return 'shell script'
    if n in MANIFESTS: return MANIFESTS[n] + ' manifest'
    if e in BINARY_EXT: return {'.png': 'image', '.jpg': 'image', '.jpeg': 'image', '.gif': 'image', '.webp': 'image', '.svg': 'vector image', '.pdf': 'PDF', '.ico': 'icon'}.get(e, 'binary file')
    return {'': 'file'}.get(e, (LANG_LABEL.get(LANG.get(e, ''), e.lstrip('.').upper() + ' file')) + ' file')

def describe_file(p: Path) -> dict:
    """{'name','size','lines','desc'} for one file. Never raises."""
    try: size = p.stat().st_size
    except OSError: return {'name': p.name, 'size': 0, 'lines': 0, 'desc': 'unreadable'}
    text = _read(p, MAX_READ)          # whole file for anything normal, so a docstring parse never sees truncated source
    if text is None: return {'name': p.name, 'size': size, 'lines': 0, 'desc': 'secret, not read' if is_secret(p) else _kind(p)}
    lines = text.count('\n') + (0 if text.endswith('\n') or not text else 1)
    if size > MAX_READ: lines = int(lines * size / max(1, len(text.encode())))
    return {'name': p.name, 'size': size, 'lines': lines, 'desc': describe_text(p, text) or _kind(p)}

# ------------------------------------------------------------------------------------------ the scan
def _git(root: Path) -> dict:
    out = {}
    if not (root / '.git').exists(): return out
    try:
        from . import shell
        c, o = shell.execute(['git', '-C', str(root), 'log', '-1', '--format=%h %s (%cr)'], timeout=4)
        if c == 0 and o.strip(): out['last_commit'] = o.strip()[:140]
        c, o = shell.execute(['git', '-C', str(root), 'rev-parse', '--abbrev-ref', 'HEAD'], timeout=4)
        if c == 0 and o.strip(): out['branch'] = o.strip()
        c, o = shell.execute(['git', '-C', str(root), 'status', '--porcelain'], timeout=6)
        if c == 0: out['dirty'] = len([l for l in o.splitlines() if l.strip()])
    except Exception: pass
    return out

def scan(root, force=False, budget=BUDGET) -> dict:
    root = Path(root).resolve(); t0 = time.time()
    files, dirs_seen, newest, truncated = [], {}, 0.0, False
    for base, dirs, names in os.walk(root, onerror=lambda e: None, followlinks=False):
        b = Path(base); rel_dir = b.relative_to(root)
        dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.git') and not os.path.islink(os.path.join(base, d)) and d not in ('.ssh', '.gnupg'))
        for n in sorted(names):
            if n in SKIP_NAMES: continue
            p = b / n
            try: st = p.stat()
            except OSError: continue
            if not (p.is_file()): continue
            files.append((str(rel_dir / n) if str(rel_dir) != '.' else n, st.st_size, st.st_mtime)); newest = max(newest, st.st_mtime)
        if len(files) >= MAX_FILES or time.time() - t0 > budget: truncated = True; break
    key = (str(root), len(files), round(newest, 3))
    with _lock:
        hit = _cache.get(str(root))
        if hit and hit['key'] == key and not force: return {**hit['report'], 'cached': True}
    langs, total_lines, by_dir, secrets, hidden_notes = {}, 0, {}, 0, []
    manifests, entries, readme, descs, big = [], [], None, {}, []
    for rel, size, mtime in files:
        p = root / rel; top = rel.split('/')[0] if '/' in rel else '.'
        d = by_dir.setdefault(top, {'files': 0, 'lines': 0, 'langs': {}, 'size': 0}); d['files'] += 1; d['size'] += size
        if is_secret(p): secrets += 1; continue
        lg = lang_of(p)
        if p.name in MANIFESTS and p.name != 'qmldir': manifests.append(rel)
        if p.name in ENTRY_NAMES: entries.append(rel)
        if readme is None and p.name in README_NAMES and '/' not in rel: readme = rel
        if p.suffix.lower() in BINARY_EXT or size > MAX_READ * 3: continue
        if time.time() - t0 > budget * 1.6: truncated = True; break
        text = _read(p)
        if text is None: continue
        n_lines = text.count('\n') + (0 if text.endswith('\n') or not text else 1)
        if lg:
            L = langs.setdefault(lg, {'files': 0, 'lines': 0}); L['files'] += 1; L['lines'] += n_lines; total_lines += n_lines
            d['lines'] += n_lines; d['langs'][lg] = d['langs'].get(lg, 0) + n_lines
        if (lg in CODE_LANGS or p.name in MANIFESTS or p.suffix.lower() in DOC_EXT) and len(descs) < 400: descs[rel] = {'desc': describe_text(p, text) or _kind(p), 'lines': n_lines, 'size': size}
        if n_lines > 600 and lg in CODE_LANGS: big.append((n_lines, rel))
    ranked = sorted(langs.items(), key=lambda kv: -kv[1]['lines'])
    prim = [LANG_LABEL.get(k, k) for k, v in ranked if k in CODE_LANGS][:3]
    rd_text = _read(root / readme, 6000) if readme else ''
    intro = ''
    if rd_text:
        para = [l.strip() for l in re.sub(r'(?s)```.*?```|<!--.*?-->', '', rd_text).split('\n\n')]
        intro = next((_clean(re.sub(r'[*_`]{1,3}', '', re.sub(r'!?\[([^\]]*)\]\([^)]*\)', r'\1', x)), 320) for x in para if len(x) > 40 and not x.startswith(('#', '|', '<', '[!', '```', '-', '*', '=')) and not set(x) <= set('-=~#* ')), '')
    kinds = sorted({MANIFESTS[Path(m).name] for m in manifests if Path(m).name in MANIFESTS and not m.endswith('qmldir')})
    tops = []
    for name, d in sorted(by_dir.items(), key=lambda kv: (kv[0] == '.', -kv[1]['lines'], kv[0])):
        if name == '.': continue
        sub = root / name; d_desc = ''
        for r in README_NAMES:
            if (sub / r).is_file(): d_desc = describe_text(sub / r, _read(sub / r, 8000) or ''); break
        if not d_desc:
            init = next(((sub / f) for f in ('__init__.py', 'index.js', 'index.ts', 'mod.rs', 'lib.rs') if (sub / f).is_file()), None)
            if init: d_desc = describe_text(init, _read(init, 8000) or '')
        top_lang = max(d['langs'].items(), key=lambda kv: kv[1])[0] if d['langs'] else ''
        tops.append({'name': name, 'files': d['files'], 'lines': d['lines'], 'lang': LANG_LABEL.get(top_lang, ''), 'desc': d_desc or _guess_dir(name)})
    # most informative files: entry points, manifests, README, then the biggest code files
    key_files = []
    for rel in ([readme] if readme else []) + manifests[:6] + entries[:6] + [r for _, r in sorted(big, reverse=True)[:6]]:
        if rel and rel in descs and rel not in [k['path'] for k in key_files]: key_files.append({'path': rel, 'lines': descs[rel]['lines'], 'desc': descs[rel]['desc']})
    for rel, d in sorted(descs.items(), key=lambda kv: -kv[1]['lines']):
        if len(key_files) >= 12: break
        if rel not in [k['path'] for k in key_files] and d['desc'] and Path(rel).suffix.lower() in {k for k, v in LANG.items() if v in CODE_LANGS}: key_files.append({'path': rel, 'lines': d['lines'], 'desc': d['desc']})
    report = {'root': str(root), 'name': root.name, 'files': len(files), 'lines': total_lines, 'secrets_skipped': secrets, 'truncated': truncated, 'ms': int((time.time() - t0) * 1000),
              'langs': [{'name': LANG_LABEL.get(k, k), 'files': v['files'], 'lines': v['lines']} for k, v in ranked[:8]], 'primary': prim, 'stack': kinds,
              'manifests': manifests[:12], 'entries': entries[:10], 'readme': readme, 'intro': intro, 'dirs': tops[:14], 'key_files': key_files, 'git': _git(root), 'descs': descs}
    with _lock:
        _cache[str(root)] = {'key': key, 'report': report}
        while len(_cache) > 8: _cache.pop(next(iter(_cache)))
    return {**report, 'cached': False}

DIR_HINTS = {'src': 'source code', 'lib': 'library code', 'tests': 'tests', 'test': 'tests', 'docs': 'documentation', 'doc': 'documentation', 'scripts': 'helper scripts', 'bin': 'executables / launchers',
             'assets': 'static assets', 'static': 'static assets', 'public': 'public web files', 'ui': 'user interface', 'web': 'web frontend', 'config': 'configuration', 'conf': 'configuration',
             'examples': 'examples', 'skills': 'skill playbooks', 'templates': 'templates', 'migrations': 'database migrations', 'cmd': 'command entry points', 'pkg': 'packages', 'internal': 'internal packages',
             'components': 'UI components', 'models': 'data models', 'utils': 'helpers', 'vendor': 'bundled third-party code', 'searxng': 'search engine config'}
def _guess_dir(name: str) -> str: return DIR_HINTS.get(name.lower(), '')

# ------------------------------------------------------------------------------------------ rendering
def _bar(n, total, w=18) -> str:
    k = round(w * n / total) if total else 0
    return '█' * k + '░' * (w - k)

def render(r: dict, detail=True) -> str:
    """Plain-text dossier. This is what the model reads to write the prose description, and what the terminal prints."""
    out = [f"PROJECT {r['name']}  ({r['root']})", f"{r['files']:,} files, {r['lines']:,} lines of code/text, scanned in {r['ms']} ms" + (' (cached)' if r.get('cached') else '') +
           (' [partial: hit the scan limit]' if r['truncated'] else '')]
    if r['primary'] or r['stack']: out.append('Stack: ' + ', '.join(dict.fromkeys(r['primary'] + r['stack'])))
    if r['intro']: out.append('README says: ' + r['intro'])
    g = r.get('git') or {}
    if g: out.append('Git: ' + ', '.join(x for x in (f"branch {g['branch']}" if g.get('branch') else '', f"{g['dirty']} uncommitted change(s)" if g.get('dirty') else 'clean' if 'dirty' in g else '', f"last: {g['last_commit']}" if g.get('last_commit') else '') if x))
    tl = sum(l['lines'] for l in r['langs']) or 1
    if r['langs']: out.append('\nLanguages:\n' + '\n'.join(f"  {l['name']:<11}{_bar(l['lines'], tl)} {l['lines']:>7,} lines, {l['files']} files" for l in r['langs'][:6]))
    if r['dirs']: out.append('\nTop-level folders:\n' + '\n'.join(f"  {d['name'] + '/':<16}{d['files']:>5} files {d['lines']:>7,} lines  {d['lang']:<10} {d['desc']}".rstrip() for d in r['dirs']))
    if r['entries'] or r['manifests']: out.append('\nEntry points: ' + (', '.join(r['entries']) or 'none found') + '\nManifests: ' + (', '.join(r['manifests']) or 'none'))
    if detail and r['key_files']: out.append('\nKey files:\n' + '\n'.join(f"  {k['path']} ({k['lines']} lines): {k['desc'] or '-'}" for k in r['key_files']))
    if r['secrets_skipped']: out.append(f"\n({r['secrets_skipped']} secret-looking file(s) were skipped and never read.)")
    return '\n'.join(out)

def card(r: dict) -> dict:
    """The compact JSON the web UI draws as a project card."""
    return {'name': r['name'], 'root': r['root'], 'files': r['files'], 'lines': r['lines'], 'ms': r['ms'], 'primary': r['primary'], 'stack': r['stack'], 'intro': r['intro'], 'git': r.get('git') or {},
            'langs': r['langs'][:6], 'dirs': r['dirs'][:10], 'key_files': r['key_files'][:10], 'entries': r['entries'][:6], 'truncated': r['truncated']}

def list_dir(root: Path, base: Path, pattern='', recursive=False, limit=40) -> tuple[list, int]:
    """Per-file one-liners for a folder. -> (rows, total). Rows: {'path','kind','size','lines','desc'} ('dir' rows for subfolders when not recursive)."""
    rows, pat = [], pattern.lower().strip(); glob = bool(re.search(r'[*?\[]', pat))
    import fnmatch
    def ok(n): return not pat or (fnmatch.fnmatch(n.lower(), pat) if glob else pat in n.lower())
    if not recursive:
        try: items = sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError: return [], 0
        for x in items:
            if x.name in IGNORE_DIRS or x.name.startswith('.git') or not ok(x.name): continue
            rel = os.path.relpath(x, root)
            if x.is_dir():
                try: n = sum(1 for _ in x.iterdir())
                except OSError: n = 0
                rows.append({'path': rel + '/', 'kind': 'dir', 'size': 0, 'lines': 0, 'desc': _guess_dir(x.name) + (f' ({n} items)' if n else ' (empty)')})
            elif x.is_file(): d = describe_file(x); rows.append({'path': rel, 'kind': 'file', 'size': d['size'], 'lines': d['lines'], 'desc': d['desc']})
    else:
        t0 = time.time()
        for b, dirs, names in os.walk(base, onerror=lambda e: None):
            dirs[:] = sorted(d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.git'))
            for n in sorted(names):
                if n in SKIP_NAMES or not ok(n): continue
                p = Path(b) / n; d = describe_file(p); rows.append({'path': os.path.relpath(p, root), 'kind': 'file', 'size': d['size'], 'lines': d['lines'], 'desc': d['desc']})
                if len(rows) >= 2000 or time.time() - t0 > BUDGET: break
            if len(rows) >= 2000 or time.time() - t0 > BUDGET: break
    return rows[:limit], len(rows)
