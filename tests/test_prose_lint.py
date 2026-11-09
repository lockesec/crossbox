"""`crossbox lint`: decisions the notes state that the structure does not carry.

Two narrow rules, both anchored, because a bare keyword scan flags far more than it should:
(a) a gating phrase names another step that depends_on omits ("blocked by TASK-A-001.c");
(b) a note line that STARTS with a decision marker ("Deferred:", "On hold:", "Superseded by
X") while the step is still open. Advisory: it never rewrites."""
import json

import pytest
from conftest import run


def _task(repo, steps, tid="TASK-A-001"):
    body = ""
    for sid, extra in steps:
        body += f"- id: {sid}\n  title: step {sid}\n  status: pending\n{extra}"
    (repo / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: t\npriority: normal\nsteps:\n{body}")


def _lint(repo):
    r = run(repo, "lint", "--json")
    return r, json.loads(r.out)


@pytest.mark.parametrize("note", ["Blocked by TASK-A-001.a until it lands.",
                                  "Waits for TASK-A-001.a to merge.",
                                  "Do not start before TASK-A-001.a is done.",
                                  "Depends on TASK-A-001.a (the schema).",
                                  "Needs TASK-A-001.a first."])
def test_a_gate_named_in_prose_that_depends_on_omits_is_found(repo, note):
    _task(repo, [("a", ""), ("b", f"  notes: {json.dumps(note)}\n")])
    r, j = _lint(repo)
    assert r.code == 1
    (row,) = j["rows"]
    assert (row["ref"], row["rule"], row["names"]) == ("TASK-A-001.b", "gate-not-in-depends_on", "TASK-A-001.a")
    assert note in row["sentence"]


@pytest.mark.parametrize("note", ["Same approach as TASK-A-001.a.",            # a mention, not a gate
                                  "TASK-A-001.a found this bug.",
                                  "Unblocks TASK-A-001.a once done."])          # the other direction
def test_a_mere_mention_is_not_a_gate(repo, note):
    _task(repo, [("a", ""), ("b", f"  notes: {json.dumps(note)}\n")])
    r, j = _lint(repo)
    assert r.code == 0 and j["rows"] == []


def test_a_gate_depends_on_already_carries_is_fine(repo):
    _task(repo, [("a", ""), ("b", '  depends_on: [a]\n  notes: "Blocked by TASK-A-001.a."\n')])
    assert _lint(repo)[1]["rows"] == []


@pytest.mark.parametrize("note", ["Deferred: waiting on the vendor.", "DEFERRED until Q3",
                                  "On hold: the owner's call.", "Superseded by TASK-B-002.a",
                                  "Won't do: the API was removed.", "Parked: no data yet."])
def test_a_decision_marker_on_an_open_step_is_found(repo, note):
    _task(repo, [("a", f"  notes: {json.dumps(note)}\n")])
    r, j = _lint(repo)
    assert r.code == 1
    (row,) = j["rows"]
    assert row["rule"] == "decision-but-open" and row["status"] == "pending"


@pytest.mark.parametrize("note", ["This was not deferred.", "We may defer the UI part later.",
                                  "The old one was superseded; this is the new one.",
                                  "Blocked tests are fixed now."])
def test_a_decision_word_inside_a_sentence_is_not_a_marker(repo, note):
    _task(repo, [("a", f"  notes: {json.dumps(note)}\n")])
    assert _lint(repo)[1]["rows"] == []


def test_closed_steps_are_not_linted_and_the_denominator_is_open_steps(repo):
    _task(repo, [("a", '  notes: "Deferred: later."\n'), ("b", ""), ("c", "")])
    q = repo / ".crossbox" / "queue" / "TASK-A-001.yaml"
    q.write_text(q.read_text().replace("- id: a\n  title: step a\n  status: pending",
                                       "- id: a\n  title: step a\n  status: skipped"))
    r, j = _lint(repo)
    assert r.code == 0 and j["scanned"] == 2
    assert run(repo, "lint").out.splitlines()[0] == "lint: 0 finding(s) over 2 open step(s)"


def test_lint_is_read_only(repo):
    from conftest import git
    from crossbox import cli
    assert "lint" in cli.READ_ONLY
    _task(repo, [("a", '  notes: "Deferred: later."\n')])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "q")
    run(repo, "lint")
    assert git(repo, "status", "--porcelain").stdout == ""


# --- cases from a run over a real 992-step queue -------------------------------------------------

