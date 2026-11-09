# Limits

## How well it works

There is no accuracy number to give: crossbox keeps records rather than classifying anything. What was checked:

- **The behaviour matches a written contract.** A clean-room rebuild from the contract's invariants found where code and contract differed. See [DESIGN.md](../DESIGN.md).
- **Parallel use: one project.** The private system it was extracted from runs daily across several agent sessions and two machines. How it holds up on another team, or how many duplicate claims it prevents, has not been measured.
- **Scale: hundreds of tasks,** which is what it has been used on. Tens of thousands has not been tried.

## Known holes

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
  alias, a glob (`.cross*`), a `cd` to a computed path, `xargs`/`env`/`bash -c`/`sh -c`
  around a writer, a writer not in its list (`curl -o`, `tar -x`, `awk -i inplace`, ...),
  a cwd in a subdirectory when `CLAUDE_PROJECT_DIR` is unset, and more than 16 words between a command and its flag ([agents](agents.md)).
- **`plan` unblock counts** use big-integer bitsets: time O(edges x steps / 64),
  memory up to steps^2 / 8 bytes in the worst case (6,000 steps: a few MB; 50,000: a
  few hundred MB).
- **`why --json` on a very deep dependency chain nests deeper than older Pythons can
  read.** The trace is nested, two levels per link, so a chain of thousands of links is
  JSON thousands of levels deep. crossbox writes it without recursion, but Python's own
  `json.loads` before 3.14 stops near 1,000 levels with `RecursionError`, and
  `sys.setrecursionlimit` does not raise that limit (the decoder's C recursion guard is
  fixed). Two ways out: read it with Python 3.14 or newer, or with
  [ijson](https://pypi.org/project/ijson/), which builds the object iteratively
  (`next(ijson.items(f, ""))`; tested to 10,000 levels on 3.9 to 3.13). The text output
  and the MCP plugin are unaffected: the plugin passes the JSON through as text.
- **Scale.** Every command parses the whole queue. Hundreds of tasks are fine;
  tens of thousands are not what this is for.
- **Note inboxes addressed to another machine's slot** cannot be validated from
  here (the slot is on the other machine).

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](limits.md) · [credits](credits.md)
