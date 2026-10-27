# Design brief: groups (0.3)

Status: brief, not a contract. Decide the open questions before the contract.

## Problem

Tasks are the unit of filing; work often arrives in a different unit. A review
produces 12 findings that land as steps in 5 tasks. A release needs 9 steps
from 4 tasks. Today nothing ties those steps together, so nobody can ask "how
much of the review is fixed?", "what is left before the release?", or reserve
the whole lane for one agent.

## Shape

A group is a named, cross-task set of step refs with provenance:

    .crossbox/groups/GRP-001.yaml
    id: GRP-001
    title: Release 0.3 findings
    kind: review            # review | release | lane | other (registry-checked)
    source: docs/reviews/0.3.md   # where the set came from (provenance)
    goal: every finding fixed or written up
    members: [CBX-CORE-001.a, CBX-REL-001.d, ...]
    status: open            # open | done

## Verbs

    crossbox group new "title" --kind K --source S --goal G [--step REF]...
    crossbox group add|rm GRP-NNN REF...
    crossbox group show GRP-NNN [--json]
    crossbox groups [--all] [--json]
    crossbox group done GRP-NNN        # only when every member is closed
    crossbox plan --groups             # rank groups: ready members, unblock fan-out

## Invariants to carry over (learned the hard way in 0.1)

- One writer: groups are written by the canonical writer under the same lock.
- Members are refs, so every rename verb (`mv`, `swap`, `reorder`,
  `renumber`) must rewrite group members too, or a group silently points at
  other work (the 0.1 bug "reorder moved a claim onto different work").
- A member that names no step is reported (orphan), never dropped.
- `group done` refuses while any member is open; a closed group whose member is
  reopened is reported by `doctor`.
- Group state is derived from members on every read, never stored as counts.
- Origin guard: minting `GRP-NNN` in two worktrees must collide loudly, like
  task ids.

## Open questions (for the maintainer)

1. Group claims: does `claim --group` reserve a lane (advisory, shown in plan)
   without marking members CLAIMED? The internal tool crossbox came from says yes; simpler to defer.
2. Can a step be in several groups? (Proposed: yes; views show all.)
3. Archive: do done groups move to `archive/groups/`?
4. Is `kind` worth a registry, or free text?

## Estimate

Similar to plan + views combined, plus rename-verb plumbing; one review round
of its own.
