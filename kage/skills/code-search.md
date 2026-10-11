---
name: code-search
description: FIRST step for any question about code in the workspace: where something lives, what a symbol does, who uses it, how a file is shaped, what a change touches. Answers from a cited index in milliseconds instead of grepping and reading many files.
---
Answer repository questions from the index before reading files. Every result is cited as `path:line`.

| Question | First tool |
|---|---|
| Map an unfamiliar project | `overview` |
| Locate a feature or concept | `search_code "<keywords>"` |
| Locate a named symbol | `find_symbol <name>` |
| Read one definition | `read_symbol <name>` (not the whole file) |
| See a file's shape | `outline <path>` |
| Who uses this symbol | `references <name>` |
| What breaks if I change this file | `impact <path>` |
| Read a cited range | `peek path:start-end` |

Rules:
- Trust the cited results. Do not re-verify with a tree-wide grep or by reading whole files.
- If you need more, stay in the index: `read_symbol`, `outline`, `references`, `peek`.
- Use `search_files` only for an exact literal, `list_files` only for directory contents.
- In the final answer keep the exact repository-relative paths from the citations.
