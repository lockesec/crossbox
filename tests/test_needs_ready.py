"""Needs registry, gates, machines and readiness (invariants 68-72)."""
import json

import pytest
from conftest import run

from crossbox import needs
from pathlib import Path
import yaml

REG = """\
schema: 1
tokens:
  docker: {kind: capability, desc: needs docker}
  ok-probe: {kind: gate, probe: "exit 0"}
  closed-probe: {kind: gate, probe: "exit 1"}
  broken-probe: {kind: gate, probe: "exit 7"}
  cwd-probe: {kind: gate, probe: "test -f .crossbox/config.yaml"}
  signoff: {kind: gate, clearable: operator}
  vendor-fix: {kind: gate}
aliases:
  containers: docker
prefix_kinds:
  "compute-": estimate
  "upstream-fix:": gate
"""


def _setup(repo, steps):
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    body = ""
    for i, (needs_, extra) in enumerate(steps):
        body += (f"- id: {chr(97 + i)}\n  title: s{i}\n  status: pending\n"
                 f"  needs: {json.dumps(needs_)}\n{extra}")
    (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text(
        f"id: TASK-A-001\ntitle: t\npriority: high\nsteps:\n{body}")


def _ready(repo, *extra):
    r = run(repo, "ready", "--json", *extra)
    return r, json.loads(r.out)


def test_probe_without_answer_is_not_evaluable_and_not_cached(repo, monkeypatch):
    # invariant 68
    _setup(repo, [(["broken-probe"], ""), (["closed-probe"], ""), (["ok-probe"], "")])
    r, j = _ready(repo)
    gated = {g["ref"]: g["gates"][0] for g in j["gated"]}
    assert gated["TASK-A-001.a"]["state"] == "not-evaluable"
    assert gated["TASK-A-001.b"]["state"] == "closed"
    assert [x["ref"] for x in j["ready"]] == ["TASK-A-001.c"]
    reg = needs.Registry.load(repo / ".crossbox" / "needs-registry.yaml")
    reg.gate_state("broken-probe", repo)
    reg.gate_state("ok-probe", repo)
    assert "broken-probe" not in reg._probe_cache and "ok-probe" in reg._probe_cache


def test_probes_run_with_cwd_at_repo_root(repo, monkeypatch, tmp_path):
    # invariant 69
    _setup(repo, [(["cwd-probe"], "")])
    monkeypatch.chdir(tmp_path)
    _, j = _ready(repo)
    assert [x["ref"] for x in j["ready"]] == ["TASK-A-001.a"]


def test_malformed_needs_resolve_unknown_without_crashing(repo):
    # invariant 70
    reg = needs.Registry.load(repo / "nope.yaml")
    for bad in (None, 3, {"a": 1}, "", "   "):
        assert reg.resolve(bad)[0]["status"] == "unknown"
    # a malformed needs list never crashes and is never READY: it is MALFORMED
    _setup(repo, [([{"x": 1}, 7, "ok-probe"], ""), (["signoff"], ""), (["ok-probe"], "")])
    r, j = _ready(repo)
    assert r.code == 1
    assert [m["ref"] for m in j["malformed"]] == ["TASK-A-001.a"]
    assert {x["ref"] for x in j["ready"]} == {"TASK-A-001.c"}


def test_restricted_targets_never_widen_any_works_everywhere(repo):
    # invariant 71 (laptop provides docker; workstation provides gpu, docker)
    _setup(repo, [([], "  target: gpu\n"), ([], "  target: any\n"), ([], "  target: mystery\n"),
                  ([], ""), ([], "  target: laptop\n")])
    _, j = _ready(repo)
    assert {x["ref"] for x in j["ready"]} == {"TASK-A-001.b", "TASK-A-001.d", "TASK-A-001.e"}
    assert j["elsewhere"] == 1
    assert [m["ref"] for m in j["malformed"]] == ["TASK-A-001.c"]
    _, j = _ready(repo) if False else (None, json.loads(run(repo, "ready", "--json",
                                                            machine="workstation").out))
    assert {x["ref"] for x in j["ready"]} == {"TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.d"}


def test_malformed_steps_excluded_from_every_count_and_flagged_as_floor(repo):
    # invariant 72
    _setup(repo, [([], "  depends_on: notalist\n"), ([], "  depends_on: [3]\n"),
                  ([], "  depends_on: [zz]\n"), ([], ""), ([], "  status: weird\n".replace(
                      "  status: weird\n", ""))])
    (repo / ".crossbox" / "queue" / "TASK-A-002.yaml").write_text(
        "id: TASK-A-002\ntitle: t\nsteps:\n- just a string\n- id: a\n  title: x\n  status: wip\n")
    r, j = _ready(repo)
    assert r.code == 1 and j["counts_are_floor"] is True
    mal = {m["ref"] for m in j["malformed"]}
    # a step that is not a mapping makes the whole file unwritable, so every
    # step in it is malformed (the writers refuse it too)
    assert {"TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.c", "TASK-A-002"} <= mal
    others = {x["ref"] for k in ("ready", "gated", "claimed", "waiting") for x in j[k]}
    assert not (mal & others)
    text = run(repo, "ready").out
    assert "floor" in text


def test_operator_opens_operator_clearable_gates_only(repo):
    _setup(repo, [(["signoff"], ""), (["vendor-fix"], ""), (["upstream-fix:libfoo#12"], "")])
    _, j = _ready(repo)
    assert j["ready"] == [] and len(j["gated"]) == 3
    _, j = _ready(repo, "--operator")
    assert [x["ref"] for x in j["ready"]] == ["TASK-A-001.a"]


def test_date_reached_gate(repo):
    _setup(repo, [(["date-reached:2000-01-01"], ""), (["date-reached:2999-01-01"], ""),
                  (["date-reached:someday"], "")])
    _, j = _ready(repo)
    assert [x["ref"] for x in j["ready"]] == ["TASK-A-001.a"]
    assert len(j["gated"]) == 2


def test_capability_tokens_never_gate(repo):
    _setup(repo, [(["docker", "containers", "compute-heavy"], "")])
    _, j = _ready(repo)
    assert [x["ref"] for x in j["ready"]] == ["TASK-A-001.a"]


def test_claimed_steps_are_their_own_bucket(repo):
    _setup(repo, [([], ""), ([], "")])
    run(repo, "claim", "TASK-A-001.a", machine="workstation")
    _, j = _ready(repo)
    assert [x["ref"] for x in j["claimed"]] == ["TASK-A-001.a"]
    assert [x["ref"] for x in j["ready"]] == ["TASK-A-001.b"]


def test_ready_reads_sibling_claims(world):
    # readiness uses the union, not only the local file (open in the original design)
    for root in (world.laptop, world.wt1):
        (root / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text(
            "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n")
    run(world.wt1, "claim", "TASK-A-001.a")
    world.commit(world.wt1)
    _, j = _ready(world.laptop)
    assert [x["ref"] for x in j["claimed"]] == ["TASK-A-001.a"]


def test_needs_audit_reports_open_steps_only(repo):
    _setup(repo, [(["dockr", "containers", "two words"], ""),
                  (["bogus"], "  status: done\n".replace("  status: done\n", ""))])
    p = repo / ".crossbox" / "queue" / "TASK-A-001.yaml"
    p.write_text(p.read_text().replace("title: s1\n  status: pending", "title: s1\n  status: done"))
    r = run(repo, "needs", "audit", "--json")
    j = json.loads(r.out)
    assert r.code == 1 and j["open_steps"] == 1 and j["closed_steps_skipped"] == 1
    kinds = {(f["token"], f["status"]) for f in j["findings"]}
    assert ("dockr", "unknown") in kinds and ("containers", "alias") in kinds
    assert ("two words", "split") in kinds
    assert [f for f in j["findings"] if f["token"] == "dockr"][0]["suggestion"] == "docker"


def test_needs_here_is_ready(repo):
    _setup(repo, [([], "")])
    assert json.loads(run(repo, "needs", "here", "--json").out)["ready"] == \
        json.loads(run(repo, "ready", "--json").out)["ready"]


# ---- release review, blocker 4: a gate that could not be evaluated is exit 2 ----

def test_a_not_evaluable_probe_makes_ready_exit_2_and_keeps_the_step_detail(repo):
    _setup(repo, [(["broken-probe"], ""), (["ok-probe"], "")])
    r, out = _ready(repo)
    assert r.code == 2, r
    [g] = out["gated"]
    assert g["ref"] == "TASK-A-001.a" and g["gates"][0]["state"] == needs.NE
    assert [x["ref"] for x in out["ready"]] == ["TASK-A-001.b"]
    assert out["gates_not_evaluable"] == 1
    assert out["counts_are_floor"] is True        # "READY is a floor" is the stated contract
    txt = run(repo, "ready")
    assert txt.code == 2 and "not-evaluable" in txt.out and "NOT EVALUABLE" in txt.out


def test_a_closed_probe_is_an_answer_not_a_failure(repo):
    # the clear direction: exit 1 from a probe is CLOSED, and ready still exits 0
    _setup(repo, [(["closed-probe"], ""), (["ok-probe"], "")])
    r, out = _ready(repo)
    assert r.code == 0 and out["gates_not_evaluable"] == 0
    assert out["counts_are_floor"] is False       # the quiet direction


def test_summary_with_ready_is_not_evaluable_when_a_probe_is(repo):
    _setup(repo, [(["broken-probe"], "")])
    r = run(repo, "status", "--summary", "--ready", "--json")
    assert r.code == 2, r
    assert json.loads(r.out)["ready"]["gates_not_evaluable"] == 1


def test_summary_text_names_the_probe_not_malformed_steps(repo):
    _setup(repo, [(["broken-probe"], "")])
    r = run(repo, "status", "--summary", "--ready")
    assert r.code == 2 and "gate probe(s) gave no answer" in r.out and "malformed)" not in r.out


def test_needs_audit_names_unparseable_tasks_and_is_not_evaluable(repo):
    # review hardening: an audit that skipped a file it could not read and
    # exited 0 read as "every need is known"
    _setup(repo, [(["docker"], "")])
    (repo / ".crossbox" / "queue" / "TASK-A-002.yaml").write_text("id: TASK-A-002\nsteps: [\n")
    r = run(repo, "needs", "audit")
    assert r.code == 2, r
    assert "TASK-A-002" in r.out and "floor" in r.out
    out = json.loads(run(repo, "needs", "audit", "--json").out)
    assert out["unparseable"] == ["TASK-A-002.yaml"] and out["counts_are_floor"] is True


def test_needs_audit_on_a_clean_queue_still_exits_0(repo):
    _setup(repo, [(["docker"], "")])
    assert run(repo, "needs", "audit").code == 0


def _step(sid, title="s", status="pending", dep=None):
    return {"id": sid, "title": title, "target": "any", "needs": [],
            "status": status, "depends_on": dep or []}


def _task(root, tid, steps, fname=None, title="T", tier="queue", **extra):
    data = {"id": tid, "title": title, "priority": "normal", "steps": steps}
    data.update(extra)
    p = Path(root) / ".crossbox" / tier / (fname or f"{tid}.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10 ** 6))
    return p


def test_ready_flags_a_task_id_defined_in_two_queue_files(repo):
    """R6/F6: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-001", [_step("a", "first")])
    _task(repo, "TASK-001", [_step("a", "second")], fname="TASK-001-copy.yaml")
    _task(repo, "TASK-002", [_step("a")])
    r = run(repo, "ready", "--json")
    res = json.loads(r.out)
    assert r.code == 1, r
    assert "TASK-001.a" not in [x["ref"] for x in res["ready"]]
    assert [x["ref"] for x in res["ready"]] == ["TASK-002.a"]
    mal = [m for m in res["malformed"] if m["ref"] == "TASK-001"]
    assert len(mal) == 2 and all("TASK-001-copy.yaml" in m["cause"] for m in mal), res
    t = run(repo, "ready")
    assert t.code == 1 and "2 malformed" in t.out, t


def test_distinct_task_ids_are_not_flagged(repo):
    """R6/F6: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-001", [_step("a")])
    _task(repo, "TASK-002", [_step("a")])
    r = run(repo, "ready", "--json")
    assert r.code == 0 and not json.loads(r.out)["malformed"], r


@pytest.mark.parametrize("verb", [["done"], ["batch-done"]])
def test_done_no_probes_does_not_run_the_probe(repo, tmp_path, verb):
    """`done` checks a closed step's gates; with --no-probes a probed gate is noted, not run."""
    marker = tmp_path / "marker"
    (repo / ".crossbox" / "needs-registry.yaml").write_text(
        f'schema: 1\ntokens:\n  boom: {{kind: gate, probe: "echo x >> {marker}; exit 1"}}\n')
    p = _task(repo, "TASK-001", [_step("a")])
    p.write_text(p.read_text().replace("needs: []", "needs: [boom]"))
    r = run(repo, *verb, "TASK-001.a", "--no-probes")
    assert r.code == 0, r
    assert not marker.exists(), r
    assert "probes disabled" in r.err
    # without the flag the gate is checked, which runs the probe (the documented CLI default)
    p2 = _task(repo, "TASK-002", [_step("a")])
    p2.write_text(p2.read_text().replace("needs: []", "needs: [boom]"))
    run(repo, *verb, "TASK-002.a")
    assert marker.exists()


def test_ready_no_probes_does_not_run_the_probe(repo, tmp_path):
    """R6/F1: see INVARIANTS.md, sixth-review findings."""
    marker = tmp_path / "marker"
    (repo / ".crossbox" / "needs-registry.yaml").write_text(
        f'schema: 1\ntokens:\n  boom: {{kind: gate, probe: "echo x >> {marker}; exit 1"}}\n')
    p = _task(repo, "TASK-001", [_step("a")])
    p.write_text(p.read_text().replace("needs: []", "needs: [boom]"))
    r = run(repo, "ready", "--no-probes")
    assert not marker.exists(), r
    assert "probes disabled" in r.out
    assert r.code == 2
