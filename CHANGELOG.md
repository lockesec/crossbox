# Changelog

All notable changes to crossbox. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-10-19

### Changed

- A machine with no name in the config is recorded as `host-` plus six hex digits of a hash
  of its host name, never the host name itself: claims, notes and `completed_by` are
  committed and pushed. A writing command says so once and how to name the machine.

### Added

- `crossbox lint [--json]`: decisions a step's notes state that its structure does not
  carry: a gate named in prose that `depends_on` omits, and a note opening with a decision
  marker ("Deferred:", "DEFER-FOR-EVIDENCE", "BLOCKED ON <WHO>", "Superseded by X") while the
  step is still open. Both rules are anchored; a gate on a closed step, a stale gate and
  moved-here work are skipped, and an unreadable task file makes the run NOT EVALUABLE. On a
  production queue of 992 open steps it reports 38 findings, each checked by hand (docs/lint.md). Read only,
  advisory.
- `crossbox closures` fixes from review: one commit with an older committer date no longer
  hides recent closing commits (the walk no longer uses `--since`); in a shallow clone a
  recorded commit outside the history is NOT EVALUABLE, not "rebased away"; an unreadable
  task file is named and makes the run NOT EVALUABLE; a bare file name (`backtest.py`) is
  present when any tracked file has that name.
- `crossbox done REF --commit SHA` (repeatable): a closure that names the commit it
  rests on. The commit is resolved to its full sha before anything is written (an
  unknown one is refused; outside git, exit 2), stored under the step's `commits:`,
  and graded REACHABLE, UNMERGED or UNKNOWN against the upstream or a local main or
  master. A commit whose subject does not name the step and that has no
  `Crossbox-Step:` trailer is recorded with a warning. Evidence can be added to a step
  that is already done; `reopen` retracts it into the notes; `show` lists it. The MCP
  `crossbox_done` tool takes an optional `commit` (`HEAD` or 7-40 hex digits).
- `crossbox activity [--json] [--hot-hours H]`: claim liveness read from git, a
  witness independent of the claim itself. Each active claim reads HOT (a commit or
  branch naming the step within H hours, default 24), QUIET (older evidence inside
  `stale_days`, or a young claim with none yet), COLD (no evidence for longer than
  `stale_days`) or UNKNOWN (git unreadable, scan truncated, or a subject-only claim).
  Evidence is a commit dated on or after the claim whose subject names the step as a
  whole token or carries a `Crossbox-Step:` trailer, or a branch whose name holds the
  ref as a whole token (dated by its reflog, else its tip; a repository with no
  commits is UNKNOWN); every local branch is read, so unpushed work in a sibling worktree counts. Also
  lists open steps with no active claim that commits named inside `stale_days`. Read
  only and advisory: nothing is released. Also the MCP tool `crossbox_activity`.
