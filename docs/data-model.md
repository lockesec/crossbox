# The data model

## Layout

| Path (default) | Contents |
|---|---|
| `.crossbox/config.yaml` | configuration (below) |
| `.crossbox/queue/<ID>.yaml` | one live task per file |
| `.crossbox/archive/<ID>[-suffix].yaml` | closed tasks; an editorial suffix is allowed |
| `.crossbox/claims.yaml` | the claim board |
| `.crossbox/notes/<from>-to-<to>.md` | append-only message inboxes |
| `.crossbox/needs-registry.yaml` | optional vocabulary for `needs` |
| `.crossbox/queue/.doctor-bak/` | backups written by `doctor fix --write` |

Only files named `<PREFIX>-*.yaml` are tasks; anything else in those directories is
ignored. A task's identity is its `id:` field, not its file name.

Each path can be moved in the config (`dir`, `queue`, `archive`, `notes`, `claims`,
`registry`), but only within the repository: a value is resolved, symlinks
included, and must land inside the repository and outside `.git`. An absolute
path, a `..` walk out, or a symlink that leaves the tree makes the config NOT
EVALUABLE (exit 2), as does any field of the wrong shape. The error names the
field; crossbox never writes to an escaped path.

## A task file

```yaml
id: TASK-API-001
title: Parser rewrite
priority: high            # one of config `priorities` (default critical, high, normal, low)
domain: api
phase: two                # optional, free text
created: 2026-09-24
created_by: laptop
source_task: ''           # free text: where this came from
steps:
- id: a
  title: Write tests for the new grammar
  target: any             # a capability the machine must provide; any = anywhere
  needs: [staging-up]     # registry tokens; only gates affect readiness
  status: done            # pending | blocked | in_progress | done | skipped
  depends_on: []          # sibling ids, TASK-X-NNN.s step refs, or TASK-X-NNN task refs
  notes: |
    Multi-line notes are kept line for line.
  completed_by: laptop
  completed: 2026-09-24
  commits:                # optional (`done --commit SHA`): full shas the closure rests on
  - 5cc141a0e3d4b2f1c9a8b7e6d5c4b3a2f1e0d9c8
  premise_verdict: STILL-VALID  # optional (`edit-step --premise-verdict`): does the
                          # reason this step exists still hold? One of STILL-VALID,
                          # PREMISE-SHIFTED, ALREADY-IMPLEMENTED, SUPERSEDED,
                          # ABSORBED, BLOCKED, NOT_EVALUABLE; anything else is refused
bucket: later             # keys crossbox does not know are kept as they are
```

- **Task ids** are `PREFIX-NNN` or `PREFIX-DOMAIN-NNN` (`DOMAIN` = upper-case
  letters, `NNN` zero-padded to three digits). The prefix is configurable.
- **Step ids** are letters: `a` to `z`, then `aa`, `ab`, ... (bijective base 26).
  The next id is one past the highest existing one. Gaps are never reused, but
  removing the *last* step and adding again mints the same letter again.
- **Statuses.** Open: `pending`, `blocked`, `in_progress`. Terminal: `done`,
  `skipped`. Any other value is neither: readers report it as malformed, and
  `doctor fix` resets it to `pending` with a note saying what it was.
- **Dependencies.** A bare id (`c`) is a sibling step. A step ref
  (`TASK-API-002.c`) is met when that step is terminal; every step of an archived
  task counts as met. A task ref (`TASK-API-002`) is met when the task is archived,
  or when it is in the queue with at least one step and all of its steps
  terminal. A reference that names nothing is *dangling*: `done` refuses and
  readiness reports the step as malformed, never as ready or waiting. `done`,
  `ready` and `doctor` share one resolver, so they never disagree.
- **Targets** route work to machines (see [Machines and readiness](readiness.md)).

## crossbox owns its files

Every write parses the whole file, changes the data, and writes it back in one
canonical form with `yaml.safe_dump`: known keys in the order shown above, then
any unknown keys in their original order; multi-line strings as `|` block
scalars; `needs` and `depends_on` as flow lists produced by the YAML emitter (so
`a: b` in a dependency is quoted, never turned into a mapping); and a line width
large enough that nothing is ever folded. Before the file is replaced, the output
is parsed again and compared with the intended data and the expected step-id
sequence; the file is then written to a temporary file in the same directory and
moved into place with `os.replace`, and read back once more. If any check fails,
nothing is written or the original bytes are restored. The restore only ever
undoes crossbox's own write: if the file no longer holds the bytes it wrote,
another writer replaced it, and the verb leaves it alone and exits 2.

**One writer at a time.** Every mutating verb holds an exclusive lock on the
repository root for its whole read-modify-write (`flock` on the directory on
Linux and macOS, so no lock file is left in the tree; a crashed process drops it).
Two agents, or an agent and a human, running verbs in one tree at once queue
behind each other instead of losing writes. A verb that cannot get the lock
within 60 seconds (`CROSSBOX_LOCK_TIMEOUT`) exits 2 without writing. Read-only
verbs take no lock. On Windows the lock is an exclusive-create lock file
(untested on Windows hardware).

The trade-off: **hand-written YAML comments are not preserved.** A write that
drops comments says so on stderr.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](limits.md) · [credits](credits.md)
