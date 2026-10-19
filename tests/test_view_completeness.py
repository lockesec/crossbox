"""One completeness state for the claims view, read by every consumer
(release review, blocker 3), and git failures that keep their meaning
(release review, hardening: typed git errors).

Before: a sibling claims file that did not parse was listed by `claims` while
`ready` classified work as if the claim it hid did not exist, and exited 0.
And `git show` mapped EVERY failure to "no file at that ref", so a corrupt
object or a bad ref read as a clean, empty view."""
import json

import pytest
from conftest import run, task_path

from crossbox import gitutil


def _break_sibling_claims(world):
    (world.wt1 / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    world.commit(world.wt1)


def _task(root):
    task_path(root, "TASK-A-001").write_text(
        "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n")


@pytest.mark.parametrize("args", [("ready",), ("ready", "--json"), ("claims",),
                                  ("claims", "--json"), ("claim-check", "unrelated gardening"),
                                  ("status", "--summary"), ("status", "--summary", "--json"),
                                  ("near-done",), ("near-done", "--json"), ("graph",),
                                  ("graph", "--format", "json")])
def test_an_unparseable_sibling_claims_file_makes_every_consumer_not_evaluable(world, args):
    _task(world.laptop)
    _break_sibling_claims(world)
    r = run(world.laptop, *args)
    assert r.code == 2, r
    assert "wt1" in r.out + r.err


def test_ready_json_names_the_incomplete_view(world):
    _task(world.laptop)
    _break_sibling_claims(world)
    out = json.loads(run(world.laptop, "ready", "--json").out)
    assert out["claims_view"] != "ok" and "unparseable" in out["claims_view"]


def test_a_complete_view_stays_evaluable(world):
    # the clear direction: nothing broken, every consumer exits 0
    _task(world.laptop)
    for args in (("ready",), ("claims",), ("status", "--summary")):
        assert run(world.laptop, *args).code == 0, args


# ---------------------------------------------------------------- typed git

def test_show_distinguishes_absent_from_failure(world):
    assert gitutil.show(world.laptop, "HEAD", "no/such/file.yaml") is None
    assert gitutil.show(world.laptop, "HEAD", ".crossbox/config.yaml")
    with pytest.raises(gitutil.GitError):
        gitutil.show(world.laptop, "no-such-ref", ".crossbox/config.yaml")


def test_ls_names_raises_instead_of_returning_an_empty_list(world):
    assert gitutil.ls_names(world.laptop, "HEAD", ["no/such/dir"]) == []
    with pytest.raises(gitutil.GitError):
        gitutil.ls_names(world.laptop, "no-such-ref", [".crossbox"])


def _fail_reads_at(monkeypatch, marker):
    """Every object read at a ref containing `marker` fails like a corrupt
    object would (exit 128), while the ref itself still resolves."""
    real = gitutil.git

    def fake(repo, *args, **kw):
        if args and args[0] in ("show", "cat-file", "ls-tree") and any(marker in a for a in args):
            class CP:
                returncode, stdout, stderr = 128, "", "fatal: bad object"
            return CP()
        return real(repo, *args, **kw)
    monkeypatch.setattr(gitutil, "git", fake)


def test_an_unreadable_sibling_ref_is_not_an_empty_claims_file(world, monkeypatch):
    _task(world.laptop)
    run(world.wt1, "claim", "x", "--step", "TASK-A-001.a")
    world.commit(world.wt1)
    _fail_reads_at(monkeypatch, "wt1")
    r = run(world.laptop, "ready")
    assert r.code == 2, r
    r = run(world.laptop, "claims")
    assert r.code == 2 and "unreadable" in r.out


def test_origin_guard_is_not_evaluable_when_a_sibling_read_fails(world, monkeypatch):
    _task(world.laptop)
    world.commit(world.laptop)
    _task(world.wt1)
    run(world.wt1, "add", "TASK-A-001", "from the slot")
    world.commit(world.wt1)
    _fail_reads_at(monkeypatch, "wt1")
    r = run(world.laptop, "add", "TASK-A-001", "here")
    assert r.code == 2 and "NOT EVALUABLE" in r.err, r


def test_check_collisions_is_not_evaluable_when_the_ref_cannot_be_read(world, monkeypatch):
    _task(world.laptop)
    world.commit(world.laptop)
    git_ref = "HEAD"
    _fail_reads_at(monkeypatch, git_ref)
    r = run(world.laptop, "check-collisions", "--against", git_ref)
    assert r.code == 2, r
