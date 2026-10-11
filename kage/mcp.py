"""Minimal MCP server over stdio: exposes the code index, memory and notes to any MCP client
(Claude Code, opencode, ...). `kage mcp --workspace /path/to/project`"""
from __future__ import annotations
import json, sys
from pathlib import Path
from . import APP, VERSION
from .index import Index
from .knowledge import Knowledge
from .vault import Vault

S = lambda desc, **props: {'description': desc, 'schema': {'type': 'object', 'properties': {k: {'type': 'string', 'description': v} for k, v in props.items()}, 'required': list(props)}}
TOOLS = {
    'overview': S('Map the project: languages, layout, entrypoints, most-depended-on files.'),
    'search_code': S('Find code by concept or keywords. Ranked, cited as path:line.', query='keywords or a question'),
    'find_symbol': S('Locate a function, class, component or setting by name.', name='symbol name'),
    'read_symbol': S('Read one symbol definition instead of the whole file.', name='symbol name, path::name or #id'),
    'outline': S("A file's symbols and signatures, no bodies.", path='repo-relative path'),
    'references': S('Every use of an identifier, with the definition first.', name='identifier'),
    'impact': S('Blast radius: what depends on this file, by subsystem, with a risk level.', path='repo-relative path'),
    'callers': S('What includes, imports, sources or mentions this file.', path='repo-relative path'),
    'peek': S('Read a cited line range.', ref='path:start-end'),
    'wip': S('Uncommitted work, TODO/FIXME markers and their blast radius.'),
    'changed': S('Map git changes to the files they could affect.'),
    'doctor': S('Structural health: cycles, dangling references, fan-in risk.'),
    'recall': S('Read durable memory.', query='optional filter'),
    'remember': S('Save a durable fact to memory.', note='the fact'),
    'notes': S('List reviewed knowledge notes and their health.'),
}
IX = {'overview': 'overview', 'search_code': 'search', 'find_symbol': 'find', 'read_symbol': 'def', 'outline': 'outline', 'references': 'refs', 'impact': 'impact',
      'callers': 'callers', 'peek': 'peek', 'wip': 'wip', 'changed': 'changed', 'doctor': 'doctor'}

def serve(workspace: str):
    ws = Path(workspace).expanduser().resolve(); ix = Index(ws); kn = Knowledge(Vault().root, ws); vault = Vault()
    def call(name, args):
        a = next(iter(args.values()), '') if args else ''
        if name in IX: return ix.run(IX[name], str(a))['text']
        if name == 'recall': return vault.memory_read(str(a))
        if name == 'remember': return vault.memory_add(str(a))
        if name == 'notes': return kn.list() + '\n' + kn.lint()
        raise ValueError(f'Unknown tool {name}')
    def send(obj): sys.stdout.write(json.dumps(obj) + '\n'); sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line: continue
        try: msg = json.loads(line)
        except ValueError: continue
        mid, method, params = msg.get('id'), msg.get('method'), msg.get('params') or {}
        if mid is None: continue   # notifications need no reply
        if method == 'initialize':
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'protocolVersion': params.get('protocolVersion', '2024-11-05'), 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'kage', 'version': VERSION}}})
        elif method == 'tools/list':
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'tools': [{'name': n, 'description': t['description'], 'inputSchema': t['schema']} for n, t in TOOLS.items()]}})
        elif method == 'tools/call':
            try: text, err = call(params.get('name'), params.get('arguments') or {}), False
            except Exception as e: text, err = f'{type(e).__name__}: {e}', True
            send({'jsonrpc': '2.0', 'id': mid, 'result': {'content': [{'type': 'text', 'text': text}], 'isError': err}})
        elif method == 'ping': send({'jsonrpc': '2.0', 'id': mid, 'result': {}})
        else: send({'jsonrpc': '2.0', 'id': mid, 'error': {'code': -32601, 'message': f'Method not found: {method}'}})
