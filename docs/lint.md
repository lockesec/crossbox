# lint: what the notes decide that the structure does not

A queue drifts when a decision is written in prose and never reaches the fields readiness
reads. `crossbox lint` reads each open step's title and notes for two things, and reports them
with the sentence and a denominator; it never rewrites anything.

| rule | finds |
|---|---|
| `gate-not-in-depends_on` | a gating phrase names another step or task that `depends_on` omits, so `ready` and `plan` rank the step as workable |
| `decision-but-open` | a note line or sentence that opens with a decision marker while the step is pending or in progress |

**Gating phrases**: anywhere in a sentence, "blocked by", "blocked on", "waits for",
"waiting on / for", "depends on", "do not start before / until"; at the start of a sentence,
"needs", "requires", "after", "once". The gate is the step or task named right after the
phrase, inside its clause (up to 60 characters, cut at `;`, `:`, a parenthesis, a dash, or
", and"): "Once stable verdict is confirmed: mark TASK-A-001.a DONE" names an action target,
not a gate. A step already in `depends_on`, or inside a task already in it, is fine.

**Decision markers**, at the start of a line (not mid-line: "... the trades table. Superseded
by lifecycle_log." is about the table): "Deferred:", "On hold:", "Parked:", "Won't do:",
"Wontfix:", "Not doing:", "Cancelled:", "Abandoned:" (with `:`, `-` or `(`), "Superseded by X";
the marker in capitals (`DEFERRED`, `ON HOLD`, `PARKED`, `WONTFIX`, `SUPERSEDED`, not
`SUPERSEDED-CANDIDATE`); and the forms a real queue uses: "Deferred by ...", "Deferred
<date>", "Deferred until", "DEFER until", "DEFER —", "DEFER-FOR-EVIDENCE", "BLOCKED until",
"BLOCKED —", "BLOCKED ON <WHO>". "DEFERRED from d2", "DEFERRED in step f" and "DEFERRED
DELIBERATELY from X" mean the work moved here and are not read. A decision word inside a
sentence ("this was not deferred", "Blocked tests are fixed now") is not a marker.

**What is skipped**: a gate on a step or task that is already closed (nothing for readiness to
miss); a sentence that says its gate no longer holds ("is stale", "no longer", "obsolete"). A
task file that does not parse is named as UNREADABLE and the run exits 2: its steps were not
read, so the counts are a floor.

**Measured** on a real queue of 992 open steps (the system crossbox was extracted from), with
its archive. The first version reported 47 findings; this file then claimed "25 findings, 24
true", and a separate review session re-read all 25 and found at most 21 true (a gate the note
itself called stale, moved-here work, a mid-line "Superseded by" about a table, gates on steps
already closed). Each became a rule above and a regression test. With the marker forms that
queue uses (taken from that system's own drift tool), the current version reports 38 findings,
all judged true by hand against the step notes; the known misses are a gate named inside
parentheses and a gate or step written as a bare relative id ("After 023.i"). Exit 0 clean, 1
findings, 2 NOT EVALUABLE (no queue, or an unreadable task file).

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
