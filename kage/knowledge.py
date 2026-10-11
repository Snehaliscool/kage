"""Durable knowledge: reviewed notes anchored to real code (the prowl-agent `knowledge` idea).

propose -> shows a diff, nothing is stored; accept -> writes portable Markdown;
lint -> flags notes whose anchored code has changed since they were accepted.
"""
from __future__ import annotations
import datetime as dt, difflib, hashlib, json, re, time, uuid
from pathlib import Path
from . import config

KINDS = {'decision': 'decisions', 'concept': 'concepts', 'gotcha': 'gotchas', 'playbook': 'playbooks', 'claim': 'claims'}

def slugify(s): return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')[:60] or 'note'

class Knowledge:
    def __init__(self, vault_root: Path, workspace: Path):
        self.ws = Path(workspace).resolve()
        self.dir = Path(vault_root) / 'knowledge' / f'{slugify(self.ws.name)}-{hashlib.sha1(str(self.ws).encode()).hexdigest()[:6]}'
        self.props = self.dir / '.proposals'

    def init(self) -> str:
        for d in KINDS.values(): (self.dir / d).mkdir(parents=True, exist_ok=True)
        self.props.mkdir(parents=True, exist_ok=True)
        return f'Knowledge store ready at {self.dir}'

    def _region_hash(self, anchor: str):
        m = re.match(r'^(.*?):(\d+)(?:-(\d+))?$', anchor.strip())
        if not m: return None
        p = (self.ws / m.group(1)).resolve()
        if self.ws not in p.parents or not p.is_file(): return None
        a = int(m.group(2)); b = int(m.group(3) or a)
        lines = p.read_text(errors='replace').split('\n')[a - 1:b]
        return hashlib.sha1('\n'.join(l.strip() for l in lines).encode()).hexdigest()[:10]

    def propose(self, title: str, kind: str, body: str, anchors=None, target: str = '') -> str:
        self.init()
        kind = kind if kind in KINDS else 'concept'
        target = target or f'{KINDS[kind]}/{slugify(title)}.md'
        if '..' in target or target.startswith('/'): raise ValueError('Invalid target path.')
        content = self._render(title, kind, body, anchors or [])
        old = (self.dir / target).read_text() if (self.dir / target).exists() else ''
        diff = ''.join(difflib.unified_diff(old.splitlines(True), content.splitlines(True), 'current', 'proposed')) or '(no change)'
        pid = uuid.uuid4().hex[:8]
        (self.props / f'{pid}.json').write_text(json.dumps({'target': target, 'content': content, 'title': title, 'at': time.time()}))
        return f'Proposal {pid} for {target}\n{diff}\nAccept with: note_accept {pid}'

    def _render(self, title, kind, body, anchors):
        fm = ['---', f'title: {title}', f'type: {kind}', f'created: {dt.date.today()}']
        if anchors:
            fm.append('anchors:')
            for a in anchors:
                h = self._region_hash(a)
                fm += [f'  - path: {a}', f'    hash: {h or "none"}']
        fm.append('---')
        return '\n'.join(fm) + f'\n\n{body.strip()}\n'

    def accept(self, pid: str) -> str:
        f = self.props / f'{re.sub(r"[^a-f0-9]", "", pid)}.json'
        if not f.exists(): raise ValueError(f'No proposal {pid}.')
        d = json.loads(f.read_text()); t = self.dir / d['target']
        t.parent.mkdir(parents=True, exist_ok=True); t.write_text(d['content']); f.unlink()
        return f'Accepted: {t}'

    @staticmethod
    def parse(text: str) -> tuple:
        m = re.match(r'^---\n(.*?)\n---\n(.*)$', text, re.S)
        if not m: return {}, text
        meta, anchors, cur = {}, [], None
        for l in m.group(1).splitlines():
            if l.startswith('  - path:'): cur = {'path': l[len('  - path:'):].strip()}; anchors.append(cur)
            elif l.startswith('    hash:') and cur is not None: cur['hash'] = l.split(':', 1)[1].strip()
            elif ':' in l and not l.startswith(' '): k, v = l.split(':', 1); meta[k.strip()] = v.strip()
        meta['anchors'] = anchors
        return meta, m.group(2)

    def notes(self) -> list:
        out = []
        if not self.dir.exists(): return out
        for p in sorted(self.dir.rglob('*.md')):
            if '.proposals' in p.parts: continue
            meta, body = self.parse(p.read_text(errors='replace'))
            stale = [a['path'] for a in meta.get('anchors', []) if a.get('hash') not in (None, 'none') and self._region_hash(a['path']) != a['hash']]
            out.append({'path': p.relative_to(self.dir).as_posix(), 'title': meta.get('title', p.stem), 'type': meta.get('type', '?'), 'stale': stale, 'body': body.strip()})
        return out

    def list(self) -> str:
        n = self.notes()
        if not n: return 'No accepted notes yet. Use note_propose after resolving something that cost real effort.'
        pend = len(list(self.props.glob('*.json'))) if self.props.exists() else 0
        return f'{len(n)} notes' + (f', {pend} proposals pending' if pend else '') + '\n' + '\n'.join(f"- [{x['type']}] {x['title']} ({x['path']})" + (' STALE: re-verify ' + ', '.join(x['stale']) if x['stale'] else '') for x in n)

    def lint(self) -> str:
        n = self.notes(); issues = []
        titles = {}
        for x in n:
            if x['stale']: issues.append(f"stale: {x['path']} — anchored code changed: {', '.join(x['stale'])}")
            if x['type'] not in KINDS: issues.append(f"type: {x['path']} has unknown type {x['type']!r}")
            if len(x['body']) < 20: issues.append(f"thin: {x['path']} has almost no content")
            if x['title'].lower() in titles: issues.append(f"duplicate title: {x['path']} and {titles[x['title'].lower()]}")
            titles[x['title'].lower()] = x['path']
        return f'{len(n)} notes checked · ' + ('all healthy' if not issues else f'{len(issues)} issues') + ''.join('\n  ' + i for i in issues)

    def search(self, query: str, limit=3) -> list:
        ws = [w for w in re.findall(r'\w{3,}', query.lower())]
        if not ws: return []
        scored = []
        for x in self.notes():
            hay = (x['title'] + ' ' + x['body']).lower()
            s = sum(hay.count(w) for w in ws) + 3 * sum(w in x['title'].lower() for w in ws)
            if s: scored.append((s, x))
        scored.sort(key=lambda t: -t[0])
        return [x for _, x in scored[:limit]]
