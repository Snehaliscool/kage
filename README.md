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

Workspace-confined file tools, secret files never read, every consequential action asks (unless auto-approve is on; HIGH RISK and folder access always ask), overwrites show a diff in the approval card, command output is scrubbed of tokens and URL credentials. The server binds to loopback only and rejects foreign `Host` headers (DNS rebinding), foreign origins, and POSTs without the `X-Kage` header. Index and vault skip `.env`, keys and `.ssh`.

## Layout

```
kage/            agent.py projtools.py projscan.py gitops.py shell.py errors.py index.py vault.py knowledge.py skills.py picker.py web.py mcp.py cli.py  ui/ skills/
searxng/ docker-compose.yml start.sh stop.sh   private web search (127.0.0.1:8888)
tests/           python3 -m unittest discover -s tests
```
Settings: `~/.config/kage/config.json` (model, num_ctx, keep_alive, recents). Env: `KAGE_SEARXNG_URL`, `BRAVE_SEARCH_API_KEY`; old `LOCALPILOT_*` variables still work.

Requirements: Python 3.10+ (stdlib only), Ollama with a model (default `gemma4:e4b`; switch live from the header), Docker only for SearXNG.

## v1.2: terminals, big tasks, file reach, archives

**Interactive terminals.** Web: a *Terminal* tab with real PTY shells (vim, htop, ssh, `gh auth login`, REPLs work; resize, colours, scrollback, tabs, copy/paste). The built-in emulator has no dependencies, so it works offline under the strict CSP. `/term` opens one; when the agent needs an interactive program (`open_terminal`) the approved command opens in a tab instead of an external window. Terminal agent: `/sh [cmd]` or `open_terminal` hands your own terminal to the program and returns to Kage when it exits.

**Big tasks.** The loop trims old tool output and folds old steps so long chains fit the context, shows a `plan` checklist back to the model after every step, tolerates sloppy tool calls (aliases such as `zip`/`bash`, wrong argument names, arguments beside `"action"`), and defaults to 30 steps. `github_repos` + `github_describe_repos` do the "check all my repos and tell me what they do" fan-out: READMEs are fetched in parallel and each repo is summarised in one short model call, 10 per page. Tip: with big lists a larger `num_ctx` in `~/.config/kage/config.json` (e.g. 16384) helps if your GPU has room.

**Workspace is the base, the rest is one OK away.** Relative paths live in the workspace. Anything outside asks once per folder (`path="~"` is the whole home folder, `/` is flagged HIGH RISK) until `/new`; `/access system` allows everything for the session, `/access workspace` limits it again. `~/.ssh`, `~/.gnupg`, `~/.aws`, browser profiles and keyrings are never opened. `find_files(kind="images", path="~")` locates files; `show_images` puts a thumbnail grid in the chat (click for full size; Pillow, if installed, makes fast cached thumbnails). Only images the agent registered can be fetched from `/api/media`.

**Zip fix.** The `TypeError: ... missing 1 required positional argument: 'command'` came from `_call()` dropping a command the model named `cmd`/`script`/etc. or placed beside `"action"`. Arguments are now repaired, and a genuinely missing one gives a clear message instead of a TypeError. Zipping has a proper tool: `create_archive` (zip, tar.gz, tar.xz, tar.bz2; no `zip` binary needed; secrets such as `.env` and keys are skipped and reported; the result is read back and VERIFIED).

**Package pre-check.** Before `run_command` or `open_terminal` shows an approval card, the programs the command needs are looked up; a missing one fails fast with the Arch package name. Each program is looked up once per session (cached, cleared by `/new` or after `pacman_install`).

## v1.2.1: ask-why, undo, briefing

**Ask Kage why.** Shells opened in the web Terminal report each command's exit code (bash and zsh; your rc files are untouched, Kage sources them first). When a command fails, an *Ask Kage why (exit N)* button appears. It sends the command, folder and the last lines of output to the agent, which diagnoses and fixes it (the `fix-failure` skill). Ctrl+C and Ctrl+Z do not trigger it.

**Action log and undo.** Every file write, copy, move, extraction, archive and commit is logged; commands, pushes and installs are logged too. Reversible ones can be undone: `/undo` (last), `/undo 12`, the Activity tab, or just ask. Files are restored from a snapshot taken before the change (up to 50 MB); files Kage created are deleted only if unchanged since; moves go back only if the destination is still there; a commit is un-made only if it is still HEAD (changes stay staged). If you edited the file since, undo refuses rather than throw your work away. `/log` or `kage log` lists everything.

