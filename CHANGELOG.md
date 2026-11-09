# Changelog

All notable changes to crossbox. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - unreleased

First public release. How it was hardened: [DESIGN.md](DESIGN.md).

- Task files: one YAML file per task, lettered steps with dependencies across steps and tasks.
  A canonical writer re-parses its output before replacing a file, and a per-tree lock allows one writer at a time.
- Verbs to create, edit, close (`done --commit SHA`), skip, reopen, rename, reorder, renumber, archive and check tasks and steps.
- One exit-code contract: 0 ok, 1 refused or findings, 2 NOT EVALUABLE, 3 partial, 64 usage.
- Advisory claims, unioned across worktrees and branches by identity, with a git merge driver. `activity` checks claims against commits.
- Origin guard and sibling-aware id minting, and a rate gate for looping agents.
- Readiness by machine capability (`ready`, `why`), with gates and a needs registry. A gate probe is a shell command, and `--no-probes` skips it.
- `plan` (ranking, critical path, waves), `progress`, `near-done` and `graph` (Mermaid, DOT, JSON), all read only.
- `closures` checks that each recorded closure names a reachable commit. `lint` finds decisions written in notes that no field carries.
- `doctor` checks, repairs and self-tests task files, with pre-commit hook and merge-driver installers.
- Claude Code plugin and marketplace: session hook, queue guard, skill, stdio MCP server (11 tools) and an opt-in status line.
