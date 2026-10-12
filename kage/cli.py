"""Command line: `kage` (terminal chat), `kage web`, `kage index`, `kage mcp`, `kage map`, `kage pick`."""
from __future__ import annotations
import argparse, json, os, shutil, subprocess, sys
from pathlib import Path
from . import APP, KANJI, VERSION, config, picker

DIM, RST, BONE, RED = '\033[2m', '\033[0m', '\033[38;5;254m', '\033[38;5;160m'

def resolve_workspace(arg, pick=False, interactive=True):
    if arg: return Path(arg).expanduser().resolve()
    if not pick:
        env = config.env('WORKSPACE')
        if env: return Path(env).expanduser().resolve()
    if interactive:
        chosen = (picker.native_pick() if not sys.stdin.isatty() else None) or picker.terminal_pick()
        if chosen: return Path(chosen).expanduser().resolve()
    last = config.load().get('workspace')
    return Path(last) if last and os.path.isdir(last) else Path.cwd()

def attach_terminal(agent):
    """Interactive programs run right here: Kage hands your terminal to the program and takes it back when it exits."""
    from . import term
    def handler(command, title):
        print(f"{DIM}  ▸ interactive session{': ' + command if command else ' (shell)'}. Exit it to return to Kage.{RST}", flush=True)
        code = term.run_inline(command, agent.workspace)
        print(f'\n{DIM}  ◂ back in Kage (exit {code}){RST}')
        if not command: return f'Shell closed (exit {code}).'
        return 'OK (exit 0)\nThe interactive session ran in the Boss\'s terminal and has ended.' if code == 0 else f'FAILED (exit {code})\nThe interactive session ended with a non-zero status.'
    agent.terminal_handler = handler

def terminal(agent, task_words):
    def emit(ev):
        t = ev.get('t')
        if t == 'step': print(f"{DIM}  ▸ {ev['tool']} {json.dumps(ev.get('args') or {}, ensure_ascii=False)[:100]}{RST}")
        elif t == 'result': print(f"{DIM}    {ev['text'].splitlines()[0][:110] if ev['text'] else ''} ({ev['ms']}ms){RST}")
        elif t == 'status': print(f"{DIM}  {ev['m']}{RST}")
        elif t == 'images':
            for it in ev['items'][:30]: print(f"{DIM}  ▣ {it['path']}{RST}")
            viewer = ['kitten', 'icat'] if shutil.which('kitten') and os.environ.get('KITTY_WINDOW_ID') else ['chafa', '-s', '48x20'] if shutil.which('chafa') else None
            if viewer and sys.stdout.isatty():
                for it in ev['items'][:6]: subprocess.run(viewer + [it['path']], check=False)
            elif ev['items']: print(f"{DIM}  (install chafa for inline previews, or use kage-web)  total {ev.get('total', len(ev['items']))}{RST}")
        elif t == 'project': print(ev['text'])
        elif t == 'files':
            print(f"{DIM}  {ev['base']}  ({len(ev['rows'])} of {ev['total']}){RST}")
            for r in ev['rows']: print(f"  {BONE}{r['path']:<34.34}{RST} {DIM}{(str(r['lines']) + ' ln') if r['lines'] else '':>8}  {r['desc'][:80]}{RST}")
        elif t == 'file':
            print(f"{DIM}  ── {ev['path']}  lines {ev['start']}-{ev['end']} of {ev['total']} ──{RST}")
            for i, l in enumerate(ev['text'].split('\n')[:120], ev['start']): print(f'{DIM}{i:>5}{RST} {l}')
            if ev['end'] - ev['start'] >= 120 or ev.get('more'): print(f"{DIM}  … /view {ev['path']} {min(ev['end'], ev['start'] + 120) + 1} for more{RST}")
        elif t == 'diff':
            for l in ev['diff'].split('\n')[:80]:
                if l.startswith(('---', '+++')): continue
                print(('\033[32m' if l.startswith('+') else '\033[31m' if l.startswith('-') else DIM) + l + RST)
        elif t == 'retract': emit.streamed = False; print(f"\n{DIM}  (retracted: that claim was not backed by a tool result){RST}")
        elif t == 'token': emit.streamed = True; sys.stdout.write(ev['d']); sys.stdout.flush()
        elif t == 'done':
            if not getattr(emit, 'streamed', False) or ev.get('kind') == 'cmd': print(ev['text'])
            else: print()
            s = ev.get('stats') or {}
            if s.get('ms'): print(f"{DIM}{s['ms']/1000:.1f}s" + (f" · {s['tps']} tok/s" if s.get('tps') else '') + RST)
            emit.streamed = False
    emit.streamed = False
    def go(task):
        try: agent.run(task, emit)
        except KeyboardInterrupt: agent.stop(); print('\nstopped')
    print(f'{RED}{KANJI}{RST} {BONE}{APP} {VERSION}{RST}  {DIM}model {agent.model} · {config.short(agent.workspace, 50)} · /help for commands · Ctrl+C to exit{RST}')
    if task_words: go(' '.join(task_words)); return
    while True:
        try: task = input(f'\n{RED}{KANJI}{RST} › ').strip()
        except (EOFError, KeyboardInterrupt): print('\nBye, Boss.'); break
        if task.lower() in {'exit', 'quit', 'q'}: break
        if task: go(task)

