"""Kage v1.2 additions, mixed into Agent (needs: path, confirm, cancel, workspace, _secret, _emit, _chat, num_ctx, _poll).

  smart_call / canonical_tool   tolerate sloppy tool calls from small models (the root cause of the zip TypeError)
  access grants                 the workspace is the base folder; anything outside needs the Boss's one-time OK per folder
  create_archive                zip / tar from Python, no `zip` binary needed, secrets never included
  find_files / show_images      locate files anywhere (once allowed) and show image previews in the chat
  package pre-check             each program a command needs is looked up once per session, not once per command
  github_repos / _describe      fan-out over every repo: list, read READMEs in parallel, summarise one line each
  plan + _shrink                a visible checklist, and context trimming so long tool chains fit num_ctx
Standard library only.
"""
from __future__ import annotations
import concurrent.futures as cf, datetime as dt, fnmatch, inspect, json, os, re, secrets, shutil, tarfile, time, urllib.error, urllib.parse, urllib.request, zipfile
from pathlib import Path
from . import config, shell
from .actions import sig
from .errors import AgentError, Cancelled

# ---------------------------------------------------------------------------------------------- tolerant tool calls
TOOL_ALIASES = {
    'zip': 'create_archive', 'zip_folder': 'create_archive', 'zip_directory': 'create_archive', 'zip_files': 'create_archive', 'compress': 'create_archive', 'compress_folder': 'create_archive',
    'compress_directory': 'create_archive', 'create_zip': 'create_archive', 'make_zip': 'create_archive', 'archive': 'create_archive', 'archive_folder': 'create_archive', 'tar': 'create_archive',
    'create_tar': 'create_archive', 'unzip': 'extract_archive', 'extract': 'extract_archive', 'decompress': 'extract_archive', 'untar': 'extract_archive',
    'bash': 'run_command', 'shell': 'run_command', 'sh': 'run_command', 'exec': 'run_command', 'execute': 'run_command', 'run': 'run_command', 'run_shell': 'run_command',
    'run_bash': 'run_command', 'cmd': 'run_command', 'terminal': 'open_terminal', 'run_interactive': 'open_terminal', 'interactive_terminal': 'open_terminal',
    'ls': 'list_files', 'list_dir': 'list_files', 'list_directory': 'list_files', 'cat': 'read_file', 'open_file': 'read_file', 'search_file': 'search_files',
    'find_images': 'find_files', 'find_pictures': 'find_files', 'search_images': 'find_files', 'locate_files': 'find_files', 'find_photos': 'find_files', 'search_computer': 'find_files',
    'show_pictures': 'show_images', 'show_image': 'show_images', 'display_images': 'show_images', 'preview_images': 'show_images', 'show_photos': 'show_images', 'view_images': 'show_images',
    'list_repos': 'github_repos', 'github_list_repos': 'github_repos', 'describe_repos': 'github_describe_repos', 'summarize_repos': 'github_describe_repos',
    'todo': 'plan', 'make_plan': 'plan', 'checklist': 'plan',
    'describe': 'describe_project', 'summarize_project': 'describe_project', 'summarise_project': 'describe_project', 'project_summary': 'describe_project', 'explain_project': 'describe_project',
    'describe_folder': 'describe_project', 'describe_directory': 'describe_project', 'describe_repo': 'describe_project', 'scan_project': 'describe_project', 'scan_folder': 'describe_project',
    'scan_directory': 'describe_project', 'project_info': 'describe_project', 'analyze_project': 'describe_project', 'analyse_project': 'describe_project',
    'describe_dir': 'describe_files', 'describe_directory_files': 'describe_files', 'file_descriptions': 'describe_files', 'summarize_files': 'describe_files', 'summarize_folder_files': 'describe_files',
    'view': 'view_file', 'show_file': 'view_file', 'display_file': 'view_file', 'preview_file': 'view_file', 'show_code': 'view_file', 'view_code': 'view_file', 'read_lines': 'view_file',
    'edit': 'edit_file', 'replace': 'edit_file', 'replace_text': 'edit_file', 'modify_file': 'edit_file', 'patch_file': 'edit_file', 'update_file': 'edit_file', 'str_replace': 'edit_file',
    'find_replace': 'edit_file', 'search_replace': 'edit_file', 'sed': 'edit_file', 'change_file': 'edit_file',
}
PARAM_ALIASES = {
    'command': ('cmd', 'shell', 'script', 'bash', 'cli', 'commandline', 'command_line', 'shell_command', 'run', 'exec', 'code'),
    'path': ('dir', 'folder', 'directory', 'file', 'filepath', 'file_path', 'pathname', 'location'),
    'source': ('src', 'from', 'folder', 'dir', 'directory', 'path', 'input', 'file', 'target_folder'),
    'destination': ('dest', 'dst', 'to', 'output', 'target', 'out', 'archive', 'zipfile', 'zip_name', 'zip_file', 'name', 'filename', 'output_path'),
    'archive': ('file', 'path', 'zip', 'zipfile', 'src', 'source'),
    'query': ('q', 'search', 'text', 'term', 'keywords', 'question'),
    'content': ('text', 'data', 'body', 'contents'),
    'url': ('link', 'href', 'uri', 'address'),
    'folder': ('dir', 'directory', 'path', 'root', 'location'),
    'paths': ('files', 'images', 'file_paths', 'filepaths', 'path', 'file'),
    'format': ('type', 'fmt', 'archive_type', 'kind'),
    'kind': ('type', 'category', 'filetype', 'file_type'),
    'steps': ('plan', 'items', 'tasks', 'todo'),
    'timeout': ('timeout_seconds', 'seconds'),
    'owner': ('user', 'username', 'account', 'login'),
    'old': ('old_str', 'old_text', 'old_string', 'search', 'find', 'original', 'before', 'target', 'from_text', 'text_to_replace'),
    'new': ('new_str', 'new_text', 'new_string', 'replace', 'replacement', 'after', 'with', 'to_text', 'replace_with'),
    'start': ('line', 'start_line', 'from_line', 'first_line', 'line_start', 'begin'),
    'end': ('end_line', 'to_line', 'last_line', 'line_end', 'stop'),
    'pattern': ('glob', 'filter', 'match', 'name_filter'),
    'recursive': ('deep', 'recurse', 'subfolders', 'subdirectories', 'include_subfolders'),
}