- `crossbox closures [--json] [--days N]`: are the queue's closures true, read from
  git. Five classes, each count beside its denominator: unevidenced (done steps with no
  `commits:`; informational), unreachable (a recorded commit not in the baseline, or
  gone), claimed-closed-but-open (an open step that a commit within `--days`, default
  30, on any local or remote-tracking branch closes by a sentence-anchored verb
  followed by the ref, or by a `Crossbox-Step:` trailer plus such a verb), artifact
  missing (a done step's notes cite a backticked path absent at HEAD), and unmerged
  remote branches (those naming a step listed, plus the total). Exit 0 when the last
  four are empty, 1 on rows, 2 NOT EVALUABLE naming each class that could not run (the
  rest still report). Read only. Also the MCP tool `crossbox_closures`. See docs/closures.md.
- `crossbox why [REF] [--json]`: why a step is not workable on this machine, traced
  through unmet dependencies to its root blockers, with one stable code per node
  (READY, CLAIMED, WAITING, GATED, MALFORMED, ELSEWHERE, DONE, SKIPPED, CYCLE). A
  step for another machine names the machines that provide its target. A task-level
  dependency expands to that task's open steps; a cycle is reported, not followed.
  Built on the classification `ready` uses, so the two agree on every step; a step in
  a malformed task file is a MALFORMED node and the walk is depth-safe. Without
  REF: "N of M open step(s) workable on <machine>", what holds the rest, and how many
  are open elsewhere by target. Exit 0 explained, 1 unknown ref, 2 when a gate on the
  path gave no answer or the claims view is incomplete. Also the MCP tool
  `crossbox_why`.
- `crossbox plan [--top N] [--json]`: the READY steps ranked by how much work each
  unblocks, the critical path, parallel waves and a topological order over every
  open step on every machine; cross-task cycles are named, malformed steps are
  listed, and an unanswerable gate or damaged claims file exits 2. Read only and
  not metered by the rate gate. A global `--machine NAME` flag plans as another
  machine sees it ([docs/plan.md](docs/plan.md)).
- A Claude Code plugin and marketplace (`plugin/`, `.claude-plugin/`): the
  SessionStart and PreToolUse hooks (moved from `examples/claude-code/`), a skill
  for the claim loop, a stdio MCP server with a fixed verb allowlist (argv only,
  no shell), and an opt-in status line ([docs/plugin.md](docs/plugin.md)).
- `crossbox progress [--by task|domain|priority] [--archive] [--json]`: closed/total
  step counts per task, domain or priority, each beside its denominator and rounded
  down; an unreadable task file is listed and makes the counts a floor (exit 1); a
  step-less task reads `0/0`, never 100%.
- `crossbox near-done [--max-open N] [--min-pct P] [--json]`: tasks few steps from
  closing, each remaining step's state on this machine and which configured machines
  can work it; a step no machine provides says `workable on: none`.
- `crossbox graph [--format mermaid|dot|json] [--task ID]... [--open] [--max-nodes N]`:
  the dependency graph for GitHub's Mermaid or Graphviz, one subgraph per task, step
  states as classes with text markers, `plan`'s critical path thick. Titles are
  untrusted: ids are generated, labels cleaned, cut and escaped for the format. The
  sample queue's graph in the walkthrough is regenerated and compared by a test
  ([docs/views.md](docs/views.md)). All three are read only and not metered by the
  rate gate.

### Fixed

- `done` no longer advises "no active claim covered" a step that a sibling worktree's
  committed claim holds, right after warning that it is claimed (it read only the local
  claims file for the advice).
- Text with a lone surrogate (an invalid UTF-8 byte in an argument) is refused by
  every writer; `doctor check` and `ready` flag it in a hand-edited file, and no
  printer dies on it.
- `ready` flags a task id defined in two queue files MALFORMED instead of listing
  both files' steps READY.
- A task-ref dependency on an archived task whose file does not parse is no longer
  MET.
- Ids are matched with `fullmatch`: `TASK-001\n` and the step id `"a\n"` are not ids.
- `ready` and `status` clean titles and ids before printing them (a newline could
  forge a row, an escape byte reached the terminal).
- The SessionStart hook passes `--no-probes`.
- The plugin never runs needs-registry probes by default: the session hook, status
  line and MCP read tools pass `--no-probes` unless `CROSSBOX_ALLOW_PROBES=1`;
  `plan`, `near-done` and `graph` gained `--no-probes`.
- `graph` also refuses past `--max-edges` and `--max-chars` (Mermaid's 500 and
  50,000 by default for `--format mermaid`).
- `plan` ranks nothing under an incomplete claims view, never lists a malformed or
  duplicate ref, counts unblocks once over the condensed graph (it was a search per
  READY step) and cleans titles and ids.
- MCP server: refuses text with a lone surrogate, answers -32603 instead of dying,
  treats exit codes other than 0/1/2, signals and tracebacks as tool errors, accepts
  every id prefix the CLI does, and kills the whole process group on a timeout (as
  does the status line).
- Session hook passes `--root`; status line cache is per root and machine and
  ignores a future mtime, and its exit-1 wording reads the cause from the JSON.
- Queue guard: catches `cd` + relative redirect, `find -delete`, `git -C`, `patch`,
  `unlink`, editors and interpreter heredocs, and acts only in a project that holds
  `.crossbox/` at its root.
- `--machine ""` is a usage error and bad machine names name their real source;
  `progress --json` domain and priority groups carry null `title`/`archived`.
- The rate gate no longer counts `batch-done` and `skip` when every ref they name is
  held by an active claim of this slot (as `done` already was); `reopen` stays metered.
- `ready --json` sets `counts_are_floor` when a gate probe gave no answer.
- Documentation corrections: the `premise_verdict` values, the walkthrough's `new`
  output, the `ready` collision line, the credit for arXiv:2606.30986, the review
  round and test counts in DESIGN.md.

## [0.1.0] - never published (its contents ship in 0.2.0)

First public release.

- Task files: one YAML file per task, lettered steps with dependencies across
  steps and tasks, a canonical writer that parses its own output again before
  replacing a file, and a per-tree writer lock.
- Verbs to create, edit, close, skip, reopen, rename, reorder, renumber, archive
  and check tasks and steps; one exit-code contract (0 ok, 1 refused or
  findings, 2 NOT EVALUABLE, 3 partial, 64 usage).
- Advisory claims, unioned across worktrees and branches by identity, with a
  git merge driver that is idempotent and does not depend on merge order.
- Origin guard and sibling-aware id minting; a rate gate for looping agents.
- Capability-routed readiness (`ready`) with gates and a needs registry.
- `doctor`: check, repair and self-test task files; pre-commit hook and merge
  driver installers.
- Example Claude Code SessionStart and PreToolUse hooks; `examples/demo.sh`,
  run by CI; a sample queue; the project's own roadmap in `.crossbox/`.
- Adversarial review before release (seven rounds by 0.2.0); every finding is mapped to
  its tests in `tests/INVARIANTS.md`; DESIGN.md tells what each round found.
