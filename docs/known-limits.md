# Known limits

- **Comments are not preserved** in files crossbox writes. Keep commentary in
  `notes`.
- **Claims are advisory.** Two sessions can claim one step before either
  pushes; crossbox reports it, it cannot prevent it. An uncommitted claim in
  another worktree, and an unpushed claim on another machine, are invisible.
- **Sibling visibility follows worktrees.** Other machines' work is seen through
  `origin/<branch>` for branches that exist as worktrees here, plus the upstream.
  To see every pushed branch, add `extra_refs: ["refs/remotes/origin/*"]`.
- **The origin guard cannot see another clone's uncommitted work.** Sibling
  worktrees of this clone are read directly; another clone's mints are seen only
  once committed, pushed and fetched. It degrades to a merge conflict.
- **Uncommitted claims in a sibling worktree are invisible** to the claims view
  (the guard and minter read sibling working copies; the claims view does not).
- **Removing the last step and adding again re-mints its letter.** Gaps in the
  middle are never reused.
- **Probes are shell commands from the repository** and run when you ask for
  readiness. Treat `needs-registry.yaml` like any other script in a repository you
  do not control (see [SECURITY.md](../SECURITY.md)); `--no-probes` skips them.
- **The queue guard is a pattern match.** It misses a script file, a variable, an
  alias, a `cd` to a computed path, `xargs`/`env`/`bash -c` around a writer, and more
  than 16 words between a command and its flag ([agents](agents.md)).
- **`plan` unblock counts** use big-integer bitsets: time O(edges x steps / 64),
  memory up to steps^2 / 8 bytes in the worst case (6,000 steps: a few MB; 50,000: a
  few hundred MB).
- **Scale.** Every command parses the whole queue. Hundreds of tasks are fine;
  tens of thousands are not what this is for.
- **Note inboxes addressed to another machine's slot** cannot be validated from
  here (the slot is on the other machine).

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
