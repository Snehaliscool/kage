"""Project tools (v1.3), mixed into Agent: describe a folder, view files in the chat, edit them safely.

describe_project / describe_files scan with projscan (milliseconds, no model) and put a card in the chat.
view_file shows real file contents with line numbers. edit_file is an exact find-and-replace that shows a diff for approval,
snapshots the file (so /undo works), writes atomically and reads the result back before it says VERIFIED.
Needs from Agent: path, confirm, workspace, _secret, _emit, actions, _log.
"""
from __future__ import annotations
import ast, difflib, json, os, re, tempfile
from pathlib import Path
from . import projscan
from .actions import sig
from .errors import AgentError
from .extras import IMAGE_EXT, human

VIEW_UI_LINES = 600          # lines drawn in the chat card
VIEW_UI_CHARS = 90_000
VIEW_MODEL_CHARS = 5_500     # what the model gets back in the tool result (it can page with start/end)
MAX_VIEW_BYTES = 5_000_000
MAX_EDIT_BYTES = 2_000_000
CITE = re.compile(r'^(.*?):(\d+)(?:-(\d+))?$')

def _int(v, default):
    try: return int(float(str(v).strip())) if str(v).strip() != '' else default
    except (TypeError, ValueError): return default

def _syntax_error(p: Path, text: str) -> str:
    """'' when the text parses (or the type is not checked), else a one-line reason."""
    try:
        if p.suffix == '.py': ast.parse(text)
        elif p.suffix.lower() in ('.json',) and text.strip(): json.loads(text)
    except SyntaxError as e: return f'Python syntax error at line {e.lineno}: {e.msg}'
    except ValueError as e: return f'invalid JSON: {e}'
    return ''

def _fuzzy_span(lines: list, old: str):
    """Find `old` ignoring leading/trailing whitespace on every line. -> [(start, end)] line spans (end exclusive)."""
    want = [l.strip() for l in old.strip('\n').split('\n')]
    if not want or not any(want): return []
    out = []
    for i in range(len(lines) - len(want) + 1):
        if all(lines[i + j].strip() == want[j] for j in range(len(want))): out.append((i, i + len(want)))
    return out

