"""A claim is bound to a step's identity, not to an id string or a position.

Renaming verbs carry this slot's claims to the renamed step and refuse to
move anyone else's; the claims view and the merge driver agree on what one
claim is; merging is idempotent and commutative."""
import json

import yaml

from conftest import git, run, task_path

from crossbox import claims as C


def _three(root):
    assert run(root, "new", "T").code == 0
    for t in ("first", "second", "third"):
        assert run(root, "add", "TASK-001", t).code == 0


def _by_title(root):
    j = json.loads(run(root, "ready", "--json").out)
    data = yaml.safe_load(task_path(root, "TASK-001").read_text())
    title = {f"TASK-001.{s['id']}": s["title"] for s in data["steps"]}
    return ({title[r["ref"]] for r in j["ready"]}, {title[r["ref"]] for r in j["claimed"]}, j)


def _claim_rows(root):
    return yaml.safe_load((root / ".crossbox" / "claims.yaml").read_text())["claims"]


# ------------------------------------------------------------ renaming verbs

def test_reorder_carries_the_local_claim_to_the_same_step(repo):
    _three(repo)
    assert run(repo, "claim", "TASK-001.b").code == 0          # "second"
    r = run(repo, "reorder", "TASK-001.c", "--to", "1")
    assert r.code == 0, r
    assert "carried claim" in r.out
    ready, claimed, j = _by_title(repo)
    assert claimed == {"second"}
    assert ready == {"first", "third"}
    assert j["orphaned_claims"] == []
    rows = _claim_rows(repo)
    old, new = rows
    assert old["status"] == "released" and new["status"] == "active"
    assert new["carried_from"] == old["id"] and new["step_ref"] == "TASK-001.c"


def test_swap_carries_the_local_claim(repo):
    _three(repo)
    assert run(repo, "claim", "TASK-001.a").code == 0          # "first"
    assert run(repo, "swap", "TASK-001.a", "TASK-001.b").code == 0
    ready, claimed, _ = _by_title(repo)
    assert claimed == {"first"} and ready == {"second", "third"}


def test_move_step_carries_the_local_claim(repo):
    _three(repo)
    assert run(repo, "claim", "TASK-001.b").code == 0
    assert run(repo, "move-step", "TASK-001.b", "q").code == 0
    ready, claimed, j = _by_title(repo)
    assert claimed == {"second"} and j["orphaned_claims"] == []


def test_renumber_carries_the_local_claim(repo):
    _three(repo)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "q")
    assert run(repo, "claim", "TASK-001.a").code == 0
    assert run(repo, "renumber", "TASK-001", "5").code == 0
    j = json.loads(run(repo, "ready", "--json").out)
    assert [r["ref"] for r in j["claimed"]] == ["TASK-005.a"]
    assert j["orphaned_claims"] == []
    assert run(repo, "ready").code == 0


def test_rename_refuses_when_another_sessions_claim_binds_the_step(repo):
    _three(repo)
    assert run(repo, "claim", "TASK-001.b", machine="workstation").code == 0
    before = task_path(repo, "TASK-001").read_text()
    for argv in (("reorder", "TASK-001.c", "--to", "1"), ("swap", "TASK-001.a", "TASK-001.b"),
                 ("move-step", "TASK-001.b", "q"), ("renumber", "TASK-001", "5")):
        r = run(repo, *argv)
        assert r.code == 1, (argv, r)
        assert "refusing to rename claimed step" in r.err
        assert task_path(repo, "TASK-001").read_text() == before
    # renaming steps nobody holds is fine
    assert run(repo, "swap", "TASK-001.a", "TASK-001.c").code == 0
    # --force goes ahead and says what it broke
    r = run(repo, "move-step", "TASK-001.b", "q", "--force")
    assert r.code == 0 and "still bind" in r.err


def test_ready_reports_an_orphaned_claim(repo):
    _three(repo)
    assert run(repo, "claim", "TASK-001.z").code == 0          # names no step
    r = run(repo, "ready", "--json")
    j = json.loads(r.out)
    assert r.code == 1
    assert [c["step_ref"] for c in j["orphaned_claims"]] == ["TASK-001.z"]
    assert j["counts_are_floor"] is True
    assert "ORPHANED" in run(repo, "ready").out
    assert "ORPHANED" in run(repo, "claims").out


def test_a_claim_on_an_existing_step_is_not_orphaned(repo):
    # control
    _three(repo)
    assert run(repo, "claim", "TASK-001.a").code == 0
    r = run(repo, "ready", "--json")
    assert r.code == 0 and json.loads(r.out)["orphaned_claims"] == []


# ------------------------------------------------------------ the view keys by identity

