# `crossbox plan`: what to work on next

`ready` answers "what can I start?". `plan` answers "which of those matters
most, and in what order does the rest have to happen?". It reads the same files
and uses no model: the same queue always gives the same plan.

    crossbox plan               # this machine's plan
    crossbox plan --top 5       # show at most 5 rows per section
    crossbox plan --json        # the whole plan, machine-readable
    crossbox --machine gpu-box plan   # the plan as another machine sees it

## The graph

`plan` builds one graph over every **open** step in the queue, on every
machine, because a critical path does not stop at a machine boundary.

- **Nodes** are open steps (status `pending`, `blocked` or `in_progress`) in task files
  that parse and pass the writers' shape check. Done and skipped steps, and
  every step in `archive/`, are not nodes.
- **Edges** run from a dependency to the step that waits on it. Each
  `depends_on` token resolves exactly as `done` and `ready` resolve it:
  - a bare step id or a step ref that is still open: one edge from that step;
  - a task ref whose task is not complete: one edge from **each open step of
    that task**, since the task completes only when all of them close;
  - a dependency that is already met adds no edge.
- A step `ready` would call **MALFORMED** (unknown status or target,
  `depends_on`/`needs` not a list of strings, an unknown or multi-word `needs`
  token, a dangling dependency, a file no writer will touch) is not a node. It is
  listed under `malformed` with the same cause `ready` gives, and every count in
  the plan is then a floor. `plan` applies these checks to every open step, so
  it also flags steps `ready` has not looked at yet (waiting, claimed, or for
  another machine). Two cases `ready` does not have are also listed there: a step
  that waits on a step `plan` cannot place (a malformed one, or one in a file no
  writer will touch), and a step that waits on a task with no open step that is
  still not complete (it can never be met). A task id held by two queue files
  makes its step refs ambiguous, so those steps are malformed too.
- Steps in a **cycle** (including cycles that cross tasks) are not ordered,
  ranked or put on a path. Each cycle is listed once, as its refs in
  edge order (a step is followed by a step that waits on it), starting from its
  smallest ref. A step that waits on its own task is a cycle of one. Where several
  cycles share steps, each is a shortest cycle through the smallest step not yet
  named, so every step in a cycle is named. A step that depends on a cycle, directly or
  through others, is listed under `blocked_by_cycle`; it can never become
  ready until the cycle is broken. Those steps are also left out of `next`, the
  critical path, waves and order (they cannot be placed), but they still count as dependents in
  `unblocks`.

## Sections

### 1. Next here (ranked READY)

The READY set for the current machine, exactly as `ready` computes it
(machine, claim, dependency and gate checks included), ranked by:

1. **unblocks**, descending: the number of distinct open steps that depend on
   this step directly or transitively (its descendants in the graph). Doing a
   step with unblocks 7 moves more work toward ready than one with 0.
2. priority (`critical`, `high`, `normal` (or `medium`), `low`, then anything else);
3. ref, ascending (plain string order).

Each row shows ref, unblocks, the number of direct dependents, priority,
whether the step is on the critical path, and its title.

### 2. Critical path

The longest chain of open steps through the graph, counted in steps. It is the
minimum number of steps that must happen one after another before all open work
can be finished, however many people or agents work in parallel. Ties between
equally long chains are broken by comparing the chains' ref sequences, from the
first step, in plain string order; the smaller one wins. Each step on it shows
its target and its current state on this machine (READY, GATED, CLAIMED,
WAITING, or `elsewhere` when this machine cannot work it).

### 3. Waves

Every non-cycle node gets a **wave** number: 0 if none of its dependencies is
an open step, otherwise one more than its deepest open dependency. Steps in the
same wave never depend on each other, so each wave is a batch that can run in
parallel once the waves before it are done. Within a wave, steps are grouped by
target and sorted by priority, then ref. The number of waves equals the
critical path's length.

### 4. Order

A topological order of all non-cycle nodes: wave by wave, and within a wave by
priority, then ref. Every step appears after everything it depends on.

## Exit codes

| code | when |
|---|---|
| 0 | the plan is complete |
| 1 | something is MALFORMED, a cycle exists, or an active claim names no step (the plan is a floor) |
| 2 | NOT EVALUABLE: the claims view could not be read or is incomplete (then `next` is empty and path states read NOT EVALUABLE, as in `near-done` and `graph`), or a gate probe gave no answer or was not run (`--no-probes`) (so READY, and section 1, is a floor) |
| 64 | usage error |

Exit 2 takes precedence over exit 1, as in `ready`. The text output names the
cause on the lines just above the summary line; `--json` carries it in the object.
When the claims file is damaged the READY set is unknown: `next` is empty, path
states read `NOT EVALUABLE`, and the graph sections are still shown.

## Text output

`--top` must be a positive integer; anything else is a usage error (64). `--machine`
is a global flag (`crossbox --machine NAME plan`), as is `CROSSBOX_MACHINE`.

Sections print in the order above. `--top N` (default 10) limits sections 1, 3
(per wave) and 4; a limited section says how many rows it left out. The last
line summarises: open steps, nodes, ready here, steps in a cycle, malformed,
waves, and the critical-path length, and says when the counts are a floor.
`nodes` is the open steps that are not malformed (cycle members included);
`open_steps` counts the open steps of every task file that parses.

## `--json`

One object, stable keys:

```json
{
  "machine": "laptop", "slot": "main",
  "open_steps": 12, "nodes": 11,
  "next": [{"ref": "...", "title": "...", "priority": "high", "target": "any",
            "unblocks": 4, "dependents": 2, "on_critical_path": true}],
  "critical_path": [{"ref": "...", "target": "any", "state": "READY"}],
  "waves": [{"wave": 0, "steps": [{"ref": "...", "target": "any", "priority": "normal"}]}],
  "order": ["..."],
  "cycles": [["A-X-001.a", "A-X-001.b"]],
  "blocked_by_cycle": ["..."],
  "malformed": [{"ref": "...", "cause": "..."}],
  "orphaned_claims": [],
  "not_evaluable": null,
  "counts_are_floor": false
}
```

`--top` does not apply to `--json`. `not_evaluable` is a string naming the
cause when the exit code is 2, otherwise `null`.

## Invariants

- **Deterministic.** The same files give byte-identical text and JSON output,
  whatever order the file system lists them in.
- **One readiness.** A ref in `next` is exactly a ref `ready` lists as READY on
  the same machine, and every READY ref appears in `next`.
- **Order respects dependencies.** In `order`, every step comes after each of
  its open dependencies; the same holds for waves.
- **Malformed never planned.** A malformed step appears in no section except
  `malformed`.
- **Cycles named, not hidden.** A cycle makes the exit code at least 1 and is
  listed; its steps appear in no ranking, wave, order or path.
- **Read only.** `plan` writes nothing and is not counted by the rate gate.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
