---
name: github
description: Use for anything involving git or GitHub: configuring git, signing in, creating a repo, committing, pushing, cloning, fixing push errors.
---
Always start with `github_status()`. It tells you exactly what is missing and the NEXT step.

Order of operations to push code to GitHub:
1. `git_setup(name, email)` if identity is missing. Ask the Boss once, in one short question, only if you cannot read it from an earlier result.
2. `github_login()` if not authenticated. It opens a terminal + browser; tell the Boss what to do there, then stop and wait. Never ask for a token or password. After the Boss says they are done, call `github_status()` and only then continue.
3. `git(args="init -b main")` if the folder is not a repo.
4. `git_remote(url)` if the repo already exists on GitHub, or `github_create_repo(name, private=true)` to make a new one.
5. `git_commit(message)`. It refuses secrets (.env, keys, tokens) and dependency folders; fix with a .gitignore rather than overriding.
6. `git_push()`. Report the VERIFIED line and the link. If it says NOT VERIFIED or FAILED, read the HINT, fix, retry.

Rules: never force-push unless the Boss explicitly asks. Never put a token in a URL. Never claim success without a VERIFIED result from this turn. Prefer git_commit / git_push over run_command for git, they check their own work.

Common failures (each comes with a HINT): rejected non-fast-forward -> `git(args="pull --rebase")` then push; GH007 private email -> use the noreply address; Permission denied (publickey) -> github_login(method="ssh") or switch the remote to https; protected branch -> push a feature branch.
