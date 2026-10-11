"""Skills: short playbooks the agent loads on demand (ported from the rashin skill set)."""
from __future__ import annotations
import re
from pathlib import Path

DIR = Path(__file__).parent / 'skills'

def _meta(text):
    m = re.match(r'^---\n(.*?)\n---\n(.*)$', text, re.S)
    if not m: return {}, text
    meta = dict(l.split(':', 1) for l in m.group(1).splitlines() if ':' in l)
    return {k.strip(): v.strip() for k, v in meta.items()}, m.group(2)

def all_skills() -> list:
    out = []
    for p in sorted(DIR.glob('*.md')):
        meta, body = _meta(p.read_text())
        out.append({'name': meta.get('name', p.stem), 'description': meta.get('description', ''), 'body': body.strip()})
    return out

def get(name: str) -> str:
    for s in all_skills():
        if s['name'] == name.strip().lower(): return f"# skill: {s['name']}\n{s['body']}"
    return 'Unknown skill. Available: ' + ', '.join(s['name'] for s in all_skills())

def routing_table() -> str:
    return '\n'.join(f"- {s['name']}: {s['description']}" for s in all_skills())
