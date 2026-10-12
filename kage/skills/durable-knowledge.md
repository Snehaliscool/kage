---
name: durable-knowledge
description: Use right after you resolve something that cost real effort and will come up again: why a design is the way it is, a convention the code does not state, a trap that bit you, a question someone will ask twice, or an answer you had to reconstruct because nobody wrote it down. Saves it as a reviewed note anchored to real code. This is for durable understanding, not transient task state; unfinished work belongs in wip(), and small personal facts belong in remember().
---
# Durable knowledge

Your context is thrown away when the session ends. Whatever you worked out about the project evaporates unless it is written down where the next session will find it. Kage stores accepted decisions, concepts, gotchas, playbooks and claims as portable Markdown, separate from the disposable index, and serves them back through `notes` and `recall`, so understanding builds up instead of being rediscovered each time.

## When to capture

Capture a note when you resolved something costly that will matter again:

- why a design is the way it is,
- a convention that is not obvious from the code,
- a trap that bit you,
- the answer to a question the Boss or a teammate will ask again.

## Workflow

1. Propose the note: `note_propose(title, kind, body, anchors)`. `kind` is one of `decision`, `concept`, `gotcha`, `playbook`, `claim`. `anchors` is a list of `path:start-end` regions the note depends on. The tool prints a deterministic diff and stores nothing yet.
2. Show the Boss the proposal in your reply. Accept it with `note_accept(id)` once it is reviewed. The approval card is the review step.
3. Check health any time with `notes()`, which lists accepted notes and flags any whose anchored code has changed as STALE. Re-verify a STALE note before you rely on it. Use `notes(query)` to search them.

Anchor claims to real code wherever you can. A note whose anchored region changes is flagged STALE, which tells future readers to re-verify it.

## What not to capture

Keep notes short and backed by evidence. Do not capture routine steps, or anything the code already states plainly. Transient task state belongs in `wip()`. Small personal facts about the Boss belong in `remember()`.
