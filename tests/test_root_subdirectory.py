"""`--root` may name a subdirectory of a git repository (the bundled
examples/sample-queue is one). Every git read must then mean that
subdirectory's queue, never the repository's own `.crossbox/` at the top.

Found while writing the sample queue: `git cat-file <ref>:<path>` takes a path
from the top of the repository, while the presence check (`ls-tree`) takes it
from the working directory, so the outer queue's claims were read as a
sibling's. `git diff --cached --name-only` names files from the top too, so the
pre-commit check under a subdirectory root saw no staged task at all."""
import yaml
from conftest import CONFIG, git, init_repo, run, task_path
import sys
from pathlib import Path


def _outer_and_inner(tmp_path):
    outer = init_repo(tmp_path / "outer")
    assert run(outer, "new", "outer task").code == 0           # TASK-001
    assert run(outer, "add", "TASK-001", "outer a").code == 0
    assert run(outer, "add", "TASK-001", "outer b").code == 0
    assert run(outer, "claim", "TASK-001.b").code == 0
    inner = outer / "sub" / "queue-root"
    inner.mkdir(parents=True)
    assert run(inner, "init").code == 0
    (inner / ".crossbox" / "config.yaml").write_text(CONFIG)
    assert run(inner, "new", "inner task").code == 0           # TASK-001 again
    assert run(inner, "add", "TASK-001", "inner a").code == 0
    git(outer, "add", "-A")
    git(outer, "commit", "-q", "-m", "both queues")
    return outer, inner


def test_claims_at_a_subdirectory_root_never_read_the_outer_queue(tmp_path):
    outer, inner = _outer_and_inner(tmp_path)
    r = run(inner, "claims")
    assert r.code == 0, r
    assert "TASK-001.b" not in r.out, r          # the outer queue's claim
    r = run(inner, "ready", "--json")
    assert yaml.safe_load(r.out)["claimed"] == [], r


def test_origin_guard_at_a_subdirectory_root_compares_the_same_file(tmp_path):
    outer, inner = _outer_and_inner(tmp_path)
    # the outer TASK-001 has a and b; the inner one only a. Adding to the inner
    # task must not be refused for lacking the outer file's `b`.
    r = run(inner, "add", "TASK-001", "inner b")
    assert r.code == 0, r


def test_origin_guard_at_a_subdirectory_root_reads_the_sibling_worktree_copy(tmp_path):
    outer, inner = _outer_and_inner(tmp_path)
    wt = tmp_path / "wt"
    git(outer, "worktree", "add", "-q", "-b", "wt", str(wt))
    wt_inner = wt / "sub" / "queue-root"
    assert run(wt_inner, "add", "TASK-001", "minted in the worktree").code == 0
    r = run(inner, "add", "TASK-001", "minted here too")
    assert r.code == 1 and "origin guard" in r.err, r


def test_staged_check_at_a_subdirectory_root_sees_its_staged_tasks(tmp_path):
    outer, inner = _outer_and_inner(tmp_path)
    p = task_path(inner, "TASK-001")
    p.write_text(p.read_text().replace("status: pending", "status: pendng"))
    git(outer, "add", "-A")
    r = run(inner, "doctor", "check", "--staged")
    assert r.code == 1, r
    assert "pendng" in r.out, r


def test_staged_check_with_an_unreadable_index_is_not_evaluable(repo):
    """`git diff --cached` failing used to read as "nothing staged": the
    pre-commit check passed without looking at a single file."""
    (repo / ".git" / "index").write_bytes(b"not an index")
    r = run(repo, "doctor", "check", "--staged")
    assert r.code == 2, r
    assert "NOT EVALUABLE" in r.err + r.out


def _cmd():
    return f"{sys.executable} -m crossbox"


SRC = str(Path(__file__).resolve().parents[1] / "src")


def test_hook_installed_under_a_subdirectory_root_names_that_root(tmp_path, monkeypatch):
    """R5/S5: see INVARIANTS.md, fifth-review findings."""
    monkeypatch.setenv("PYTHONPATH", SRC)
    top = tmp_path / "top"
    top.mkdir()
    git(top, "init", "-q", "-b", "main")
    sub = top / "my sub"
    sub.mkdir()
    assert run(sub, "init").code == 0
    assert run(sub, "doctor", "install-hook", "--command", _cmd()).code == 0
    hook = (top / ".git" / "hooks" / "pre-commit").read_text()
    assert "--root 'my sub'" in hook, hook
    # an unrelated commit at the top must go through (the hook finds the queue)
    (top / "README").write_text("x")
    git(top, "add", "README")
    assert git(top, "commit", "-q", "-m", "x", check=False).returncode == 0
    # and a damaged staged task under the subdirectory root is still refused
    p = task_path(sub, "TASK-001")
    p.write_text("id: [broken\n")
    git(top, "add", "-f", str(p))
    assert git(top, "commit", "-q", "-m", "bad", check=False).returncode != 0


def test_hook_at_the_toplevel_keeps_the_command_unchanged(repo):
    """R5/S5: see INVARIANTS.md, fifth-review findings."""
    assert run(repo, "doctor", "install-hook", "--command", "xbox").code == 0
    hook = (repo / ".git" / "hooks" / "pre-commit").read_text()
    assert "exec xbox doctor check --staged --quiet\n" in hook, hook
    assert "--root" not in hook
