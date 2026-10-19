"""The rate gate (invariants 64-67)."""
import json

import pytest
from conftest import run

from crossbox import rategate
from crossbox.cli import _parser
from conftest import task_path


@pytest.fixture
def gated(repo, monkeypatch, tmp_path):
    monkeypatch.delenv("CROSSBOX_RATE_DISABLE", raising=False)
    monkeypatch.setenv("CROSSBOX_RATE_CAP", "3")
    monkeypatch.setenv("CROSSBOX_RATE_WINDOW", "600")
    (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text(
        "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n"
        "- id: b\n  title: y\n  status: pending\n")
    return repo


def _state(tmp_path):
    return json.loads((tmp_path / "rate" / "state.json").read_text())["stamps"]


def test_paced_use_never_trips_flood_trips_window_expiry_reopens(gated, tmp_path, monkeypatch):
    # invariant 64
    for i in range(3):
        assert run(gated, "note", "--to", "workstation", f"n{i}").code == 0
    r = run(gated, "note", "--to", "workstation", "flood")
    assert r.code == 1 and "rate gate" in r.err and "retry in" in r.err
    assert len(_state(tmp_path)) == 3                  # a trip does not consume budget
    stamps = [s - 601 for s in _state(tmp_path)]       # age every call out of the window
    (tmp_path / "rate" / "state.json").write_text(json.dumps({"stamps": stamps}))
    assert run(gated, "note", "--to", "workstation", "later").code == 0


def test_reads_are_never_counted(gated, tmp_path):
    for _ in range(10):
        assert run(gated, "status").code == 0
        assert run(gated, "show", "TASK-A-001").code == 0
    assert not (tmp_path / "rate" / "state.json").exists()


def test_gate_fails_open_on_corrupt_or_unwritable_state(gated, tmp_path, monkeypatch):
    # invariant 65
    st = tmp_path / "rate" / "state.json"
    st.parent.mkdir(parents=True, exist_ok=True)
    for junk in ("not json", '{"stamps": "x"}', "[1, 2]", '{"stamps": ["a", null]}'):
        st.write_text(junk)
        assert run(gated, "note", "--to", "workstation", "x").code == 0
    monkeypatch.setenv("CROSSBOX_RATE_STATE", str(tmp_path / "file-not-dir" / "s.json"))
    (tmp_path / "file-not-dir").write_text("i am a file")
    for _ in range(5):
        assert run(gated, "note", "--to", "workstation", "x").code == 0


def test_claimed_close_exemption_only_for_this_slots_active_claim(gated, tmp_path, monkeypatch):
    # invariant 66
    monkeypatch.setenv("CROSSBOX_RATE_CAP", "1000")
    assert run(gated, "claim", "TASK-A-001.a").code == 0
    assert run(gated, "claim", "TASK-A-001.b", "--slot", "wt9").code == 0   # another slot
    n = len(_state(tmp_path))
    assert run(gated, "edit-step", "TASK-A-001.a", "--append-notes", "x").code == 0
    assert run(gated, "done", "TASK-A-001.a").code == 0
    assert len(_state(tmp_path)) == n                                    # exempt
    assert run(gated, "done", "TASK-A-001.b").code == 0                  # peer's claim
    assert len(_state(tmp_path)) == n + 1
    assert run(gated, "release", "CLM-laptop-001").code == 0             # own claim id
    assert len(_state(tmp_path)) == n + 1
    assert run(gated, "reopen", "TASK-A-001.a", "--reason", "r").code == 0
    assert run(gated, "done", "TASK-A-001.a").code == 0                  # claim now released
    assert len(_state(tmp_path)) == n + 3
    assert run(gated, "claim", "TASK-A-001.a").code == 0                 # minting: never exempt
    assert len(_state(tmp_path)) == n + 4


def test_every_mutating_verb_is_metered_whatever_its_spelling(gated, tmp_path):
    # invariant 67 (open in the original design)
    sub = next(a for a in _parser()._actions if a.dest == "cmd")
    verbs = {p.get_default("verb") for p in sub.choices.values()}
    readers = {"show", "status", "claims", "claim-check", "ready", "needs", "doctor",
               "check-collisions", "check-duplicate-ids", "check-empty-tasks", "notes", "plan",
               "progress", "near-done", "graph", "merge-driver", "init", "why", "activity", "lint", "closures"}
    assert verbs - readers <= rategate.COUNTED
    assert {"batch-add", "reopen", "remove-step", "renumber", "unarchive"} <= rategate.COUNTED
    for alias, canonical in (("ba", "batch-add"), ("mv", "move-step"), ("ro", "reorder"),
                             ("rm-step", "remove-step"), ("mv-task", "renumber"),
                             ("bd", "batch-done"), ("es", "edit-step")):
        assert sub.choices[alias].get_default("verb") == canonical
    before = len(_state(tmp_path)) if (tmp_path / "rate" / "state.json").exists() else 0
    run(gated, "rm-step", "TASK-A-001.b")
    run(gated, "mv", "TASK-A-001.a", "q")
    assert len(_state(tmp_path)) == before + 2
    r = run(gated, "ro", "TASK-A-001.q", "--to", "1")
    assert r.code == 0
    r = run(gated, "mv-task", "TASK-A-001", "9")
    assert r.code == 1 and "rate gate" in r.err


def test_cap_zero_disables(gated, monkeypatch):
    monkeypatch.setenv("CROSSBOX_RATE_CAP", "0")
    for _ in range(6):
        assert run(gated, "note", "--to", "workstation", "x").code == 0


def test_batch_done_and_skip_exempt_for_own_claims_reopen_metered(gated, tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSBOX_RATE_CAP", "1000")
    assert run(gated, "claim", "TASK-A-001.a").code == 0
    assert run(gated, "claim", "TASK-A-001.b").code == 0
    assert run(gated, "add", "TASK-A-001", "c").code == 0
    assert run(gated, "claim", "TASK-A-001.c").code == 0
    n = len(_state(tmp_path))
    assert run(gated, "batch-done", "TASK-A-001.a", "TASK-A-001.b").code == 0
    assert len(_state(tmp_path)) == n                                   # exempt
    assert run(gated, "skip", "TASK-A-001.c", "--reason", "not needed").code == 0
    assert len(_state(tmp_path)) == n                                   # exempt
    assert run(gated, "reopen", "TASK-A-001.a", "--reason", "r").code == 0
    assert len(_state(tmp_path)) == n + 1                               # reopen stays metered


def test_batch_done_and_skip_metered_when_any_ref_is_not_mine(gated, tmp_path, monkeypatch):
    monkeypatch.setenv("CROSSBOX_RATE_CAP", "1000")
    assert run(gated, "claim", "TASK-A-001.a").code == 0
    assert run(gated, "claim", "TASK-A-001.b", "--slot", "wt9").code == 0
    n = len(_state(tmp_path))
    assert run(gated, "batch-done", "TASK-A-001.a", "TASK-A-001.b").code == 0
    assert len(_state(tmp_path)) == n + 1                               # mixed: metered


def _gate_env(monkeypatch, tmp_path, **env):
    monkeypatch.setenv("CROSSBOX_RATE_DISABLE", "0")
    monkeypatch.setenv("CROSSBOX_RATE_STATE", str(tmp_path / "gate.json"))
    for k, v in env.items():
        monkeypatch.setenv(k, v)


def test_an_invalid_rate_env_is_not_evaluable_and_names_the_variable(repo, monkeypatch, tmp_path):
    """R5/F5: see INVARIANTS.md, fifth-review findings."""
    assert run(repo, "new", "T").code == 0
    for var, bad in (("CROSSBOX_RATE_CAP", "two"), ("CROSSBOX_RATE_CAP", "2.5"),
                     ("CROSSBOX_RATE_WINDOW", "nan"), ("CROSSBOX_RATE_WINDOW", "inf"),
                     ("CROSSBOX_RATE_WINDOW", "-5"), ("CROSSBOX_RATE_WINDOW", "0"),
                     ("CROSSBOX_RATE_WINDOW", "soon")):
        _gate_env(monkeypatch, tmp_path, **{var: bad})
        r = run(repo, "add", "TASK-001", "x")
        assert r.code == 2 and var in r.err, (var, bad, r)
        monkeypatch.delenv(var)
    assert "id: a" not in task_path(repo, "TASK-001").read_text()


def test_a_bad_cap_does_not_hide_behind_a_good_window_and_reads_are_unmetered(repo, monkeypatch, tmp_path):
    """R5/F5: see INVARIANTS.md, fifth-review findings."""
    _gate_env(monkeypatch, tmp_path, CROSSBOX_RATE_CAP="two", CROSSBOX_RATE_WINDOW="600")
    assert run(repo, "status").code == 0          # reads never reach the gate


def test_documented_rate_env_values_still_work(repo, monkeypatch, tmp_path):
    """R5/F5: see INVARIANTS.md, fifth-review findings."""
    assert run(repo, "new", "T").code == 0
    _gate_env(monkeypatch, tmp_path, CROSSBOX_RATE_CAP="0", CROSSBOX_RATE_WINDOW="600")
    assert run(repo, "add", "TASK-001", "x").code == 0           # cap <= 0 disables
    _gate_env(monkeypatch, tmp_path, CROSSBOX_RATE_CAP="1", CROSSBOX_RATE_WINDOW="600")
    assert run(repo, "add", "TASK-001", "y").code == 0
    assert run(repo, "add", "TASK-001", "z").code == 1           # the gate still trips


def _at_cap(repo, monkeypatch, tmp_path, cap=3):
    (task_path(repo, "TASK-A-001")).write_text(
        "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n"
        "- id: b\n  title: y\n  status: pending\n")
    assert run(repo, "claim", "TASK-A-001.a").code == 0          # metering is off here
    assert run(repo, "claim", "TASK-A-001.b", "--slot", "wt9").code == 0
    monkeypatch.delenv("CROSSBOX_RATE_DISABLE", raising=False)
    monkeypatch.setenv("CROSSBOX_RATE_CAP", str(cap))
    monkeypatch.setenv("CROSSBOX_RATE_WINDOW", "600")
    for i in range(cap):
        assert run(repo, "note", "--to", "workstation", f"n{i}").code == 0
    assert run(repo, "note", "--to", "workstation", "over").code == 1


def _stamps(tmp_path):
    import json
    return json.loads((tmp_path / "rate" / "state.json").read_text())["stamps"]


def test_release_all_mine_is_exempt_at_the_cap_like_done(repo, monkeypatch, tmp_path):
    """R5/F7: see INVARIANTS.md, fifth-review findings."""
    _at_cap(repo, monkeypatch, tmp_path)
    n = len(_stamps(tmp_path))
    r = run(repo, "release", "--all-mine")
    assert r.code == 0, r
    assert len(_stamps(tmp_path)) == n


def test_release_that_touches_another_slots_claim_is_still_metered(repo, monkeypatch, tmp_path):
    """R5/F7: see INVARIANTS.md, fifth-review findings."""
    _at_cap(repo, monkeypatch, tmp_path)
    r = run(repo, "release", "--all-box", "--include-other-slots")
    assert r.code == 1 and "rate gate" in r.err, r
    # no claim of mine to release: nothing to exempt, the call is metered
    assert run(repo, "release", "--all-mine").code == 0
    r = run(repo, "release", "--all-mine")
    assert r.code == 1 and "rate gate" in r.err, r
