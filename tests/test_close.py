"""done, batch-done, skip, reopen, and the shared dependency resolution
(invariants 23-34)."""
import yaml
from conftest import run
from conftest import task_path
import json
from pathlib import Path


def _w(repo, tid, body, tier="queue", name=None):
    d = repo / ".crossbox" / tier
    p = d / (name or f"{tid}.yaml")
    p.write_text(f"id: {tid}\ntitle: {tid} title\nsteps:\n{body}")
    return p


def S(i, st="pending", d="", target="any", extra=""):
    return (f"- id: {i}\n  title: s{i}\n  target: {target}\n  status: {st}\n"
            f"  depends_on: [{d}]\n{extra}")


def _step(p, i):
    return [s for s in yaml.safe_load(p.read_text())["steps"] if s["id"] == i][0]


def _ready_refs(repo):
    import json
    r = run(repo, "ready", "--json")
    j = json.loads(r.out)
    return j, {k: [x["ref"] for x in j[k]] for k in ("ready", "waiting", "malformed")}


def test_done_readiness_and_doctor_resolve_deps_identically(repo):
    # invariant 23: cross-task, whole-task and archived deps
    _w(repo, "TASK-B-001", S("a", "done") + S("b", "pending"))
    _w(repo, "TASK-C-001", S("a", "pending"), tier="archive", name="TASK-C-001-old.yaml")
    _w(repo, "TASK-A-001", S("a", d="TASK-B-001.a") + S("b", d="TASK-C-001.a")
       + S("c", d="TASK-B-001") + S("d", d="TASK-C-001"))
    _, refs = _ready_refs(repo)
    assert set(refs["ready"]) >= {"TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.d"}
    assert "TASK-A-001.c" in refs["waiting"]
    for i in "abd":
        assert run(repo, "done", f"TASK-A-001.{i}").code == 0
    assert run(repo, "done", "TASK-A-001.c").code == 1
    assert run(repo, "doctor", "check").code == 0


def test_skipped_counts_as_terminal_for_every_reader(repo):
    # invariant 24
    _w(repo, "TASK-B-001", S("a", "skipped"))
    _w(repo, "TASK-A-001", S("a", d="TASK-B-001.a") + S("b", "skipped") + S("c", d="b")
       + S("d", d="TASK-B-001"))
    _, refs = _ready_refs(repo)
    assert {"TASK-A-001.a", "TASK-A-001.c", "TASK-A-001.d"} <= set(refs["ready"])
    assert run(repo, "done", "TASK-A-001.c").code == 0


def test_dangling_dep_blocks_done_and_reads_malformed(repo):
    # invariant 25
    _w(repo, "TASK-A-001", S("a", d="zz") + S("b", d="TASK-Q-404.a") + S("c", d="TASK-Q-404"))
    j, refs = _ready_refs(repo)
    assert set(refs["malformed"]) == {"TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.c"}
    assert not refs["ready"] and not refs["waiting"]
    for i in "abc":
        r = run(repo, "done", f"TASK-A-001.{i}")
        assert r.code == 1 and "dangling" in r.err


def test_whole_task_dep_ignores_task_level_status(repo):
    # invariant 26
    p = repo / ".crossbox" / "queue" / "TASK-B-001.yaml"
    p.write_text("id: TASK-B-001\ntitle: b\nstatus: done\nsteps:\n- id: a\n  title: x\n"
                 "  status: pending\n")
    _w(repo, "TASK-A-001", S("a", d="TASK-B-001"))
    assert run(repo, "done", "TASK-A-001.a").code == 1
    p.write_text("id: TASK-B-001\ntitle: b\nstatus: pending\nsteps:\n- id: a\n  title: x\n"
                 "  status: done\n")
    assert run(repo, "done", "TASK-A-001.a").code == 0


def test_whole_task_dep_on_empty_queue_task_is_unmet(repo):
    (repo / ".crossbox" / "queue" / "TASK-B-001.yaml").write_text(
        "id: TASK-B-001\ntitle: b\nsteps: []\n")
    _w(repo, "TASK-A-001", S("a", d="TASK-B-001"))
    assert run(repo, "done", "TASK-A-001.a").code == 1


