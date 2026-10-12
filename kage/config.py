"""Paths, settings and small shared helpers. Standard library only."""
from __future__ import annotations
import json, os, threading
from pathlib import Path

def env(name: str, default=None):
    """Read KAGE_<name>, falling back to the old LOCALPILOT_<name>."""
    return os.environ.get('KAGE_' + name) or os.environ.get('LOCALPILOT_' + name) or default

HOME = Path.home()
DATA_DIR = Path(env('HOME_DIR') or os.environ.get('XDG_DATA_HOME') and Path(os.environ['XDG_DATA_HOME']) / 'kage' or HOME / '.local/share/kage')
CONFIG_DIR = Path(os.environ.get('XDG_CONFIG_HOME') or HOME / '.config') / 'kage'
CONFIG_FILE = CONFIG_DIR / 'config.json'
VAULT_DIR = Path(env('VAULT') or DATA_DIR / 'vault')
INDEX_DIR = DATA_DIR / 'index'

DEFAULTS = {
    'model': 'gemma4:e4b',
    'ollama_url': 'http://127.0.0.1:11434',
    'workspace': '',
    'recents': [],
    'system_access': False,
    'num_ctx': 8192,
    'keep_alive': '30m',
    'auto_approve': False,      # approve every card except HIGH RISK and folder-scope grants (persisted)
    'habits_history': True,   # mine shell history for command counts (never leaves the machine)
    'theme': 'ink',
}
_lock = threading.Lock()

def load() -> dict:
    try:
        data = json.loads(CONFIG_FILE.read_text())
        if isinstance(data, dict):
            return {**DEFAULTS, **data}
    except (OSError, ValueError):
        pass
    return dict(DEFAULTS)

def save(patch: dict) -> dict:
    with _lock:
        cfg = load(); cfg.update(patch)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        tmp = CONFIG_FILE.with_suffix('.tmp')
        tmp.write_text(json.dumps(cfg, indent=2))
        tmp.replace(CONFIG_FILE)
        return cfg

def add_recent(path: str) -> list:
    cfg = load()
    rec = [p for p in cfg.get('recents', []) if p != path]
    rec.insert(0, path)
    save({'recents': rec[:12], 'workspace': path})
    return rec[:12]

def short(path, n=48) -> str:
    p = str(path).replace(str(HOME), '~', 1)
    return p if len(p) <= n else '…' + p[-(n - 1):]
