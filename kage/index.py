"""Code intelligence index (the Prowl idea, in the standard library).

One SQLite file per workspace holds files, symbols, a dependency graph and a
full-text index of the code. Every query first refreshes only the files whose
mtime/size changed, so answers are always current and cited as path:line.
"""
from __future__ import annotations
import ast, hashlib, os, re, sqlite3, subprocess, threading, time
from collections import defaultdict, deque
from pathlib import Path
from . import config

IGNORE_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', '.cache', 'target', 'dist', 'build', '.next', '.mypy_cache',
               '.pytest_cache', '.idea', '.vscode', '.prowl', '.kage', 'vendor', '.tox', '.gradle', 'site-packages', '.svn', '.hg'}
SKIP_NAMES = {'package-lock.json', 'yarn.lock', 'pnpm-lock.yaml', 'Cargo.lock', 'poetry.lock', 'uv.lock', 'go.sum'}
LANG = {'.py': 'python', '.js': 'js', '.mjs': 'js', '.cjs': 'js', '.jsx': 'js', '.ts': 'ts', '.tsx': 'ts', '.qml': 'qml', '.lua': 'lua',
        '.sh': 'sh', '.bash': 'sh', '.zsh': 'sh', '.fish': 'sh', '.go': 'go', '.rs': 'rust', '.c': 'c', '.h': 'c', '.cpp': 'c', '.cc': 'c',
        '.hpp': 'c', '.java': 'java', '.kt': 'kotlin', '.rb': 'ruby', '.php': 'php', '.cs': 'csharp', '.md': 'md', '.toml': 'toml',
        '.json': 'json', '.jsonc': 'json', '.yml': 'yaml', '.yaml': 'yaml', '.conf': 'conf', '.ini': 'conf', '.css': 'css', '.html': 'html',
        '.nix': 'nix', '.txt': 'text', '.service': 'conf', '.desktop': 'conf', '.sql': 'sql', '.vue': 'js', '.svelte': 'js'}
NAME_LANG = {'qmldir': 'conf', 'Dockerfile': 'docker', 'Containerfile': 'docker', 'Makefile': 'make', 'PKGBUILD': 'sh'}
MAX_SIZE = 600_000
MAX_FILES = 30_000
CHUNK = 40
STOP = set('the a an of to in is it how what where does do which who why when and or for with this that these those are was be by on at from as into about code file files function functions find show me my i you can please tell'.split())
KW_NOT_FUNC = {'if', 'for', 'while', 'switch', 'catch', 'return', 'function', 'else', 'with', 'sizeof', 'typeof', 'new', 'await'}

def is_secret(p: Path) -> bool:
    n = p.name.lower()
    return (n == '.env' or (n.startswith('.env.') and not n.endswith(('.example', '.sample', '.template'))) or p.suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.kdbx'}
            or n.startswith(('id_rsa', 'id_ed25519', 'id_ecdsa')) or n in {'credentials', 'credentials.json', '.netrc', '.pgpass'} or '.ssh' in p.parts or '.gnupg' in p.parts)

def split_ident(s: str) -> list:
    s = re.sub(r'([a-z0-9])([A-Z])', r'\1 \2', s)
    return [w.lower() for w in re.split(r'[_\W]+', s) if len(w) > 1]

