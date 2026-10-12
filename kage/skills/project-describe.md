---
name: project-describe
description: Describe a project or folder, say what each file does, show a file, or change one. Use for "what is this project", "explain this folder", "what does X do", "open/show X", "fix/rename/add ... in X".
---
Scan first, read second, edit last. Everything here is instant and cited; do not grep or read whole files to get the gist.

| The Boss asks | Do this |
|---|---|
| What is this project / folder, summarise the repo | `describe_project` once, then answer in 3-6 sentences |
| What does each file do, what is in this folder | `describe_files(path)`; add `pattern="*.py"`, `recursive=true` to go deeper |
| Show / open / read a file or a range | `view_file(path)` or `view_file("path:10-40")` |
| How does X work | `search_code` / `read_symbol`, then explain (code-search skill) |
| Change / fix / rename / add something in a file | `view_file`, then `edit_file`, then `changed()` |
| Create a new file, or rewrite one completely | `write_file` |

Describing a project: lead with what it is FOR (from the README line and entry points), then the stack, then how it is laid out (the top folders), then how to run it. Name real files as `path`. Do not repeat the card or the tables; the Boss can already see them. If the dossier says "partial", say the scan was cut short. For a sub-folder, call `describe_project(path="sub/dir")`.

Editing with `edit_file(path, old, new)`:
- `old` is copied EXACTLY from `view_file` output, without the line-number gutter, and short but unique (2-4 lines is usually right).
- To insert, use one unique anchor line as `old` and repeat it inside `new` with the added lines around it.
- If it says "not found", re-run `view_file` and copy again. If it says "appears N times", add the neighbouring line. `replace_all=true` only when every occurrence should change.
- A `WARNING: ... syntax error` means your edit broke the file: view it and fix it now, or tell the Boss `/undo` reverts it.
- Say an edit is done only after `VERIFIED`.
