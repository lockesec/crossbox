"""Claims, the sibling union, claim-check and the merge driver (invariants 51-63)."""
import datetime as dt
import json
import sys

import yaml
from conftest import git, run

from crossbox import claims as C
from crossbox import gitutil


def _claims(root):
    p = root / ".crossbox" / "claims.yaml"
    return (yaml.safe_load(p.read_text()) or {}).get("claims", []) if p.exists() else []


def _task(root, tid="TASK-A-001", steps="abc"):
    body = "".join(f"- id: {i}\n  title: s{i}\n  status: pending\n" for i in steps)
    (root / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: t\nsteps:\n{body}")


def test_subject_that_is_a_ref_binds_a_mention_does_not(repo):
    # invariant 51
    _task(repo)
    r = run(repo, "claim", "TASK-A-001.a")
    assert r.code == 0 and "-> TASK-A-001.a" in r.out
    r = run(repo, "claim", "looking at TASK-A-001.b today")
    assert r.code == 0 and "UNBOUND" in r.out
    rows = _claims(repo)
    assert rows[0]["step_ref"] == "TASK-A-001.a" and "step_ref" not in rows[1]


def test_repeated_step_flag_mints_one_claim_per_step(repo):
    # invariant 52
    _task(repo)
    assert run(repo, "claim", "batch", "--step", "TASK-A-001.a", "--step", "TASK-A-001.b").code == 0
    rows = _claims(repo)
    assert [r["step_ref"] for r in rows] == ["TASK-A-001.a", "TASK-A-001.b"]
    assert len({r["id"] for r in rows}) == 2
    assert run(repo, "release", rows[0]["id"]).code == 0
    st = {r["step_ref"]: r["status"] for r in _claims(repo)}
    assert st == {"TASK-A-001.a": "released", "TASK-A-001.b": "active"}


def test_slots_and_machines_never_share_a_counter(world):
    # invariant 53
    run(world.laptop, "claim", "x")
    run(world.wt1, "claim", "x")
    run(world.workstation, "claim", "x", machine="workstation")
    run(world.workstation, "claim", "y", "--slot", "wt1", machine="workstation")
    ids = [r["id"] for r in _claims(world.laptop) + _claims(world.wt1)
           + _claims(world.workstation)]
    assert ids == ["CLM-laptop-001", "CLM-laptop-wt1-001", "CLM-workstation-001",
                   "CLM-workstation-wt1-001"]
    wt1 = _claims(world.wt1)[0]
    assert wt1["machine"] == "laptop" and wt1["slot"] == "wt1"


def test_primary_and_non_git_trees_use_the_machine_namespace(plain, repo):
    # invariant 54
    for root in (plain, repo):
        run(root, "claim", "x")
        row = _claims(root)[0]
        assert row["id"] == "CLM-laptop-001" and row["slot"] == "main"
        assert "unknown" not in json.dumps(row)


def test_release_all_mine_after_a_detected_slot_claim(world):
    # the slot-form bug: a claim minted through DETECTION must be "mine"
    run(world.wt1, "claim", "x")                       # no --slot
    r = run(world.wt1, "release", "--all-mine")
    assert r.code == 0 and "released 1 of 1" in r.out
    assert _claims(world.wt1)[0]["status"] == "released"


def test_release_all_mine_touches_only_this_slot(repo):
    # invariant 55
    run(repo, "claim", "mine")
    run(repo, "claim", "peer", "--slot", "wt9")
    run(repo, "claim", "other machine", machine="workstation")
    r = run(repo, "release", "--all-mine")
    assert "released 1 of 1" in r.out
    r = run(repo, "claim", "mine again")
    r = run(repo, "release", "--all-machine")
    assert r.code == 0 and "released 1 of 2" in r.out and "wt9" in r.err
    r = run(repo, "release", "--all-machine", "--include-other-slots")
    assert "released 1 of 1" in r.out
    st = {x["subject"]: x["status"] for x in _claims(repo)}
    assert st["other machine"] == "active"
    assert run(repo, "release", "--all-mine").code == 1      # nothing matched
    assert run(repo, "release", "CLM-nope-001").code == 1


def test_claims_output_states_what_it_cannot_see(repo, world):
    # invariant 56
    run(repo, "claim", "x")
    out = run(repo, "claims").out
    assert "UNCOMMITTED" in out and "no upstream" in out
    run(world.laptop, "claim", "y")
    world.commit(world.laptop)
    out = run(world.laptop, "claims").out
    assert "not yet on origin/main" in out and "siblings:" in out


def test_sibling_union_release_is_monotonic_and_not_double_counted(world):
    # invariant 57
    _task(world.wt1)
    run(world.wt1, "claim", "TASK-A-001.a")
    world.commit(world.wt1)
    j = json.loads(run(world.laptop, "claims", "--json").out)
    assert [c["id"] for c in j["foreign_active"]] == ["CLM-laptop-wt1-001"]
    git(world.laptop, "merge", "-q", "wt1")
    j = json.loads(run(world.laptop, "claims", "--json").out)
    assert j["foreign_active"] == [] and len(j["local"]) == 1      # not double counted
    # released in the slot's branch -> released in main's view (monotonic)
    assert run(world.wt1, "release", "--all-mine").code == 0
    world.commit(world.wt1)
    j = json.loads(run(world.laptop, "claims", "--json").out)
    assert j["local"] == [] and j["foreign_active"] == []
    s = json.loads(run(world.laptop, "status", "--json").out)
    assert s["claims"]["active"] == 0
    r = run(world.laptop, "ready", "--json")
    assert "TASK-A-001.a" in [x["ref"] for x in json.loads(r.out)["ready"]]
    # a stale snapshot never resurrects: release locally, sibling still has the old active copy
    world2 = world.wt1
    run(world2, "claim", "TASK-A-001.b")
    world.commit(world2)
    git(world.laptop, "merge", "-q", "wt1")
    run(world.laptop, "release", "CLM-laptop-wt1-002")
    s = json.loads(run(world.laptop, "status", "--json").out)
    j = json.loads(run(world.laptop, "claims", "--json").out)
    assert s["claims"]["active"] == 0 and j["foreign_active"] == []


def test_unparseable_sibling_reported_and_enumeration_failure_not_evaluable(world, monkeypatch):
    # invariant 58
    (world.wt1 / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    world.commit(world.wt1)
    r = run(world.laptop, "claims")
    assert "UNPARSEABLE" in r.out and "wt1" in r.out

    def boom(repo):
        raise gitutil.GitError("simulated")
    monkeypatch.setattr(gitutil, "worktrees", boom)
    r = run(world.laptop, "claims")
    assert r.code == 2 and "NOT EVALUABLE" in r.out


def test_claim_check_unreadable_sibling_view_is_nonzero(world, monkeypatch):
    # invariant 59
    run(world.laptop, "claim", "rework the parser module", machine="workstation")
    assert run(world.laptop, "claim-check", "rework parser module tests").code == 1
    assert run(world.laptop, "claim-check", "unrelated gardening").code == 0

    def boom(repo):
        raise gitutil.GitError("simulated")
    monkeypatch.setattr(gitutil, "worktrees", boom)
    r = run(world.laptop, "claim-check", "unrelated gardening")
    assert r.code == 2 and "NOT EVALUABLE" in r.out


def test_claim_check_excludes_own_claims(world):
    run(world.wt1, "claim", "rework the parser module")
    assert run(world.wt1, "claim-check", "rework the parser module").code == 0
    assert run(world.laptop, "claim-check", "rework the parser module").code == 0  # uncommitted: invisible
    world.commit(world.wt1)
    assert run(world.laptop, "claim-check", "rework the parser module").code == 1


def test_staleness_rule(repo):
    # invariant 60
    today = dt.date(2026, 3, 10)
    rows = [{"id": f"CLM-x-{i}", "status": s, "created": c}
            for i, (s, c) in enumerate([("active", "2026-03-06"), ("active", "2026-03-07"),
                                        ("active", "not a date"), ("released", "2020-01-01")])]
    assert [C.is_stale(r, today, 3) for r in rows] == [True, False, False, False]
    p = repo / ".crossbox" / "claims.yaml"
    p.write_text(C.render_claims([dict(rows[0], machine="laptop", slot="main",
                                       subject="old", created="2000-01-01")]))
    r = run(repo, "claims")
    assert "STALE" in r.out
    assert _claims(repo)[0]["status"] == "active"                 # never auto-released


def test_collision_tie_break_is_first_to_reach_upstream(world):
    # invariant 61
    _task(world.laptop)
    world.commit(world.laptop)
    git(world.laptop, "push", "-q", "origin", "main")
    git(world.wt1, "merge", "-q", "main")
    run(world.wt1, "claim", "TASK-A-001.a")
    world.commit(world.wt1)
    run(world.laptop, "claim", "TASK-A-001.a")
    out = run(world.laptop, "claims").out
    assert "COLLISION on TASK-A-001.a" in out and "NOT EVALUABLE" in out
    world.commit(world.laptop)
    git(world.laptop, "push", "-q", "origin", "main")
    git(world.laptop, "fetch", "-q")
    out = run(world.laptop, "claims").out
    assert "CLM-laptop-001 reached upstream first" in out


def test_damaged_claims_make_readiness_and_summary_refuse(repo):
    # invariant 62
    _task(repo)
    (repo / ".crossbox" / "claims.yaml").write_text("<<<<<<< HEAD\nclaims: []\n=======\n")
    assert run(repo, "ready").code == 2
    assert run(repo, "status", "--summary").code == 2
    assert run(repo, "claim", "x").code == 2
    for bad in ("- a\n", "claims: {}\n", "claims:\n- just a string\n"):
        (repo / ".crossbox" / "claims.yaml").write_text(bad)
        assert run(repo, "ready", "--json").code == 2, bad


def _row(i, **kw):
    r = {"id": i, "machine": "laptop", "slot": "main", "subject": "s", "created": "2026-01-01",
         "status": "active"}
    r.update(kw)
    return r


def test_merge_driver_rules(tmp_path):
    # invariant 63
    ours = [_row("CLM-laptop-001"), _row("CLM-laptop-002", subject="ours only")]
    theirs = [_row("CLM-laptop-001", status="released", released="2026-01-02"),
              _row("CLM-laptop-002", subject="different work", machine="laptop"),
              _row("CLM-workstation-001", machine="workstation")]
    out = C.merge_rows(ours, theirs)
    assert out[0]["status"] == "released" and out[0]["released"] == "2026-01-02"
    by_id = {r["id"]: r for r in out}
    assert sorted(by_id) == ["CLM-laptop-001", "CLM-laptop-002", "CLM-laptop-003",
                             "CLM-workstation-001"]
    # two different claims minted as 002: both kept; the one whose identity
    # sorts lower keeps the id on every side, the other is renumbered
    assert by_id["CLM-laptop-002"]["subject"] == "different work"
    assert by_id["CLM-laptop-003"]["subject"] == "ours only"
    assert by_id["CLM-laptop-003"]["renumbered_from"] == "CLM-laptop-002"
    # statuses only advance: a released ours never re-activates
    back = C.merge_rows([_row("CLM-laptop-001", status="released")], [_row("CLM-laptop-001")])
    assert back[0]["status"] == "released"
    o, t, b = (tmp_path / n for n in ("o", "t", "b"))
    b.write_text("")
    o.write_text(C.render_claims(ours))
    t.write_text("claims: [broken\n")
    before = o.read_text()
    assert C.merge_driver(str(b), str(o), str(t)) == 1 and o.read_text() == before
    t.write_text(C.render_claims(theirs))
    assert C.merge_driver(str(b), str(o), str(t)) == 0
    assert len(C.parse_claims(o.read_text())) == 4


def test_merge_driver_installed_unions_claims_across_branches(world):
    cmd = f"{sys.executable} -m crossbox"
    src = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src")
    import os
    os.environ["PYTHONPATH"] = src + os.pathsep + os.environ.get("PYTHONPATH", "")
    try:
        r = run(world.laptop, "doctor", "install-merge-driver", "--command", cmd)
        assert r.code == 0, r
        # .gitattributes already maps claims.yaml (written by `crossbox init`)
        run(world.wt1, "claim", "slot work")
        world.commit(world.wt1)
        run(world.laptop, "claim", "main work")
        world.commit(world.laptop)
        cp = git(world.laptop, "merge", "-q", "--no-edit", "wt1", check=False)
        assert cp.returncode == 0, cp.stderr
        subjects = sorted(c["subject"] for c in _claims(world.laptop))
        assert subjects == ["main work", "slot work"]
    finally:
        os.environ["PYTHONPATH"] = os.environ["PYTHONPATH"].split(os.pathsep, 1)[1]


def test_claim_warns_but_never_refuses_on_a_held_step(world):
    _task(world.wt1)
    run(world.wt1, "claim", "TASK-A-001.a")
    world.commit(world.wt1)
    _task(world.laptop)
    r = run(world.laptop, "claim", "TASK-A-001.a")
    assert r.code == 0 and "already claimed by CLM-laptop-wt1-001" in r.err
    assert "only in this tree" in r.out


def test_without_the_merge_driver_the_same_merge_conflicts(world):
    # negative control for the test above: proves the driver is what unions
    run(world.wt1, "claim", "slot work")
    world.commit(world.wt1)
    run(world.laptop, "claim", "main work")
    world.commit(world.laptop)
    cp = git(world.laptop, "merge", "-q", "--no-edit", "wt1", check=False)
    assert cp.returncode != 0 and "CONFLICT" in (cp.stdout + cp.stderr)


def test_all_box_is_a_hidden_alias_of_all_machine(repo):
    """`release --all-box` is the old spelling of `--all-machine`: same effect, not in --help."""
    run(repo, "claim", "mine")
    run(repo, "claim", "peer", "--slot", "wt9")
    r = run(repo, "release", "--all-box")
    assert r.code == 0 and "released 1 of 2" in r.out and "wt9" in r.err
    r = run(repo, "release", "--all-box", "--include-other-slots")
    assert "released 1 of 1" in r.out
    import os
    import subprocess
    import sys
    from conftest import SRC
    helptext = subprocess.run([sys.executable, "-m", "crossbox", "release", "--help"],
                              capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": str(SRC)}).stdout
    assert "--all-machine" in helptext and "--all-box" not in helptext
