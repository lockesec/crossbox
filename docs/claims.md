# Claims: advisory by design

A claim is a row in `claims.yaml` saying "this machine and this slot (a slot is one git worktree of a
clone; the primary checkout is `main`) are working on this step":

```yaml
claims:
- id: CLM-laptop-wt1-001
  machine: laptop
  slot: wt1               # the git worktree name; the primary checkout is `main`
  subject: TASK-API-001.a
  source: ''
  intent: ''
  created: '2026-09-24'
  status: active          # or released, with a `released:` date
  step_ref: TASK-API-001.a
```

Rows may also carry `renumbered_from:` (set by the merge driver) and
`carried_from:` (set when a renaming verb moved the claim; see below). An older
`box:` field is read as `machine`.

- **Binding.** `claim TASK-API-001.a` binds the claim to that step. A subject that
  merely *mentions* a step binds nothing and is reported as unbound (no collision
  check or readiness view can see it). `--step REF` may be repeated: one claim per
  step, each releasable on its own.
- **Namespaces.** Ids are `CLM-<machine>-NNN` from the primary checkout and
  `CLM-<machine>-<slot>-NNN` from a linked worktree, so two slots, or one slot name
  on two machines, never share a counter. `machine` and `slot` are stored as
  separate fields in one canonical form, and "mine" means *same machine and same
  slot* everywhere it is asked: `release --all-mine`, `claim-check` and the
  rate-gate exemption.
- **Seeing other sessions.** A claim committed in another worktree is visible at
  once: every reader also reads `claims.yaml` at each worktree's branch, at
  `origin/<branch>` for each, and at the upstream (last-fetched state; no
  network). Claims are unioned by *identity* (the id they were minted with, plus
  machine, slot, created date, subject and step), never by id alone, and **release
  is monotonic**: released in any readable copy means released. Two clones that
  share a machine and slot name can mint one id for two different claims; both are
  kept, a release of one never releases the other, and `claims` prints an
  `ID COLLISION` line and `ready` a `claims view: N different claims share the id ...`
  line until a merge renumbers one of them. An unparseable copy is reported, never dropped; if
  worktrees cannot be enumerated the view is NOT EVALUABLE. `ready` uses this union.
- **What it cannot see**, and says so: a claim another session has not committed;
  a claim on another machine that has not been pushed and fetched. `claims` prints
  a visibility line when your own `claims.yaml` has uncommitted or unlanded
  changes, or when there is no upstream to compare with.
- **Staleness.** An active claim created more than `stale_days` (3) whole days ago is
  flagged STALE. It is never released automatically. STALE reads only the claim's
  age; `crossbox activity` (below) asks git whether anyone is working on it.
- **Collisions.** When one step is held by claims from two different (machine,
  slot) pairs, `claims` names them. The claim whose `id:` line reached the
  upstream first keeps the step; if none has reached it, the verdict is "not
  evaluable: push, then re-run". An older `created` date is *not* used, because two
  sessions can create claims in either order before either pushes.
- **`claim-check TITLE [--source PATH]`** compares a planned piece of work with
  every active claim held elsewhere (two or more shared words, or the same source
  file). Exit 0 no overlap, 1 overlap, 2 when the sibling view could not be read
  (never "no overlap"). `new` runs it quietly and prints any overlap as advice.
- **The merge driver.** Two branches that each append claims would conflict on
  every merge. `crossbox merge-driver` unions them instead: records are matched on
  the same identity the view uses (the minted id, machine, slot, step, created,
  subject; a renumbered copy remembers its minted id in `renumbered_from:`, so it
  is still the same claim); statuses only move forward (released wins). Two
  *different* claims minted with one id are both kept: the one whose identity
  sorts lower keeps the id, the other is renumbered. The result depends only on
  the set of claims, so merging in either direction, or merging again, gives the
  same ids on every clone. Anything it cannot parse exits 1, so git falls back to
  an ordinary conflict.
- **Renaming a claimed step.** `move-step`, `swap`, `reorder` and `renumber`
  change step ids, so they also carry the claims: a claim this slot holds on a
  renamed step is released and re-minted on the step's new id (`carried_from:`
  names the old claim), so the claim stays with the same *work*. A claim held by
  anyone else cannot be rewritten from here, so the rename is refused (exit 1)
  unless you pass `--force`. `ready` and `claims` list an active claim whose step
  no longer exists as ORPHANED, and `ready` then exits 1 with its counts marked as
  a floor.

**Why advisory.** A claim in a file is not a lock. Two sessions can both read "free"
and both write a claim before either pushes; a real mutual exclusion across machines
needs a lease server that hands out fencing tokens and a resource that checks them
(see Burrows, *The Chubby lock service*, 2006). crossbox does not pretend: it warns
and names the other holder, shows the collision, and picks a deterministic winner
once the claims reach the upstream. `claim` warns when another session already
holds the step; `done` closes a step another session claims all the same and warns
on stderr, naming that claim and its holder.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)

## activity

A claim is a statement someone chose to make; a session that never claims is
invisible to it. `crossbox activity` reads a second witness that nobody has to
remember to produce: git. For every active step claim it reports one state:

| state | meaning |
|---|---|
| `HOT` | a commit or branch naming the step within `--hot-hours` (default 24) |
| `QUIET` | evidence older than that but inside `stale_days`, or a claim younger than `stale_days` with no evidence yet |
| `COLD` | no evidence for longer than `stale_days` |
| `UNKNOWN` | git could not be read, the scan stopped at its 50,000-commit cap, or the claim names no step |

Evidence is a commit dated on or after the claim's date whose subject names the
step ref as a whole token (`TASK-API-001.a` never matches `TASK-API-001.ab`) or
that carries a trailer `Crossbox-Step: TASK-API-001.a`, or a local or
remote-tracking branch whose name holds the ref as a whole token. Branch names
split at `/`, `-`, `_` and their ends, case-insensitively: `feat/TASK-API-001.a-x`
counts for `.a`, `feat/TASK-API-001.ab-x` does not. A branch is dated by its own
reflog (when it was created or last moved), so a branch made today at an old commit
counts as made today; with no reflog (remote-tracking refs, fresh clones, reflogs
off) its tip commit date is used. A repository with no commits yet is UNKNOWN
("repository has no commits yet"), exit 2. Every local
branch is read, so unpushed commits in a sibling worktree count; another
machine's commits count once they are pushed and fetched.

It also lists **unclaimed activity**: open steps with no active claim that
commits or branches named inside `stale_days`. That is the case a claims-only
view cannot see: someone working on a step `ready` still shows as READY.

```
$ crossbox activity
activity: hot 1 · quiet 0 · cold 0 · unknown 0 of 1 active claim(s); 4 commit(s) scanned since 2026-10-13
  HOT      CLM-laptop-wt1-001     TASK-API-001.a     laptop/wt1  1 commit(s), last 0m ago (5cc141a on branch wt1)
unclaimed activity: 1 open step(s) with no active claim named by commits or branches in the last 3 day(s):
  TASK-API-001.b     1 commit(s), last 0m ago (620bfb3 on main)  handler
```

Advisory, like every claim: nothing is released, and "no commit naming the step
seen" never means idle (an agent can work for an hour before it commits). Exit 0,
or 2 when git or the sibling claims view could not be read.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
