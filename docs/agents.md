# Using crossbox with coding agents

The [Claude Code plugin](plugin.md) bundles both hooks below, a skill, MCP tools and a status line.

- [plugin/hooks/session-start.sh](../plugin/hooks/session-start.sh):
  a Claude Code SessionStart hook that shows the agent `ready`, claims and notes
  when a session starts.
- [plugin/hooks/queue-guard.py](../plugin/hooks/queue-guard.py):
  a PreToolUse hook that refuses direct writes under `.crossbox/` (task files,
  claims, notes, config and the needs registry) so agents go through the CLI:
  Write, Edit, MultiEdit and NotebookEdit on those paths, and shell commands that
  redirect into the directory or name it together with a writing command (`sed -i`,
  `cp`, `mv`, `rm`, `tee`, `python -c`, ...). Paths are matched case-insensitively;
  a custom `dir:` is passed with `--dir`. It also
  catches a relative redirect after `cd`/`pushd` into the directory, `find ...
  -delete/-exec`, `git -C DIR` and `git rm/mv/clean`, `patch`, `unlink`, `shred`,
  `sponge`, `vim`-family editors and an interpreter fed a heredoc. It is active
  only in a project that holds the directory at its root. It never blocks reads,
  and it is a best-effort nudge, not a sandbox. Known limits: a command that hides
  the path (a script file, a variable, an alias, a `cd` to a computed path, `xargs`,
  `env` or `bash -c` around the writer), or puts more than 16 words between `sed`
  and its `-i`, can get past it. To stop an agent editing the guard itself away, pair
  it with an integrity check of the hook surface (any check that the hook files and settings are unchanged).

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
