"""Task-id resolution, sibling-aware minting, the origin guard and renumber
(invariants 35-45). Uses real git repositories: a bare origin, a laptop clone
with a linked worktree slot, and a workstation clone."""
import yaml
from conftest import git, run, task_path

from crossbox import guard
from pathlib import Path
import pytest
from crossbox import ids, model
from crossbox.errors import UsageError


def _t(root, tid, steps="", tier="queue", name=None, title=None, extra=""):
    p = root / ".crossbox" / tier / (name or f"{tid}.yaml")
    p.write_text(f"id: {tid}\ntitle: {title or tid + ' title'}\n{extra}steps:\n{steps}"
                 if steps else f"id: {tid}\ntitle: {title or tid + ' title'}\n{extra}steps: []\n")
    return p


def S(i, st="pending"):
    return f"- id: {i}\n  title: s{i}\n  status: {st}\n"


def test_resolution_by_id_field_ambiguity_and_queue_wins(repo):
    # invariant 35
    _t(repo, "TASK-A-001", S("a"), tier="archive", name="TASK-A-001-old.yaml", title="old")
    r = run(repo, "show", "TASK-A-001")
    assert r.code == 0 and "ARCHIVED" in r.out
    _t(repo, "TASK-A-001", S("a"), title="live")
    r = run(repo, "show", "TASK-A-001")
    assert "live" in r.out and "ARCHIVED" not in r.out
    _t(repo, "TASK-A-001", S("a"), name="TASK-A-001-copy.yaml", title="copy")
    r = run(repo, "show", "TASK-A-001")
    assert r.code == 1 and "ambiguous" in r.err
    assert "TASK-A-001.yaml" in r.err and "TASK-A-001-copy.yaml" in r.err
    assert run(repo, "done", "TASK-A-001.a").code == 1


def test_did_you_mean_is_short(repo):
    for n in range(1, 12):
        _t(repo, f"TASK-A-{n:03d}")
    r = run(repo, "show", "TASK-A-100")
    assert r.code == 1 and "did you mean" in r.err
    assert r.err.count("TASK-A-") <= 6


def test_new_refuses_an_existing_destination(repo, monkeypatch):
    # invariant 36
    p = _t(repo, "TASK-A-001", S("a"))
    before = p.read_text()
    monkeypatch.setattr(guard, "mint_task_id", lambda ctx, d: ("TASK-A-001", []))
    r = run(repo, "new", "x", "-d", "a")
    assert r.code == 1 and "refusing to overwrite" in r.err
    assert p.read_text() == before


def test_task_mint_sees_unlanded_siblings_and_suffixed_names(world):
    # invariant 37
    _t(world.wt1, "TASK-API-001")
    world.commit(world.wt1)                            # committed in slot wt1, never pushed
    _t(world.workstation, "TASK-API-004", tier="archive", name="TASK-API-004-retired.yaml")
    world.commit(world.workstation)
    git(world.workstation, "push", "-q", "origin", "main")
    r = run(world.laptop, "new", "next one", "-d", "api")
    assert r.code == 0, r
    assert task_path(world.laptop, "TASK-API-005").exists()


def test_legacy_mint_is_archive_and_sibling_aware(world):
    _t(world.wt1, "TASK-007")
    world.commit(world.wt1)
    _t(world.laptop, "TASK-009", tier="archive")
    r = run(world.laptop, "new", "legacy")
    assert r.code == 0 and task_path(world.laptop, "TASK-010").exists(), r


def test_task_mint_fetches_first_and_distinguishes_no_remote(world, repo):
    # invariant 38
    _t(world.workstation, "TASK-API-005")
    world.commit(world.workstation)
    git(world.workstation, "push", "-q", "origin", "main")
    r = run(world.laptop, "new", "x", "-d", "api")   # no manual fetch
    assert task_path(world.laptop, "TASK-API-006").exists(), r
    r = run(repo, "new", "x", "-d", "api")            # lone repo: silent
    assert r.code == 0 and "warning" not in r.err
    git(world.laptop, "remote", "set-url", "origin", str(world.origin) + "-gone")
    r = run(world.laptop, "new", "y", "-d", "api")
    assert r.code == 0 and "fetch" in r.err and "stale" in r.err


