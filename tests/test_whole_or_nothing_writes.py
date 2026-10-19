"""A write that cannot complete in full changes nothing: a refused swap, move, reorder or removal, and a failure partway through a multi-file commit, leave every file as it was."""
from conftest import run, task_path
from crossbox import model


def _one(root):
    assert run(root, "new", "T").code == 0
    assert run(root, "add", "TASK-001", "original work").code == 0


def _swap_setup(root):
    _one(root)
    assert run(root, "add", "TASK-001", "second work").code == 0
    assert run(root, "claim", "TASK-001.a").code == 0
    assert run(root, "new", "Dependent").code == 0
    task_path(root, "TASK-002").write_text(
        "id: TASK-002\ntitle: Dependent\nsteps:\n"
        "- {id: a, title: one, status: pending, depends_on: [TASK-001.a]}\n"
        "- {id: a, title: duplicate, status: pending}\n")


def _snapshot(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted((root / ".crossbox").rglob("*")) if p.is_file()}


def _two_dependents(root):
    _one(root)
    assert run(root, "add", "TASK-001", "second work").code == 0
    assert run(root, "claim", "TASK-001.a").code == 0
    for n in (2, 3):
        assert run(root, "new", f"Dep{n}").code == 0
        assert run(root, "add", f"TASK-00{n}", "waits", "--depends", "TASK-001.a").code == 0


def test_rejected_swap_changes_nothing(repo):
    """R4/N2: see INVARIANTS.md, fourth-review findings."""
    _swap_setup(repo)
    before = _snapshot(repo)
    r = run(repo, "swap", "TASK-001.a", "TASK-001.b")
    assert r.code == 1, r
    assert "duplicate step id" in r.err
    assert _snapshot(repo) == before


def test_rejected_move_step_changes_nothing(repo):
    """R4/N2: see INVARIANTS.md, fourth-review findings."""
    _swap_setup(repo)
    before = _snapshot(repo)
    r = run(repo, "move-step", "TASK-001.a", "q")
    assert r.code == 1, r
    assert _snapshot(repo) == before


def test_rejected_reorder_changes_nothing(repo):
    """R4/N2: see INVARIANTS.md, fourth-review findings."""
    _swap_setup(repo)
    before = _snapshot(repo)
    r = run(repo, "reorder", "TASK-001.b", "--to", "1")
    assert r.code == 1, r
    assert _snapshot(repo) == before


def test_rejected_remove_step_force_changes_nothing(repo):
    """R4/N2: see INVARIANTS.md, fourth-review findings."""
    _swap_setup(repo)
    before = _snapshot(repo)
    r = run(repo, "remove-step", "TASK-001.a", "--force")
    assert r.code == 1, r
    assert _snapshot(repo) == before


def test_a_write_failure_mid_commit_restores_every_file(repo, monkeypatch):
    """R4/N2: see INVARIANTS.md, fourth-review findings."""
    _two_dependents(repo)
    before = _snapshot(repo)
    real = model.atomic_write_text
    calls = {"n": 0}

    def flaky(path, text):
        if path.name == "TASK-003.yaml" and calls["n"] == 0:
            calls["n"] += 1
            raise OSError("disk full (injected)")
        return real(path, text)

    monkeypatch.setattr(model, "atomic_write_text", flaky)
    r = run(repo, "swap", "TASK-001.a", "TASK-001.b")
    assert r.code == 1, r
    assert "restored" in r.err
    assert calls["n"] == 1
    assert _snapshot(repo) == before