def canonical_tool(name, tools) -> str:
    n = re.sub(r'[\s\-.]+', '_', str(name or '').strip().lower())
    if n in tools: return n
    if n.startswith('tool_') and n[5:] in tools: return n[5:]
    hit = TOOL_ALIASES.get(n)
    return hit if hit and hit in tools else str(name)

def smart_call(fn, args, extra=None):
    """Call a tool, repairing the usual small-model slips: wrong argument names, arguments placed beside "action" instead of
    inside "args", a single bare value, a JSON string. Missing required values become a clear AgentError (never a raw TypeError)."""
    name = fn.__name__.removeprefix('tool_')
    if isinstance(args, str):
        try: parsed = json.loads(args); args = parsed if isinstance(parsed, dict) else {'_': args}
        except ValueError: args = {'_': args}
    elif args is None: args = {}
    elif not isinstance(args, dict): args = {'_': args}
    args = dict(args)
    for k, v in (extra or {}).items(): args.setdefault(k, v)
    params = inspect.signature(fn).parameters
    order = [n for n, p in params.items() if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)]
    required = [n for n in order if params[n].default is inspect.Parameter.empty]
    if '_' in args and order:
        v = args.pop('_'); args.setdefault(next((n for n in required if n not in args), order[0]), v)
    for p in order:
        if p in args: continue
        for al in PARAM_ALIASES.get(p, ()):
            if al in args and al not in params: args[p] = args.pop(al); break
    unknown = [k for k in args if k not in params]; missing = [n for n in required if n not in args]
    for k, n in zip(unknown, missing): args[n] = args.pop(k)
    args = {k: v for k, v in args.items() if k in params}
    missing = [n for n in required if n not in args]
    if missing: raise AgentError(f'{name}() is missing required argument(s): {", ".join(missing)}. Usage: {name}{inspect.signature(fn)}. Call it again with "args" containing {", ".join(missing)}.')
    return fn(**args)

# ---------------------------------------------------------------------------------------------- constants
SENSITIVE = ('.ssh', '.gnupg', '.aws', '.kube', '.docker', '.config/gh', '.config/git', '.password-store', '.local/share/keyrings', '.mozilla', '.config/google-chrome',
             '.config/chromium', '.config/BraveSoftware', '.config/Bitwarden', '.thunderbird', '.config/Signal')
IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg', '.avif', '.tif', '.tiff', '.ico'}
KINDS = {'images': IMAGE_EXT, 'videos': {'.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v', '.flv', '.wmv'}, 'audio': {'.mp3', '.flac', '.ogg', '.opus', '.wav', '.m4a', '.aac', '.wma'},
         'documents': {'.pdf', '.doc', '.docx', '.odt', '.txt', '.md', '.rtf', '.epub', '.xls', '.xlsx', '.ods', '.ppt', '.pptx', '.odp', '.csv'}, 'pdf': {'.pdf'},
         'archives': {'.zip', '.tar', '.gz', '.tgz', '.xz', '.bz2', '.7z', '.rar', '.zst'}, 'code': {'.py', '.js', '.ts', '.c', '.h', '.cpp', '.rs', '.go', '.java', '.sh', '.lua', '.qml', '.rb', '.php'}}
KIND_ALIASES = {'image': 'images', 'img': 'images', 'imgs': 'images', 'picture': 'images', 'pictures': 'images', 'photo': 'images', 'photos': 'images', 'pics': 'images', 'pic': 'images',
                'video': 'videos', 'movie': 'videos', 'movies': 'videos', 'music': 'audio', 'song': 'audio', 'songs': 'audio', 'sound': 'audio', 'document': 'documents', 'doc': 'documents',
                'docs': 'documents', 'archive': 'archives', 'zip': 'archives', 'zips': 'archives', 'source': 'code', 'script': 'code', 'scripts': 'code'}
SKIP_DIRS = {'.git', 'node_modules', '.venv', 'venv', '__pycache__', 'site-packages', 'proc', 'sys', 'dev', 'run', 'lost+found', 'snap', '.Trash', 'Trash', '.thumbnails', 'thumbnails',
             'target', '.cargo', '.rustup', '.npm', '.gradle', '.m2', 'steamapps', '.wine', '.var', 'flatpak'}