def _shared_task(world):
    _t(world.laptop, "TASK-API-001", S("a"))
    world.commit(world.laptop)
    git(world.laptop, "push", "-q", "origin", "main")
    git(world.workstation, "pull", "-q", "origin", "main")
    git(world.wt1, "merge", "-q", "main")


def test_step_mint_refuses_when_origin_holds_unknown_ids(world):
    # invariant 39
    _shared_task(world)
    assert run(world.workstation, "add", "TASK-API-001", "from workstation").code == 0
    world.commit(world.workstation)
    git(world.workstation, "push", "-q", "origin", "main")
    p = task_path(world.laptop, "TASK-API-001")
    before = p.read_text()
    r = run(world.laptop, "add", "TASK-API-001", "from laptop")
    assert r.code == 1 and "REFUSED" in r.err and "origin/main: b" in r.err
    assert p.read_text() == before
    git(world.laptop, "pull", "-q", "origin", "main")
    assert run(world.laptop, "add", "TASK-API-001", "after pull").out.strip() == "added TASK-API-001.c"


def test_step_mint_unreachable_origin_is_not_evaluable(world):
    _shared_task(world)
    git(world.laptop, "remote", "set-url", "origin", str(world.origin) + "-gone")
    r = run(world.laptop, "add", "TASK-API-001", "x")
    assert r.code == 2 and "NOT EVALUABLE" in r.err


def test_guard_reads_the_minters_ref_list_including_slot_branches(world):
    # invariant 40: a commit in slot wt1 (never pushed) blocks minting in main
    _shared_task(world)
    assert run(world.wt1, "add", "TASK-API-001", "in slot").code == 0
    world.commit(world.wt1)
    r = run(world.laptop, "add", "TASK-API-001", "in main")
    assert r.code == 1 and "wt1: b" in r.err


def test_guard_opt_out_env_is_named_in_refusals(world, monkeypatch):
    _shared_task(world)
    run(world.wt1, "add", "TASK-API-001", "in slot")
    world.commit(world.wt1)
    r = run(world.laptop, "add", "TASK-API-001", "in main")
    assert guard.SKIP_ENV in r.err
    monkeypatch.setenv(guard.SKIP_ENV, "1")
    assert run(world.laptop, "add", "TASK-API-001", "in main").code == 0


def test_removed_tail_step_is_not_a_gap_in_own_history(world):
    _shared_task(world)
    run(world.laptop, "add", "TASK-API-001", "b")
    world.commit(world.laptop)
    assert run(world.laptop, "remove-step", "TASK-API-001.b").code == 0
    world.commit(world.laptop)
    assert run(world.laptop, "add", "TASK-API-001", "again").code == 0


def test_batch_add_runs_guard_once(repo, monkeypatch, tmp_path):
    # invariant 41
    _t(repo, "TASK-A-001")
    calls = []
    real = guard.origin_guard

    def counting(ctx, path):
        calls.append(path)
        return real(ctx, path)
    monkeypatch.setattr(guard, "origin_guard", counting)
    f = tmp_path / "rows"
    f.write_text("one\ntwo\nthree\n")
    assert run(repo, "batch-add", "TASK-A-001", f).code == 0
    assert len(calls) == 1


def test_renumber_never_overwrites_an_occupied_id(repo):
    # invariant 42
    _t(repo, "TASK-A-001", S("a"))
    _t(repo, "TASK-A-002", S("a"), tier="archive", name="TASK-A-002-old.yaml")
    _t(repo, "TASK-A-003", S("a"), name="TASK-A-009.yaml")   # id field disagrees with name
    for target in ("2", "TASK-A-002", "TASK-A-003", "9"):
        r = run(repo, "renumber", "TASK-A-001", target)
        assert r.code == 1 and "occupied" in r.err, (target, r)
    assert task_path(repo, "TASK-A-001").exists()