@pytest.mark.parametrize("note", [
    "Once stable verdict is confirmed: mark TASK-A-001.a step (f) DONE.",      # an action target, not a gate
    "Both are already known to TASK-A-001.a (its c is BLOCKED on the exit knobs).",
    "There is no measurement (TASK-A-001.a blocked on the smoke run).",
    "Requires the run corpus, so this is run-tree work (the TASK-A-001.a guard applies).",
])
def test_a_step_named_away_from_the_gating_phrase_is_not_a_gate(repo, note):
    _task(repo, [("a", ""), ("b", f"  notes: {json.dumps(note)}\n")])
    assert _lint(repo)[1]["rows"] == []


def test_only_the_step_right_after_the_gating_phrase_is_the_gate(repo):
    note = "Absorbs TASK-A-001.a and TASK-A-001.c, and depends on TASK-A-001.d (warm state)."
    _task(repo, [("a", ""), ("b", f"  notes: {json.dumps(note)}\n"), ("c", ""), ("d", "")])
    assert [r["names"] for r in _lint(repo)[1]["rows"]] == ["TASK-A-001.d"]


@pytest.mark.parametrize("note", ["DEFERRED from d2 to keep batch context manageable.",
                                  "DEFERRED in step f.",
                                  "SUPERSEDED-CANDIDATE, NOT CLOSED: a human call."])
def test_moved_here_and_candidate_markers_are_not_decisions(repo, note):
    _task(repo, [("a", f"  notes: {json.dumps(note)}\n")])
    assert _lint(repo)[1]["rows"] == []


def _lint_r7(repo):
    return json.loads(run(repo, "lint", "--json").out)["rows"]


def _task_r7(repo, steps, tid="TASK-A-001"):
    body = "".join(f"- id: {sid}\n  title: step {sid}\n  status: {st}\n{extra}" for sid, st, extra in steps)
    (repo / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: t\npriority: normal\nsteps:\n{body}")


def test_a_gate_on_a_step_already_closed_is_not_drift(repo):
    """R7: see INVARIANTS.md, seventh-review findings."""
    _task_r7(repo, [("a", "done", ""), ("b", "pending", '  notes: "Once TASK-A-001.a ships, rename."\n')])
    assert _lint_r7(repo) == []


def test_a_sentence_saying_a_gate_is_stale_is_not_a_gate(repo):
    """R7: see INVARIANTS.md, seventh-review findings."""
    note = "The note that 'Blocked on TASK-A-001.a' is stale; now blocked on a publisher."
    _task_r7(repo, [("a", "pending", ""), ("b", "pending", f"  notes: {json.dumps(note)}\n")])
    assert _lint_r7(repo) == []


def test_deferred_with_an_adverb_from_elsewhere_is_moved_work(repo):
    """R7: see INVARIANTS.md, seventh-review findings."""
    _task_r7(repo, [("a", "pending", '  notes: "DEFERRED DELIBERATELY from TASK-A-001.b, 2026-08-21."\n')])
    assert _lint_r7(repo) == []


def test_a_decision_marker_counts_only_at_the_start_of_a_line(repo):
    """R7: see INVARIANTS.md, seventh-review findings."""
    note = "Writes go to the trades table. Superseded by lifecycle_log."
    _task_r7(repo, [("a", "pending", f"  notes: {json.dumps(note)}\n")])
    assert _lint_r7(repo) == []
    _task_r7(repo, [("a", "pending", '  notes: "Superseded by TASK-B-002.a"\n')])
    assert [r["rule"] for r in _lint_r7(repo)] == ["decision-but-open"]


@pytest.mark.parametrize("note", [
    "Deferred by user decision 2026-07-20 until TASK-B-002.a lands.",
    "Deferred 2026-09-27 (laptop): the clone refuses the change.",
    "DEFER until patch_validate has real use.",
    "DEFER — waiting on the next release, premise-checked 2026-08-31.",
    "DEFER-FOR-EVIDENCE: run on the baseline after the refactor.",
    "BLOCKED until TASK-B-002 ships.",
    "BLOCKED — the reader is correct and nothing produces the input.",
    "BLOCKED ON OPERATOR 2026-08-21: the decision is the owner's.",
])
def test_the_marker_forms_a_real_queue_uses_are_read(repo, note):
    """R7: see INVARIANTS.md, seventh-review findings."""
    _task_r7(repo, [("a", "pending", f"  notes: {json.dumps(note)}\n")])
    assert [r["rule"] for r in _lint_r7(repo)] == ["decision-but-open"]


@pytest.mark.parametrize("note", ["Blocked tests are fixed now.", "Deferred work is listed below.",
                                  "Defer to the owner on naming.", "BLOCKED-tests list is empty."])
def test_their_near_misses_are_not(repo, note):
    """R7: see INVARIANTS.md, seventh-review findings."""
    _task_r7(repo, [("a", "pending", f"  notes: {json.dumps(note)}\n")])
    assert _lint_r7(repo) == []
