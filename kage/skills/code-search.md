---
name: code-search
description: MUST be the first step for any question about the workspace's code: locating a feature, concept, symbol, setting or keybind; reading a definition; tracing callers or references; mapping a file's structure or the project's architecture; estimating what a change touches. Answers from a cited index in milliseconds instead of grepping and reading many files. Grep is only for exact literal or regex text.
---
# Code search

Answer repository questions from Kage's cited index before reading or grepping files. Every index result is cited as `path:line`. The index re-scans changed files before each answer, so results are never stale.

## Routing table

| Question | First tool |
|---|---|
| Map an unfamiliar repository | `overview()` |
| Locate a feature or concept | `search_code(query)` |
| Locate a named symbol | `find_symbol(name)` |
| Read one symbol's definition | `read_symbol(name)` |
| Inspect one file's structure | `outline(path)` |
| Trace who uses a symbol | `references(name)` |
| Estimate a change's blast radius | `impact(path)` |
| Find what includes or imports a file | `callers(path)` |
| Read a located line range | `peek("path:start-end")` |
| Resume or inspect current changes | `wip()` / `changed()` |
| Find an exact literal or regex | `search_files(query, path)` in a bounded scope |
| List filenames or a folder | `list_files(path)` / `find_files(...)` |

For any semantic or structural question, the first discovery step is an index tool, not a grep of the whole tree.

## Read only what you need

Once the index has located the code, keep every follow-up read bounded and cited:

- `read_symbol` returns one symbol's source, not the whole file.
- `outline` lists a file's symbols and signatures with no bodies.
- `peek` turns a citation into just those lines.

## Rules

- Trust the cited results. Do not re-verify them with a tree-wide grep or by reading whole files, and do not launch a second search just to confirm what the index already returned.
- If a search comes back empty, try at least one alternate strategy (a different term, a symbol name, a broader path) before concluding the target does not exist.
- Stay in the index for follow-ups: `read_symbol`, `outline`, `references`, `peek`.
- Use `search_files` only for an exact literal or regex, and `list_files` only for directory contents. Use `find_files` for filename patterns.
- In the final answer keep the exact repository-relative paths from the citations, for example `kage/agent.py:120`, not shortened names.
- Output is compact by design. Ask for one more step only when the question needs it.