def test_renumber_rewrites_refs_boundary_safely(repo):
    # invariant 43
    _t(repo, "TASK-A-034", S("a"))
    _t(repo, "TASK-A-0344", S("a"))
    other = _t(repo, "TASK-B-001", "- id: a\n  title: see TASK-A-034, not TASK-A-0344\n"
               "  status: pending\n  depends_on: [TASK-A-034.a, TASK-A-0344]\n"
               "  notes: (was TASK-A-034)\n")
    r = run(repo, "renumber", "TASK-A-034", "TASK-A-050")
    assert r.code == 0, r
    s = yaml.safe_load(other.read_text())["steps"][0]
    assert s["title"] == "see TASK-A-050, not TASK-A-0344"
    assert s["depends_on"] == ["TASK-A-050.a", "TASK-A-0344"]
    assert s["notes"] == "(was TASK-A-050)"
    moved = yaml.safe_load(task_path(repo, "TASK-A-050").read_text())
    assert moved["id"] == "TASK-A-050" and not task_path(repo, "TASK-A-034").exists()


def test_renumber_leaves_external_refs_when_another_claimant_remains(repo):
    # invariant 44
    a = _t(repo, "TASK-A-001", "- id: a\n  title: self ref TASK-A-001\n  status: pending\n")
    _t(repo, "TASK-A-001", S("a"), name="TASK-A-001-dup.yaml", title="dup")
    other = _t(repo, "TASK-B-001", "- id: a\n  title: t\n  status: pending\n"
               "  depends_on: [TASK-A-001]\n")
    assert run(repo, "renumber", "TASK-A-001", "5").code == 1          # ambiguous source
    r = run(repo, "renumber", "TASK-A-001", "5", "--file", str(a))
    assert r.code == 0, r
    assert yaml.safe_load(other.read_text())["steps"][0]["depends_on"] == ["TASK-A-001"]
    moved = yaml.safe_load(task_path(repo, "TASK-A-005").read_text())
    assert moved["steps"][0]["title"] == "self ref TASK-A-005"


def test_renumber_next_is_sibling_aware_and_uses_git_mv(world):
    # invariant 45 (open in the original design)
    _t(world.wt1, "TASK-API-002")
    world.commit(world.wt1)
    _t(world.laptop, "TASK-API-001", S("a"))
    world.commit(world.laptop)
    r = run(world.laptop, "renumber", "TASK-API-001", "--next")
    assert r.code == 0, r
    assert task_path(world.laptop, "TASK-API-003").exists()
    st = git(world.laptop, "status", "--porcelain").stdout
    assert "R " in st or "RM" in st  # staged as a rename by git mv


def test_renumber_dry_run_writes_nothing(repo):
    p = _t(repo, "TASK-A-001", S("a"))
    before = p.read_text()
    r = run(repo, "renumber", "TASK-A-001", "7", "--dry-run")
    assert r.code == 0 and p.read_text() == before
    assert not task_path(repo, "TASK-A-007").exists()


def test_no_remote_and_no_commits_mints_silently(tmp_path):
    from conftest import init_repo
    r0 = init_repo(tmp_path / "fresh", commit=False)
    r = run(r0, "new", "first", "-d", "api")
    assert r.code == 0 and r.err == "", r


def test_mint_sees_an_uncommitted_task_in_a_sibling_worktree(world):
    r = run(world.wt1, "new", "Feature task")
    assert r.code == 0 and "TASK-001" in r.out          # not committed
    r = run(world.laptop, "new", "Main task")
    assert r.code == 0 and "TASK-002" in r.out, r


def test_guard_sees_an_uncommitted_step_in_a_sibling_worktree(world):
    assert run(world.laptop, "new", "T").code == 0
    assert run(world.laptop, "add", "TASK-001", "first").code == 0
    world.commit(world.laptop, "task")
    from conftest import git
    git(world.wt1, "merge", "-q", "main")
    # control: an unchanged sibling working copy is no gap
    assert run(world.laptop, "add", "TASK-001", "second").code == 0
    world.commit(world.laptop, "second")
    git(world.wt1, "merge", "-q", "main")
    assert run(world.wt1, "add", "TASK-001", "feature step").code == 0   # c, uncommitted
    before = (world.laptop / ".crossbox" / "queue" / "TASK-001.yaml").read_text()
    r = run(world.laptop, "add", "TASK-001", "main step")
    assert r.code == 1, r
    assert "uncommitted" in r.err and "c" in r.err
    assert (world.laptop / ".crossbox" / "queue" / "TASK-001.yaml").read_text() == before


