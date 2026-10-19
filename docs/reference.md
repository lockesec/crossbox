# Command reference

## Verbs

Every verb is also a Python function (`crossbox.verbs.cmd_add(ctx, ...)` and so on;
`crossbox.cli.main(argv)` runs one from a list of arguments).

| Verb | What it does |
|---|---|
| `init [--prefix P]` | create `.crossbox/`, a starter config and the `.gitattributes` line |
| `new "title" [-d DOM] [-p PRIO]` | mint the next task id (sibling-aware, below) and write the task; refuses if the file exists |
| `add TASK "title" [-t TARGET] [-n a,b] [-D a,b] [--notes S] [-p PRIO]` | add a step; runs the origin guard first; dependencies are checked |
| `batch-add TASK FILE\|-` (`ba`) | one step per line: `title \| target \| needs \| depends \| notes \| priority`, or JSONL rows. `\|` is a literal pipe; a surplus column refuses the whole batch; apostrophes survive. Prints `Added N of M (yaml delta: D)` |
| `edit TASK [-p P] [--phase X] [--source S] [--title T]` | task-level fields |
| `edit-step REF ...` (`es`) | `--title`, `--notes` (refuses over existing notes without `--replace-notes`), `--append-notes`, `--clear-notes`, `--target`, `--needs`, `--depends` (refuses self-deps and cycles through this step), `--premise-verdict`. Works on archived tasks too |
| `remove-step REF [--force]` (`rm-step`) | refuses closed steps and steps others depend on unless `--force`, which also strips the references |
| `move-step REF NEW [--force]` (`mv`) | rename a step id and rewrite every dependency on it, including other tasks'; carries this slot's claims and refuses when another session's claim binds the step ([claims](claims.md)) |
| `swap REF OTHER [--force]` | exchange two step ids in place; claims as for `move-step` |
| `reorder REF --before X \| --after X \| --to N [--force]` (`ro`) | move a step and renumber all ids from `a`; refuses to rename closed steps, or steps another session has claimed, without `--force`; a no-op exits 0 |
| `done REF [--force] [--commit SHA]...` | close a step: target must be workable here, dependencies met (or `--force`, which records what was unmet); never overwrites `completed_by`/`completed`; refuses a skipped step; unknown flags are usage errors. `--commit` records the full sha under `commits:` after checking it exists (refused otherwise, exit 2 outside git), prints REACHABLE / UNMERGED / UNKNOWN against the upstream (or a local main/master), and warns when the commit neither names the step in its subject nor carries a `Crossbox-Step:` trailer. On a step already done it adds the evidence; `reopen` retracts it into the notes |
| `batch-done REF... [--force]` (`bd`) | close several; continues past failures and reports `closed N of M` |
| `skip REF --reason R [--superseded-by X]` | retire a step, no dependency gate; the reason goes into its notes |
| `reopen REF --reason R` | back to `pending`; the retracted stamps go into its notes; lists dependents still marked done |
| `show TASK [--full]` | one task; notes are cut to two lines unless `--full` (a truncated view is not evidence that a note is absent) |
| `status [--domain D]` | the queue listing |
| `status --summary [--json] [--ready]` | counts by status, priority, domain and target, claims, and (with `--ready`) readiness |
| `archive` | move every task whose steps are all terminal; never overwrites a different task |
| `unarchive TASK` | move a task back; refuses when both tiers hold the id |
| `renumber TASK (N \| NEW-ID \| --next) [--file PATH] [--dry-run] [--force]` (`mv-task`) | new id, `git mv` when tracked, references rewritten with a boundary-safe match (`TASK-A-034` never matches inside `TASK-A-0344`); claims as for `move-step` |
| `check-collisions [--against REF] [--json]` | queue files that differ from REF: same title = modified, different title = collision |
| `check-duplicate-ids [--baseline REF] [--json]` | ids held by two files in one tier (queue-and-archive is only "shadowed"); a file that does not parse makes it exit 2 |
| `check-empty-tasks [--strict] [--json]` | tasks with no steps; a file that does not parse makes it exit 2 |
| `note [--to MACHINE[/SLOT]] [--] MSG` | append to another machine's or slot's inbox |
| `notes [all]` / `clear-notes` | read your inboxes / archive them with a date |
| `claim`, `release`, `claims`, `claim-check` | [claims](claims.md) |
| `ready` (= `needs here`), `needs registry`, `needs audit` | [readiness](readiness.md) |
| `activity [--json] [--hot-hours H]` | claim liveness from git (HOT, QUIET, COLD, UNKNOWN) and open steps with commits but no claim ([claims](claims.md#activity)) |
| `closures [--json] [--days N]` | are the queue's closures true? unreachable commits, closing commits for open steps, missing artifacts, unmerged branches ([closures](closures.md)) |
| `lint [--json]` | decisions the notes state that the structure does not carry: a gate named in prose that `depends_on` omits, a note opening with a decision marker on an open step ([lint](lint.md)); advisory, never rewrites |
| `why [REF] [--json] [--operator] [--no-probes]` | why a step is not workable here, traced to its root blockers; without REF, the queue-wide count ([readiness](readiness.md#why)) |
| `plan [--top N] [--json]` | ranked READY steps, the critical path, waves and a topological order over every open step; read only, no model: [plan](plan.md) |
| `progress [--by task\|domain\|priority] [--archive] [--json]` | closed/total step counts per task, domain or priority, each beside its denominator; unreadable task files listed and the counts marked a floor; read only: [views](views.md) |
| `near-done [--max-open N] [--min-pct P] [--json]` | tasks with few open steps and mostly closed, each remaining step's state here and which configured machines could work it; read only: [views](views.md) |
| `graph [--format mermaid\|dot\|json] [--task ID]... [--open] [--max-nodes N] [--max-edges N] [--max-chars N] [--no-probes]` | the dependency graph for Mermaid or Graphviz, states as classes, critical path thick, titles escaped; refuses past `--max-nodes` instead of dropping nodes; read only: [views](views.md) |
| `doctor check \| fix \| selftest \| install-hook \| install-merge-driver` | [doctor](doctor.md) |
| `merge-driver %O %A %B` | the git merge driver for `claims.yaml` |

`renumber`, `move-step`, `swap`, `reorder` and `remove-step` rewrite or strip references in
other task files. A file that does not parse and whose text names the task or step
cannot be checked, so the verb exits 2 naming it and changes nothing.

`--machine NAME` (before the verb; env `CROSSBOX_MACHINE`) acts as that machine, so
`crossbox --machine gpu-box plan` shows the plan as that machine sees it.

`--root DIR` (or `CROSSBOX_ROOT`) points crossbox at a repository other than the
one containing the current directory.


## Exit codes

| Code | Meaning |
|---|---|
| 0 | ok (including a no-op such as `done` on a done step or `reorder` to the same place) |
| 1 | **Refused, or findings.** Includes a rate-gate trip, an origin-guard refusal, a `claim-check` overlap, `doctor check` problems, and a batch where nothing succeeded, and a `plan` with a malformed step, a dependency cycle or an orphaned claim, a `progress`, `near-done` or `graph` that hit an unreadable task file or a malformed or cyclic step it lists, and a `graph` over `--max-nodes`, `--max-edges` or `--max-chars`. The listings (`status`, `status --summary`, `ready`) also exit 1 when they skipped a task file that does not parse: they still show what they could read, name the file, and mark their counts as a floor |
| 2 | **Not evaluable.** The tool could not look: a fetch failed, a claims file is damaged, a ref does not resolve, a gate probe gave no answer (`ready`, `plan`, `near-done`, `graph`), the needs registry is malformed, a task file a check needs does not parse (`check-*`, `needs audit`), the queue is missing or `--root` is not a directory (every reader: `ready`, `plan`, `progress`, `near-done`, `graph`, `status`, `doctor check`, `check-*`, `needs audit`, `claims`, `claim-check`). Never a pass |
| 3 | **Partial.** `batch-add`, `batch-done`, `archive` or `doctor fix` did some of its work and refused the rest |
| 64 | Usage error, including an unknown flag |

`--json` output starts at byte 0; warnings go to stderr. A `--json` verb that is
not evaluable prints `{"not_evaluable": "<why>"}` on stdout.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
