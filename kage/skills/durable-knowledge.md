---
name: durable-knowledge
description: Use right after resolving something that cost real effort and will come up again: why a design is the way it is, an unwritten convention, a trap that bit you, an answer you had to reconstruct. Saves it as a reviewed note anchored to code.
---
Your context is thrown away when the session ends. Whatever you figured out evaporates unless it is written down.

When to capture: you resolved something costly that will matter again (a design reason, a hidden convention, a gotcha, a question asked twice).

Workflow:
1. `note_propose title, kind (decision|concept|gotcha|playbook|claim), body, anchors` where anchors are `path:start-end` regions the note depends on. This prints a diff and stores nothing yet.
2. Show the Boss the note. Only after approval: `note_accept <id>`.
3. `notes` lists and health-checks them. A note whose anchored code changed is flagged STALE so it gets re-verified.

Keep notes short and evidence-backed. Do not capture routine steps or anything the code already states plainly. Transient task state belongs in `wip`, small personal facts belong in `remember`.
