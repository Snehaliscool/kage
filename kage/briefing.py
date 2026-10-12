"""Morning briefing: one instant, model-free report of what needs you. Standard library only.

Machine (uptime, load, memory, disk), failed services, pending updates, git repos with uncommitted or unpushed work,
and what was worked on recently. Everything is read-only; nothing is fetched from the network except `checkupdates`
(which syncs a temporary pacman database) when pacman-contrib is installed.
"""
from __future__ import annotations
import concurrent.futures as cf, datetime as dt, os, re, shutil, time
from pathlib import Path
from . import config, picker, shell

def _run(argv, timeout=15):
    try: return shell.execute(argv, timeout=timeout)
    except Exception as e: return 1, str(e)

def _gb(n) -> str: return f'{n / 2**30:.1f}'

def system_lines() -> list:
    out, warn = [], []
    try: secs = float(Path('/proc/uptime').read_text().split()[0]); d, rem = divmod(int(secs), 86400); out.append(f'up {d}d {rem // 3600}h' if d else f'up {rem // 3600}h {rem % 3600 // 60}m')
    except (OSError, ValueError): pass
    try: out.append('load ' + ' '.join(f'{x:.1f}' for x in os.getloadavg()))
    except OSError: pass
    try:
        mem = {k: int(v.split()[0]) for k, v in (l.split(':', 1) for l in Path('/proc/meminfo').read_text().splitlines() if ':' in l)}
        avail, total = mem['MemAvailable'] / 1048576, mem['MemTotal'] / 1048576; out.append(f'mem {avail:.1f}/{total:.1f} GB free')
        if avail / total < 0.1: warn.append('memory is nearly full')
    except (OSError, KeyError, ValueError): pass
    seen = set()
    for label, path in (('/', '/'), ('home', str(config.HOME))):
        try:
            st = os.stat(path).st_dev
            if st in seen: continue
            seen.add(st); u = shutil.disk_usage(path); pct = u.used * 100 // u.total; out.append(f'{label} {pct}% used ({_gb(u.free)} GB free)')
            if pct >= 90: warn.append(f'{label} is {pct}% full')
        except OSError: pass
    return ['SYSTEM  ' + ' · '.join(out)] + [f'  ! {w}' for w in warn]

def service_lines() -> list:
    if not shutil.which('systemctl'): return []
    failed = []
    for scope in ([], ['--user']):
        c, o = _run(['systemctl', *scope, '--failed', '--no-legend', '--plain'], 10)
        if c == 0: failed += [l.split()[0] + (' (user)' if scope else '') for l in o.splitlines() if l.strip() and not l.startswith(('Failed', 'Unit', 'Warning'))]
    return ['SERVICES  ' + (f'{len(failed)} failed: ' + ', '.join(failed[:6]) + (' …' if len(failed) > 6 else '') if failed else 'all healthy')]

def update_line() -> list:
    if not shutil.which('checkupdates'): return ['UPDATES  (install pacman-contrib to see pending updates)'] if shutil.which('pacman') else []
    c, o = _run(['checkupdates'], 90)
    if c == 2: return ['UPDATES  up to date']
    if c != 0: return ['UPDATES  could not check: ' + o.strip()[:80]]
    pk = [l.split()[0] for l in o.splitlines() if l.strip()]
    return [f'UPDATES  {len(pk)} pending' + (' (' + ', '.join(pk[:5]) + (' …' if len(pk) > 5 else '') + ')' if pk else '')]

def repo_state(path: str) -> dict:
    c, o = _run(['git', '-C', path, 'status', '--porcelain=v1', '-b', '--untracked-files=normal'], 12)
    if c: return {'path': path, 'error': True}
    lines = o.splitlines(); head = lines[0] if lines else ''; dirty = len([l for l in lines[1:] if l.strip()])
    ahead = int(m.group(1)) if (m := re.search(r'ahead (\d+)', head)) else 0; behind = int(m.group(1)) if (m := re.search(r'behind (\d+)', head)) else 0
    no_up = '...' not in head and 'No commits yet' not in head and head.startswith('## ')
    c2, when = _run(['git', '-C', path, 'log', '-1', '--format=%cr'], 8)
    return {'path': path, 'name': Path(path).name, 'dirty': dirty, 'ahead': ahead, 'behind': behind, 'no_upstream': no_up, 'last': when.strip() if c2 == 0 else '', 'error': False}

def repo_lines(limit=14) -> tuple:
    found = [r['path'] for r in picker.repos(80) if 'git' in r.get('badges', [])]
    if not found: return ['REPOS  no git repositories found near your home folder'], []
    with cf.ThreadPoolExecutor(8) as ex: states = [s for s in ex.map(repo_state, found) if not s['error']]
    need = [s for s in states if s['dirty'] or s['ahead'] or s['behind'] or s['no_upstream']]
    need.sort(key=lambda s: (-(s['dirty'] > 0) - (s['ahead'] > 0), s['name'].lower()))
    rows = []
    for s in need[:limit]:
        bits = [f"{s['dirty']} uncommitted" if s['dirty'] else '', f"{s['ahead']} unpushed" if s['ahead'] else '', f"{s['behind']} behind remote" if s['behind'] else '', 'no upstream branch' if s['no_upstream'] and not s['ahead'] else '']
        rows.append(f"  {s['name'][:24]:<24} {' · '.join(b for b in bits if b)}" + (f"  (last commit {s['last']})" if s['last'] else ''))
    if len(need) > limit: rows.append(f'  …and {len(need) - limit} more')
    head = f"REPOS  {len(states)} checked · " + (f'{len(need)} need attention' if need else 'all clean and pushed')
    return [head] + rows, need

def recent_lines(vault) -> list:
    out = []
    for back in (0, 1):
        f = vault.root / 'journal' / f'{dt.date.today() - dt.timedelta(days=back)}.md'
        try: lines = [l[2:] for l in f.read_text().splitlines() if l.startswith('- ')][-3:]
        except OSError: continue
        if lines: out += [f"  {'today' if not back else 'yesterday'}: {l[:130]}" for l in lines]
    return ['RECENTLY'] + out if out else []

def build(agent=None) -> str:
    t0 = time.time(); now = dt.datetime.now(); hello = 'Good morning' if now.hour < 12 else 'Good afternoon' if now.hour < 18 else 'Good evening'
    with cf.ThreadPoolExecutor(3) as ex:
        upd = ex.submit(update_line); repos = ex.submit(repo_lines); svc = ex.submit(service_lines)
        sysl = system_lines(); rl, need = repos.result(); sv = svc.result(); ul = upd.result()
    lines = [f'{hello}, Boss. {now:%A %d %B %Y, %H:%M}', ''] + sysl + sv + ul + rl
    if agent is not None and getattr(agent, 'vault', None): lines += recent_lines(agent.vault)
    todo = [f'say "commit and push {s["name"]}"' for s in need[:2] if s['dirty'] or s['ahead']]
    if todo: lines += ['', 'NEXT  ' + ' · '.join(todo)]
    return '\n'.join(lines) + f'\n\n({time.time() - t0:.1f}s, read-only)'
