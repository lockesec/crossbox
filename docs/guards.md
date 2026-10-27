# Guards against parallel sessions

## Origin guard

Before `add` (and once before `batch-add`), crossbox fetches the remote and reads
the task file at every sibling ref: each worktree's branch, `origin/<branch>` for
each, and the upstream. If any of them holds a step id the local file lacks, it
refuses (exit 1) and names the ref and the ids: rebase or land first. It does not
mint around the gap; a silent "use the next free letter" would hide that two
sessions are editing one task.

It exits 2 (NOT EVALUABLE) when the fetch fails or times out (30 s), worktrees
cannot be listed, no ref can be read while a remote exists, or the config sets a
`remote:` that the repository does not have (a typo is not a lone repository; `new`
is refused the same way). A repository with no `remote:` setting and no
`origin`, or a remote that fetches fine but has no branches yet (a freshly created
one), has nothing to compare and is fine. Refs whose tip is already in your history,
and ids you removed yourself in your last commit, are not gaps.
`CROSSBOX_SKIP_ORIGIN_GUARD=1` skips it, and every refusal says so.

**Task ids** are minted the same way, from the union of local queue and archive
(`id:` fields and file names) and the file names under the queue and archive at
every sibling ref, suffixed names included. If the fetch fails the id is minted
with a "possibly stale" warning. `renumber --next` uses the same minter.

Refs only show what was committed, so both the guard and the minter also read the
**working copies of the other worktrees of this clone** (from `git worktree list`,
read-only): a step id or task file another worktree has minted and not yet
committed counts too. What remains unseen is an uncommitted mint in *another
clone* (another machine, or a second clone on this one): the window lasts until
that session commits, pushes and you fetch, which can be hours, not seconds. It
shows up as a loud merge conflict, not a silent duplicate.

## Rate gate

Every mutating verb except `init` and `doctor install-hook` / `install-merge-driver`
is metered before it runs, by its canonical name (so `ba` and `batch-add` are one
verb); read-only verbs are never counted. Default: 30 calls per
rolling 600 seconds per machine user, shared by all slots on the machine. Past the
cap the verb is refused (exit 1) with the time until the window reopens. A refused
call does not use up budget.

`done`, `batch-done`, `skip`, `edit-step` and `release` are exempt when every step or
claim they name is held by an active claim of *this* slot (`release --all-mine` names
exactly those claims, so it qualifies when this slot holds any), so an agent closing
or skipping work it has claimed is never locked out. `reopen` and the minting verbs
(`new`, `add`, `claim`, ...) are never exempt.

The state file lives outside the repository (`$XDG_STATE_HOME/crossbox/rate-gate.json`,
override with `CROSSBOX_RATE_STATE`). A corrupt state file counts as empty and an
unwritable one lets the call through: only a trip ever blocks. The count is
read, checked and written under a machine-wide lock file beside the state file,
so parallel callers cannot each read the same short list and all pass.
`CROSSBOX_RATE_DISABLE=1` turns it off; `CROSSBOX_RATE_CAP` and
`CROSSBOX_RATE_WINDOW` (or `rate_gate:` in the config) change it. In the config
`cap` must be at least 1; to switch the gate off for one shell, set
`CROSSBOX_RATE_CAP=0`.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