def test_done_never_overwrites_attribution(repo):
    # invariant 27
    p = _w(repo, "TASK-A-001", S("a", "pending", extra="  completed_by: workstation\n"
                                                      "  completed: 2020-01-01\n"))
    assert run(repo, "done", "TASK-A-001.a").code == 0
    s = _step(p, "a")
    assert s["completed_by"] == "workstation" and str(s["completed"]) == "2020-01-01"


def test_done_on_done_is_a_noop(repo):
    # invariant 28
    p = _w(repo, "TASK-A-001", S("a", "done"))
    before = p.read_text()
    r = run(repo, "done", "TASK-A-001.a")
    assert r.code == 0 and "already done" in r.out
    assert p.read_text() == before


def test_done_refuses_target_not_workable_here(repo):
    # invariant 29 (laptop provides docker, not gpu)
    p = _w(repo, "TASK-A-001", S("a", target="gpu") + S("b", target="docker")
           + S("c", target="workstation"))
    assert run(repo, "done", "TASK-A-001.a").code == 1
    assert run(repo, "done", "TASK-A-001.c").code == 1
    assert run(repo, "done", "TASK-A-001.b").code == 0
    assert run(repo, "done", "TASK-A-001.a", machine="workstation").code == 0
    assert _step(p, "a")["completed_by"] == "workstation"


def test_done_rejects_unknown_flags(repo):
    # invariant 30 (open in the original design)
    p = _w(repo, "TASK-A-001", S("a"))
    before = p.read_text()
    r = run(repo, "done", "TASK-A-001.a", "--notes", "x")
    assert r.code == 64
    assert p.read_text() == before


def test_done_refuses_to_flip_skipped(repo):
    p = _w(repo, "TASK-A-001", S("a", "skipped"))
    before = p.read_text()
    assert run(repo, "done", "TASK-A-001.a").code == 1
    assert p.read_text() == before


def test_done_force_records_unmet_deps_in_notes(repo):
    p = _w(repo, "TASK-A-001", S("a") + S("b", d="a"))
    assert run(repo, "done", "TASK-A-001.b", "--force").code == 0
    assert "CLOSED WITH --force" in _step(p, "b")["notes"]


def test_batch_done_continues_and_reports_partial(repo):
    _w(repo, "TASK-A-001", S("a") + S("b", d="zz") + S("c"))
    r = run(repo, "batch-done", "TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.c")
    assert r.code == 3 and "closed 2 of 3" in r.out
    r = run(repo, "bd", "TASK-A-001.b", "--force")
    assert r.code == 0
    r = run(repo, "batch-done", "TASK-A-001.q")
    assert r.code == 1


def test_skip_and_reopen_require_reason_and_refuse_noops(repo):
    # invariant 31
    p = _w(repo, "TASK-A-001", S("a") + S("b", "done"))
    assert run(repo, "skip", "TASK-A-001.a").code == 64
    assert run(repo, "reopen", "TASK-A-001.b").code == 64
    before = p.read_text()
    assert run(repo, "skip", "TASK-A-001.b", "--reason", "x").code == 1
    assert run(repo, "reopen", "TASK-A-001.a", "--reason", "x").code == 1
    assert run(repo, "skip", "TASK-A-001.a", "--reason", "  ").code == 1
    assert p.read_text() == before


def test_superseded_by_must_resolve(repo):
    # invariant 32
    _w(repo, "TASK-B-001", S("a"))
    _w(repo, "TASK-C-001", S("a"), tier="archive", name="TASK-C-001-retired.yaml")
    p = _w(repo, "TASK-A-001", S("a") + S("b") + S("c") + S("d"))
    assert run(repo, "skip", "TASK-A-001.a", "--reason", "r", "--superseded-by", "TASK-Z-001").code == 1
    assert run(repo, "skip", "TASK-A-001.a", "--reason", "r", "--superseded-by", "TASK-B-001.q").code == 1
    assert run(repo, "skip", "TASK-A-001.a", "--reason", "r", "--superseded-by", "TASK-A-001.a").code == 1
    assert run(repo, "skip", "TASK-A-001.a", "--reason", "r", "--superseded-by", "TASK-B-001.a").code == 0
    assert run(repo, "skip", "TASK-A-001.b", "--reason", "r", "--superseded-by", "TASK-C-001").code == 0
    assert "Superseded by: TASK-C-001" in _step(p, "b")["notes"]


