# Configuration

Everything lives in `.crossbox/config.yaml`, and every key is optional.
`crossbox init` writes a starter file. [examples/config.yaml](../examples/config.yaml)
is a commented two-machine example.

```yaml
prefix: TASK                  # task ids: TASK-001 or TASK-<DOMAIN>-001
remote: origin                # the remote the origin guard fetches
upstream: origin/main         # default: origin/<default branch>
extra_refs: ["refs/remotes/origin/*"]   # also read every pushed branch
priorities: [critical, high, normal, low]
stale_days: 3                 # claims older than this are flagged STALE, never released
rate_gate: {cap: 30, window: 600}       # cap >= 1; switch off with CROSSBOX_RATE_DISABLE=1
target_aliases: {either: any} # old target spellings, accepted on read only
machines:
  laptop:
    hostnames: [my-laptop]    # hostname -> machine name
    provides: [docker]
  workstation:
    provides: [gpu, docker]
# data paths (defaults shown), each relative to the repository root
dir: .crossbox
queue: .crossbox/queue
archive: .crossbox/archive
notes: .crossbox/notes
claims: .crossbox/claims.yaml
registry: .crossbox/needs-registry.yaml
```

**Validation.** The config is checked at the boundary. A field of the wrong type
makes every verb exit 2 (NOT EVALUABLE), and the error names the field. A key
crossbox does not know is reported on stderr, so a typo such as `stale_day:` is
visible and doesn't silently fall back to the default.

**Data paths stay inside the repository.** Each path is resolved, symlinks
included, and must land inside the repository and outside `.git`. An absolute
path, a `..` walk out, or a symlink that leaves the tree is refused. crossbox
never writes to an escaped path.

**Machine and slot.** The current machine is `CROSSBOX_MACHINE`, else the machine
whose `hostnames` include this host, else `host-` and six hex digits of a hash of the host name (stable and distinct per machine, but never the name itself, because claims and notes are committed; a writing command says so once). The current slot is the
git worktree name (`main` for the primary checkout); `CROSSBOX_SLOT` overrides it.
Both names must be a single safe path component (see [readiness](readiness.md)).

## Environment variables

| Variable | Effect |
|---|---|
| `CROSSBOX_ROOT` | the repository to operate on (same as `--root`) |
| `CROSSBOX_MACHINE`, `CROSSBOX_SLOT` | override machine and slot detection |
| `CROSSBOX_LOCK_TIMEOUT` | seconds to wait for the per-tree writer lock (default 60) |
| `CROSSBOX_RATE_CAP`, `CROSSBOX_RATE_WINDOW` | override `rate_gate:`; a cap of 0 or less disables it; a cap that is not an integer or a window that is not a positive number makes a metered verb exit 2 naming the variable |
| `CROSSBOX_RATE_DISABLE=1` | turn the rate gate off |
| `CROSSBOX_RATE_STATE` | where the rate-gate state lives (default `$XDG_STATE_HOME/crossbox/rate-gate.json`) |
| `CROSSBOX_SKIP_ORIGIN_GUARD=1` | skip the origin guard; every refusal names this override |

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
