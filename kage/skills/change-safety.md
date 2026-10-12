---
name: change-safety
description: Use when about to edit, refactor, rename or delete code, before committing or opening a pull request, or when resuming a task and needing to see what was left unfinished. Use impact(path) to see the blast radius before a change, changed() and doctor() after, and wip() to recover in-progress work. This answers what depends on the code you are touching; it does not run tests, linters or builds, so keep using those for correctness.
---
# Change safety

Before you change code, know what depends on it. After you change it, know what you touched and whether the repository is still healthy. When you pick up a task, know what the last session left unfinished. The index answers all three in one call each, cited to file and line.

## Before editing a file

- `impact(path)`: the blast radius. How many files depend on it, the subsystems involved, and the direct importers. Use it to decide how careful to be and what to re-check.
- `callers(path)`: everything that includes, imports, sources or mentions the file.
- `references(name)`: call sites of one symbol, when you are about to rename or change its signature.

## After editing

- `changed()`: maps your git changes to the files they could affect, so you check the right things before committing.
- `doctor()`: structural health (dependency cycles, dangling references, fan-in risk) with a score. Run it after structural edits, not after every small one.

## Resuming a task

- `wip()`: uncommitted work with each touched file's git status, the unfinished-work markers inside them (TODO, FIXME and similar), and the blast radius of each indexed file. Run it first when picking up where a previous session stopped, instead of re-reading the tree to reconstruct state.

All of these re-index what changed before answering, so they never report stale state.

## Limits

These tools say what depends on the code. They do not prove the change is correct. Run the project's own tests, linters or build as the final check.
