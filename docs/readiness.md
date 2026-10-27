# Machines, capabilities and `ready`

```yaml
# .crossbox/config.yaml
machines:
  laptop:
    hostnames: [my-laptop]
    provides: [docker]
  workstation:
    provides: [gpu, docker]
```

The current machine is `CROSSBOX_MACHINE`, else the machine whose `hostnames`
include this host, else `host-` plus a short hash of the host name (never the name
itself; see docs/configuration.md). The current slot is the git worktree name
(`main` for the primary checkout; `CROSSBOX_SLOT` overrides).

Machine and slot names become parts of note file names, so each must be one safe
path component: not empty, not `.` or `..`, no `/`, `\`, control characters, `+`
or `-to-` (the last two are the separators inside a note file name). A name that
breaks the rule, from the config, the environment or a `note --to` recipient, is
refused before any path is built.

A step's `target` names a capability the machine must provide, or a machine name;
`any` means anywhere. Writers accept only `any`, a declared capability or a
machine name. A target no machine provides is never widened to `any` on read: the
step is reported as malformed until someone fixes it. `target_aliases` in the
config lets old spellings be read (never written).

`crossbox ready` (also `crossbox needs here`) classifies every open step workable
on this machine:

| Bucket | Meaning |
|---|---|
| READY | nothing stands in the way |
| GATED | a gate in `needs` is closed or could not be evaluated |
| CLAIMED | an active claim (from any readable branch) holds it |
| WAITING | a dependency is not met yet |
| MALFORMED | unknown status or target, `depends_on` or `needs` not a list of strings, an unknown or multi-word `needs` token, a dangling dependency, or a file no writer will touch (a missing, invalid or duplicated step id, a step that is not a mapping); excluded from every other count, which is then a floor |

Steps for other machines are counted as "for other machines". `--json` gives
`{machine, slot, ready, gated, claimed, waiting, malformed, elsewhere, open_here,
orphaned_claims, claim_id_collisions, claims_view, gates_not_evaluable, counts,
counts_are_floor}`. `claims_view` is `ok` or the cause the claims could not be read in
full; `gates_not_evaluable` counts steps with a gate whose probe gave no answer.
`counts_are_floor` is true when any step is malformed, any claim is orphaned, or
`gates_not_evaluable` is above zero.

**The needs registry** (optional, [example](../examples/needs-registry.yaml)) is the
vocabulary for `needs`. Only `gate` tokens affect readiness; `capability`,
`estimate` and `annotation` tokens are documentation (routing is done by `target`,
one mechanism, not two). A gate is open when:

- `date-reached:YYYY-MM-DD`: that UTC day has come (built in);
- it has a `probe`: the shell command exits 0 (1 = closed; anything else, a crash
  or the 10-second timeout is **not evaluable**, shown as such and not cached). The
  probe runs with the repository root as its working directory;
- it has no probe: never, unless it is `clearable: operator` and you pass
  `--operator`.

The registry is validated as a whole, and anything but the declared shape makes
`ready`, `status --summary --ready` and `needs audit` exit 2 (NOT EVALUABLE),
never a weaker registry: every token is a mapping with a `kind` of exactly
`capability`, `annotation`, `estimate` or `gate`; `probe` is a non-empty string
and `clearable` is `external` or `operator`, both on gates only; `desc` is a
string; an alias must name a token; unknown fields are refused. A step's
`needs` must be a list of strings. With a registry present, a token it does not
define is refused by the writers (`add`, `batch-add`, `edit-step`) and reported
MALFORMED by `ready`. **With no registry**, `date-reached:` still works, but any
other token cannot be evaluated: writers warn, and `ready` keeps the step GATED as
not evaluable and exits 2, because a missing or mis-pathed registry must not
silently open every gate.

A probe with no answer makes `ready` exit 2: the step stays GATED with its cause,
and READY is a floor. `needs audit` lists unknown, alias and multi-word tokens on
open steps (exit 1 when any are unknown; exit 2, with the files named, when a task
file cannot be read or is of the wrong shape, or when steps list needs and there is
no registry). `ready --no-probes` skips probes.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)

## why

`crossbox why <ref>` answers "why is this step not workable here?" from the same
classification `ready` uses, so the two never disagree. A WAITING step is followed
through each unmet dependency (a task-level dependency expands to that task's open
steps) until the walk reaches steps that wait on nothing: the roots. Each node
carries one code:

| code | meaning |
|---|---|
| `READY` | workable on this machine now |
| `CLAIMED` | an active claim holds it; the claim and its holder are named |
| `WAITING` | a dependency is not closed |
| `GATED` | a gate is closed, or gave no answer; each gate's state and cause are shown |
| `MALFORMED` | the step or its file cannot be read as written; the cause is shown |
| `ELSEWHERE` | its target is provided by other machines, which are named |
| `DONE` | closed, or its task is archived |
| `SKIPPED` | retired with a reason |
| `CYCLE` | the walk came back to a step already on its path |

```
$ crossbox why TASK-API-001.c
TASK-API-001.c  WAITING  waiting on TASK-API-001.b
  TASK-API-001.b  WAITING  waiting on TASK-API-001.a
    TASK-API-001.a  CLAIMED  held by CLM-laptop-wt1-001 (laptop/wt1)
root: TASK-API-001.a  CLAIMED  held by CLM-laptop-wt1-001 (laptop/wt1)
```

A step whose task file is malformed as a whole (a duplicated step id, a task id
defined in two queue files, a file that does not parse), and any step depending on
it, is a `MALFORMED` node carrying the file's cause, never an abort. The walk is
iterative, so a chain of thousands of tasks explains; below 40 levels of nesting the
text tree stops indenting and prints `[depth N]`, and the JSON trace is compact.

Without a ref, `crossbox why` prints one line for the queue: how many open steps
are workable on this machine out of how many (the same denominator `ready` prints),
what holds the rest, and how many are open elsewhere by target. Task files that
cannot be read are a file, not a step: they are reported apart ("k task file(s)
unreadable"; JSON `unreadable_files`). Use `--machine` to ask as another machine.

Exit 0 when explained, 1 for a ref that names nothing, 2 (NOT EVALUABLE) when a
gate on the path gave no answer or the claims view is incomplete. `why` is read
only and runs gate probes like `ready` (`--no-probes` turns them off).

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
