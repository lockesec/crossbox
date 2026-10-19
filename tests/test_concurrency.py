"""Parallel crossbox processes in one tree: one writer at a time, nothing lost,
and a failed writer never erases another writer's success."""
import os
import subprocess
import sys

import pytest
import yaml

from conftest import SRC, init_repo, run, task_path

from crossbox import lock, model
from crossbox.errors import CrossboxError


def _spawn(root, *args, env_extra=None):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
    env["CROSSBOX_SKIP_ORIGIN_GUARD"] = "1"
    env.update(env_extra or {})
    return subprocess.Popen([sys.executable, "-m", "crossbox", "--root", str(root),
                             *map(str, args)], env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


def _run_parallel(root, argvs, env_extra=None):
    procs = [_spawn(root, *a, env_extra=env_extra) for a in argvs]
    out = []
    for p in procs:
        o, e = p.communicate(timeout=120)
        out.append((p.returncode, o, e))
    return out


def test_parallel_add_loses_nothing(repo):

    assert run(repo, "new", "T").code == 0
    n = 8
    res = _run_parallel(repo, [("add", "TASK-001", f"step {i}") for i in range(n)])
    assert [r[0] for r in res] == [0] * n, res
    data = yaml.safe_load(task_path(repo, "TASK-001").read_text())
    titles = sorted(s["title"] for s in data["steps"])
    assert titles == sorted(f"step {i}" for i in range(n))
    ids = [s["id"] for s in data["steps"]]
    assert len(set(ids)) == n


def test_parallel_claims_lose_nothing(repo):

    n = 10
    res = _run_parallel(repo, [("claim", f"work item {i}") for i in range(n)])
    assert [r[0] for r in res] == [0] * n, res
    rows = yaml.safe_load((repo / ".crossbox" / "claims.yaml").read_text())["claims"]
    assert len(rows) == n
    assert len({r["id"] for r in rows}) == n


def test_rate_gate_holds_under_parallel_callers(repo, tmp_path):
    # callers in TWO trees share the machine budget, so the queue
    # lock alone cannot serialise them; the rate gate needs its own
    other = init_repo(tmp_path / "other")
    env = {"CROSSBOX_RATE_DISABLE": "0", "CROSSBOX_RATE_CAP": "5",
           "CROSSBOX_RATE_STATE": str(tmp_path / "rg" / "state.json")}
    procs = [_spawn(repo if i % 2 else other, "claim", f"item {i}", env_extra=env)
             for i in range(20)]
    codes = []
    for p in procs:
        p.communicate(timeout=120)
        codes.append(p.returncode)
    assert codes.count(0) == 5, codes
    assert codes.count(1) == 15, codes


def test_restore_never_overwrites_a_foreign_write(repo, monkeypatch):
    # a read-back mismatch caused by ANOTHER writer is left alone
    assert run(repo, "new", "T").code == 0
    p = task_path(repo, "TASK-001")
    foreign = p.read_text().replace("title: T", "title: written by someone else")
    real = model.atomic_write_text
    calls = []

    def interleaved(path, text):
        real(path, text)
        calls.append(path)
        if len(calls) == 1:
            real(path, foreign)   # a second process replaces the file after us

    monkeypatch.setattr(model, "atomic_write_text", interleaved)
    data = yaml.safe_load(p.read_text())
    data["title"] = "mine"
    with pytest.raises(CrossboxError) as e:
        model.write_task(p, data, [])
    assert e.value.code == 2
    assert p.read_text() == foreign          # their success survives
    assert len(calls) == 1                   # no "restore" write happened


def test_restore_still_undoes_our_own_bad_write(repo, monkeypatch):
    # control: when the file holds OUR bytes, we restore
    assert run(repo, "new", "T").code == 0
    p = task_path(repo, "TASK-001")
    before = p.read_text()
    real_parse = model.parse_text
    n = {"k": 0}

    def flaky(text):
        n["k"] += 1
        if n["k"] == 2:   # the read-back parse
            raise model.ParseError("simulated")
        return real_parse(text)

    monkeypatch.setattr(model, "parse_text", flaky)
    data = yaml.safe_load(before)
    data["title"] = "mine"
    with pytest.raises(CrossboxError) as e:
        model.write_task(p, data, [])
    assert e.value.code == 1
    assert p.read_text() == before


def test_writer_waits_for_the_lock_and_times_out_not_evaluable(repo):
    # no lock, no write; the wait ends in exit 2, never a silent write
    assert run(repo, "new", "T").code == 0
    before = task_path(repo, "TASK-001").read_text()
    with lock.hold(repo, what="test"):
        (code, _o, err), = _run_parallel(repo, [("add", "TASK-001", "blocked")],
                                         env_extra={"CROSSBOX_LOCK_TIMEOUT": "0.3"})
    assert code == 2, err
    assert "lock" in err
    assert task_path(repo, "TASK-001").read_text() == before
    # and once the lock is free the same verb goes through
    (code, _o, err), = _run_parallel(repo, [("add", "TASK-001", "free")])
    assert code == 0, err
