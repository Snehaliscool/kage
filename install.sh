#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP="$HOME/.local/share/kage/app"; BIN="$HOME/.local/bin"
mkdir -p "$BIN" "$APP" "$HOME/.local/share/applications"
rm -rf "$APP/kage"; cp -r "$ROOT/kage" "$APP/kage"; find "$APP" -name __pycache__ -prune -exec rm -rf {} +
launcher() { cat > "$BIN/$1" <<L
#!/usr/bin/env bash
export PYTHONPATH="$APP\${PYTHONPATH:+:\$PYTHONPATH}" KAGE_SEARXNG_URL="\${KAGE_SEARXNG_URL:-\${LOCALPILOT_SEARXNG_URL:-http://127.0.0.1:8888}}"
exec /usr/bin/env python3 -m kage $2 "\$@"
L
chmod 755 "$BIN/$1"; }
launcher kage ""; launcher kage-web "web --open"
cat > "$HOME/.local/share/applications/kage.desktop" <<D
[Desktop Entry]
Name=Kage
GenericName=Local AI agent
Comment=Local-first agent: code index, memory, and your machine
Exec=$BIN/kage-web
Icon=utilities-terminal
Terminal=false
Type=Application
Categories=Utility;Development;
D
for old in localpilot localpilot-web; do [ -e "$BIN/$old" ] && echo "note: old launcher $BIN/$old is no longer needed (rm it)"; done
echo "Installed Kage to $APP"
echo "  kage            terminal agent (shows a folder selector)"
echo "  kage-web        browser UI at http://127.0.0.1:3600/ (folder selector built in)"
echo "  kage index overview -w <dir>   instant code intelligence, no model"
echo "  kage mcp -w <dir>              MCP server for Claude Code and friends"
echo "Make sure $BIN is on PATH. Start search with ./start.sh in this folder."
