"""Action log + undo. Every consequential thing Kage does is written down, and where it can be reversed safely it is.

Undo never guesses: files are restored from a snapshot taken before the change, new files are deleted only if they have not
been modified since, moves go back only if the destination is still there, and a commit is un-made only if it is still HEAD.
Standard library only.
"""
from __future__ import annotations
import json, os, shutil, threading, time, uuid
from pathlib import Path
from . import config, shell
from .errors import AgentError

MAX_SNAP = 50 << 20      # files bigger than this are logged but not undoable
KEEP = 300               # log entries kept (older ones, and their snapshots, are pruned)

def sig(p) -> str:
    try: st = Path(p).stat(); return f'{st.st_size}:{st.st_mtime_ns}'
    except OSError: return ''

class ActionLog:
    def __init__(self, root=None):
        self.root = Path(root) if root else config.DATA_DIR / 'actions'
        self.file = self.root / 'log.jsonl'; self.snaps = self.root / 'snap'; self.lock = threading.Lock()

    # ---------------------------------------------------------------- storage
    def _load(self) -> list:
        try: return [json.loads(l) for l in self.file.read_text().splitlines() if l.strip()]
        except (OSError, ValueError): return []
    def _save(self, entries):
        self.root.mkdir(parents=True, exist_ok=True); tmp = self.file.with_suffix('.tmp')
        tmp.write_text('\n'.join(json.dumps(e) for e in entries) + '\n'); tmp.replace(self.file)

    def snapshot(self, path):
        """Copy a file aside before it is changed. -> snapshot path, or None (not a file / too big)."""
        p = Path(path)
        try:
            if not p.is_file() or p.stat().st_size > MAX_SNAP: return None
            d = self.snaps / uuid.uuid4().hex[:12]; d.mkdir(parents=True, exist_ok=True); dst = d / 'file'; shutil.copy2(p, dst); return str(dst)
        except OSError: return None

    def record(self, tool: str, summary: str, undo=None, workspace: str = '') -> dict:
        with self.lock:
            entries = self._load(); n = (entries[-1]['id'] if entries else 0) + 1
            e = {'id': n, 'ts': time.time(), 'tool': tool, 'summary': ' '.join(str(summary).split())[:300], 'ws': workspace, 'undo': undo, 'undone': False}
            entries.append(e)
            while len(entries) > KEEP:
                old = entries.pop(0); s = (old.get('undo') or {}).get('snap')
                if s: shutil.rmtree(Path(s).parent, ignore_errors=True)
            self._save(entries); return e

    def recent(self, n=20) -> list:
        return [self.public(e) for e in reversed(self._load()[-n:])]

    @staticmethod
    def public(e: dict) -> dict:
        return {'id': e['id'], 'ts': e['ts'], 'tool': e['tool'], 'summary': e['summary'], 'undoable': bool(e.get('undo')) and not e.get('undone'), 'undone': e.get('undone', False), 'ws': e.get('ws', '')}

    # ---------------------------------------------------------------- undo
    def find(self, entry_id=None):
        entries = self._load()
        if entry_id in (None, '', 0):
            for e in reversed(entries):
                if e.get('undo') and not e.get('undone'): return e
            raise AgentError('Nothing to undo: no recent action can be reversed.')
        for e in entries:
            if e['id'] == int(entry_id): return e
        raise AgentError(f'No action #{entry_id} in the log.')

    @staticmethod
    def describe(e: dict) -> str:
        u = e.get('undo') or {}; k = u.get('kind')
        return {'restore': f"restore the previous contents of {u.get('path')}", 'delete': f"delete {u.get('path')} (it did not exist before)",
                'delete_many': f"delete the {len(u.get('paths', []))} file(s) that extraction created", 'move_back': f"move {u.get('dst')} back to {u.get('src')}",
                'git_reset': f"un-commit in {u.get('repo')} (your changes stay, staged)"}.get(k, 'not reversible')

    def undo(self, entry_id=None, force=False) -> str:
        e = self.find(entry_id); u = e.get('undo')
        if e.get('undone'): raise AgentError(f"Action #{e['id']} was already undone.")
        if not u: raise AgentError(f"Action #{e['id']} ({e['tool']}) cannot be reversed automatically: {e['summary']}")
        k = u['kind']
        if k in ('restore', 'delete') and not force and u.get('after') and sig(u['path']) != u['after']:
            raise AgentError(f"{u['path']} has changed since Kage touched it, so undoing would throw away newer work. Nothing was changed. (Ask again with force to override.)")
        if k == 'restore':
            snap = Path(u['snap'])
            if not snap.is_file(): raise AgentError('The snapshot is gone; cannot restore.')
            Path(u['path']).parent.mkdir(parents=True, exist_ok=True); shutil.copy2(snap, u['path'])
            if Path(u['path']).read_bytes() != snap.read_bytes(): raise AgentError('NOT VERIFIED: restored file does not match the snapshot.')
        elif k == 'delete':
            p = Path(u['path'])
            if p.is_file(): p.unlink()
            elif p.exists(): raise AgentError(f'{p} is not a plain file any more; leaving it alone.')
        elif k == 'delete_many':
            for f in u['paths']:
                p = Path(f)
                if p.is_file() or p.is_symlink(): p.unlink()
            for d in sorted(u.get('dirs', []), key=lambda x: -len(x)):
                try: Path(d).rmdir()
                except OSError: pass
        elif k == 'move_back':
            src, dst = Path(u['src']), Path(u['dst'])
            if not dst.exists(): raise AgentError(f'{dst} is no longer there, so the move cannot be reversed.')
            if src.exists(): raise AgentError(f'{src} exists again; refusing to overwrite it.')
            src.parent.mkdir(parents=True, exist_ok=True); shutil.move(str(dst), str(src))
        elif k == 'git_reset':
            c, head = shell.execute(['git', '-C', u['repo'], 'rev-parse', 'HEAD'], timeout=20)
            if c or head.strip() != u['sha']: raise AgentError('That commit is no longer the latest one in the repo, so it cannot be safely un-made.')
            args = ['update-ref', '-d', 'HEAD'] if u.get('root') else ['reset', '--soft', 'HEAD~1']
            c, out = shell.execute(['git', '-C', u['repo'], *args], timeout=30)
            if c: raise AgentError(out.strip()[:300])
        else: raise AgentError('Unknown undo type.')
        with self.lock:
            entries = self._load()
            for x in entries:
                if x['id'] == e['id']: x['undone'] = True
            self._save(entries)
        return f"UNDONE #{e['id']}: {self.describe(e)}."