class ProjectMixin:
    # ------------------------------------------------------------------------------------------ describe
    def _describe(self, path):
        root = self.path(path)
        if not root.is_dir(): raise AgentError(f'Not a folder: {root}')
        self._emit({'t': 'status', 'm': f'Scanning {root.name}…'})
        r = projscan.scan(root)
        self._emit({'t': 'project', 'card': projscan.card(r), 'text': projscan.render(r)})
        return r

    def _files(self, path, pattern, recursive, limit):
        base = self.path(path)
        if not base.is_dir(): raise AgentError(f'Not a folder: {base}')
        limit = max(1, min(_int(limit, 40), 200)); rec = str(recursive).lower() in ('true', '1', 'yes')
        rows, total = projscan.list_dir(self.workspace if self.workspace in (base, *base.parents) else base, base, str(pattern or ''), rec, limit)
        if rows: self._emit({'t': 'files', 'base': str(base), 'rows': rows, 'total': total})
        return base, rows, total

    def tool_describe_project(self, path='.'):
        """Scan a folder (default: the workspace) in milliseconds and show a project card: stack, languages, layout, entry points, key files."""
        r = self._describe(path)
        return ('OK (exit 0)\nA project card is now on the Boss\'s screen. Write a SHORT description from the dossier below: what it is, the stack, how it is laid out, how to run it, and anything notable. '
                'Do not repeat the tables. Cite files as path.\n\n' + projscan.render(r))

    def tool_describe_files(self, path='.', pattern='', recursive=False, limit=40):
        """One-line purpose for every file in a folder (from its own docstring/comment/heading). pattern filters names (e.g. '*.py'); recursive walks sub-folders."""
        base, rows, total = self._files(path, pattern, recursive, limit)
        if not rows: return f'No files{" matching " + repr(pattern) if pattern else ""} in {base}.'
        more = f'\n…{total - len(rows)} more; narrow with pattern= or raise limit.' if total > len(rows) else ''
        return (f'OK (exit 0)\nShown in the chat: {len(rows)} of {total} entries in {base}.\n' +
                '\n'.join(f"{r['path']}" + (f" ({r['lines']} lines)" if r['lines'] else '') + f": {r['desc']}" for r in rows) + more)

    # instant slash versions: the card/list/file is drawn from events, the reply is one line
    def slash_describe(self, arg):
        r = self._describe(arg.strip() or '.')
        return f"{r['name']}: {r['files']:,} files, {r['lines']:,} lines, {', '.join(r['primary']) or 'no code detected'}. Scanned in {r['ms']} ms{' (cached)' if r.get('cached') else ''}."

    def slash_files(self, arg):
        bits = arg.split(); pat = next((b for b in bits if re.search(r'[*?]', b) or (b.startswith('.') and '/' not in b)), ''); where = next((b for b in bits if b != pat), '.')
        base, rows, total = self._files(where, pat, bool(pat), 60)
        return f'{len(rows)} of {total} entries in {base}.' if rows else f'Nothing in {base}' + (f' matches {pat}.' if pat else '.')

    def slash_view(self, arg):
        if not arg.strip(): return 'Usage: /view <path>  or  /view <path>:10-40  or  /view <path> 10 40'
        bits = arg.split()
        if len(bits) >= 2 and bits[1].isdigit(): out = self.tool_view_file(bits[0], bits[1], bits[2] if len(bits) > 2 else 0)
        else: out = self.tool_view_file(arg.strip())
        m = re.search(r'Shown in the chat: (.+?)\. Line numbers', out)
        return m.group(1) + '.' if m else (out.split('\n')[1] if '\n' in out else out)

    # ------------------------------------------------------------------------------------------ view
    def tool_view_file(self, path, start=1, end=0):
        """Show a file in the chat with line numbers. path may carry a range ('agent.py:120-180'); otherwise use start/end. Long files show the first 600 lines."""
        m = CITE.match(str(path))
        if m and not Path(str(path)).expanduser().exists() and not (self.workspace / str(path)).exists():      # 'file.py:10-40' as the index prints it
            path = m.group(1); start = int(m.group(2)) if _int(start, 1) == 1 else start; end = end or (int(m.group(3)) if m.group(3) else 0)
        p = self.path(path)
        if self._secret(p): raise AgentError('Refusing to show likely secrets/private-key files.')
        if p.is_dir(): raise AgentError(f'{p} is a folder. Use describe_files(path) to see what is in it.')
        if not p.is_file(): raise AgentError(f'File not found: {p}')
        if p.suffix.lower() in IMAGE_EXT: return self.tool_show_images(paths=[str(p)])
        size = p.stat().st_size
        if size > MAX_VIEW_BYTES: raise AgentError(f'File is {human(size)}; too big to display. Use outline/read_symbol/peek, or search_files for the part you need.')
        raw = p.read_bytes()
        if b'\0' in raw[:4096]: raise AgentError(f'{p.name} is a binary file ({human(size)}); it cannot be shown as text.')
        lines = raw.decode('utf-8', 'replace').splitlines(); total = len(lines)
        s = max(1, _int(start, 1)); e = _int(end, 0)
        if e <= 0: e = min(total, s + VIEW_UI_LINES - 1)
        e = min(max(e, s), total, s + VIEW_UI_LINES - 1)
        if total and s > total: raise AgentError(f'{p.name} has only {total} lines.')
        chunk = lines[s - 1:e]; text = '\n'.join(chunk); cut = len(text) > VIEW_UI_CHARS
        if cut: text = text[:VIEW_UI_CHARS]; e = s + text.count('\n')
        try: rel = str(p.relative_to(self.workspace))
        except ValueError: rel = str(p)
        self._emit({'t': 'file', 'path': rel, 'abs': str(p), 'start': s, 'end': e, 'total': total, 'size': size, 'lang': projscan.lang_of(p), 'text': text,
                    'more': e < total or cut, 'desc': projscan.describe_file(p)['desc'] if s == 1 else ''})
        body, used = [], 0
        for i, l in enumerate(chunk[:e - s + 1], s):
            line = f'{i:>5}│ {l}'
            if used + len(line) > VIEW_MODEL_CHARS: break
            body.append(line); used += len(line) + 1
        shown_to = s + len(body) - 1
        nxt = f'\nMore: call view_file(path, start={shown_to + 1}) to continue ({total - shown_to} lines left).' if shown_to < total else ''
        return (f'OK (exit 0)\nShown in the chat: {rel} lines {s}-{e} of {total}. Line numbers below are display only, not part of the file.\n' + '\n'.join(body) + nxt)

    # ------------------------------------------------------------------------------------------ edit
    def tool_edit_file(self, path, old, new, replace_all=False):
        """Exact find-and-replace inside one existing file. `old` must be copied from the file (no line numbers) and be unique unless replace_all=true.
        To insert, make `old` one unique anchor line and put it back inside `new` with the added lines. Shows a diff for approval; verified after."""
        p = self.path(path)
        if self._secret(p): raise AgentError('Refusing to edit secrets/private-key files through the agent.')
        if not p.is_file(): raise AgentError(f'File not found: {p}. To create a new file use write_file.')
        if p.stat().st_size > MAX_EDIT_BYTES: raise AgentError(f'File is {human(p.stat().st_size)}; too large for edit_file.')
        old, new = '' if old is None else str(old), '' if new is None else str(new)
        if not old.strip('\n'): raise AgentError('edit_file needs a non-empty `old` string: the exact text to replace.')
        if old == new: raise AgentError('`old` and `new` are identical; nothing to change.')
        replace_all = str(replace_all).lower() in ('true', '1', 'yes')
        try: raw = p.read_bytes().decode('utf-8')
        except UnicodeDecodeError: raise AgentError(f'{p.name} is not valid UTF-8 text; edit_file will not touch it.')
        crlf = '\r\n' in raw; text = raw.replace('\r\n', '\n') if crlf else raw
        if crlf: old, new = old.replace('\r\n', '\n'), new.replace('\r\n', '\n')
        note, count = '', text.count(old)
        if count == 0:                                   # forgiving second try: same lines, different indentation / trailing spaces
            lines = text.split('\n'); spans = _fuzzy_span(lines, old)
            if len(spans) == 1:
                a, b = spans[0]; trail = old.endswith('\n'); old = '\n'.join(lines[a:b]); count = text.count(old)
                if trail: new = new.rstrip('\n')
                note = ' (matched ignoring whitespace differences)'
            elif len(spans) > 1: raise AgentError(f'`old` matches {len(spans)} places once whitespace is ignored (lines {", ".join(str(a + 1) for a, _ in spans[:6])}). Add more surrounding lines to make it unique.')
        if count == 0:
            first = next((l.strip() for l in old.split('\n') if l.strip()), ''); near = difflib.get_close_matches(first, [l.strip() for l in text.split('\n')], n=2, cutoff=0.55)
            hint = ('\nClosest lines in the file:\n' + '\n'.join(f'  {l}' for l in near)) if near else ''
            raise AgentError(f'`old` was not found in {p.name}. Call view_file to see the exact current text, then copy it exactly (without line numbers).{hint}')
        if count > 1 and not replace_all:
            at = [text.count('\n', 0, m.start()) + 1 for m in re.finditer(re.escape(old), text)][:8]
            raise AgentError(f'`old` appears {count} times in {p.name} (lines {", ".join(map(str, at))}). Include more surrounding lines so it is unique, or pass replace_all=true to change every one.')
        out = text.replace(old, new) if replace_all else text.replace(old, new, 1); done = count if replace_all else 1
        before_err, after_err = _syntax_error(p, text), _syntax_error(p, out)
        warn = f'{after_err}' if after_err and not before_err else ''
        diff = ''.join(difflib.unified_diff(text.splitlines(True), out.splitlines(True), f'a/{p.name}', f'b/{p.name}', n=2))
        added = sum(1 for l in diff.splitlines() if l.startswith('+') and not l.startswith('+++')); removed = sum(1 for l in diff.splitlines() if l.startswith('-') and not l.startswith('---'))
        card = f'Edit {p}: {done} change{"s" if done != 1 else ""}, +{added} -{removed} lines{note}\n' + (f'WARNING after this edit: {warn}\n' if warn else '') + diff[:1800]
        if not self.confirm(card): return 'Cancelled by the Boss. Nothing was changed.'
        snap = self.actions.snapshot(p); final = out.replace('\n', '\r\n') if crlf else out
        try: mode = p.stat().st_mode
        except OSError: mode = None
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix='.kage-edit-')
        try:
            with os.fdopen(fd, 'wb') as f: f.write(final.encode('utf-8'))
            if mode is not None: os.chmod(tmp, mode & 0o7777)
            os.replace(tmp, p)
        except OSError as e:
            try: os.unlink(tmp)
            except OSError: pass
            raise AgentError(f'Could not write {p}: {e}')
        check = p.read_bytes().decode('utf-8', 'replace'); ok = (check.replace('\r\n', '\n') if crlf else check) == out
        self._log('edit_file', f'Edited {p} ({done} change{"s" if done != 1 else ""}, +{added} -{removed})', {'kind': 'restore', 'path': str(p), 'snap': snap, 'after': sig(p)} if snap else None)
        try: rel = str(p.relative_to(self.workspace))
        except ValueError: rel = str(p)
        self._emit({'t': 'diff', 'path': rel, 'diff': diff[:20000], 'added': added, 'removed': removed, 'changes': done})
        if not ok: return f'NOT VERIFIED: {p} was written but reading it back does not match. Check it with view_file.'
        return (f'OK (exit 0)\nVERIFIED: edited {rel}: {done} change{"s" if done != 1 else ""}, +{added} -{removed} lines{note}. Undo with /undo.' + (f'\nWARNING: {warn}. View the file and fix it.' if warn else ''))
