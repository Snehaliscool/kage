# Kage 影

A fast, local-first agent for your Arch machine. It used to be LocalPilot; it is now **Kage** ("shadow"): it follows you into whatever folder you choose, reads code from a cited index, remembers what matters, and acts on the machine only after you approve.

The browser UI follows the [ryoku.dev](https://ryoku.dev) look (bone on ink, dithered art, `00 // LABEL` marks, wallpaper-driven accent from `~/.cache/ryoku/colors.json`) and replaces Rashin's dashboard on port 3600.

## Quick start

```bash
./start.sh          # optional: private SearXNG for free web search (needs Docker)
./install.sh        # installs `kage` and `kage-web` into ~/.local/bin
kage-web            # opens http://127.0.0.1:3600/  and shows the FOLDER SELECTOR
```

No path typing anywhere. The selector offers Resume, Recent, common places and detected git projects, with breadcrumbs, filter, hidden-folder toggle, and the system file dialog (zenity/kdialog/yad) if you have one. `Ctrl+O` re-opens it any time. Approval cards answer to a single keypress: **Y** approves, **N** or **Esc** denies (the terminal agent works the same, no Enter needed). In the terminal, `kage` shows an `fzf` picker (or a numbered menu). Pass `-w DIR` only if you want to skip it.

Run without installing: `./kage.sh` (terminal) or `./start-web.sh` (browser). If Rashin owns port 3600, stop it first (`ss -ltnp 'sport = :3600'`) or use `kage web --port 3601`.

## What came from rashin

| Rashin / Prowl idea | In Kage |
|---|---|
| `prowl-agent` code index (`search find def outline references impact callers peek wip changed doctor overview`) | `kage/index.py`: SQLite + FTS5, symbols for 15 languages (Python via `ast`), dependency graph, incremental refresh by mtime. Works on QML, JS, Lua, shell, Hyprland `source =` lines, Python, Go, Rust, C. A 889-file / 165k-line tree indexes cold in about 3 s; queries answer in 1-40 ms |
| `code-search`, `change-safety`, `durable-knowledge`, `scout` skills | `kage/skills/*.md`, listed in the system prompt, loaded on demand with `skill(name)` |
| Durable knowledge (`propose -> diff -> accept`, stale detection) | `kage/knowledge.py`: notes anchored to `path:lines`; flagged STALE when that code changes |
| `scout` read-only sub-agent | `scout(question)` tool and `/scout`: a short loop restricted to index tools |
| Vault: `AGENTS.md`, `memory/`, `journal/`, generated `system/desktop/packages/user/habits.md` | `kage/vault.py` at `~/.local/share/kage/vault`, with `kage:generated` markers so your own prose survives regeneration. Every finished task is journaled automatically |
| `habits.md` from shell history | Counts only, never leaves the machine. Disable with `"habits_history": false` in `~/.config/kage/config.json` |
| Prowl MCP server | `kage mcp -w DIR`: the same index, memory and notes for Claude Code, opencode and others |

MCP config example:
```json
{"mcpServers":{"kage":{"command":"kage","args":["mcp","-w","/path/to/project"]}}}
```

## Speed

- Model stays loaded (`keep_alive` 30m) and is pre-warmed at startup; context is set explicitly (`num_ctx` 8192).
- Output is JSON-constrained and the answer **streams** token by token; tool steps appear live (no 650 ms approval polling any more; approvals are pushed over the same stream).
- **Slash commands never touch the model**: `/search`, `/find`, `/def`, `/outline`, `/refs`, `/impact`, `/callers`, `/peek`, `/overview`, `/wip`, `/changed`, `/doctor`, `/github`, `/trust`, `/remember`, `/recall`, `/notes`, `/map`. Type `/` in the UI for a menu.
- `web_research` searches and reads the top three pages in parallel in one step instead of four round trips. Searches are cached for 5 minutes.
- History is compacted only when it nears the context limit, so Ollama's prompt cache stays warm between turns.
- `bash -c` instead of a login shell for commands.

## Git and GitHub (v1.1)

Ask in plain words: *"configure git"*, *"push this folder to my GitHub repo"*, *"create a private repo called X and push"*. Kage runs `github_status` first (identity, `gh` login, SSH, remote, ahead/behind), then does only what is missing:

| Tool | What it does | How it proves it worked |
|---|---|---|
| `github_status` / `/github` | readiness check with the exact NEXT step | reads real state |
| `git_setup(name,email)` | global identity, default branch, `push.autoSetupRemote` | reads the config back |
| `github_login(method)` | opens a real terminal for `gh auth login` (browser code flow, then `gh auth setup-git`) or SSH key setup. No token is ever typed into chat | `github_status` afterwards |
| `git_remote(url)` / `github_create_repo(name,private)` | set or create `origin` | `ls-remote` reachability |
| `git_commit(message)` | stages and commits; **refuses `.env`, keys, tokens and `node_modules`** | HEAD moved, tree state |
| `git_push()` | pushes with upstream; force needs a HIGH RISK approval | `ls-remote` SHA equals local HEAD, plus the repo link |
| `git(args)` | any other git command (clone, pull, checkout, merge, stash...) | exit code + hint |

Everything runs with prompts disabled (`GIT_TERMINAL_PROMPT=0`, SSH `BatchMode`, stdin closed), so nothing can hang waiting for a password. Failures come back with a `HINT:` (identity missing, auth, rejected non-fast-forward, private-email block GH007, protected branch, 100 MB limit...) so the model fixes the cause instead of guessing. The `github` skill holds the recipe.

First-time sign-in: say *"push my code to GitHub"*; Kage opens a terminal window, you approve in the browser, then say *done*. Needs `github-cli` (`sudo pacman -S github-cli`) for the browser flow, or use `github_login(method="ssh")`.

## Run anything

`run_command` now runs any shell command: pipes, `&&`, redirects, scripts, `timeout` up to 30 min. Policy:

- **Read-only** (`git status`, `ls`, `gh auth status`...): runs without a card. Turn off with `"auto_approve_readonly": false`.
- **Everything else**: approval card showing the exact command.
- **HIGH RISK** (sudo, `rm -r`, force-push, `reset --hard`, shutdown, `curl | sh`, package removal...): flagged on the card and never auto-approved.
- **Refused outright**: wiping the system or home, formatting disks, fork bombs, and anything that would print private keys, tokens, `.env` files or all environment variables.
- Interactive programs or anything needing a password (`sudo`, `gh auth login`, editors) go through `open_terminal`, which opens a real terminal window.
- `/trust on` auto-approves normal commands and git actions for the current session (HIGH RISK still asks; `/new` or `/trust off` ends it).

## Why it no longer "pretends"

Three guards in the agent loop, all covered by tests: (1) if the model says it configured/pushed/installed something but no action tool succeeded this turn, the message is retracted from the UI and the model is told to run the tool, or admit it did not; (2) asking permission in chat ("Shall I proceed?") is rejected, because the tool's own approval card is the permission step; (3) asking for a password, token or passphrase is rejected and redirected to a real terminal sign-in. Tool results start with `OK (exit 0)`, `FAILED`, `TIMED OUT`, `VERIFIED` or `NOT VERIFIED`, which is what the model is told to trust.

## Safety

Workspace-confined file tools, secret files never read, every consequential action asks, overwrites show a diff in the approval card, command output is scrubbed of tokens and URL credentials. The server binds to loopback only and rejects foreign `Host` headers (DNS rebinding), foreign origins, and POSTs without the `X-Kage` header. Index and vault skip `.env`, keys and `.ssh`.

## Layout

```
kage/            agent.py gitops.py shell.py errors.py index.py vault.py knowledge.py skills.py picker.py web.py mcp.py cli.py  ui/ skills/
searxng/ docker-compose.yml start.sh stop.sh   private web search (127.0.0.1:8888)
tests/           python3 -m unittest discover -s tests
```
Settings: `~/.config/kage/config.json` (model, num_ctx, keep_alive, recents). Env: `KAGE_SEARXNG_URL`, `BRAVE_SEARCH_API_KEY`; old `LOCALPILOT_*` variables still work.

Requirements: Python 3.10+ (stdlib only), Ollama with a model (default `gemma4:e4b`; switch live from the header), Docker only for SearXNG.
