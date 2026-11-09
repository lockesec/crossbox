"""`crossbox why`: why a step is not workable here, traced to its root blockers."""
import io
import json
import sys

import pytest

from conftest import run

REG = """\
schema: 1
tokens:
  closed-probe: {kind: gate, probe: "exit 1"}
  broken-probe: {kind: gate, probe: "exit 7"}
"""


def _task(repo, tid, steps, prio="normal"):
    body = ""
    for sid, extra in steps:
        body += f"- id: {sid}\n  title: step {sid}\n  status: pending\n{extra}"
    (repo / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: t\npriority: {prio}\nsteps:\n{body}")


def _why(repo, *args):
    r = run(repo, "why", *args, "--json")
    return r, json.loads(r.out)


def test_chain_reaches_the_workable_root(repo):
    _task(repo, "TASK-A-001", [("a", ""), ("b", "  depends_on: [a]\n"),
                               ("c", "  depends_on: [b]\n")])
    r, j = _why(repo, "TASK-A-001.c")
    assert r.code == 0, r
    assert j["code"] == "WAITING"
    assert [n["ref"] for n in j["trace"]["children"]] == ["TASK-A-001.b"]
    assert j["trace"]["children"][0]["children"][0]["code"] == "READY"
    assert j["roots"] == [{"ref": "TASK-A-001.a", "code": "READY",
                           "detail": "workable now on laptop"}]
    text = run(repo, "why", "TASK-A-001.c").out
    assert "TASK-A-001.c  WAITING" in text
    assert "root: TASK-A-001.a  READY" in text


def test_a_claimed_dependency_names_the_holder(repo):
    _task(repo, "TASK-A-001", [("a", ""), ("b", "  depends_on: [a]\n")])
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _, j = _why(repo, "TASK-A-001.b")
    (root,) = j["roots"]
    assert root["ref"] == "TASK-A-001.a" and root["code"] == "CLAIMED"
    assert "laptop/main" in root["detail"]


def test_gates_report_state_and_cause_and_unanswered_probe_is_not_evaluable(repo):
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    _task(repo, "TASK-A-001", [("a", "  needs: [closed-probe]\n"),
                               ("b", "  needs: [broken-probe]\n")])
    r, j = _why(repo, "TASK-A-001.a")
    assert r.code == 0 and j["code"] == "GATED"
    assert "closed-probe closed (probe exit 1)" in j["trace"]["detail"]
    r, j = _why(repo, "TASK-A-001.b")
    assert r.code == 2 and j["code"] == "GATED"
    assert j["not_evaluable"]


def test_a_step_for_another_machine_names_who_provides_its_target(repo):
    _task(repo, "TASK-A-001", [("a", "  target: gpu\n"), ("b", "  depends_on: [a]\n")])
    _, j = _why(repo, "TASK-A-001.b")
    (root,) = j["roots"]
    assert root["code"] == "ELSEWHERE"
    assert root["detail"] == "target gpu: provided by workstation, not laptop"
    # asked as the other machine, the same step is workable
    r = run(repo, "why", "TASK-A-001.a", "--json", machine="workstation")
    assert json.loads(r.out)["code"] == "READY"


def test_task_level_dependency_expands_to_that_tasks_open_steps(repo):
    _task(repo, "TASK-B-002", [("a", ""), ("b", "")])
    _task(repo, "TASK-A-001", [("a", "  depends_on: [TASK-B-002]\n")])
    _, j = _why(repo, "TASK-A-001.a")
    (child,) = j["trace"]["children"]
    assert child["ref"] == "TASK-B-002" and child["code"] == "WAITING"
    assert {n["ref"] for n in child["children"]} == {"TASK-B-002.a", "TASK-B-002.b"}


def test_cycle_is_reported_not_followed_forever(repo):
    _task(repo, "TASK-A-001", [("a", "  depends_on: [b]\n"), ("b", "  depends_on: [a]\n")])
    r, j = _why(repo, "TASK-A-001.a")
    assert r.code == 0
    assert any(n["code"] == "CYCLE" for n in j["roots"])


def test_closed_and_unknown_refs(repo):
    _task(repo, "TASK-A-001", [("a", "")])
    assert run(repo, "done", "TASK-A-001.a").code == 0
    r, j = _why(repo, "TASK-A-001.a")
    assert r.code == 0 and j["code"] == "DONE"
    r = run(repo, "why", "TASK-A-001.z")
    assert r.code == 1 and "no step" in (r.out + r.err)
    r = run(repo, "why", "TASK-Z-999.a")
    assert r.code == 1


def test_why_agrees_with_ready_on_every_open_step(repo):
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    _task(repo, "TASK-A-001", [("a", ""), ("b", "  depends_on: [a]\n"),
                               ("c", "  needs: [closed-probe]\n"), ("d", "  target: gpu\n"),
                               ("e", "  depends_on: [nope]\n"), ("f", "")])
    assert run(repo, "claim", "TASK-A-001.f").code == 0
    ready = json.loads(run(repo, "ready", "--json").out)
    bucket = {}
    for k in ("ready", "gated", "claimed", "waiting", "malformed"):
        for row in ready[k]:
            bucket[row["ref"]] = k.upper()
    assert len(bucket) == 5  # d is elsewhere
    for ref, want in bucket.items():
        _, j = _why(repo, ref)
        assert j["code"] == want, (ref, j)
    assert _why(repo, "TASK-A-001.d")[1]["code"] == "ELSEWHERE"


def test_no_ref_prints_the_queue_summary_with_its_denominator(repo):
    _task(repo, "TASK-A-001", [("a", ""), ("b", "  depends_on: [a]\n"),
                               ("c", "  target: gpu\n")])
    r = run(repo, "why")
    assert r.code == 0, r
    first = r.out.splitlines()[0]
    assert first == "1 of 2 open step(s) workable on laptop: 1 waiting; 1 more open elsewhere (gpu: 1)"
    j = json.loads(run(repo, "why", "--json").out)
    assert j["counts"] == {"ready": 1, "gated": 0, "claimed": 0, "waiting": 1,
                           "malformed": 0}
    assert j["open_here"] == 2 and j["elsewhere"] == {"gpu": 1}


def test_why_is_read_only(repo):
    from conftest import git
    from crossbox import cli
    assert "why" in cli.READ_ONLY
    _task(repo, "TASK-A-001", [("a", ""), ("b", "  depends_on: [a]\n")])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")
    assert run(repo, "why", "TASK-A-001.b").code == 0
    assert run(repo, "why").code == 0
    assert git(repo, "status", "--porcelain").stdout == ""


def _raw(repo, name, text):
    (repo / ".crossbox" / "queue" / name).write_text(text)


DUP = ("id: TASK-D-001\ntitle: t\npriority: normal\nsteps:\n"
       "- id: a\n  title: x\n  status: pending\n"
       "- id: a\n  title: y\n  status: pending\n")


def test_a_step_in_a_malformed_file_is_a_malformed_node_not_an_abort(repo):
    _raw(repo, "TASK-D-001.yaml", DUP)
    _task(repo, "TASK-A-001", [("a", "  depends_on: [TASK-D-001.a]\n"),
                               ("b", "  depends_on: [TASK-D-001]\n")])
    ready = json.loads(run(repo, "ready", "--json").out)
    assert any(m["ref"] == "TASK-D-001" for m in ready["malformed"])
    r, j = _why(repo, "TASK-D-001.a")
    assert r.code == 0, r
    assert j["code"] == "MALFORMED" and "duplicate" in j["trace"]["detail"].lower()
    for ref in ("TASK-A-001.a", "TASK-A-001.b"):
        r, j = _why(repo, ref)
        assert r.code == 0, r
        assert j["roots"][0]["code"] == "MALFORMED"
        assert "duplicate" in j["roots"][0]["detail"].lower()
    r, j = _why(repo, "TASK-D-001")
    assert r.code == 0 and j["code"] == "MALFORMED"


def test_an_unparseable_file_and_a_twice_defined_id_are_malformed_nodes(repo):
    _raw(repo, "TASK-U-001.yaml", "id: TASK-U-001\nsteps: [unclosed\n  : :\n")
    _task(repo, "TASK-T-001", [("a", "")])
    _raw(repo, "TASK-T-001-copy.yaml", (repo / ".crossbox/queue/TASK-T-001.yaml").read_text())
    for ref in ("TASK-U-001.a", "TASK-T-001.a"):
        r, j = _why(repo, ref)
        assert r.code == 0, (ref, r)
        assert j["code"] == "MALFORMED"
    # a ref that names nothing at all still exits 1
    assert run(repo, "why", "TASK-Z-999.a").code == 1


def test_no_ref_denominator_is_readys_and_unreadable_files_are_named_apart(repo):
    _task(repo, "TASK-A-001", [("a", ""), ("b", "  depends_on: [a]\n")])
    _raw(repo, "TASK-D-001.yaml", DUP)
    ready = json.loads(run(repo, "ready", "--json").out)
    assert ready["open_here"] == 2
    j = json.loads(run(repo, "why", "--json").out)
    assert j["open_here"] == ready["open_here"] == 2
    assert j["unreadable_files"] == 1
    first = run(repo, "why").out.splitlines()[0]
    assert first.startswith("1 of 2 open step(s) workable on laptop")
    assert "1 task file(s) unreadable" in first
    assert "malformed" not in first.split("unreadable")[0]


def _deep_loads(text):
    """The stdlib json decoder recurses per nesting level and, before Python 3.14, stops near
    1,000 levels whatever sys.setrecursionlimit says; ijson builds the object iteratively.
    docs/limits.md tells users the same two ways out."""
    if sys.version_info >= (3, 14):
        return json.loads(text)
    ijson = pytest.importorskip("ijson", reason="reading 10,000-level JSON before Python 3.14 needs ijson")
    return next(ijson.items(io.BytesIO(text.encode("utf-8")), ""))


def test_a_very_deep_dependency_chain_is_explained_not_a_recursion_error(repo):
    import time
    n = 5000
    for i in range(n):
        dep = f"  depends_on: [TASK-C-{i - 1:04d}]\n" if i else ""
        _task(repo, f"TASK-C-{i:04d}", [("a", dep)])
    t = time.time()
    r = run(repo, "why", f"TASK-C-{n - 1:04d}.a", "--json")
    j = _deep_loads(r.out)
    assert r.code == 0, r.err[-300:]
    assert j["code"] == "WAITING"
    assert j["roots"] == [{"ref": "TASK-C-0000.a", "code": "READY",
                           "detail": "workable now on laptop"}]
    depth, node = 0, j["trace"]
    while node["children"]:
        node, depth = node["children"][0], depth + 1
    assert depth == 2 * (n - 1)  # task node, then its step, per link
    assert time.time() - t < 20