def test_skip_and_edit_step_reach_archived_editorial_filenames(repo):
    # invariant 33
    p = _w(repo, "TASK-C-001", S("a"), tier="archive", name="TASK-C-001-retired.yaml")
    assert run(repo, "skip", "TASK-C-001.a", "--reason", "gone").code == 0
    assert run(repo, "edit-step", "TASK-C-001.a", "--append-notes", "correction").code == 0
    s = _step(p, "a")
    assert s["status"] == "skipped" and s["notes"].endswith("correction")


def test_reopen_preserves_retracted_stamps(repo):
    # invariant 34
    p = _w(repo, "TASK-A-001", S("a", "done", extra="  completed_by: workstation\n"
                                                   "  completed: 2020-01-01\n") + S("b", "done", d="a"))
    r = run(repo, "reopen", "TASK-A-001.a", "--reason", "regressed")
    assert r.code == 0
    s = _step(p, "a")
    assert s["status"] == "pending" and "completed" not in s and "completed_by" not in s
    assert "completed_by=workstation" in s["notes"] and "completed=2020-01-01" in s["notes"]
    assert "TASK-A-001.b depends on" in r.err


def _one_step(repo):
    assert run(repo, "new", "T").code == 0
    assert run(repo, "add", "TASK-001", "work").code == 0


def test_done_on_a_step_another_session_claims_closes_but_warns(repo):
    """R5/F9: see INVARIANTS.md, fifth-review findings."""
    _one_step(repo)
    assert run(repo, "claim", "TASK-001.a", machine="workstation").code == 0
    r = run(repo, "done", "TASK-001.a")
    assert r.code == 0, r                                  # claims are advisory
    assert "status: done" in task_path(repo, "TASK-001").read_text()
    assert "CLM-workstation-" in r.err and "workstation/" in r.err, r
    assert "TASK-001.a" in r.err


def test_done_on_my_own_claim_or_an_unclaimed_step_does_not_warn_about_a_holder(repo):
    """R5/F9: see INVARIANTS.md, fifth-review findings."""
    _one_step(repo)
    assert run(repo, "claim", "TASK-001.a").code == 0
    r = run(repo, "done", "TASK-001.a")
    assert r.code == 0 and "already claimed" not in r.err and "claimed by" not in r.err, r
    assert run(repo, "add", "TASK-001", "more").code == 0
    r = run(repo, "done", "TASK-001.b")
    assert r.code == 0 and "claimed by" not in r.err, r


def test_a_released_foreign_claim_is_not_mentioned(repo):
    """R5/F9: see INVARIANTS.md, fifth-review findings."""
    _one_step(repo)
    assert run(repo, "claim", "TASK-001.a", machine="workstation").code == 0
    assert run(repo, "release", "CLM-workstation-001", machine="workstation").code == 0
    r = run(repo, "done", "TASK-001.a")
    assert r.code == 0 and "claimed by" not in r.err, r


def _task(root, tid, steps, fname=None, title="T", tier="queue", **extra):
    data = {"id": tid, "title": title, "priority": "normal", "steps": steps}
    data.update(extra)
    p = Path(root) / ".crossbox" / tier / (fname or f"{tid}.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10 ** 6))
    return p


def _step_r6(sid, title="s", status="pending", dep=None):
    return {"id": sid, "title": title, "target": "any", "needs": [],
            "status": status, "depends_on": dep or []}


def test_a_task_ref_to_an_unparseable_archived_task_is_not_met(repo):
    """R6/F7: see INVARIANTS.md, sixth-review findings."""
    (repo / ".crossbox" / "archive" / "TASK-050.yaml").write_text(
        "id: TASK-050\ntitle: x\nsteps: [\n")
    _task(repo, "TASK-001", [_step_r6("a", dep=["TASK-050"]),
                              _step_r6("b", dep=["TASK-050.a"])])
    res = json.loads(run(repo, "ready", "--json").out)
    assert res["ready"] == [], res
    causes = {m["ref"]: m["cause"] for m in res["malformed"]}
    assert "does not parse" in causes["TASK-001.a"], causes
    assert "does not parse" in causes["TASK-001.b"], causes


def test_a_task_ref_to_a_good_archived_task_is_still_met(repo):
    """R6/F7: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-050", [_step_r6("a", status="done")], tier="archive")
    _task(repo, "TASK-001", [_step_r6("a", dep=["TASK-050"])])
    res = json.loads(run(repo, "ready", "--json").out)
    assert [x["ref"] for x in res["ready"]] == ["TASK-001.a"], res