def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    sub = argv[0] if argv and argv[0] in {'web', 'index', 'mcp', 'map', 'pick', 'version', 'brief', 'log'} else None
    if sub: argv = argv[1:]
    ap = argparse.ArgumentParser(prog='kage' + (f' {sub}' if sub else ''), description=f'{APP} {KANJI}: a local-first agent for your machine')
    ap.add_argument('-w', '--workspace', help='folder to work in (omit to pick one)')
    ap.add_argument('--pick', action='store_true', help='always show the folder selector')
    ap.add_argument('--model'); ap.add_argument('--url', help='Ollama base URL'); ap.add_argument('--max-steps', type=int, default=30)
    ap.add_argument('--system-access', action='store_true', help='allow file tools outside the workspace')
    if sub == 'web':
        ap.add_argument('--host', default='127.0.0.1'); ap.add_argument('--port', type=int, default=3600); ap.add_argument('--open', action='store_true', help='open the browser')
    if sub == 'index': ap.add_argument('cmd'); ap.add_argument('arg', nargs='*'); ap.add_argument('--json', action='store_true')
    if sub not in ('web', 'index', 'mcp', 'map', 'pick', 'version', 'brief', 'log'): ap.add_argument('task', nargs='*')
    a = ap.parse_args(argv)
    if sub == 'version': print(f'{APP} {VERSION}'); return
    if sub == 'brief':      # instant morning briefing, no model: add `kage brief` to your shell startup if you like
        from . import briefing; from .vault import Vault; from types import SimpleNamespace
        print(briefing.build(SimpleNamespace(vault=Vault()))); return
    if sub == 'log':
        from .actions import ActionLog; import datetime as dt
        rows = ActionLog().recent(20); print('\n'.join(f"#{r['id']} {dt.datetime.fromtimestamp(r['ts']):%a %H:%M} {r['tool']}: {r['summary'][:110]}" + (' [undone]' if r['undone'] else ' [can undo]' if r['undoable'] else '') for r in rows) or 'Nothing logged yet.'); return
    if sub == 'pick': p = picker.native_pick() or picker.terminal_pick(); print(p or ''); return
    if sub == 'map':
        from .vault import Vault
        r = Vault().reindex(); print(f"Vault maps rebuilt in {r['ms']}ms at {config.VAULT_DIR}" + (f"\nwarnings: {r['errors']}" if r['errors'] else '')); return
    if sub == 'web':
        from . import web
        ws = Path(a.workspace).expanduser().resolve() if a.workspace else None
        if a.pick and not ws: p = picker.native_pick(); ws = Path(p) if p else None
        web.serve(a.host, a.port, ws, a.model, a.url, a.max_steps, a.system_access, a.open); return
    if sub == 'mcp':
        from . import mcp; mcp.serve(str(resolve_workspace(a.workspace, interactive=False))); return
    ws = resolve_workspace(a.workspace, a.pick, interactive=sub != 'index')
    if sub == 'index':
        from .index import Index
        r = Index(ws).run(a.cmd, ' '.join(a.arg))
        print(json.dumps(r, indent=1) if a.json else r['text']); return
    from .agent import Agent
    agent = Agent(ws, a.model, a.url, a.max_steps, a.system_access); agent.warm(); attach_terminal(agent)
    terminal(agent, a.task)

if __name__ == '__main__':
    try: main()
    except KeyboardInterrupt: print()