ARCH = {'zip': '.zip', 'tar.gz': '.tar.gz', 'tgz': '.tar.gz', 'gz': '.tar.gz', 'tar': '.tar', 'tar.xz': '.tar.xz', 'xz': '.tar.xz', 'tar.bz2': '.tar.bz2', 'bz2': '.tar.bz2'}
TAR_MODE = {'.tar.gz': 'w:gz', '.tar': 'w', '.tar.xz': 'w:xz', '.tar.bz2': 'w:bz2'}
MAX_IMG = 25_000_000
PKG_HINTS = {'zip': 'zip', 'unzip': 'unzip', 'gh': 'github-cli', 'rg': 'ripgrep', 'fd': 'fd', '7z': 'p7zip', '7za': 'p7zip', 'convert': 'imagemagick', 'magick': 'imagemagick', 'ffmpeg': 'ffmpeg',
             'pip': 'python-pip', 'pip3': 'python-pip', 'node': 'nodejs', 'npm': 'npm', 'docker': 'docker', 'jq': 'jq', 'tree': 'tree', 'make': 'make', 'gcc': 'gcc', 'cmake': 'cmake',
             'htop': 'htop', 'vim': 'vim', 'nano': 'nano', 'curl': 'curl', 'wget': 'wget', 'git': 'git', 'rsync': 'rsync', 'xclip': 'xclip', 'wl-copy': 'wl-clipboard', 'chafa': 'chafa'}
KEYWORDS = {'if', 'then', 'else', 'elif', 'fi', 'do', 'done', 'while', 'until', '!', '{', '}'}
SKIP_SEGMENT = {'for', 'case', 'select', 'function', 'esac', 'in'}
WRAPPERS = {'sudo': {'-u', '-g', '-h', '-p', '-C', '-T', '-r', '-t', '-D', '-U'}, 'doas': {'-u', '-C'}, 'pkexec': set(), 'env': {'-u', '-C', '-S'}, 'time': set(), 'nohup': set(),
            'nice': {'-n'}, 'ionice': {'-c', '-n', '-p'}, 'command': set(), 'exec': set(), 'timeout': {'-s', '-k'}, 'stdbuf': set(), 'xargs': {'-n', '-I', '-P', '-d', '-L', '-s'}}
SEPARATORS = {'|', '||', '&&', ';', '&', '(', ')', ';;', '|&'}

def human(n) -> str:
    n = float(n)
    for u in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or u == 'GB': return f'{n:.0f} {u}' if u == 'B' else f'{n:.1f} {u}'
        n /= 1024

def _date(ts) -> str: return dt.datetime.fromtimestamp(ts).strftime('%Y-%m-%d')

def _as_list(v) -> list:
    if v is None or v == '': return []
    if isinstance(v, (list, tuple)): return [str(x).strip() for x in v if str(x).strip()]
    return [s.strip().strip('"\'') for s in re.split(r'[\n,]+', str(v)) if s.strip()]

def clean_readme(text: str, limit=1800) -> str:
    text = re.sub(r'<!--.*?-->', '', text or '', flags=re.S); text = re.sub(r'`{3}.*?`{3}', ' ', text, flags=re.S)
    text = re.sub(r'\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)', '', text); text = re.sub(r'!\[[^\]]*\]\([^)]*\)', '', text)
    text = re.sub(r'<[^>]+>', ' ', text); text = re.sub(r'\[([^\]]+)\]\([^)]*\)', r'\1', text)
    lines = [l.strip(' #>*-\t`') for l in text.splitlines()]
    return ' '.join(l for l in lines if len(l) > 2 and not re.fullmatch(r'[-=_*\s|:]+', l))[:limit]

def programs_in(command: str) -> list:
    """Program names a shell command line will try to run (best effort; anything exotic returns [] so it is never blocked)."""
    if re.search(r'\$\(|`|\$\{|\n|<\(|\bcase\b|\(\)\s*\{|\bfunction\b', command): return []
    try: toks = shell._tokens(command)
    except ValueError: return []
    segs, cur, i = [], [], 0
    while i < len(toks):
        t = toks[i]
        if t in SEPARATORS: segs.append(cur); cur = []
        elif t and all(c in '<>&' for c in t): i += 1          # redirection: skip it and its target
        else: cur.append(t)
        i += 1
    segs.append(cur); out = []
    for seg in segs:
        j = 0
        while j < len(seg):
            t = seg[j]
            if re.fullmatch(r'[A-Za-z_]\w*=.*', t) or t in KEYWORDS: j += 1; continue
            if t in SKIP_SEGMENT: j = len(seg); break
            if t in WRAPPERS:
                j += 1
                while j < len(seg):
                    w = seg[j]
                    if w in WRAPPERS[t]: j += 2                       # an option that takes a value, e.g. sudo -u bob
                    elif w.startswith('-') or re.fullmatch(r'[A-Za-z_]\w*=.*', w) or (t == 'timeout' and re.fullmatch(r'[\d.]+[smhd]?', w)): j += 1
                    else: break
                continue
            if re.fullmatch(r'[A-Za-z0-9_.+@-]+', t) and not t.startswith(('.', '-')) and t not in out: out.append(t)
            break
    return out

