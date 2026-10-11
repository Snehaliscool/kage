---
name: change-safety
description: Use before editing, refactoring, renaming or deleting code, before committing, and when resuming work. Shows the blast radius first, then what you touched, then what was left unfinished.
---
Before you change code, know what depends on it. After, know what you touched.

Before editing a file:
- `impact <path>`: how many files depend on it, which subsystems, the direct importers. Decide how careful to be and what to re-check.
- `callers <path>`: everything that includes, imports, sources or mentions it.

After editing:
- `changed`: maps your git changes to the files they could affect.
- `doctor`: structural health (cycles, dangling references, fan-in risk).

Resuming a task:
- `wip`: uncommitted files, TODO/FIXME markers inside them, and the blast radius of each. Run it first instead of re-reading the tree.

These answer "what depends on this". They do not run tests or builds; keep using those for correctness.