# ---------------------------------------------------------------- symbol extraction
_R = lambda p: re.compile(p)
PATTERNS = {
    'js': [(_R(r'^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?function\*?\s+([A-Za-z_$][\w$]*)\s*\('), 'function'),
           (_R(r'^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)'), 'class'),
           (_R(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>'), 'function'),
           (_R(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?function'), 'function'),
           (_R(r'^\s{2,}(?:static\s+)?(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{\s*$'), 'method')],
    'qml': [(_R(r'^\s*function\s+([A-Za-z_]\w*)\s*\('), 'function'), (_R(r'^\s*signal\s+([A-Za-z_]\w*)'), 'signal'),
            (_R(r'^\s*(?:readonly\s+|required\s+|default\s+)*property\s+[\w.<>]+\s+([A-Za-z_]\w*)'), 'property'),
            (_R(r'^\s*component\s+([A-Z]\w*)\s*:'), 'component'), (_R(r'^\s*id:\s*([A-Za-z_]\w*)'), 'id'),
            (_R(r'^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:\([^)]*\)|\w+)\s*=>'), 'function')],
    'lua': [(_R(r'^\s*(?:local\s+)?function\s+([\w.:]+)\s*\('), 'function'), (_R(r'^\s*(?:local\s+)?([\w.]+)\s*=\s*function\s*\('), 'function')],
    'sh': [(_R(r'^\s*function\s+([\w:.-]+)'), 'function'), (_R(r'^\s*([\w:.-]+)\s*\(\)\s*\{?'), 'function')],
    'go': [(_R(r'^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)'), 'function'), (_R(r'^type\s+([A-Za-z_]\w*)\s+(?:struct|interface)'), 'type')],
    'rust': [(_R(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+([A-Za-z_]\w*)'), 'function'),
             (_R(r'^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait|mod|type)\s+([A-Za-z_]\w*)'), 'type'), (_R(r'^\s*impl(?:<[^>]*>)?\s+(?:[\w:<>]+\s+for\s+)?([A-Za-z_]\w*)'), 'impl')],
    'c': [(_R(r'^(?:typedef\s+)?(?:struct|enum|union)\s+([A-Za-z_]\w*)'), 'type'), (_R(r'^#define\s+([A-Za-z_]\w*)'), 'macro'),
          (_R(r'^[A-Za-z_][\w\s\*:<>,&]*?[\s\*&]([A-Za-z_~]\w*)\s*\([^;]*$'), 'function')],
    'java': [(_R(r'^\s*(?:public|private|protected|static|final|abstract|\s)*(?:class|interface|enum)\s+([A-Za-z_]\w*)'), 'class'),
             (_R(r'^\s{2,}(?:public|private|protected|static|final|synchronized|\s)+[\w<>\[\],\s]+\s+([A-Za-z_]\w*)\s*\([^;]*$'), 'method')],
    'md': [(_R(r'^(#{1,4})\s+(.+?)\s*#*\s*$'), 'heading')],
    'toml': [(_R(r'^\[+([\w.\-"]+)\]+\s*$'), 'section')],
    'docker': [(_R(r'^(?i:from)\s+\S+\s+(?i:as)\s+(\S+)'), 'stage')],
    'make': [(_R(r'^([A-Za-z_][\w.-]*)\s*:(?!=)'), 'target')],
}
PATTERNS['ts'] = PATTERNS['js'] + [(_R(r'^\s*(?:export\s+)?(?:interface|type|enum)\s+([A-Za-z_$][\w$]*)'), 'type')]
PATTERNS['kotlin'] = [(_R(r'^\s*(?:\w+\s+)*(?:fun)\s+(?:<[^>]*>\s*)?(?:[\w.]+\.)?([A-Za-z_]\w*)'), 'function'), (_R(r'^\s*(?:\w+\s+)*(?:class|object|interface)\s+([A-Za-z_]\w*)'), 'class')]
PATTERNS['ruby'] = [(_R(r'^\s*def\s+(?:self\.)?([A-Za-z_]\w*[?!]?)'), 'function'), (_R(r'^\s*(?:class|module)\s+([A-Z][\w:]*)'), 'class')]
PATTERNS['php'] = [(_R(r'^\s*(?:public|private|protected|static|\s)*function\s+([A-Za-z_]\w*)'), 'function'), (_R(r'^\s*(?:abstract\s+)?class\s+([A-Za-z_]\w*)'), 'class')]
PATTERNS['csharp'] = PATTERNS['java']

def _py_symbols(src: str):
    try: tree = ast.parse(src)
    except (SyntaxError, ValueError): return None
    out = []
    def visit(body, parent):
        for n in body:
            if isinstance(n, ast.ClassDef):
                out.append((n.name, 'class', n.lineno, f'class {n.name}', parent, getattr(n, 'end_lineno', n.lineno))); visit(n.body, n.name)
            elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                try: sig = ast.get_source_segment(src, n).split('\n', 1)[0].strip()
                except Exception: sig = f'def {n.name}(...)'
                out.append((n.name, 'method' if parent else 'function', n.lineno, sig[:160], parent, getattr(n, 'end_lineno', n.lineno)))
            elif isinstance(n, ast.Assign) and parent is None:
                for t in n.targets:
                    if isinstance(t, ast.Name) and t.id.isupper() and len(t.id) > 2: out.append((t.id, 'const', n.lineno, f'{t.id} = ...', None, n.lineno))
    visit(tree.body, None)
    return out

def _brace_end(lines, i, cap=600):
    depth, seen = 0, False
    for j in range(i, min(len(lines), i + cap)):
        s = re.sub(r'"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|//.*$', '', lines[j])
        for ch in s:
            if ch == '{': depth += 1; seen = True
            elif ch == '}': depth -= 1
        if seen and depth <= 0: return j + 1
        if not seen and j > i + 6: return None
    return None

def _indent_end(lines, i):
    ind = len(lines[i]) - len(lines[i].lstrip())
    for j in range(i + 1, min(len(lines), i + 600)):
        s = lines[j]
        if s.strip() and len(s) - len(s.lstrip()) <= ind and re.match(r'\s*end\b', s): return j + 1
    return None

def extract_symbols(lang: str, src: str, rel: str):
    lines = src.split('\n'); out = []
    if lang == 'python':
        r = _py_symbols(src)
        if r is not None: return r
        lang = 'python-fallback'
    if lang == 'python-fallback':
        pats = [(_R(r'^\s*(?:async\s+)?def\s+(\w+)'), 'function'), (_R(r'^\s*class\s+(\w+)'), 'class')]
    else:
        pats = PATTERNS.get(lang, [])
    if lang == 'qml':
        for i, l in enumerate(lines[:60]):
            m = re.match(r'^\s*([A-Z]\w*)\s*\{', l)
            if m: out.append((Path(rel).stem, 'component', i + 1, f'{m.group(1)} {{ … }}', None, len(lines))); break
    for i, line in enumerate(lines):
        if len(line) > 400: continue
        for rx, kind in pats:
            m = rx.match(line)
            if not m: continue
            name = m.group(m.lastindex) if lang == 'md' else (m.group(1) or '')
            if lang == 'md': name = m.group(2)
            if not name or name in KW_NOT_FUNC: continue
            end = None
            if lang == 'md':
                lvl = len(m.group(1)); end = len(lines)
                for j in range(i + 1, len(lines)):
                    mm = re.match(r'^(#{1,4})\s', lines[j])
                    if mm and len(mm.group(1)) <= lvl: end = j; break
            elif lang in ('lua',): end = _indent_end(lines, i)
            elif lang in ('js', 'ts', 'qml', 'go', 'rust', 'c', 'java', 'sh', 'kotlin', 'php', 'csharp') and kind not in ('property', 'signal', 'id', 'macro'): end = _brace_end(lines, i)
            if kind == 'function' and lang == 'c' and not (line.rstrip().endswith('{') or (i + 1 < len(lines) and lines[i + 1].strip().startswith('{')) or not line.rstrip().endswith(';')): continue
            out.append((name, kind, i + 1, line.strip()[:160], None, end or i + 1))
            break
    return out

# ---------------------------------------------------------------- dependency edges
RX_PY_IMPORT = re.compile(r'^\s*(?:from\s+([.\w]+)\s+import\s+([\w, *()]+)|import\s+([\w., ]+))', re.M)
RX_JS = re.compile(r'''(?:import\s+(?:[^'"]*?\s+from\s+)?|require\(\s*|import\(\s*|export\s+[^'"]*?\s+from\s+)['"]([^'"]+)['"]''')
RX_QML_IMPORT = re.compile(r'''^\s*import\s+"([^"]+)"''', re.M)
RX_QML_TYPE = re.compile(r'\b([A-Z][A-Za-z0-9_]+)\s*\{')
RX_QML_SINGLETON = re.compile(r'\b([A-Z][A-Za-z0-9_]+)\.[a-z_]')
RX_LUA = re.compile(r'''(?:require|dofile|loadfile)\s*\(?\s*['"]([^'"]+)['"]''')
RX_SH = re.compile(r'^\s*(?:source|\.)\s+["\']?([^\s"\';&|]+)', re.M)
RX_INC = re.compile(r'^\s*#\s*include\s+"([^"]+)"', re.M)
RX_CONF_SOURCE = re.compile(r'^\s*source\s*=\s*([^\s#]+)', re.M)
RX_HTML = re.compile(r'''(?:src|href)\s*=\s*["']([^"':]+\.[a-z]{1,5})["']''', re.I)
RX_CSS = re.compile(r'''@import\s+(?:url\()?["']?([^"')\s;]+)''')
RX_RUST_MOD = re.compile(r'^\s*(?:pub\s+)?mod\s+(\w+)\s*;', re.M)
RX_MENTION = re.compile(r'(?<![\w/.-])((?:[\w.-]+/)*[\w.-]+\.(?:py|js|mjs|ts|tsx|jsx|qml|lua|sh|fish|conf|json|toml|yml|yaml|css|html|service|md|rs|go|c|h))\b')

def extract_edges(lang: str, src: str, rel: str):
    """-> [(kind, raw, line)] ; kind in import/include/source/require/qml-type/mentions"""
    out = []
    def add(rx, kind, text=src, grp=1):
        for m in rx.finditer(text):
            out.append((kind, m.group(grp), text.count('\n', 0, m.start()) + 1))
    if lang == 'python':
        for m in RX_PY_IMPORT.finditer(src):
            ln = src.count('\n', 0, m.start()) + 1
            if m.group(1):
                mod = m.group(1); out.append(('import', mod, ln))
                for nm in re.split(r'[,\s()]+', m.group(2) or ''):
                    if nm and nm != '*' and nm != 'as': out.append(('import', (mod + nm) if mod.endswith('.') else f'{mod}.{nm}', ln))
            else:
                for nm in re.split(r'\s*,\s*', m.group(3)): out.append(('import', nm.split(' as ')[0].strip(), ln))
    elif lang in ('js', 'ts'): add(RX_JS, 'import')
    elif lang == 'qml':
        add(RX_QML_IMPORT, 'import'); add(RX_JS, 'import')
        seen_t = set()
        for rx in (RX_QML_TYPE, RX_QML_SINGLETON):
            for m in rx.finditer(src):
                if m.group(1) not in seen_t: seen_t.add(m.group(1)); out.append(('qml-type', m.group(1), src.count('\n', 0, m.start()) + 1))
    elif lang == 'lua':
        for m in RX_LUA.finditer(src):
            if not m.group(1).endswith('.'): out.append(('require', m.group(1), src.count('\n', 0, m.start()) + 1))
    elif lang == 'sh': add(RX_SH, 'source')
    elif lang == 'c': add(RX_INC, 'include')
    elif lang == 'conf': add(RX_CONF_SOURCE, 'source')
    elif lang == 'html': add(RX_HTML, 'import')
    elif lang == 'css': add(RX_CSS, 'import')
    elif lang == 'rust': add(RX_RUST_MOD, 'import')
    if lang in ('sh', 'conf', 'lua', 'json', 'toml', 'yaml', 'md', 'python', 'js', 'ts', 'qml', 'html', 'make', 'docker'):
        seen = set()
        for m in RX_MENTION.finditer(src[:200_000]):
            raw = m.group(1)
            if raw in seen or len(raw) < 5 or raw.startswith(('http', 'www')): continue
            seen.add(raw); out.append(('mentions', raw, src.count('\n', 0, m.start()) + 1))
            if len(seen) > 80: break
    return out[:600]

SCHEMA = """
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, path TEXT UNIQUE, mtime INTEGER, size INTEGER, lang TEXT, lines INTEGER);
CREATE TABLE IF NOT EXISTS symbols(id INTEGER PRIMARY KEY, fid INTEGER, name TEXT, lname TEXT, kind TEXT, line INTEGER, eline INTEGER, sig TEXT, parent TEXT);
CREATE INDEX IF NOT EXISTS sym_name ON symbols(lname); CREATE INDEX IF NOT EXISTS sym_fid ON symbols(fid);
CREATE TABLE IF NOT EXISTS edges(id INTEGER PRIMARY KEY, src INTEGER, kind TEXT, raw TEXT, line INTEGER, dst INTEGER);
CREATE INDEX IF NOT EXISTS edge_src ON edges(src); CREATE INDEX IF NOT EXISTS edge_dst ON edges(dst);
CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(text, idents, path UNINDEXED, fid UNINDEXED, lstart UNINDEXED, lend UNINDEXED, tokenize="unicode61 tokenchars '_'");
"""

class Index:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        config.INDEX_DIR.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha1(str(self.root).encode()).hexdigest()[:12]
        self.db_path = config.INDEX_DIR / f'{self.root.name[:24]}-{key}.db'
        self.lock = threading.RLock(); self.last = 0.0; self.last_ms = 0
        with self._db() as db: db.executescript(SCHEMA)

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=30)
        db.execute('PRAGMA journal_mode=WAL'); db.execute('PRAGMA synchronous=OFF')
        return db

    # ---------------------------------------------------------- scanning
    def _scan(self):
        files = []
        try:
            inside = subprocess.run(['git', '-C', str(self.root), 'rev-parse', '--is-inside-work-tree'], capture_output=True, text=True, timeout=5)
            if inside.stdout.strip() == 'true':
                r = subprocess.run(['git', '-C', str(self.root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'], capture_output=True, timeout=20)
                names = [n for n in r.stdout.decode('utf-8', 'replace').split('\0') if n]
                files = [n for n in names if not (set(Path(n).parts[:-1]) & IGNORE_DIRS)]
            else: raise OSError
        except (OSError, subprocess.SubprocessError):
            for base, dirs, names in os.walk(self.root):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not os.path.islink(os.path.join(base, d))]
                for n in names:
                    files.append(os.path.relpath(os.path.join(base, n), self.root))
                    if len(files) > MAX_FILES: break
                if len(files) > MAX_FILES: break
        out = []
        for rel in files[:MAX_FILES]:
            p = Path(rel)
            lang = LANG.get(p.suffix.lower()) or NAME_LANG.get(p.name)
            if p.name in SKIP_NAMES: continue
            full = self.root / rel
            try: st = full.lstat()
            except OSError: continue
            if not (st.st_mode & 0o170000 == 0o100000): continue
            if st.st_size > MAX_SIZE or st.st_size == 0 or is_secret(p): continue
            if lang is None:
                if p.suffix == '' and st.st_mode & 0o111:
                    try:
                        with open(full, 'rb') as f: head = f.read(80)
                    except OSError: continue
                    if head.startswith(b'#!'):
                        lang = 'python' if b'python' in head else ('sh' if re.search(rb'sh|bash|fish|zsh', head) else None)
                if lang is None: continue
            out.append((rel, st.st_mtime_ns, st.st_size, lang))
        return out

    def refresh(self, force=False) -> dict:
        with self.lock:
            if not force and time.time() - self.last < 2.0: return {'changed': 0}
            t0 = time.time(); scanned = {r[0]: r for r in self._scan()}
            db = self._db()
            try:
                have = {p: (i, m, s) for i, p, m, s in db.execute('SELECT id,path,mtime,size FROM files')}
                gone = [p for p in have if p not in scanned]
                todo = [r for p, r in scanned.items() if p not in have or have[p][1] != r[1] or have[p][2] != r[2]]
                for p in gone: self._drop(db, have[p][0])
                for rel, mtime, size, lang in todo:
                    if rel in have: self._drop(db, have[rel][0])
                    self._index_file(db, rel, mtime, size, lang)
                if todo or gone or force:
                    self._resolve(db)
                db.execute("INSERT OR REPLACE INTO meta VALUES('indexed_at',?)", (str(time.time()),))
                db.commit()
            finally: db.close()
            self.last = time.time(); self.last_ms = int((self.last - t0) * 1000)
            return {'changed': len(todo), 'removed': len(gone), 'ms': self.last_ms}

    @staticmethod
    def _drop(db, fid):
        for t, c in (('symbols', 'fid'), ('edges', 'src'), ('chunks', 'fid'), ('files', 'id')): db.execute(f'DELETE FROM {t} WHERE {c}=?', (fid,))

    def _index_file(self, db, rel, mtime, size, lang):
        try: data = (self.root / rel).read_bytes()
        except OSError: return
        if b'\0' in data[:4096]: return
        src = data.decode('utf-8', 'replace'); lines = src.split('\n')
        cur = db.execute('INSERT INTO files(path,mtime,size,lang,lines) VALUES(?,?,?,?,?)', (rel, mtime, size, lang, len(lines)))
        fid = cur.lastrowid
        try: syms = extract_symbols(lang, src, rel)
        except Exception: syms = []
        db.executemany('INSERT INTO symbols(fid,name,lname,kind,line,eline,sig,parent) VALUES(?,?,?,?,?,?,?,?)',
                       [(fid, n, n.lower(), k, ln, el, sg, pa) for n, k, ln, sg, pa, el in syms])
        try: edges = extract_edges(lang, src, rel)
        except Exception: edges = []
        db.executemany('INSERT INTO edges(src,kind,raw,line) VALUES(?,?,?,?)', [(fid, k, r, ln) for k, r, ln in edges])
        rows = []
        for s in range(0, len(lines), CHUNK):
            block = lines[s:s + CHUNK]; text = '\n'.join(block)
            if not text.strip(): continue
            idents = ' '.join(sorted({w for t in set(re.findall(r'[A-Za-z_]\w{2,}', text)) for w in split_ident(t)}))
            rows.append((text[:8000], idents[:3000], rel, fid, s + 1, s + len(block)))
        db.executemany('INSERT INTO chunks(text,idents,path,fid,lstart,lend) VALUES(?,?,?,?,?,?)', rows)

    # ---------------------------------------------------------- edge resolution
    def _resolve(self, db):
        paths = {p: i for i, p in db.execute('SELECT id,path FROM files')}
        by_base = defaultdict(list)
        for p, i in paths.items(): by_base[Path(p).name].append(i)
        qml_cands = defaultdict(list)
        for p, i in paths.items():
            if p.endswith('.qml'): qml_cands[Path(p).stem].append((p, i))
        def nearest(stem, spath):
            c = qml_cands.get(stem, [])
            if len(c) == 1: return c[0][1]
            if not c: return None
            sp = Path(spath).parts[:-1]
            def score(p):
                n = 0
                for a, b in zip(sp, Path(p).parts[:-1]):
                    if a != b: break
                    n += 1
                return n
            ranked = sorted(c, key=lambda x: -score(x[0]))
            return ranked[0][1] if score(ranked[0][0]) > score(ranked[1][0]) else None
        # suffix map for dotted modules: "a/b/c" -> ids
        def find_suffix(rel_noext, exts):
            for e in exts:
                cand = rel_noext + e
                if cand in paths: return paths[cand]
            for p, i in paths.items():
                for e in exts:
                    if p.endswith('/' + rel_noext + e): return i
            return None
        updates = []
        for eid, src, kind, raw, line, lang, spath in db.execute('SELECT e.id,e.src,e.kind,e.raw,e.line,f.lang,f.path FROM edges e JOIN files f ON f.id=e.src'):
            dst = None; base = str(Path(spath).parent)
            def rel_join(x):
                return os.path.normpath(os.path.join(base, x)) if base != '.' else os.path.normpath(x)
            if kind == 'qml-type':
                dst = nearest(raw, spath)
            elif kind == 'mentions':
                cands = by_base.get(Path(raw).name, [])
                dst = paths.get(raw) or paths.get(rel_join(raw)) or (cands[0] if len(cands) == 1 else None)
            elif lang == 'python':
                if raw.startswith('.'):
                    lvl = len(raw) - len(raw.lstrip('.')); b = Path(spath).parent
                    for _ in range(lvl - 1): b = b.parent
                    rest = raw.lstrip('.').replace('.', '/'); cand = (b / rest).as_posix() if rest else b.as_posix()
                    cand = cand.lstrip('./') if cand.startswith('./') else cand
                    dst = find_suffix(cand, ['.py', '/__init__.py']) if rest else paths.get((b / '__init__.py').as_posix())
                else:
                    dst = find_suffix(raw.replace('.', '/'), ['.py', '/__init__.py'])
            elif lang in ('js', 'ts', 'qml', 'html', 'css'):
                if raw.startswith('.') or lang in ('qml', 'html', 'css') or raw.startswith('/'):
                    t = rel_join(raw.lstrip('/')) if not raw.startswith('.') else rel_join(raw)
                    for e in ('', '.ts', '.tsx', '.js', '.jsx', '.mjs', '.qml', '.css', '/index.ts', '/index.js', '/index.tsx', '/qmldir'):
                        if t + e in paths: dst = paths[t + e]; break
                    if dst is None and lang == 'qml' and any(p.startswith(t + '/') for p in paths): dst = next(i for p, i in paths.items() if p.startswith(t + '/'))
            elif lang == 'lua':
                dst = find_suffix(raw.replace('.', '/'), ['.lua', '/init.lua'])
            elif lang in ('sh', 'conf'):
                r = raw.replace('$HOME/', '').replace('~/', '').replace('${HOME}/', '')
                dst = paths.get(rel_join(r)) or paths.get(r) or (by_base[Path(r).name][0] if len(by_base.get(Path(r).name, [])) == 1 else None)
            elif lang == 'c':
                dst = paths.get(rel_join(raw)) or (by_base[Path(raw).name][0] if len(by_base.get(Path(raw).name, [])) == 1 else None)
            elif lang == 'rust':
                dst = paths.get(rel_join(raw + '.rs')) or paths.get(rel_join(raw + '/mod.rs'))
            if dst == src: dst = None
            updates.append((dst, eid))
        db.executemany('UPDATE edges SET dst=? WHERE id=?', updates)

    # ---------------------------------------------------------- helpers
    def _q(self, sql, args=()):
        self.refresh()
        db = self._db()
        try: return db.execute(sql, args).fetchall()
        finally: db.close()

    def resolve_path(self, p: str):
        p = (p or '').strip().strip('"\'').lstrip('./') if not (p or '').startswith('/') else p
        if p.startswith(str(self.root)): p = os.path.relpath(p, self.root)
        rows = self._q('SELECT id,path FROM files WHERE path=?', (p,))
        if rows: return rows[0]
        rows = self._q('SELECT id,path FROM files WHERE path LIKE ? ORDER BY length(path) LIMIT 5', ('%' + p,))
        return rows[0] if rows else None

    def _read(self, rel):
        try: return (self.root / rel).read_text(errors='replace').split('\n')
        except OSError: return []

    @staticmethod
    def cite(path, a, b=None): return f'{path}:{a}' + (f'-{b}' if b and b != a else '')

    def stats(self) -> dict:
        self.refresh()
        db = self._db()
        try:
            f = db.execute('SELECT count(*),coalesce(sum(lines),0) FROM files').fetchone()
            s = db.execute('SELECT count(*) FROM symbols').fetchone()[0]
            e = db.execute('SELECT count(*),sum(dst IS NOT NULL) FROM edges').fetchone()
            langs = db.execute('SELECT lang,count(*) c FROM files GROUP BY lang ORDER BY c DESC LIMIT 8').fetchall()
        finally: db.close()
        return {'files': f[0], 'lines': f[1], 'symbols': s, 'edges': e[1] or 0, 'langs': langs, 'ms': self.last_ms, 'root': str(self.root)}

    # ---------------------------------------------------------- queries (each returns (text, items))
    def overview(self):
        st = self.stats(); db = self._db()
        try:
            dirs = db.execute("SELECT CASE WHEN instr(path,'/')>0 THEN substr(path,1,instr(path,'/')-1) ELSE '.' END d,count(*) c FROM files GROUP BY d ORDER BY c DESC LIMIT 10").fetchall()
            hubs = db.execute('SELECT f.path,count(DISTINCT e.src) c FROM edges e JOIN files f ON f.id=e.dst GROUP BY e.dst ORDER BY c DESC LIMIT 8').fetchall()
            entry = db.execute("SELECT path FROM files WHERE (path NOT LIKE '%/%' AND (path LIKE 'README%' OR path IN ('package.json','pyproject.toml','Cargo.toml','go.mod','Makefile','docker-compose.yml','compose.yaml','Dockerfile','shell.qml','hyprland.lua')))"
                               " OR path GLOB '*/main.*' OR path GLOB 'main.*' OR path GLOB '*/__main__.py' OR path GLOB 'index.*' OR path GLOB 'app.*' ORDER BY length(path) LIMIT 10").fetchall()
        finally: db.close()
        t = [f"workspace {self.root.name} ({self.root})", f"{st['files']} files · {st['lines']:,} lines · {st['symbols']:,} symbols · {st['edges']:,} resolved links · indexed in {st['ms']}ms",
             'languages: ' + ', '.join(f'{l} {c}' for l, c in st['langs']), 'layout: ' + ', '.join(f'{d}/ ({c})' for d, c in dirs)]
        if entry: t.append('entrypoints: ' + ', '.join(p for (p,) in entry))
        if hubs: t.append('hubs (most depended on): ' + ', '.join(f'{p} ({c})' for p, c in hubs))
        return '\n'.join(t), [{'path': p, 'line': 1, 'kind': 'file', 'name': p, 'snippet': f'{c} dependents'} for p, c in hubs]

    def _terms(self, q):
        out = []
        for t in re.findall(r'[A-Za-z_][A-Za-z0-9_]*|\d{2,}', q):
            if t.lower() in STOP or len(t) < 2: continue
            out.append(t)
            for sub in split_ident(t):
                if sub != t.lower() and len(sub) >= 3 and sub not in STOP: out.append(sub)
        return list(dict.fromkeys(out))[:14]

    def search(self, q, limit=8):
        terms = self._terms(q)
        if not terms: return 'Give a few keywords, e.g. search "wallpaper picker".', []
        match = ' OR '.join(f'"{t}"*' for t in terms)
        self.refresh()
        db = self._db()
        try:
            try: rows = db.execute("SELECT fid,path,lstart,lend,text,bm25(chunks,1.0,2.0) s FROM chunks WHERE chunks MATCH ? ORDER BY s LIMIT 80", (match,)).fetchall()
            except sqlite3.OperationalError: return 'Could not parse that query; try plain words.', []
            lterms = [t.lower() for t in terms]
            symhits = {}
            for lt in lterms[:8]:
                for fid, name, kind, line in db.execute("SELECT fid,name,kind,line FROM symbols WHERE lname=? AND kind NOT IN ('id','property','signal') LIMIT 20", (lt,)): symhits.setdefault(fid, []).append((name, kind, line))
        finally: db.close()
        best = {}
        for fid, path, a, b, text, s in rows:
            score = s - (3 if fid in symhits else 0) - (2 if any(t in path.lower() for t in lterms) else 0)
            if fid not in best or score < best[fid][0]: best[fid] = (score, path, a, b, text)
        for fid, hits in symhits.items():
            if fid not in best:
                r = self._q('SELECT path FROM files WHERE id=?', (fid,))
                if r: best[fid] = (-1.0, r[0][0], hits[0][2], hits[0][2], '')
        ranked = sorted(best.items(), key=lambda kv: kv[1][0])[:limit]
        if not ranked: return f'No indexed matches for "{q}".', []
        items, out = [], []
        for fid, (score, path, a, b, text) in ranked:
            lines = text.split('\n') if text else []
            pick, hits = 0, -1
            for i, l in enumerate(lines):
                h = sum(1 for t in lterms if t in l.lower())
                if h > hits: pick, hits = i, h
            ln = a + pick; snip = (lines[pick].strip() if lines else '')[:140]
            sym = symhits.get(fid, [None])[0]
            if sym: ln = sym[2]
            tag = f'[{sym[1]} {sym[0]}] ' if sym else ''
            out.append(f'{self.cite(path, ln)}  {tag}{snip}')
            items.append({'path': path, 'line': ln, 'kind': sym[1] if sym else 'match', 'name': sym[0] if sym else '', 'snippet': snip})
        return '\n'.join(out), items

    def find(self, name, limit=15):
        name = name.strip().split('::')[-1].lstrip('#'); ln = name.lower()
        rows = self._q("SELECT s.id,s.name,s.kind,s.line,s.eline,s.sig,s.parent,f.path FROM symbols s JOIN files f ON f.id=s.fid "
                       "WHERE s.lname=? OR s.lname LIKE ? OR s.lname LIKE ? ORDER BY (s.kind IN ('id','property','signal')) ASC, (s.lname=?) DESC, (s.lname LIKE ?) DESC, length(s.name) LIMIT ?",
                       (ln, ln + '%', '%' + ln + '%', ln, ln + '%', limit))
        if not rows: return f'No symbol matching "{name}".', []
        items = [{'id': i, 'path': p, 'line': l, 'end': e, 'kind': k, 'name': (par + '.' if par else '') + n, 'snippet': sg} for i, n, k, l, e, sg, par, p in rows]
        return '\n'.join(f"{self.cite(i['path'], i['line'], i['end'])}  {i['kind']} {i['name']}  — {i['snippet']}" for i in items), items

    def definition(self, ref, max_lines=160):
        ref = ref.strip()
        if ref.startswith('#') and ref[1:].isdigit(): rows = self._q('SELECT s.id,s.name,s.kind,s.line,s.eline,f.path FROM symbols s JOIN files f ON f.id=s.fid WHERE s.id=?', (int(ref[1:]),))
        else:
            path, _, nm = ref.partition('::') if '::' in ref else ('', '', ref)
            q = ("SELECT s.id,s.name,s.kind,s.line,s.eline,f.path FROM symbols s JOIN files f ON f.id=s.fid WHERE s.lname=? " + ("AND f.path LIKE ? " if path else '') +
                 "ORDER BY CASE s.kind WHEN 'function' THEN 0 WHEN 'class' THEN 0 WHEN 'component' THEN 0 WHEN 'method' THEN 1 WHEN 'type' THEN 1 ELSE 2 END LIMIT 8")
            rows = self._q(q, (nm.lower(), '%' + path) if path else (nm.lower(),))
            if not rows:  # allow Class.method
                if '.' in nm:
                    return self.definition(nm.split('.')[-1])
        if not rows: return f'No definition found for "{ref}". Try find first.', []
        sid, name, kind, a, b, path = rows[0]
        b = min(b or a, a + max_lines - 1); lines = self._read(path)
        body = '\n'.join(f'{i:>5}  {lines[i - 1]}' for i in range(a, min(b, len(lines)) + 1))
        extra = ''
        if len(rows) > 1: extra = '\n\nalso defined at: ' + ', '.join(self.cite(r[5], r[3]) + f' ({r[2]})' for r in rows[1:6])
        return f'{self.cite(path, a, b)}  {kind} {name}\n{body}{extra}', [{'path': path, 'line': a, 'end': b, 'kind': kind, 'name': name}]

    def outline(self, path):
        r = self.resolve_path(path)
        if not r: return f'File not indexed: {path}', []
        fid, rel = r
        rows = self._q('SELECT id,name,kind,line,eline,sig,parent FROM symbols WHERE fid=? ORDER BY line', (fid,))
        if not rows: return f'{rel}: no symbols extracted.', []
        items = [{'id': i, 'path': rel, 'line': l, 'end': e, 'kind': k, 'name': n, 'snippet': sg} for i, n, k, l, e, sg, p in rows]
        txt = [f'{rel} ({len(rows)} symbols)'] + [f"{'  ' if p else ''}{l:>5}  {k:<9} {sg}" for i, n, k, l, e, sg, p in rows[:200]]
        return '\n'.join(txt), items

    def references(self, name, limit=40):
        name = name.strip().split('::')[-1].split('.')[-1].lstrip('#')
        if not re.fullmatch(r'[A-Za-z_]\w*', name): return 'Give a plain identifier name.', []
        self.refresh(); db = self._db()
        try:
            rows = db.execute('SELECT path,lstart,text FROM chunks WHERE chunks MATCH ? LIMIT 400', (f'text : "{name}"',)).fetchall()
            defs = {(p, l) for p, l in db.execute('SELECT f.path,s.line FROM symbols s JOIN files f ON f.id=s.fid WHERE s.lname=?', (name.lower(),))}
        finally: db.close()
        rx = re.compile(r'\b' + re.escape(name) + r'\b'); seen, items = set(), []
        for path, a, text in rows:
            for i, l in enumerate(text.split('\n')):
                if rx.search(l) and (path, a + i) not in seen:
                    seen.add((path, a + i)); items.append({'path': path, 'line': a + i, 'kind': 'def' if (path, a + i) in defs else 'use', 'name': name, 'snippet': l.strip()[:140]})
        items.sort(key=lambda x: (x['kind'] != 'def', x['path'], x['line']))
        if not items: return f'No references to "{name}".', []
        files = len({i['path'] for i in items}); items = items[:limit]
        return f'{len(seen)} references to {name} in {files} files\n' + '\n'.join(f"{self.cite(i['path'], i['line'])}  {'DEF ' if i['kind'] == 'def' else ''}{i['snippet']}" for i in items), items

    def _dependents(self, fid, depth=3, cap=600):
        db = self._db(); seen = {fid: 0}; q = deque([fid])
        try:
            while q and len(seen) < cap:
                cur = q.popleft()
                if seen[cur] >= depth: continue
                for (s,) in db.execute('SELECT DISTINCT src FROM edges WHERE dst=?', (cur,)):
                    if s not in seen: seen[s] = seen[cur] + 1; q.append(s)
            paths = {i: p for i, p in db.execute('SELECT id,path FROM files WHERE id IN (%s)' % ','.join(map(str, seen)))} if seen else {}
        finally: db.close()
        return {paths[i]: d for i, d in seen.items() if i != fid and i in paths}

    def impact(self, path):
        r = self.resolve_path(path)
        if not r: return f'File not indexed: {path}', []
        fid, rel = r; self.refresh(); deps = self._dependents(fid)
        direct = [p for p, d in deps.items() if d == 1]
        subs = defaultdict(int)
        for p in deps: subs['/'.join(Path(p).parts[:2]) if len(Path(p).parts) > 2 else (Path(p).parts[0] if len(Path(p).parts) > 1 else '.')] += 1
        risk = 'low' if len(deps) < 5 else 'medium' if len(deps) < 20 else 'high'
        det = self._q('SELECT f.path,e.kind,e.line FROM edges e JOIN files f ON f.id=e.src WHERE e.dst=? ORDER BY f.path LIMIT 25', (fid,))
        txt = [f'impact of {rel}: {len(direct)} direct, {len(deps)} total dependents · risk {risk}']
        if subs: txt.append('subsystems: ' + ', '.join(f'{k} ({v})' for k, v in sorted(subs.items(), key=lambda x: -x[1])[:8]))
        txt += [f'  {self.cite(p, l)}  ({k})' for p, k, l in det[:15]]
        return '\n'.join(txt), [{'path': p, 'line': l, 'kind': k, 'name': p, 'snippet': 'depends on ' + rel} for p, k, l in det]

    def callers(self, path):
        r = self.resolve_path(path)
        if not r: return f'File not indexed: {path}', []
        fid, rel = r
        rows = self._q('SELECT f.path,e.kind,e.line,e.raw FROM edges e JOIN files f ON f.id=e.src WHERE e.dst=? ORDER BY f.path,e.line LIMIT 60', (fid,))
        if not rows: return f'Nothing in the workspace includes, imports, sources or mentions {rel}.', []
        return f'{len(rows)} links into {rel}\n' + '\n'.join(f'{self.cite(p, l)}  {k}: {raw}' for p, k, l, raw in rows), [{'path': p, 'line': l, 'kind': k, 'name': p, 'snippet': raw} for p, k, l, raw in rows]

    def peek(self, ref):
        m = re.match(r'^(.*?):(\d+)(?:-(\d+))?$', ref.strip())
        path, a, b = (m.group(1), int(m.group(2)), int(m.group(3) or 0)) if m else (ref.strip(), 1, 0)
        if not b: a, b = max(1, a - 8), a + 12 if m else a + 60
        b = min(b, a + 200)
        r = self.resolve_path(path)
        rel = r[1] if r else path
        full = (self.root / rel).resolve()
        if full != self.root and self.root not in full.parents: return 'Path is outside the workspace.', []
        if is_secret(full): return 'Refusing to show a likely secrets file.', []
        lines = self._read(rel)
        if not lines: return f'Cannot read {path}.', []
        b = min(b, len(lines))
        return f'{self.cite(rel, a, b)}\n' + '\n'.join(f'{i:>5}  {lines[i - 1]}' for i in range(a, b + 1)), [{'path': rel, 'line': a, 'end': b, 'kind': 'range', 'name': rel}]

    def _git(self, *args, timeout=15):
        try: return subprocess.run(['git', '-C', str(self.root), *args], capture_output=True, text=True, timeout=timeout).stdout
        except (OSError, subprocess.SubprocessError): return ''

    def _git_status(self):
        out = self._git('status', '--porcelain=v1', '-uall')
        res = []
        for l in out.splitlines():
            if len(l) > 3: res.append((l[:2].strip() or '?', l[3:].split(' -> ')[-1].strip('"')))
        return res

    def wip(self):
        if self._git('rev-parse', '--is-inside-work-tree').strip() != 'true':
            now = time.time(); rec = []
            for base, dirs, names in os.walk(self.root):
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
                for n in names:
                    try:
                        mt = os.stat(os.path.join(base, n)).st_mtime
                        if now - mt < 2 * 86400: rec.append((mt, os.path.relpath(os.path.join(base, n), self.root)))
                    except OSError: pass
                if len(rec) > 400: break
            rec.sort(reverse=True)
            return 'Not a git repository. Modified in the last 2 days:\n' + ('\n'.join('  ' + p for _, p in rec[:25]) or '  (nothing)'), [{'path': p, 'line': 1, 'kind': 'recent', 'name': p, 'snippet': ''} for _, p in rec[:25]]
        st = self._git_status()
        if not st: return 'Working tree clean. Nothing in progress.', []
        txt, items = [f'{len(st)} files in progress'], []
        for code, p in st[:40]:
            marks = []
            f = self.root / p
            if f.is_file() and not is_secret(f) and f.stat().st_size < MAX_SIZE:
                for i, l in enumerate(self._read(p), 1):
                    if re.search(r'\b(TODO|FIXME|XXX|HACK)\b', l): marks.append((i, l.strip()[:90]))
            r = self.resolve_path(p); blast = len(self._dependents(r[0], 2)) if r else 0
            txt.append(f'  {code:<2} {p}' + (f'  · blast radius {blast}' if blast else '')); items.append({'path': p, 'line': 1, 'kind': code, 'name': p, 'snippet': f'{blast} dependents'})
            txt += [f'       {self.cite(p, i)}  {m}' for i, m in marks[:3]]
        return '\n'.join(txt), items

    def changed(self):
        st = self._git_status()
        if not st: return 'No changes versus HEAD.', []
        affected = {}
        for code, p in st:
            r = self.resolve_path(p)
            if r:
                for dp, d in self._dependents(r[0], 2).items(): affected.setdefault(dp, p)
        chg = {p for _, p in st}; aff = {k: v for k, v in affected.items() if k not in chg}
        txt = [f'{len(st)} changed · {len(aff)} others could be affected'] + [f'  changed  {c:<2} {p}' for c, p in st[:30]]
        txt += [f'  check    {p}  (depends on {src})' for p, src in list(aff.items())[:30]]
        return '\n'.join(txt), [{'path': p, 'line': 1, 'kind': 'affected', 'name': p, 'snippet': f'depends on {s}'} for p, s in aff.items()]

    def doctor(self, profile='auto'):
        self.refresh(); db = self._db()
        try:
            nodes = [i for (i,) in db.execute('SELECT id FROM files')]; names = {i: p for i, p in db.execute('SELECT id,path FROM files')}
            edges = db.execute("SELECT src,dst FROM edges WHERE dst IS NOT NULL AND kind NOT IN ('mentions','qml-type')").fetchall()
            dang = db.execute("SELECT f.path,e.line,e.kind,e.raw FROM edges e JOIN files f ON f.id=e.src WHERE e.dst IS NULL AND ((e.kind IN ('source','include','require') AND (e.raw LIKE '%/%' OR e.raw LIKE '%.%')) OR (e.kind='import' AND (e.raw LIKE './%' OR e.raw LIKE '../%'))) AND e.raw NOT LIKE '%$%' AND e.raw NOT LIKE '/%' AND e.raw NOT LIKE '<%' LIMIT 60").fetchall()
            fan = db.execute('SELECT f.path,count(DISTINCT e.src) c FROM edges e JOIN files f ON f.id=e.dst GROUP BY e.dst ORDER BY c DESC LIMIT 6').fetchall()
            total = len(nodes)
        finally: db.close()
        adj = defaultdict(list)
        for s, d in edges: adj[s].append(d)
        idx, low, onst, st, cyc, c = {}, {}, set(), [], [], [0]
        def strong(v):  # iterative Tarjan
            work = [(v, iter(adj[v]))]; idx[v] = low[v] = c[0]; c[0] += 1; st.append(v); onst.add(v)
            while work:
                node, it = work[-1]
                for w in it:
                    if w not in idx: idx[w] = low[w] = c[0]; c[0] += 1; st.append(w); onst.add(w); work.append((w, iter(adj[w]))); break
                    elif w in onst: low[node] = min(low[node], idx[w])
                else:
                    work.pop()
                    if work: low[work[-1][0]] = min(low[work[-1][0]], low[node])
                    if low[node] == idx[node]:
                        comp = []
                        while True:
                            w = st.pop(); onst.discard(w); comp.append(w)
                            if w == node: break
                        if len(comp) > 1: cyc.append(comp)
        for n in nodes:
            if n not in idx: strong(n)
        risky = [(p, c2) for p, c2 in fan if c2 >= max(15, total // 20)]
        score = max(0, 100 - 5 * min(len(cyc), 6) - 2 * min(len(dang), 15) - 3 * min(len(risky), 5))
        txt = [f'health {score}/100 · {total} files · {len(edges)} resolved links']
        txt.append(f'cycles: {len(cyc)}' + ''.join(f"\n  {' → '.join(names[i] for i in comp[:5])}{' …' if len(comp) > 5 else ''}" for comp in cyc[:4]))
        txt.append(f'dangling references: {len(dang)}' + ''.join(f'\n  {self.cite(p, l)}  {k} {raw}' for p, l, k, raw in dang[:10]))
        txt.append('fan-in risk: ' + (', '.join(f'{p} ({c2})' for p, c2 in risky) or 'none'))
        return '\n'.join(txt), [{'path': p, 'line': l, 'kind': 'dangling', 'name': raw, 'snippet': f'{k} {raw} does not resolve'} for p, l, k, raw in dang]

    def run(self, cmd, arg='') -> dict:
        """Dispatch used by the web API, MCP server and slash commands."""
        table = {'overview': lambda: self.overview(), 'search': lambda: self.search(arg), 'find': lambda: self.find(arg), 'def': lambda: self.definition(arg),
                 'outline': lambda: self.outline(arg), 'refs': lambda: self.references(arg), 'impact': lambda: self.impact(arg), 'callers': lambda: self.callers(arg),
                 'peek': lambda: self.peek(arg), 'wip': lambda: self.wip(), 'changed': lambda: self.changed(), 'doctor': lambda: self.doctor(arg or 'auto')}
        if cmd not in table: return {'text': f'Unknown index command: {cmd}', 'items': []}
        t0 = time.time(); text, items = table[cmd]()
        return {'text': text, 'items': items, 'ms': int((time.time() - t0) * 1000)}