class ExtrasMixin:
    # ------------------------------------------------------------------------------------------ access scope
    @staticmethod
    def _sensitive(p: Path) -> bool:
        try: rel = '/'.join(p.relative_to(config.HOME).parts)
        except ValueError: return p.parent == Path('/etc') and (p.name in ('shadow', 'gshadow', 'sudoers') or p.name.startswith('sudoers'))
        return any(rel == s or rel.startswith(s + '/') for s in SENSITIVE)

    def _grant(self, p: Path):
        """The workspace is the base folder. Reaching outside it needs the Boss's OK once per folder (until /new)."""
        root = p if p.is_dir() else p.parent
        if any(g == root or g in root.parents for g in self._grants): return
        scope = 'your ENTIRE computer (every folder you can read)' if str(root) == '/' else 'your whole home folder' if root == config.HOME else str(root)
        if not self.confirm(f'Allow Kage to look outside the workspace?\n  {scope}\nRead access to it and everything under it, until /new or /access workspace.', danger=str(root) == '/', scope=True):
            raise AgentError(f'The Boss did not allow access outside the workspace ({root}). Stay inside {self.workspace}, or ask the Boss to allow it.')
        self._grants.add(root)

    # ------------------------------------------------------------------------------------------ package pre-check (once per program per session)
    def _have(self, name: str) -> str:
        hit = self._pkg_cache.get(name)
        if hit is not None: return hit
        self._pkg_checks += 1
        path = shutil.which(name) or ''
        if not path:
            code, out = shell.execute(['bash', '-c', 'type -t -- "$0"', name], timeout=5)
            if code == 0 and out.strip() in ('builtin', 'keyword', 'function', 'alias'): path = 'shell ' + out.strip()
        self._pkg_cache[name] = path
        return path

    def _precheck(self, command: str):
        """-> None when every program the command needs exists, else a message the model can act on. Each program is looked up once per session."""
        missing = [n for n in programs_in(command) if not self._have(n)]
        if not missing: return None
        parts = []
        for n in missing:
            pkg = PKG_HINTS.get(n)
            parts.append(f"'{n}' is not installed" + (f' (Arch package: {pkg})' if pkg else ''))
        extra = ' For archives use create_archive / extract_archive, which need no extra program.' if {'zip', 'unzip', 'tar', '7z'} & set(missing) else ''
        return ('MISSING PROGRAM: ' + '; '.join(parts) + '. Nothing was run. Install it with pacman_install([...]) after pacman_search confirms the package name, or choose another way.' + extra)

    def _forget_programs(self, packages):
        names = set(packages) | {n for n, p in PKG_HINTS.items() if p in set(packages)}
        for n in names: self._pkg_cache.pop(n, None)

    # ------------------------------------------------------------------------------------------ action log + undo
    def _log(self, tool, summary, undo=None):
        try: self.actions.record(tool, summary, undo, str(self.workspace or ''))
        except OSError: pass

    def _log_call(self, action, args, extra=None):
        """Log actions that cannot be undone (commands, pushes, installs) as a plain record. Read-only commands are skipped."""
        a = dict(args) if isinstance(args, dict) else {}; a.update(extra or {})
        text = str(a.get('command') or a.get('cmd') or a.get('args') or a.get('packages') or a.get('url') or a.get('app') or a.get('name') or a.get('message') or json.dumps(a, default=str))
        if action == 'run_command' and shell.classify(text)[0] == 'readonly': return
        if action == 'git' and shell.readonly('git ' + text): return
        if action == 'clipboard' and a.get('action') == 'read': return
        self._log(action, text)

    def tool_action_log(self, limit=15):
        """What Kage has done recently (newest first), and which of it can be undone."""
        try: limit = max(1, min(int(limit), 60))
        except (TypeError, ValueError): limit = 15
        rows = self.actions.recent(limit)
        if not rows: return 'Nothing logged yet.'
        return '\n'.join(f"#{r['id']} {dt.datetime.fromtimestamp(r['ts']):%a %H:%M} {r['tool']}: {r['summary'][:120]}" + (' [undone]' if r['undone'] else ' [can undo]' if r['undoable'] else '') for r in rows) + '\nReverse one with undo(id) or /undo.'

    def tool_undo(self, id=''):
        """Reverse a logged action (default: the most recent reversible one). Shows exactly what will be undone first."""
        e = self.actions.find(id or None)
        if e.get('undone'): raise AgentError(f"Action #{e['id']} was already undone.")
        if not e.get('undo'): raise AgentError(f"Action #{e['id']} ({e['tool']}) cannot be reversed automatically: {e['summary']}")
        if not self.confirm(f"Undo #{e['id']} ({e['tool']}): {e['summary']}\nThis will {self.actions.describe(e)}."): return 'Cancelled. Nothing was undone.'
        return self.actions.undo(e['id'])

    # ------------------------------------------------------------------------------------------ briefing
    def tool_briefing(self):
        """Morning briefing: machine health, failed services, pending updates, repos that need attention, recent work. No model involved."""
        from . import briefing
        return briefing.build(self)

    # ------------------------------------------------------------------------------------------ plan
    def _plan_text(self) -> str:
        if not self._plan: return ''
        done = sum(1 for s in self._plan if s['d']); nxt = next((i for i, s in enumerate(self._plan, 1) if not s['d']), None)
        body = '\n'.join(f"[{'x' if s['d'] else ' '}] {i}. {s['t']}" for i, s in enumerate(self._plan, 1))
        return f'PLAN ({done}/{len(self._plan)} done):\n{body}\n' + (f'Next: step {nxt}.' if nxt else 'All steps done: write the final answer now.')

    def tool_plan(self, steps=None, done=None):
        """Keep a visible checklist for a long task. plan(steps=[...]) creates it; plan(done=[1,2]) ticks steps off."""
        if steps:
            items = steps if isinstance(steps, (list, tuple)) else [re.sub(r'^\s*(?:\d+[.)]|[-*])\s*', '', s) for s in re.split(r'\n|;', str(steps))]
            items = [str(s).strip() for s in items if str(s).strip()][:12]
            if not items: raise AgentError('plan(steps=[...]) needs at least one step.')
            self._plan = [{'t': s[:160], 'd': False} for s in items]
        if done is not None and self._plan:
            if str(done).lower() == 'all': done = list(range(1, len(self._plan) + 1))
            for d in done if isinstance(done, (list, tuple)) else [done]:
                try: i = int(d) - 1
                except (TypeError, ValueError): continue
                if 0 <= i < len(self._plan): self._plan[i]['d'] = True
        if not self._plan: raise AgentError('There is no plan yet. Call plan(steps=["first", "second", ...]) first.')
        return self._plan_text()

    # ------------------------------------------------------------------------------------------ context control for long tool chains
    def _shrink(self, messages, anchor=0) -> bool:
        """Keep a long think-act chain inside num_ctx: trim old tool results, then fold the oldest steps of this turn into a digest."""
        est = lambda: sum(len(m['content']) for m in messages) / 3.6
        budget = self.num_ctx * 0.66
        if est() <= budget: return False
        res = [i for i, m in enumerate(messages) if m['role'] == 'user' and m['content'].startswith('Result from')]
        for keep in (2, 1, 0):
            for i in (res[:-keep] if keep else res):
                c = messages[i]['content']
                if len(c) > 420: messages[i]['content'] = c[:380] + '\n…(older result trimmed to save context; call the tool again if you need the detail)'
            if est() <= budget: return True
        if len(messages) - anchor > 6:
            lines = []
            for m in messages[anchor:len(messages) - 4]:
                if m['role'] == 'assistant':
                    try: o = json.loads(m['content']); lines.append(f"- {o.get('action')}({json.dumps(o.get('args') or {}, ensure_ascii=False)[:90]})")
                    except (ValueError, AttributeError): pass
                elif m['content'].startswith('Result from') and lines and ' → ' not in lines[-1]:
                    lines[-1] += ' → ' + m['content'].split('\n', 1)[-1][:200].replace('\n', ' ')
            messages[anchor:len(messages) - 4] = [{'role': 'user', 'content': 'Earlier steps of this task (folded to save context):\n' + '\n'.join(lines[-14:])}]
        return True

    # ------------------------------------------------------------------------------------------ archives
    def tool_create_archive(self, source, destination='', format='zip'):
        """Zip or tar a file or folder (pure Python: no `zip` program needed). Secrets (.env, keys) are never included."""
        src = self.path(source)
        if not src.exists(): raise AgentError(f'Not found: {src}')
        fmt = str(format or 'zip').lower().lstrip('.')
        if fmt not in ARCH: raise AgentError('format must be zip, tar.gz, tar.xz, tar.bz2 or tar.')
        ext = ARCH[fmt]
        if destination:
            dst = self.path(destination)
            if dst.is_dir(): dst = dst / (src.name + ext)
            elif not any(dst.name.endswith(e) for e in set(ARCH.values())): dst = dst.with_name(dst.name + ext)
        else: dst = (src if src == self.workspace else src.parent) / (src.name + ext)
        dst = self.path(str(dst)); ext = next(e for e in sorted(set(ARCH.values()), key=len, reverse=True) if dst.name.endswith(e))
        entries, skipped, failed, total, links = [], [], [], 0, 0
        if src.is_file(): entries.append((src, src.name)); total = src.stat().st_size
        else:
            for base, dirs, files in os.walk(src, followlinks=False):
                dirs.sort(); b = Path(base)
                if not dirs and not files and b != src: entries.append((b, str(b.relative_to(src.parent)) + '/'))
                for n in sorted(files):
                    f = b / n
                    if f == dst or n.endswith('.part') and f.with_suffix('') == dst: continue
                    rel = str(f.relative_to(src.parent))
                    if f.is_symlink(): links += 1; continue
                    if self._secret(f) or self._sensitive(f): skipped.append(rel); continue
                    try: total += f.stat().st_size
                    except OSError: continue
                    entries.append((f, rel))
                if len(entries) > 100_000 or total > 8 << 30: raise AgentError('That is too big to archive in one go (over 100,000 files or 8 GB). Pick a smaller folder.')
        if not entries: raise AgentError(f'Nothing to archive in {src} (everything was skipped or the folder is empty).')
        note = (f'\n  skipping {len(skipped)} secret file(s): ' + ', '.join(skipped[:4]) + (' …' if len(skipped) > 4 else '')) if skipped else ''
        if not self.confirm(f"Create {ext[1:]} archive{' (replaces the existing file)' if dst.exists() else ''}:\n  {dst}\n  from {src}\n  {len(entries)} items, {human(total)}{note}"): return 'Cancelled. No archive was created.'
        replaced = dst.is_file(); snap = self.actions.snapshot(dst) if replaced else None
        tmp = dst.with_name(dst.name + '.part'); dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if ext == '.zip':
                with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED, compresslevel=6, allowZip64=True) as z:
                    for i, (f, rel) in enumerate(entries):
                        if i % 200 == 0 and self.cancel.is_set(): raise Cancelled()
                        try: z.write(f, rel)
                        except OSError as e: failed.append(f'{rel} ({e.strerror})')
            else:
                with tarfile.open(tmp, TAR_MODE[ext]) as t:
                    for i, (f, rel) in enumerate(entries):
                        if i % 200 == 0 and self.cancel.is_set(): raise Cancelled()
                        try: t.add(f, rel.rstrip('/'), recursive=False)
                        except OSError as e: failed.append(f'{rel} ({e.strerror})')
            os.replace(tmp, dst)
        except Cancelled: raise
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as e: raise AgentError(f'Archive failed: {e}')
        finally:
            try: tmp.unlink()
            except OSError: pass
        try:
            if ext == '.zip':
                with zipfile.ZipFile(dst) as z: count = len(z.namelist()); bad = z.testzip() if dst.stat().st_size < 400 << 20 else None
            else:
                with tarfile.open(dst) as t: count = len(t.getmembers()); bad = None
        except (OSError, zipfile.BadZipFile, tarfile.TarError) as e: return f'NOT VERIFIED: wrote {dst} but could not read it back: {e}'
        skipped += failed
        extra = (f'\nSkipped: {", ".join(skipped[:6])}' + (' …' if len(skipped) > 6 else '')) if skipped else ''
        if links: extra += f'\nIgnored {links} symlink(s).'
        if bad or count < len(entries) - len(failed): return f'NOT VERIFIED: {dst} has {count} entries but {len(entries)} were expected{f"; corrupt member {bad}" if bad else ""}.{extra}'
        self._log('create_archive', f'Created {dst}', {'kind': 'restore', 'path': str(dst), 'snap': snap, 'after': sig(dst)} if snap else None if replaced else {'kind': 'delete', 'path': str(dst), 'after': sig(dst)})
        return f'VERIFIED: created {dst} ({count} items, {human(dst.stat().st_size)}, from {human(total)}).{extra}'

    # ------------------------------------------------------------------------------------------ find + show files (anywhere, once the Boss allows it)
    def _walk_files(self, root: Path, exts=None, pat='', hidden=False, budget=25.0, cap=400_000):
        out, scanned, t0, truncated = [], 0, time.time(), False
        glob = bool(re.search(r'[*?\[]', pat)); pat = pat.lower()
        for base, dirs, files in os.walk(root, onerror=lambda e: None, followlinks=False):
            if self.cancel.is_set(): raise Cancelled()
            b = Path(base)
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (hidden or not d.startswith('.')) and not self._sensitive(b / d)]
            for n in files:
                scanned += 1
                if not hidden and n.startswith('.'): continue
                low = n.lower()
                if exts and os.path.splitext(low)[1] not in exts: continue
                if pat and not (fnmatch.fnmatch(low, pat) if glob else pat in low): continue
                f = b / n
                if self._secret(f): continue
                try: st = f.stat()
                except OSError: continue
                if st.st_size: out.append((st.st_mtime, st.st_size, str(f)))
            if time.time() - t0 > budget or scanned > cap: truncated = True; break
        out.sort(reverse=True)
        return out, scanned, truncated, time.time() - t0

    @staticmethod
    def _exts(kind: str):
        k = str(kind or '').strip().lower(); k = KIND_ALIASES.get(k, k)
        if not k: return None, ''
        if k in KINDS: return KINDS[k], k
        if re.fullmatch(r'\.?[a-z0-9]{1,6}', k): return {'.' + k.lstrip('.')}, k
        raise AgentError(f'Unknown kind {kind!r}. Use one of: {", ".join(KINDS)} (or a file extension such as png).')

    def tool_find_files(self, kind='', pattern='', path='.', limit=60, include_hidden=False):
        """Find files by kind (images, videos, audio, documents, pdf, archives, code or an extension) and/or name pattern. path="~" searches the whole home folder; outside the workspace the Boss is asked first."""
        exts, label = self._exts(kind)
        if not exts and not str(pattern or '').strip(): raise AgentError('Give a kind (e.g. images) and/or a pattern (e.g. "invoice*").')
        root = self.path(path)
        if not root.is_dir(): raise AgentError(f'Not a folder: {root}')
        try: limit = max(1, min(int(limit), 300))
        except (TypeError, ValueError): limit = 60
        found, scanned, trunc, secs = self._walk_files(root, exts, str(pattern or '').strip(), bool(include_hidden))
        what = label or f'files matching {pattern!r}'
        if not found: return f'No {what} found under {root} (scanned {scanned:,} files in {secs:.1f}s).' + (' The search hit its time limit; narrow the path.' if trunc else '')
        folders = {}
        for _, _, f in found: folders[os.path.dirname(f)] = folders.get(os.path.dirname(f), 0) + 1
        top = ', '.join(f'{config.short(d, 40)} ({n})' for d, n in sorted(folders.items(), key=lambda x: -x[1])[:8])
        lines = [f'{f} ({human(s)}, {_date(m)})' for m, s, f in found[:limit]]
        tail = '\nTo see previews: show_images(folder="<one of these folders>") or show_images(paths=[...]).' if label == 'images' or (exts and exts <= IMAGE_EXT) else ''
        return (f'Found {len(found):,} {what} under {root} (scanned {scanned:,} files in {secs:.1f}s)' + ('; search stopped at its time limit, so this may be incomplete' if trunc else '') +
                f'\nBy folder: {top}\nNewest first, showing {min(limit, len(found))} of {len(found):,}:\n' + '\n'.join(lines) + tail)

    def tool_show_images(self, paths=None, folder='', limit=40, offset=0, pattern=''):
        """Show image previews in the chat. Give paths=[...] or folder="~/Pictures" (searched recursively, newest first; page with offset)."""
        try: limit = max(1, min(int(limit), 60)); offset = max(0, int(offset))
        except (TypeError, ValueError): limit, offset = 40, 0
        cand, total = [], 0
        listed = _as_list(paths)
        if listed:
            for p in listed:
                q = self.path(p)
                if q.is_dir(): raise AgentError(f'{q} is a folder. Use show_images(folder="{p}") for folders.')
                cand.append(q)
            total = len(cand); cand = cand[offset:offset + limit]
        elif str(folder or '').strip():
            root = self.path(folder)
            if not root.is_dir(): raise AgentError(f'Not a folder: {root}')
            found, scanned, trunc, _ = self._walk_files(root, IMAGE_EXT, str(pattern or '').strip())
            total = len(found); cand = [Path(f) for _, _, f in found[offset:offset + limit]]
        else: raise AgentError('Give paths=[...] or folder="...".')
        items, bad = [], []
        for p in cand:
            try: ok = p.is_file() and p.suffix.lower() in IMAGE_EXT and not self._secret(p) and not self._sensitive(p) and p.stat().st_size <= MAX_IMG
            except OSError: ok = False
            if not ok: bad.append(p.name); continue
            tok = secrets.token_hex(8); self.media[tok] = str(p)
            while len(self.media) > 3000: self.media.pop(next(iter(self.media)))
            items.append({'name': p.name, 'path': str(p), 'url': f'/api/media?t={tok}', 'size': p.stat().st_size})
        if not items: raise AgentError('None of those are viewable images (need png/jpg/gif/webp/svg/bmp/avif under 25 MB).' + (f' Skipped: {", ".join(bad[:5])}' if bad else ''))
        self._emit({'t': 'images', 'items': items, 'total': total, 'offset': offset})
        end = offset + len(items)
        more = f'\n{total - end} more available: call show_images again with offset={end}.' if total > end else ''
        return (f'OK (exit 0)\nDisplayed {len(items)} image preview(s) in the chat ({offset + 1}-{end} of {total}). The Boss can see them now; do not list every path again.\n' +
                '\n'.join(f"- {i['path']} ({human(i['size'])})" for i in items[:12]) + ('\n…' if len(items) > 12 else '') + (f'\nSkipped (not viewable): {", ".join(bad[:5])}' if bad else '') + more)

    # ------------------------------------------------------------------------------------------ GitHub fan-out
    def _gh_state(self) -> bool:
        t, v = self._gh_cache
        if time.time() - t > 120: v = self._gh_logged_in(self.workspace)[0]; self._gh_cache = (time.time(), v)
        return v

    def _gh_owner(self, owner='') -> str:
        owner = str(owner or '').strip().lstrip('@')
        if owner:
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}', owner): raise AgentError('That is not a valid GitHub username.')
            return owner
        if self._gh_state():
            code, out = shell.execute(['gh', 'api', 'user', '-q', '.login'], cwd=self.workspace, timeout=20, on_poll=self._poll)
            if code == 0 and out.strip(): return out.strip().splitlines()[-1].strip()
        user = self._gcfg('github.user', self.workspace)
        if user: return user
        code, out = self._git(['remote', 'get-url', 'origin'], self.workspace)
        m = re.search(r'github\.com[:/]([\w.-]+)/', out) if code == 0 else None
        if m: return m.group(1)
        raise AgentError('I cannot tell which GitHub account is the Boss\'s. Call github_login() first, or use github_repos(owner="username").')

    def _repos(self, owner: str, limit=100) -> list:
        hit = self._repo_cache.get(owner.lower())
        if hit and time.time() - hit[0] < 300 and hit[2] >= limit: return hit[1]
        repos, why = None, ''
        if self._gh_state():
            code, out = shell.execute(['gh', 'repo', 'list', owner, '--limit', str(limit), '--json', 'name,nameWithOwner,description,url,primaryLanguage,isPrivate,isFork,isArchived,pushedAt,stargazerCount'],
                                      cwd=self.workspace, timeout=90, on_poll=self._poll)
            if code == 0:
                try:
                    repos = [{'name': r['name'], 'full': r['nameWithOwner'], 'desc': (r.get('description') or '').strip(), 'lang': (r.get('primaryLanguage') or {}).get('name', ''), 'private': r.get('isPrivate', False),
                              'fork': r.get('isFork', False), 'archived': r.get('isArchived', False), 'pushed': (r.get('pushedAt') or '')[:10], 'stars': r.get('stargazerCount', 0), 'url': r.get('url', '')}
                             for r in json.loads(out[out.find('['):])]
                except (ValueError, KeyError, TypeError) as e: why = f'gh output not understood: {e}'
            else: why = shell.redact(out)[:200]
        if repos is None:
            repos = []
            try:
                for page in range(1, (limit - 1) // 100 + 2):
                    req = urllib.request.Request(f'https://api.github.com/users/{owner}/repos?per_page=100&sort=pushed&page={page}', headers={'User-Agent': 'Kage/1.2', 'Accept': 'application/vnd.github+json'})
                    with urllib.request.urlopen(req, timeout=20) as r: data = json.loads(r.read().decode('utf-8', 'replace'))
                    repos += [{'name': r['name'], 'full': r['full_name'], 'desc': (r.get('description') or '').strip(), 'lang': r.get('language') or '', 'private': r.get('private', False), 'fork': r.get('fork', False),
                               'archived': r.get('archived', False), 'pushed': (r.get('pushed_at') or '')[:10], 'stars': r.get('stargazers_count', 0), 'url': r.get('html_url', '')} for r in data]
                    if len(data) < 100: break
            except urllib.error.HTTPError as e:
                raise AgentError(f'GitHub user {owner!r} was not found.' if e.code == 404 else f'GitHub refused the request (HTTP {e.code}; unauthenticated calls are rate limited). Call github_login() so gh can list repos.' + (f' gh said: {why}' if why else ''))
            except (urllib.error.URLError, OSError, ValueError) as e: raise AgentError(f'Could not reach GitHub: {e}. ' + (f'gh said: {why}' if why else 'Check the network, or call github_login().'))
        repos = repos[:limit]; self._repo_cache[owner.lower()] = (time.time(), repos, limit)
        return repos

    def tool_github_repos(self, owner='', limit=100):
        """List the Boss's GitHub repositories (or another user's public ones) with language, activity and description."""
        try: limit = max(1, min(int(limit), 300))
        except (TypeError, ValueError): limit = 100
        owner = self._gh_owner(owner); repos = self._repos(owner, limit)
        if not repos: return f'{owner} has no repositories I can see.' + ('' if self._gh_state() else ' (Not signed in to gh, so private repos are invisible; call github_login().)')
        flags = lambda r: ','.join(x for x, on in (('private', r['private']), ('fork', r['fork']), ('archived', r['archived'])) if on)
        rows = '\n'.join(f"{i}. {r['full']} | {r['lang'] or '-'}{' | ' + flags(r) if flags(r) else ''} | pushed {r['pushed'] or '?'} | {(r['desc'] or '(no description)')[:90]}" for i, r in enumerate(repos, 1))
        return (f'{len(repos)} repositories for {owner} ({"signed in via gh" if self._gh_state() else "public only, not signed in"}):\n{rows}\n'
                'To learn what each one actually does, call github_describe_repos() (reads the READMEs, 10 per call, page with offset).')

    def _readme(self, r: dict) -> str:
        if self._gh_state():
            code, out = shell.execute(['gh', 'api', f"repos/{r['full']}/readme", '-H', 'Accept: application/vnd.github.raw'], cwd=self.workspace, timeout=30)
            if code == 0: return out
        if r['private']: return ''
        for name in ('README.md', 'readme.md', 'README.rst', 'README'):
            try:
                with urllib.request.urlopen(urllib.request.Request(f"https://raw.githubusercontent.com/{r['full']}/HEAD/{name}", headers={'User-Agent': 'Kage/1.2'}), timeout=15) as resp: return resp.read(200_000).decode('utf-8', 'replace')
            except (urllib.error.URLError, OSError): continue
        return ''

    def _one_line(self, r: dict, readme: str) -> str:
        fallback = r['desc'] or readme[:140] or 'No description or README.'
        if not r['desc'] and not readme: return fallback
        ctx = f"Repository: {r['name']}\nLanguage: {r['lang'] or 'unknown'}\nGitHub description: {r['desc'] or '(none)'}\nREADME excerpt: {readme or '(no README)'}"
        msgs = [{'role': 'system', 'content': 'You summarise GitHub repositories. Reply with ONE plain sentence of at most 22 words saying what the project does. Use only the facts given. If you cannot tell, reply exactly: Unclear from the README.'},
                {'role': 'user', 'content': ctx}]
        try: raw, _ = self._chat(msgs, None, use_format=False, num_predict=90)
        except AgentError: raw = ''
        line = re.sub(r'\s+', ' ', raw).strip().strip('"')[:220]
        return line if line and 'unclear' not in line.lower()[:12] else (r['desc'] or line or fallback)

    def tool_github_describe_repos(self, owner='', offset=0, limit=10):
        """Read the README of each repo (in parallel) and write a one-line "what it does" for each. 10 per call; page with offset."""
        try: offset = max(0, int(offset)); limit = max(1, min(int(limit), 15))
        except (TypeError, ValueError): offset, limit = 0, 10
        owner = self._gh_owner(owner); repos = self._repos(owner)
        if not repos: return f'{owner} has no repositories I can see.'
        page = repos[offset:offset + limit]
        if not page: return f'Offset {offset} is past the end: {owner} has {len(repos)} repositories. You already have everything; write the final answer.'
        self._emit({'t': 'status', 'm': f'Reading {len(page)} READMEs in parallel…'})
        with cf.ThreadPoolExecutor(6) as ex: texts = list(ex.map(lambda r: clean_readme(self._readme(r)), page))
        self._poll(); digest = self._repo_digest.setdefault(owner.lower(), {}); lines = []
        for i, (r, text) in enumerate(zip(page, texts), 1):
            self._poll(); self._emit({'t': 'status', 'm': f"Summarising {r['name']} ({offset + i}/{len(repos)})…"})
            s = self._one_line(r, text); digest[r['name']] = (r['lang'], s)
            tags = ','.join(x for x, on in (('private', r['private']), ('fork', r['fork']), ('archived', r['archived'])) if on)
            lines.append(f"{offset + i}. {r['full']} [{r['lang'] or '-'}{',' + tags if tags else ''}] pushed {r['pushed'] or '?'}: {s}")
        end = offset + len(page); out = '\n'.join(lines)
        if end < len(repos): return out + f'\n\n{len(repos) - end} repositories left. NEXT: call github_describe_repos(offset={end}). Do not answer yet.'
        everything = '\n'.join(f'- {n} [{l or "-"}]: {s[:110]}' for n, (l, s) in digest.items())
        return out + f'\n\nThat is all {len(repos)} repositories. COMPLETE LIST SO FAR (use this for the final answer, grouped by theme if long):\n' + everything[:5500]
