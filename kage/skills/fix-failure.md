---
name: fix-failure
description: Use when a command or build failed (often sent from the terminal's "Ask Kage why" button): diagnose from the real output, fix it, verify.
---
1. Read the pasted output from the bottom up. The last error is usually the cause; earlier lines are noise. Note the exit code and the folder.
2. Classify: missing program (`command not found` -> `which_command`, `pacman_search`, `pacman_install`), missing file or wrong folder (`list_files`, check the Folder line), permissions, syntax or test failure (`search_code`, `read_symbol`, `peek` the cited line), network or auth (`github_status`, `github_login`).
3. Read only what you need: the file and lines named in the error, not whole files.
4. Fix with the smallest change using a tool (`write_file` shows a diff, `run_command` shows a card). Every change is logged, so the Boss can `/undo`.
5. Re-run the failing command with `run_command` (same Folder as `cwd`) and report the result: say "fixed" only if it now exits 0. If it still fails, read the new output and repeat once, then explain plainly what is left.