**Briefing.** `/brief` (web or terminal) or `kage brief` prints, instantly and without the model: uptime, load, memory, disk warnings, failed systemd units (system and user), pending updates (needs `pacman-contrib` for `checkupdates`), every git repo near home with uncommitted, unpushed or behind-remote work, and the last journal lines. Put `kage brief` in your shell startup if you like.

Tests: `python3 -m unittest discover -s tests` (the emulator tests run under node when it is installed).

## v1.3: describe a project, view and edit files

Kage can now say what the folder it is opened in is, what each file is for, show any file in the chat, and change one, all without leaving the conversation.

| Ask in plain words | Instant version (no model) | Tool the agent uses |
|---|---|---|
| *"what is this project?"*, *"describe this folder"* | `/describe [folder]` | `describe_project(path)` |
| *"what does each file do?"* | `/files [folder] [*.py]` | `describe_files(path, pattern, recursive, limit)` |
| *"show me agent.py"*, *"open lines 120-180"* | `/view path`, `/view path:120-180`, `/view path 120 180` | `view_file(path, start, end)` |
| *"rename X to Y in app.py"*, *"fix that typo"* | (agent only: it needs the model to choose the change) | `edit_file(path, old, new, replace_all)` |

**How the scan stays fast.** `kage/projscan.py` walks the folder once (skipping `.git`, `node_modules`, virtualenvs, build output and lock files; secrets such as `.env` and keys are counted but never opened), counts lines per language, finds manifests, entry points and the README, and reads one line of intent from each file: the Python module docstring, the leading comment of a shell/JS/CSS/Lua file, a Markdown heading or front-matter description, `package.json` `description`, an HTML `<title>`, Compose service names, Makefile targets. A 50-file project takes about 0.1 s; the result is cached by the tree's newest mtime, so asking again costs nothing and any edit triggers a fresh scan. The scan is time-boxed (about 6 s; a 30,000-file tree returns a partial result in roughly 7 s and says so). The model then writes the prose description from that dossier in one step instead of reading files one by one.

**What you see.** The web UI draws a project card (stack, language bar, README line, git branch and dirty state, folders, key files). Rows are clickable: a folder opens `/files`, a file opens `/view`. File views have line numbers, a copy button and *show more* paging (600 lines per page). Edits show a coloured diff afterwards. The terminal agent prints the same information as plain text. Images go to the existing preview grid.

**Edits are safe by construction.** `edit_file` is an exact find-and-replace: `old` must match once (otherwise it says where the other matches are, or offers the closest lines when nothing matches; a whitespace-only mismatch is forgiven when it is unambiguous). The approval card shows the diff and warns if the result would no longer parse (Python, JSON). The file is snapshotted first, written atomically, keeps its permissions and CRLF line endings, is read back and reported `VERIFIED`, and `/undo` restores it. Non-UTF-8 files and secrets are refused. The agent's "I did it" guard now also covers edit verbs, so it cannot claim an edit that did not happen.

MCP: `kage mcp` also serves `describe_project` and `describe_files` (read-only, confined to the workspace). New skill: `project-describe`. Tests: `tests/test_v13.py`.

## v1.4: auto-approve, Rashin skills in full, Ryoku finish

**Auto-approve.** The header switch (or `/auto on|off`) runs actions without an approval card, and the choice is saved in `~/.config/kage/config.json` (`"auto_approve"`). It covers file writes and edits, copies, moves, archives, commands, git commits and pushes, package and service actions, and opening apps or URLs. It never covers two things: **HIGH RISK** actions (sudo, `rm -r`, force-push, reset --hard, package removal, `curl | sh`) and **folder access outside the workspace**. Those always ask, so auto-approve cannot widen what Kage can touch. Refused commands stay refused. `/trust on` is the same switch for one session only. Auto-approved actions appear in the answer as an `AUTO` line and are still listed in `/log`, so `/undo` works on them.

**Skills from rashin.** `code-search`, `change-safety`, `durable-knowledge` and `scout` now carry Rashin's full procedure: the routing table, the rule to trust cited results instead of re-grepping, the try-another-strategy rule for empty searches, the read-only scout contract and its JSON result (`summary`, `files`, `architecture`). They are written against Kage's own tools (`search_code`, `impact`, `changed`, `wip`, `note_propose`, `notes`, and so on) rather than the `prowl-agent` CLI.

**Look and answers.** Section marks (`00 // LABEL`, numbered tabs and turns), tool calls as rows with `RUN`, `OK` and `FAIL` pills, approval cards with corner marks, and answers that render markdown tables, quotes and rules. Replies are answer-first and use no emoji. The HIGH RISK and edit-warning prefixes are plain text now.

Tests: `python3 -m unittest discover -s tests` (90 tests, including `tests/test_v14.py`).
