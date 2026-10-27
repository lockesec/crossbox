"""archive, unarchive and the check-* verbs (invariants 46-50)."""
import json

from conftest import git, run, task_path


def _t(root, tid, body="- id: a\n  title: x\n  status: done\n", tier="queue", name=None,
       title=None):
    p = root / ".crossbox" / tier / (name or f"{tid}.yaml")
    p.write_text(f"id: {tid}\ntitle: {title or 'T ' + tid}\nsteps:\n{body}")
    return p


def test_check_collisions_same_title_is_modified_not_collision(world):
    # invariant 46
    _t(world.laptop, "TASK-A-001", title="shared")
    _t(world.laptop, "TASK-A-002", title="mine")
    world.commit(world.laptop)
    git(world.laptop, "push", "-q", "origin", "main")
    _t(world.laptop, "TASK-A-001", "- id: a\n  title: changed\n  status: done\n", title="shared")
    r = run(world.laptop, "check-collisions")
    assert r.code == 0 and "modified locally: TASK-A-001.yaml" in r.out
    _t(world.laptop, "TASK-A-002", title="someone else's")
    r = run(world.laptop, "check-collisions", "--json")
    j = json.loads(r.out)
    assert r.code == 1 and [c["file"] for c in j["collisions"]] == ["TASK-A-002.yaml"]
    assert j["compared"] == 2 and j["of_queue_files"] == 2
    _t(world.laptop, "TASK-A-003")                        # absent on the ref: never a collision
    assert json.loads(run(world.laptop, "check-collisions", "--json").out)["compared"] == 2


def test_check_collisions_unreadable_ref_is_not_evaluable(repo):
    r = run(repo, "check-collisions", "--against", "origin/nope")
    assert r.code == 2 and "NOT EVALUABLE" in r.out


def test_duplicate_ids_same_tier_blocks_cross_tier_is_info(repo):
    # invariant 47
    _t(repo, "TASK-A-001")
    _t(repo, "TASK-A-001", tier="archive")
    r = run(repo, "check-duplicate-ids")
    assert r.code == 0 and "shadowed" in r.out
    _t(repo, "TASK-A-002")
    _t(repo, "TASK-A-002", name="TASK-A-002-copy.yaml")
    r = run(repo, "check-duplicate-ids", "--json")
    j = json.loads(r.out)
    assert r.code == 1 and j["blocking"] == ["TASK-A-002"] and j["shadowed"] == ["TASK-A-001"]


def test_duplicate_ids_baseline_demotes_known_and_fails_closed(repo):
    _t(repo, "TASK-A-002")
    _t(repo, "TASK-A-002", name="TASK-A-002-copy.yaml")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "dup")
    r = run(repo, "check-duplicate-ids", "--baseline", "HEAD")
    assert r.code == 0 and "pre-existing" in r.out
    r = run(repo, "check-duplicate-ids", "--baseline", "no-such-ref")
    assert r.code == 2


def test_archive_never_overwrites_a_different_task(repo):
    # invariant 48
    _t(repo, "TASK-A-001", title="new title")
    _t(repo, "TASK-A-001", tier="archive", title="old title")
    _t(repo, "TASK-A-002")
    _t(repo, "TASK-A-003")
    (repo / ".crossbox" / "archive" / "TASK-A-003.yaml").write_text("id: [broken\n")
    r = run(repo, "archive")
    assert r.code == 3, r
    assert task_path(repo, "TASK-A-001").exists() and task_path(repo, "TASK-A-003").exists()
    assert not task_path(repo, "TASK-A-002").exists()
    assert "old title" in (repo / ".crossbox" / "archive" / "TASK-A-001.yaml").read_text()
    r = run(repo, "archive")
    assert r.code == 1


def test_archive_replaces_same_id_and_title(repo):
    _t(repo, "TASK-A-001", title="same")
    _t(repo, "TASK-A-001", tier="archive", name="TASK-A-001-v1.yaml", title="same")
    r = run(repo, "archive")
    assert r.code == 0 and "replacing" in r.out
    files = sorted(p.name for p in (repo / ".crossbox" / "archive").glob("TASK-*"))
    assert files == ["TASK-A-001.yaml"]


def test_unparseable_or_stepless_never_archivable_skipped_only_is(repo):
    # invariant 49
    _t(repo, "TASK-A-001", "- id: a\n  title: x\n  status: skipped\n")
    (repo / ".crossbox" / "queue" / "TASK-A-002.yaml").write_text("id: TASK-A-002\ntitle: e\nsteps: []\n")
    (repo / ".crossbox" / "queue" / "TASK-A-003.yaml").write_text("id: TASK-A-003\nsteps: [\n")
    _t(repo, "TASK-A-004", "- id: a\n  title: x\n  status: pending\n")
    r = run(repo, "archive")
    assert r.code == 0 and "archived 1 of 1" in r.out
    assert not task_path(repo, "TASK-A-001").exists()
    for t in ("TASK-A-002", "TASK-A-003", "TASK-A-004"):
        assert task_path(repo, t).exists()


def test_unarchive_refuses_divergent_copies_and_unparseable(repo):
    # invariant 50
    _t(repo, "TASK-A-001")
    _t(repo, "TASK-A-001", tier="archive")
    assert run(repo, "unarchive", "TASK-A-001").code == 1
    (repo / ".crossbox" / "archive" / "TASK-A-002.yaml").write_text("id: TASK-A-002\nsteps: [\n")
    assert run(repo, "unarchive", "TASK-A-002").code == 1
    _t(repo, "TASK-A-003", "- id: a\n  title: x\n  status: pending\n", tier="archive",
       name="TASK-A-003-old.yaml")
    r = run(repo, "unarchive", "TASK-A-003")
    assert r.code == 0 and "1 of 1 step(s) open" in r.out
    assert task_path(repo, "TASK-A-003").exists()
    r = run(repo, "unarchive", "TASK-A-003")
    assert r.code == 0 and "already in the queue" in r.out


def test_check_empty_tasks_informational_unless_strict(repo):
    (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text("id: TASK-A-001\ntitle: dup\nsteps: []\n")
    _t(repo, "TASK-A-002", title="dup", tier="archive")
    (repo / ".crossbox" / "queue" / "TASK-A-003.yaml").write_text(
        "id: TASK-A-003\ntitle: payload\nsteps: []\ntype: research\n")
    r = run(repo, "check-empty-tasks")
    assert r.code == 0 and "TASK-A-001" in r.out and "TASK-A-003" not in r.out
    assert "same title" in r.out
    assert run(repo, "check-empty-tasks", "--strict").code == 1
