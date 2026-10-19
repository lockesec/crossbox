"""`done REF --commit SHA`: a closure that names the commit it rests on."""
from conftest import git, run

from crossbox import model


def _task(repo, sids="ab"):
    body = "".join(f"- id: {s}\n  title: step {s}\n  status: pending\n" for s in sids)
    (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text(
        f"id: TASK-A-001\ntitle: t\npriority: normal\nsteps:\n{body}")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")


def _commit(repo, msg, path="work.txt"):
    f = repo / path
    f.write_text((f.read_text() if f.exists() else "") + msg + "\n")
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", msg)
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def _step(repo, sid):
    data = model.parse_text((repo / ".crossbox" / "queue" / "TASK-A-001.yaml").read_text())
    return next(s for s in data["steps"] if s["id"] == sid)


def test_records_the_full_sha_and_grades_it_reachable(repo):
    _task(repo)
    sha = _commit(repo, "TASK-A-001.a: the work")
    r = run(repo, "done", "TASK-A-001.a", "--commit", "HEAD")
    assert r.code == 0, r
    assert _step(repo, "a")["commits"] == [sha]
    assert f"commit {sha[:7]} REACHABLE from main" in r.out
    assert "does not name" not in r.err


def test_an_unknown_commit_is_refused_and_nothing_is_written(repo):
    _task(repo)
    before = (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").read_text()
    r = run(repo, "done", "TASK-A-001.a", "--commit", "deadbeefdeadbeef")
    assert r.code == 1 and "not a commit" in r.err
    assert (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").read_text() == before


def test_a_commit_only_on_a_side_branch_is_unmerged(repo):
    _task(repo)
    git(repo, "checkout", "-q", "-b", "side")
    sha = _commit(repo, "TASK-A-001.a: on the side", path="side.txt")
    git(repo, "checkout", "-q", "main")
    r = run(repo, "done", "TASK-A-001.a", "--commit", sha)
    assert r.code == 0, r
    assert f"commit {sha[:7]} UNMERGED: not in main" in r.out


def test_a_commit_that_does_not_name_the_step_is_recorded_with_a_warning(repo):
    _task(repo)
    _commit(repo, "unrelated tidy-up")
    r = run(repo, "done", "TASK-A-001.a", "--commit", "HEAD")
    assert r.code == 0
    assert "does not name TASK-A-001.a" in r.err
    sha = _commit(repo, "refactor\n\nCrossbox-Step: TASK-A-001.b")
    r = run(repo, "done", "TASK-A-001.b", "--commit", sha)
    assert "does not name" not in r.err


def test_evidence_can_be_added_to_a_closed_step_once(repo):
    _task(repo)
    assert run(repo, "done", "TASK-A-001.a").code == 0
    sha = _commit(repo, "TASK-A-001.a: the real fix")
    for _ in range(2):
        r = run(repo, "done", "TASK-A-001.a", "--commit", sha)
        assert r.code == 0
    assert _step(repo, "a")["commits"] == [sha]
    assert "recorded commit" in r.out or "already recorded" in r.out


def test_outside_git_a_commit_cannot_be_checked(plain):
    (plain / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text(
        "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n")
    r = run(plain, "done", "TASK-A-001.a", "--commit", "abc1234")
    assert r.code == 2
    assert _step(plain, "a")["status"] == "pending"


def test_reopen_retracts_the_recorded_commits(repo):
    _task(repo)
    sha = _commit(repo, "TASK-A-001.a: done")
    assert run(repo, "done", "TASK-A-001.a", "--commit", sha).code == 0
    assert run(repo, "reopen", "TASK-A-001.a", "--reason", "regressed").code == 0
    s = _step(repo, "a")
    assert "commits" not in s and f"commits={sha}" in s["notes"]


def test_show_lists_the_commits(repo):
    _task(repo)
    sha = _commit(repo, "TASK-A-001.a: done")
    assert run(repo, "done", "TASK-A-001.a", "--commit", sha).code == 0
    assert f"commits: {sha[:12]}" in run(repo, "show", "TASK-A-001").out


def test_done_does_not_advise_claiming_a_step_a_sibling_worktree_holds(world):
    import os
    from conftest import task_path
    task_path(world.laptop, "TASK-A-001").write_text(
        "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n")
    world.commit(world.laptop, "queue")
    git(world.wt1, "merge", "-q", "main")
    os.environ["CROSSBOX_SLOT"] = "wt1"
    try:
        assert run(world.wt1, "claim", "TASK-A-001.a").code == 0
    finally:
        os.environ.pop("CROSSBOX_SLOT")
    world.commit(world.wt1, "claim")  # committed, not pushed: the union reads it
    r = run(world.laptop, "done", "TASK-A-001.a")
    assert r.code == 0
    assert "is claimed by CLM-laptop-wt1-001" in r.err
    assert "no active claim covered" not in r.err
