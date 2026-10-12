---
name: multi-step
description: Use for tasks with several parts or many items (all my repos, every picture, a whole folder tree): plan first, use the batch tools, page through results, finish with one complete answer.
---
1. `plan(steps=[...])` with 2-6 short steps. Tick them with `plan(done=[n])` as each finishes; the checklist is shown back to you after every tool result.
2. Prefer batch tools over one call per item: `github_repos` then `github_describe_repos` (10 per call, page with `offset` until it says that is all), `find_files` then `show_images`, `create_archive`.
3. Results are trimmed when the context fills. Write down what you need in the plan steps, or re-run the tool for the detail.
4. Outside the workspace? Just use an absolute or ~ path. The Boss gets a one-time approval card for that folder.
5. Finish with ONE answer that covers every item (group by theme if the list is long). Never stop after the first page.