def _same_name_clones(world):
    """Two clones that both call themselves laptop/main."""
    a, b = world.laptop, world.workstation
    task_path(a, "TASK-001").write_text(
        "id: TASK-001\ntitle: t\nsteps:\n- {id: a, title: x, status: pending}\n"
        "- {id: b, title: y, status: pending}\n")
    world.commit(a, "task")
    git(a, "push", "-q", "origin", "HEAD:main")
    git(b, "pull", "-q", "origin", "main")
    return a, b


def test_view_keeps_distinct_claims_that_share_an_id(world):
    a, b = _same_name_clones(world)
    assert "CLM-laptop-001" in run(a, "claim", "TASK-001.a").out
    world.commit(a, "claim a")
    git(a, "push", "-q", "origin", "HEAD:main")
    assert "CLM-laptop-001" in run(b, "claim", "TASK-001.b").out   # same id, other claim
    world.commit(b, "claim b")
    git(b, "fetch", "-q", "origin")
    j = json.loads(run(b, "ready", "--json").out)
    assert {r["ref"] for r in j["claimed"]} == {"TASK-001.a", "TASK-001.b"}
    assert j["claim_id_collisions"] == {"CLM-laptop-001": 2}
    out = run(b, "claims").out
    assert "1 foreign active" in out and "ID COLLISION" in out


def test_release_does_not_cross_identities(world):
    a, b = _same_name_clones(world)
    run(a, "claim", "TASK-001.a")
    world.commit(a, "claim a")
    git(a, "push", "-q", "origin", "HEAD:main")
    run(b, "claim", "TASK-001.b")
    world.commit(b, "claim b")
    assert run(a, "release", "CLM-laptop-001").code == 0
    world.commit(a, "release")
    git(a, "push", "-q", "origin", "HEAD:main")
    git(b, "fetch", "-q", "origin")
    j = json.loads(run(b, "ready", "--json").out)
    assert [r["ref"] for r in j["claimed"]] == ["TASK-001.b"]     # B's claim still holds
    assert [r["ref"] for r in j["ready"]] == ["TASK-001.a"]
    assert "1 active local claim" in run(b, "claims").out


def test_the_same_claim_seen_at_two_refs_counts_once(world):
    # control
    a, b = _same_name_clones(world)
    run(a, "claim", "TASK-001.a")
    world.commit(a, "claim a")
    git(a, "push", "-q", "origin", "HEAD:main")
    git(b, "pull", "-q", "origin", "main")
    j = json.loads(run(b, "claims", "--json").out)
    assert len(j["local"]) == 1 and j["foreign_active"] == [] and j["id_collisions"] == {}


# ------------------------------------------------------------ merge algebra

def _row(cid, ref, **kw):
    r = {"id": cid, "machine": "laptop", "slot": "main", "subject": ref, "source": "",
         "intent": "", "created": "2026-01-01", "status": "active", "step_ref": ref}
    r.update(kw)
    return r


def _canon(rows):
    return sorted((r["id"], r["step_ref"], r["status"], r.get("renumbered_from"))
                  for r in rows)


def test_merge_is_idempotent_on_colliding_claims():
    a = [_row("CLM-laptop-001", "T-001.a")]
    b = [_row("CLM-laptop-001", "T-001.b"), _row("CLM-laptop-002", "T-001.c")]
    m = C.merge_rows(a, b)
    assert _canon(C.merge_rows(m, b)) == _canon(m)
    assert _canon(C.merge_rows(m, a)) == _canon(m)
    assert _canon(C.merge_rows(m, m)) == _canon(m)
    assert len(m) == 3 and len({r["id"] for r in m}) == 3


def test_merge_is_commutative_on_colliding_claims():
    a = [_row("CLM-laptop-001", "T-001.a"), _row("CLM-laptop-002", "T-001.d")]
    b = [_row("CLM-laptop-001", "T-001.b"), _row("CLM-laptop-002", "T-001.c")]
    assert _canon(C.merge_rows(a, b)) == _canon(C.merge_rows(b, a))


def test_criss_cross_merges_converge_on_one_id_per_claim():
    a = [_row("CLM-laptop-001", "T-001.a")]
    b = [_row("CLM-laptop-001", "T-001.b")]
    for i in range(3):
        a.append(_row(f"CLM-laptop-{10 + i:03d}", f"T-00{i}.x"))
        a, b = C.merge_rows(a, b), C.merge_rows(b, a)
        assert _canon(a) == _canon(b)
    assert len(a) == 5
    ref_of = {r["id"]: r["step_ref"] for r in a}
    assert ref_of == {r["id"]: r["step_ref"] for r in b}


def test_release_follows_the_claim_through_a_renumber():
    a = [_row("CLM-laptop-001", "T-001.a")]
    b = [_row("CLM-laptop-001", "T-001.b")]
    m = C.merge_rows(a, b)
    b_released = [_row("CLM-laptop-001", "T-001.b", status="released", released="2026-01-02")]
    m2 = C.merge_rows(m, b_released)
    status = {r["step_ref"]: r["status"] for r in m2}
    assert status == {"T-001.a": "active", "T-001.b": "released"}