def test_first_add_in_a_clone_of_an_empty_remote_is_not_blocked(tmp_path):
    from conftest import git
    bare = tmp_path / "empty.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(bare))
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "-q", str(bare), str(clone))
    assert run(clone, "init").code == 0
    assert run(clone, "new", "T").code == 0
    r = run(clone, "add", "TASK-001", "first step")
    assert r.code == 0, r
    git(clone, "add", "-A")
    git(clone, "commit", "-q", "-m", "first")      # committed, still nothing pushed
    assert run(clone, "add", "TASK-001", "second step").code == 0


def test_an_unreachable_remote_is_still_not_evaluable(tmp_path):
    # control: a fetch that fails is never "nothing to compare"
    from conftest import git
    clone = tmp_path / "c"
    clone.mkdir()
    git(clone, "init", "-q", "-b", "main")
    git(clone, "remote", "add", "origin", str(tmp_path / "missing.git"))
    assert run(clone, "init").code == 0
    assert run(clone, "new", "T").code == 0
    assert run(clone, "add", "TASK-001", "x").code == 2


def _set_remote(root, name):
    cfg = root / ".crossbox" / "config.yaml"
    cfg.write_text(cfg.read_text() + f"remote: {name}\n")


def test_a_mistyped_remote_makes_the_guard_and_minter_not_evaluable(world):
    """R5/F2: see INVARIANTS.md, fifth-review findings."""
    a = world.laptop
    assert run(a, "new", "T").code == 0
    assert run(a, "add", "TASK-001", "one").code == 0
    world.commit(a)
    _set_remote(a, "orign")
    r = run(a, "add", "TASK-001", "two")
    assert r.code == 2 and "orign" in r.err, r
    r = run(a, "new", "another")
    assert r.code == 2 and "orign" in r.err, r
    assert not (a / ".crossbox" / "queue" / "TASK-002.yaml").exists()


def test_a_lone_repo_without_remote_setting_stays_local_only(repo):
    """R5/F2: see INVARIANTS.md, fifth-review findings."""
    assert run(repo, "new", "T").code == 0
    assert run(repo, "add", "TASK-001", "one").code == 0


def test_an_explicit_remote_that_exists_is_still_used(world):
    """R5/F2: see INVARIANTS.md, fifth-review findings."""
    a = world.laptop
    _set_remote(a, "origin")
    assert run(a, "new", "T").code == 0
    assert run(a, "add", "TASK-001", "one").code == 0


def _task(root, tid, steps, fname=None, title="T", tier="queue", **extra):
    data = {"id": tid, "title": title, "priority": "normal", "steps": steps}
    data.update(extra)
    p = Path(root) / ".crossbox" / tier / (fname or f"{tid}.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10 ** 6))
    return p


def _step(sid, title="s", status="pending", dep=None):
    return {"id": sid, "title": title, "target": "any", "needs": [],
            "status": status, "depends_on": dep or []}


def test_a_step_id_with_a_trailing_newline_is_malformed():
    """R6/F8: see INVARIANTS.md, sixth-review findings."""
    assert model.STEP_ID_RE.fullmatch("a\n") is None
    assert model.structure_problems({"id": "TASK-001", "steps": [{"id": "a\n"}]})
    assert not model.structure_problems({"id": "TASK-001", "steps": [{"id": "a"}]})


def test_id_grammar_rejects_a_trailing_newline():
    """R6/F8: see INVARIANTS.md, sixth-review findings."""
    g = ids.Grammar("TASK")
    assert g.is_task_id("TASK-001") and g.is_task_id("TASK-A-001")
    assert not g.is_task_id("TASK-001\n")
    assert g.is_ref("TASK-001.a") and not g.is_ref("TASK-001.a\n")
    assert not g.file_re.fullmatch("TASK-001.yaml\n")
    assert g.file_re.fullmatch("TASK-001.yaml")
    assert g.domain_of("TASK-A-001\n") is None
    assert g.number_of("TASK-001\n") is None
    with pytest.raises(UsageError):
        g.parse_ref("TASK-001.a\n")


def test_doctor_check_flags_a_step_id_with_a_trailing_newline(repo):
    """R6/F8: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-001", [_step("a\n"), _step("b")])
    d = run(repo, "doctor", "check")
    assert d.code == 1, d
    assert run(repo, "done", "TASK-001.a\n").code == 64