def test_merge_of_distinct_ids_is_unchanged():
    # control
    a = [_row("CLM-laptop-001", "T-001.a")]
    b = [_row("CLM-laptop-wt1-001", "T-001.b", slot="wt1")]
    m = C.merge_rows(a, b)
    assert [r["id"] for r in m] == ["CLM-laptop-001", "CLM-laptop-wt1-001"]
    assert all("renumbered_from" not in r for r in m)


def _one(root):
    assert run(root, "new", "T").code == 0
    assert run(root, "add", "TASK-001", "original work").code == 0


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted((root / ".crossbox").rglob("*")) if p.is_file()}


def _ready(root):
    return json.loads(run(root, "ready", "--json").out)


def test_removing_my_claimed_step_is_refused_without_force(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "claim", "TASK-001.a").code == 0
    before = _snapshot(repo)
    r = run(repo, "remove-step", "TASK-001.a")
    assert r.code == 1, r
    assert "claim" in r.err
    assert _snapshot(repo) == before


def test_claim_delete_add_never_claims_the_replacement(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "claim", "TASK-001.a").code == 0
    run(repo, "remove-step", "TASK-001.a")               # refused
    run(repo, "remove-step", "TASK-001.a", "--force")    # deliberate resolution
    assert run(repo, "add", "TASK-001", "unrelated replacement").code == 0
    j = _ready(repo)
    assert [c["ref"] for c in j["claimed"]] == []
    assert [c["ref"] for c in j["ready"]] == ["TASK-001.a"]
    assert not [c for c in _claim_rows(repo) if c["status"] == "active"]


def test_force_removal_releases_my_claim_and_says_so(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "claim", "TASK-001.a").code == 0
    r = run(repo, "remove-step", "TASK-001.a", "--force")
    assert r.code == 0, r
    assert "released" in (r.out + r.err)
    assert [c["status"] for c in _claim_rows(repo)] == ["released"]


def test_removing_a_step_another_session_claimed_is_refused_then_add_cannot_inherit(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "claim", "TASK-001.a", machine="workstation").code == 0
    before = _snapshot(repo)
    r = run(repo, "remove-step", "TASK-001.a")
    assert r.code == 1 and "workstation" in r.err
    assert _snapshot(repo) == before
    # --force removes the step but cannot rewrite their claim: the id is then
    # unusable for new work, so the claim cannot bind a replacement
    r = run(repo, "remove-step", "TASK-001.a", "--force")
    assert r.code == 0, r
    assert "still bind" in r.err
    r = run(repo, "add", "TASK-001", "unrelated replacement")
    assert r.code == 1, r
    assert "TASK-001.a" in r.err and "claim" in r.err
    assert task_path(repo, "TASK-001").read_text().count("unrelated") == 0


def test_add_that_would_reuse_a_claimed_id_is_refused(repo):
    """R4/N1: A claim that exists before the id does (hand-written, or merged in)."""
    _one(repo)
    assert run(repo, "claim", "TASK-001.b", machine="workstation").code == 0
    r = run(repo, "add", "TASK-001", "second")
    assert r.code == 1 and "TASK-001.b" in r.err


def test_control_unclaimed_step_removes_and_replacement_is_ready(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "remove-step", "TASK-001.a").code == 0
    assert run(repo, "add", "TASK-001", "replacement").code == 0
    j = _ready(repo)
    assert [c["ref"] for c in j["ready"]] == ["TASK-001.a"] and j["claimed"] == []


def test_control_a_released_claim_does_not_block_removal_or_reuse(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "claim", "TASK-001.a").code == 0
    assert run(repo, "release", "--all-mine").code == 0
    assert run(repo, "remove-step", "TASK-001.a").code == 0
    assert run(repo, "add", "TASK-001", "replacement").code == 0
    assert _ready(repo)["claimed"] == []


def test_control_removing_an_unclaimed_step_beside_a_claimed_one(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "add", "TASK-001", "second").code == 0
    assert run(repo, "claim", "TASK-001.a").code == 0
    assert run(repo, "remove-step", "TASK-001.b").code == 0
    assert [c["ref"] for c in _ready(repo)["claimed"]] == ["TASK-001.a"]


def test_control_valid_swap_and_back_carry_the_claim(repo):
    """R4/N1: see INVARIANTS.md, fourth-review findings."""
    _one(repo)
    assert run(repo, "add", "TASK-001", "second work").code == 0
    assert run(repo, "claim", "TASK-001.a").code == 0
    assert run(repo, "swap", "TASK-001.a", "TASK-001.b").code == 0
    assert run(repo, "swap", "TASK-001.b", "TASK-001.a").code == 0
    active = [c for c in _claim_rows(repo) if c["status"] == "active"]
    assert [c["step_ref"] for c in active] == ["TASK-001.a"]
