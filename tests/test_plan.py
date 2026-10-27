"""`crossbox plan` (docs/plan.md; plan invariants P1-P12 in INVARIANTS.md, CBX-CORE-001.c)."""
import json
import time

from conftest import init_repo, run
import yaml
import random
from crossbox import plan as planmod

REG = """\
schema: 1
tokens:
  ok-probe: {kind: gate, probe: "exit 0"}
  closed-probe: {kind: gate, probe: "exit 1"}
  broken-probe: {kind: gate, probe: "exit 7"}
"""


def put(root, tid, steps, priority="normal", tier="queue"):
    """steps: [(id, {status, target, depends_on, needs, title})]; written as
    flow-style YAML (JSON is YAML)."""
    rows = []
    for sid, kw in steps:
        row = {"id": sid, "title": kw.get("title", f"t-{sid}"),
               "status": kw.get("status", "pending")}
        for k in ("target", "depends_on", "needs"):
            if k in kw:
                row[k] = kw[k]
        rows.append(row)
    d = root / ".crossbox" / tier
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{tid}.yaml").write_text(json.dumps(
        {"id": tid, "title": tid, "priority": priority, "steps": rows}, indent=1) + "\n")


def plan(root, *extra, machine=None):
    r = run(root, "plan", "--json", *extra, machine=machine)
    return r, json.loads(r.out)


def refs(rows):
    return [r["ref"] for r in rows]


def planned_refs(j):
    return (set(refs(j["next"])) | set(j["order"]) | set(refs(j["critical_path"]))
            | {s["ref"] for w in j["waves"] for s in w["steps"]})


def _claim(repo, ref):
    assert run(repo, "claim", ref).code == 0


def test_ranking_unblocks_then_priority_then_ref(repo):
    # plan invariant P1
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["b"]}), ("d", {}), ("e", {})],
        priority="low")
    put(repo, "TASK-B-001", [("a", {}), ("b", {})], priority="critical")
    put(repo, "TASK-C-001", [("a", {})], priority="high")
    r, j = plan(repo)
    assert r.code == 0
    got = [(x["ref"], x["unblocks"], x["priority"]) for x in j["next"]]
    assert got == [("TASK-A-001.a", 2, "low"),
                   ("TASK-B-001.a", 0, "critical"), ("TASK-B-001.b", 0, "critical"),
                   ("TASK-C-001.a", 0, "high"),
                   ("TASK-A-001.d", 0, "low"), ("TASK-A-001.e", 0, "low")]
    assert j["next"][0]["dependents"] == 1  # b only; c is transitive


def test_unblocks_counts_distinct_descendants_once(repo):
    # plan invariant P1: a diamond counts the join step once
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["a"]}),
                             ("d", {"depends_on": ["b", "c"]})])
    _, j = plan(repo)
    assert j["next"][0]["unblocks"] == 3 and j["next"][0]["dependents"] == 2


def test_task_ref_dependency_fans_out_to_every_open_step(repo):
    # plan invariant P2
    put(repo, "TASK-A-001", [("a", {}), ("b", {}), ("c", {"status": "done"})])
    put(repo, "TASK-B-001", [("a", {"depends_on": ["TASK-A-001"]})])
    r, j = plan(repo)
    byref = {x["ref"]: x for x in j["next"]}
    assert byref["TASK-A-001.a"]["dependents"] == 1
    assert byref["TASK-A-001.b"]["dependents"] == 1
    assert "TASK-A-001.c" not in byref
    w = {s["ref"]: w["wave"] for w in j["waves"] for s in w["steps"]}
    assert w["TASK-B-001.a"] == 1


def test_met_and_archived_dependencies_add_no_edge(repo):
    # plan invariant P2
    put(repo, "TASK-Z-001", [("a", {"status": "done"})], tier="archive")
    put(repo, "TASK-A-001", [("a", {"status": "done"}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["TASK-Z-001", "TASK-Z-001.a"]})])
    r, j = plan(repo)
    assert r.code == 0
    assert refs(j["next"]) == ["TASK-A-001.b", "TASK-A-001.c"]
    assert [x["unblocks"] for x in j["next"]] == [0, 0]
    assert len(j["waves"]) == 1 and j["critical_path"][0]["ref"] == "TASK-A-001.b"


def test_critical_path_length_and_tiebreak(repo):
    # plan invariant P3: two chains of 3; the one that starts with the smaller ref wins
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["b"]})])
    put(repo, "TASK-B-001", [("a", {}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["b"]}), ("d", {})])
    _, j = plan(repo)
    assert refs(j["critical_path"]) == ["TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.c"]
    assert [x["on_critical_path"] for x in j["next"] if x["ref"] == "TASK-A-001.a"] == [True]
    assert [x["on_critical_path"] for x in j["next"] if x["ref"] == "TASK-B-001.d"] == [False]


def test_critical_path_tiebreak_inside_a_fork(repo):
    # plan invariant P3: at a fork the smaller ref wins, not the first one found
    put(repo, "TASK-A-001", [("a", {}), ("z", {"depends_on": ["a"]}),
                             ("b", {"depends_on": ["a"]}),
                             ("y", {"depends_on": ["z"]}), ("c", {"depends_on": ["b"]})])
    _, j = plan(repo)
    assert refs(j["critical_path"]) == ["TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.c"]


def test_waves_match_path_length_and_never_depend_inside(repo):
    # plan invariant P4
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["a"]}), ("d", {"depends_on": ["b"]}),
                             ("e", {"depends_on": ["c", "d"]}), ("f", {})])
    put(repo, "TASK-B-001", [("a", {"depends_on": ["TASK-A-001.d"], "target": "gpu"})])
    r, j = plan(repo)
    assert len(j["waves"]) == len(j["critical_path"]) == 4
    wave = {s["ref"]: w["wave"] for w in j["waves"] for s in w["steps"]}
    assert wave["TASK-A-001.e"] == 3 and wave["TASK-B-001.a"] == 3
    deps = {"TASK-A-001.b": ["TASK-A-001.a"], "TASK-A-001.c": ["TASK-A-001.a"],
            "TASK-A-001.d": ["TASK-A-001.b"], "TASK-A-001.e": ["TASK-A-001.c", "TASK-A-001.d"],
            "TASK-B-001.a": ["TASK-A-001.d"]}
    pos = {ref: i for i, ref in enumerate(j["order"])}
    assert len(pos) == len(j["order"]) == len(wave) == 7
    for ref, ds in deps.items():
        for d in ds:
            assert wave[d] < wave[ref] and pos[d] < pos[ref]


def test_wave_rows_are_grouped_by_target_then_priority_then_ref(repo):
    # plan invariant P4
    put(repo, "TASK-A-001", [("a", {"target": "gpu"}), ("b", {}), ("c", {"target": "gpu"})],
        priority="low")
    put(repo, "TASK-B-001", [("a", {"target": "gpu"})], priority="high")
    _, j = plan(repo)
    assert [(s["target"], s["ref"]) for s in j["waves"][0]["steps"]] == [
        ("any", "TASK-A-001.b"), ("gpu", "TASK-B-001.a"),
        ("gpu", "TASK-A-001.a"), ("gpu", "TASK-A-001.c")]
    assert j["order"][0] == "TASK-B-001.a"  # order ignores target: priority first


def test_next_is_exactly_what_ready_calls_ready(repo):
    # plan invariant P5
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", [
        ("a", {}), ("b", {"needs": ["closed-probe"]}), ("c", {}),
        ("d", {"target": "gpu"}), ("e", {"depends_on": ["a"]}),
        ("f", {"needs": ["ok-probe"]})])
    _claim(repo, "TASK-A-001.c")
    rj = json.loads(run(repo, "ready", "--json").out)
    r, j = plan(repo)
    assert r.code == 0
    assert set(refs(j["next"])) == set(refs(rj["ready"])) == {"TASK-A-001.a", "TASK-A-001.f"}
    assert len(j["next"]) == len(rj["ready"])


def test_machine_flag_uses_that_machines_capabilities(repo):
    # plan invariant P5
    put(repo, "TASK-A-001", [("a", {"target": "gpu"}), ("b", {})])
    _, here = plan(repo)
    assert refs(here["next"]) == ["TASK-A-001.b"] and here["machine"] == "laptop"
    r = run(repo, "--machine", "workstation", "plan", "--json")
    there = json.loads(r.out)
    assert there["machine"] == "workstation"
    assert refs(there["next"]) == ["TASK-A-001.a", "TASK-A-001.b"]
    ready = json.loads(run(repo, "--machine", "workstation", "ready", "--json").out)
    assert set(refs(there["next"])) == set(refs(ready["ready"]))
    assert plan(repo)[1]["machine"] == "laptop"


def test_path_states_name_every_kind(repo):
    # plan invariant P5
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["b"], "target": "gpu"})])
    put(repo, "TASK-B-001", [("a", {"needs": ["closed-probe"]}),
                             ("b", {"depends_on": ["a"]}), ("c", {"depends_on": ["b"]}),
                             ("d", {"depends_on": ["c"]}), ("e", {"depends_on": ["d"]})])
    _, j = plan(repo)
    assert [(x["ref"], x["state"]) for x in j["critical_path"]] == [
        ("TASK-B-001.a", "GATED"), ("TASK-B-001.b", "WAITING"), ("TASK-B-001.c", "WAITING"),
        ("TASK-B-001.d", "WAITING"), ("TASK-B-001.e", "WAITING")]
    put(repo, "TASK-B-001", [("a", {"status": "done"})])
    _claim(repo, "TASK-A-001.a")
    _, j = plan(repo)
    assert [(x["ref"], x["state"]) for x in j["critical_path"]] == [
        ("TASK-A-001.a", "CLAIMED"), ("TASK-A-001.b", "WAITING"),
        ("TASK-A-001.c", "elsewhere")]
    _, j = plan(repo, machine="workstation")
    assert j["critical_path"][0]["state"] == "CLAIMED" or j["critical_path"][0]["state"] == "READY"
    assert j["critical_path"][2]["state"] == "WAITING"


def test_malformed_steps_appear_only_under_malformed(repo):
    # plan invariant P6
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", [
        ("a", {}), ("b", {"depends_on": ["nope"]}), ("c", {"target": "mystery"}),
        ("d", {"needs": ["not-in-registry"]}), ("e", {"status": "weird"}),
        ("f", {"depends_on": "a"}),
        ("g", {"depends_on": ["b"]}),     # waits on a malformed step
        ("h", {"depends_on": ["a"], "needs": ["not-in-registry"]})])  # waiting, bad needs
    r, j = plan(repo)
    assert r.code == 1 and j["counts_are_floor"] is True
    bad = {m["ref"]: m["cause"] for m in j["malformed"]}
    assert set(bad) == {f"TASK-A-001.{x}" for x in "bcdefgh"}
    assert "dangling dependency" in bad["TASK-A-001.b"]
    assert "unknown target" in bad["TASK-A-001.c"] and "unknown need" in bad["TASK-A-001.d"]
    assert "unknown status" in bad["TASK-A-001.e"] and "not a list" in bad["TASK-A-001.f"]
    assert "TASK-A-001.b" in bad["TASK-A-001.g"]
    assert planned_refs(j) == {"TASK-A-001.a"} and not j["blocked_by_cycle"]
    assert j["nodes"] == 1 and j["open_steps"] == 8


def test_a_file_no_writer_will_touch_is_malformed_and_so_are_its_dependents(repo):
    # plan invariant P6
    (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text("id: TASK-A-001\nsteps: [\n")
    put(repo, "TASK-B-001", [("a", {}), ("b", {"depends_on": ["TASK-A-001.a"]})])
    r, j = plan(repo)
    assert r.code == 1
    assert refs(j["malformed"]) == ["TASK-A-001", "TASK-B-001.b"]
    assert j["order"] == ["TASK-B-001.a"]


def _cycle_world(repo):
    put(repo, "TASK-A-001", [("a", {"depends_on": ["TASK-B-001.a"]}),
                             ("b", {"depends_on": ["a"]}), ("c", {})])
    put(repo, "TASK-B-001", [("a", {"depends_on": ["TASK-A-001.a"]}),
                             ("z", {"depends_on": ["TASK-A-001.b"]}), ("y", {})])


def test_cross_task_cycle_is_named_once_and_excluded(repo):
    # plan invariant P7
    _cycle_world(repo)
    r, j = plan(repo)
    assert r.code == 1
    assert j["cycles"] == [["TASK-A-001.a", "TASK-B-001.a"]]
    assert j["blocked_by_cycle"] == ["TASK-A-001.b", "TASK-B-001.z"]
    assert set(j["order"]) == {"TASK-A-001.c", "TASK-B-001.y"}
    gone = {"TASK-A-001.a", "TASK-B-001.a", "TASK-A-001.b", "TASK-B-001.z"}
    assert not gone & planned_refs(j)
    assert j["nodes"] == 6 and j["counts_are_floor"] is True
    out = run(repo, "plan").out
    assert out.count("CYCLE: TASK-A-001.a -> TASK-B-001.a -> TASK-A-001.a") == 1


def test_self_dependency_and_longer_cycles_start_at_the_smallest_ref(repo):
    # plan invariant P7
    put(repo, "TASK-A-001", [("a", {"depends_on": ["TASK-A-001"]}),   # waits on own task
                             ("b", {"depends_on": ["d"]}), ("c", {"depends_on": ["b"]}),
                             ("d", {"depends_on": ["c"]}), ("e", {})])
    r, j = plan(repo)
    assert r.code == 1
    assert j["cycles"] == [["TASK-A-001.a"], ["TASK-A-001.b", "TASK-A-001.c", "TASK-A-001.d"]]
    assert j["order"] == ["TASK-A-001.e"]


def test_not_evaluable_wins_over_malformed(repo):
    # plan invariant P8
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["nope"]})])
    (repo / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    r, j = plan(repo)
    assert r.code == 2 and "claims file damaged" in j["not_evaluable"]
    assert j["next"] == [] and j["malformed"] and j["order"] == ["TASK-A-001.a"]
    assert j["critical_path"][0]["state"] == "NOT EVALUABLE"
    out = run(repo, "plan")
    assert out.code == 2 and "NOT EVALUABLE" in out.out


def test_a_gate_probe_with_no_answer_is_not_evaluable(repo):
    # plan invariant P8
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", [("a", {"needs": ["broken-probe"]}), ("b", {}),
                             ("c", {"depends_on": ["nope"]})])
    r, j = plan(repo)
    assert r.code == 2 and "probe" in j["not_evaluable"]
    assert refs(j["next"]) == ["TASK-A-001.b"] and j["counts_are_floor"] is True
    assert "NOT EVALUABLE" in run(repo, "plan").out


def test_orphaned_claim_exits_1_and_is_listed(repo):
    # plan invariant P8
    put(repo, "TASK-A-001", [("a", {})])
    _claim(repo, "TASK-A-001.a")
    put(repo, "TASK-A-001", [("b", {})])
    r, j = plan(repo)
    assert r.code == 1 and len(j["orphaned_claims"]) == 1 and j["counts_are_floor"]
    assert "ORPHANED claim" in run(repo, "plan").out


def test_missing_queue_is_not_evaluable(plain):
    # plan invariant P8
    q = plain / ".crossbox" / "queue"
    for f in q.iterdir():
        f.unlink()
    q.rmdir()
    r = run(plain, "plan", "--json")
    assert r.code == 2 and "not_evaluable" in json.loads(r.out)


def test_output_is_byte_identical_and_independent_of_file_creation_order(tmp_path):
    # plan invariant P9
    specs = [("TASK-A-001", [("a", {}), ("b", {"depends_on": ["TASK-B-001.a"]})]),
             ("TASK-B-001", [("a", {}), ("b", {"depends_on": ["a"]})]),
             ("TASK-C-001", [("a", {"depends_on": ["TASK-A-001"]}), ("b", {})])]
    outs = []
    for i, order in enumerate((specs, list(reversed(specs)))):
        root = init_repo(tmp_path / f"r{i}")
        for tid, steps in order:
            put(root, tid, steps, priority="high")
        outs.append((run(root, "plan").out, run(root, "plan").out,
                     run(root, "plan", "--json").out, run(root, "plan", "--json").out))
    assert outs[0][0] == outs[0][1] == outs[1][0]
    assert outs[0][2] == outs[0][3] == outs[1][2]
    assert "TASK-A-001.a" in outs[0][0]


def test_json_schema_keys_are_exactly_the_contract(repo):
    # plan invariant P10
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]})], priority="high")
    r, j = plan(repo)
    assert list(j) == ["machine", "slot", "open_steps", "nodes", "next", "critical_path",
                       "waves", "order", "cycles", "blocked_by_cycle", "malformed",
                       "orphaned_claims", "not_evaluable", "counts_are_floor"]
    assert list(j["next"][0]) == ["ref", "title", "priority", "target", "unblocks",
                                  "dependents", "on_critical_path"]
    assert list(j["critical_path"][0]) == ["ref", "target", "state"]
    assert list(j["waves"][0]) == ["wave", "steps"]
    assert list(j["waves"][0]["steps"][0]) == ["ref", "target", "priority"]
    assert (j["not_evaluable"], j["counts_are_floor"], j["slot"]) == (None, False, "main")
    assert (j["open_steps"], j["nodes"], j["order"]) == (2, 2, ["TASK-A-001.a", "TASK-A-001.b"])
    r = run(repo, "plan", "--json", "--top", "1")  # --top does not apply to --json
    assert json.loads(r.out) == j


def test_top_limits_text_sections_and_says_what_it_left_out(repo):
    # plan invariant P10
    put(repo, "TASK-A-001", [(c, {}) for c in "abcdefgh"])
    out = run(repo, "plan", "--top", "3").out
    assert out.count("... 5 more not shown (--top)") == 3  # next, the wave, order
    assert "top 3 of 8 ready" in out
    assert out.rstrip().splitlines()[-1].startswith("plan: 8 open step(s) · 8 node(s) · 8 ready")
    assert out.count("  TASK-A-001.") == 3 + 3  # next and wave rows capped
    assert "more not shown" not in run(repo, "plan").out
    assert run(repo, "plan").out.count("  TASK-A-001.") == 8 + 8
    for bad in ("0", "-1", "x"):
        assert run(repo, "plan", "--top", bad).code == 64


def test_empty_queue_is_an_empty_plan_not_an_error(repo):
    r, j = plan(repo)
    assert r.code == 0 and j["next"] == [] and j["waves"] == [] and j["order"] == []
    assert run(repo, "plan").out.rstrip().endswith("critical path 0")


def test_plan_is_read_only_and_not_metered(repo, monkeypatch, tmp_path):
    # plan invariant P11
    put(repo, "TASK-A-001", [("a", {})])
    before = {p: p.read_bytes() for p in (repo / ".crossbox").rglob("*") if p.is_file()}
    monkeypatch.setenv("CROSSBOX_RATE_DISABLE", "0")
    monkeypatch.setenv("CROSSBOX_RATE_CAP", "1")
    for _ in range(4):
        assert run(repo, "plan").code == 0
        assert run(repo, "plan", "--json").code == 0
    assert not (tmp_path / "rate" / "state.json").exists()
    after = {p: p.read_bytes() for p in (repo / ".crossbox").rglob("*") if p.is_file()}
    assert before == after


def test_plan_does_not_take_the_writer_lock(repo, monkeypatch):
    # plan invariant P11: a writer holding the queue lock does not stall a reader
    from crossbox import lock
    put(repo, "TASK-A-001", [("a", {})])
    monkeypatch.setenv("CROSSBOX_LOCK_TIMEOUT", "0.2")
    with lock.hold(repo):
        assert run(repo, "plan").code == 0
        assert run(repo, "ready").code == 0
        assert run(repo, "done", "TASK-A-001.a").code == 2  # a writer does wait


def _sid(i):
    return "x" + chr(97 + i // 26) + chr(97 + i % 26)


def _big(root, tasks=60, per=50, fan=False):
    """tasks*per steps: a chain (each step waits on the one before, across
    tasks too) or a fan (every step waits on the one root step)."""
    n = 0
    for t in range(tasks):
        steps = []
        for i in range(per):
            if fan:
                dep = [] if (t, i) == (0, 0) else ["TASK-P-001." + _sid(0)]
            elif i:
                dep = [_sid(i - 1)]
            elif t:
                dep = [f"TASK-P-{t:03d}." + _sid(per - 1)]
            else:
                dep = []
            steps.append((_sid(i), {"depends_on": dep}))
            n += 1
        put(root, f"TASK-P-{t + 1:03d}", steps)
    return n


def test_3000_step_chain_and_fan_graphs_are_fast(repo):
    # plan invariant P12
    assert _big(repo) == 3000
    t = time.time()
    r, j = plan(repo)
    chain = time.time() - t
    assert r.code == 0 and j["nodes"] == 3000 and len(j["critical_path"]) == 3000
    assert len(j["waves"]) == 3000 and len(j["order"]) == 3000
    assert j["next"][0]["unblocks"] == 2999
    for p in (repo / ".crossbox" / "queue").glob("TASK-P-*.yaml"):
        p.unlink()
    assert _big(repo, fan=True) == 3000
    t = time.time()
    r, j = plan(repo)
    fan = time.time() - t
    assert r.code == 0 and j["nodes"] == 3000 and len(j["waves"]) == 2
    assert j["next"][0]["unblocks"] == 2999 and len(j["next"]) == 1
    assert chain < 8 and fan < 8, (chain, fan)


def _task(root, tid, steps, fname=None, title="T"):
    data = {"id": tid, "title": title, "priority": "normal", "steps": steps}
    p = root / ".crossbox" / "queue" / (fname or f"{tid}.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10 ** 6))
    return p


def _step(sid, title="s", status="pending", dep=None, needs=None, target="any"):
    return {"id": sid, "title": title, "target": target, "needs": needs or [],
            "status": status, "depends_on": dep or []}


def test_plan_never_ranks_a_task_id_defined_in_two_files(repo):
    """R6/F6: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-A-001", [_step("a", "first")])
    _task(repo, "TASK-A-001", [_step("a", "second")], fname="TASK-A-001-copy.yaml")
    _task(repo, "TASK-A-002", [_step("a")])
    r = run(repo, "plan", "--json")
    p = json.loads(r.out)
    nxt = [n["ref"] for n in p["next"]]
    assert r.code == 1 and nxt == ["TASK-A-002.a"], p
    assert [m["ref"] for m in p["malformed"] if m["ref"] == "TASK-A-001.a"] == ["TASK-A-001.a"]
    assert "TASK-A-001.a" not in p["order"]


def test_plan_lists_a_step_once(repo):
    """R6/F6: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-A-001", [_step("a")])
    p = json.loads(run(repo, "plan", "--json").out)
    assert [n["ref"] for n in p["next"]] == ["TASK-A-001.a"]


def test_plan_cleans_titles_and_ids(repo):
    """R6/F9: see INVARIANTS.md, sixth-review findings."""
    fake = "  TASK-A-001.zz   unblocks 99 forged"
    _task(repo, "TASK-A-001", [_step("a", "real\n" + fake)])
    p = _task(repo, "TASK-A-003", [_step("a")])
    p.write_text(yaml.safe_dump({"id": "TASK-A-003\x1b[2J", "title": "t", "steps": [_step("a")]}))
    r = run(repo, "plan")
    assert not any(ln.lstrip().startswith("TASK-A-001.zz") for ln in r.out.splitlines()), r.out
    assert "\x1b" not in r.out + r.err
    assert "forged" in r.out
    j = run(repo, "plan", "--json")
    assert "\\u001b" in j.out                    # JSON keeps the raw text, escaped


def test_a_good_machine_flag_still_plans_as_that_machine(repo):
    """R6/F16: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-A-001", [_step("a", target="gpu")])
    assert [n["ref"] for n in json.loads(run(repo, "--machine", "workstation", "plan",
                                              "--json").out)["next"]] == ["TASK-A-001.a"]
    assert json.loads(run(repo, "plan", "--json").out)["next"] == []


def _bfs_counts(nodes, succ):
    out = {}
    for r in nodes:
        seen, stack = {r}, [r]
        while stack:
            for w in succ[stack.pop()]:
                if w not in seen:
                    seen.add(w)
                    stack.append(w)
        out[r] = len(seen) - 1
    return out


def _sid_r6(i):
    n, out = i + 1, ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def test_descendant_counts_match_a_search_on_random_graphs_with_cycles():
    """R6/F17: see INVARIANTS.md, sixth-review findings."""
    rng = random.Random(6)
    for _ in range(200):
        n = rng.randint(1, 40)
        nodes = [f"r{i:02d}" for i in range(n)]
        succ = {r: sorted({rng.choice(nodes) for _ in range(rng.randint(0, 3))}) for r in nodes}
        comps = planmod._sccs(nodes, succ)
        got = planmod._descendant_counts(nodes, succ, comps, set(nodes))
        assert got == _bfs_counts(nodes, succ), (nodes, succ)
        part = set(rng.sample(nodes, max(1, n // 3)))
        assert planmod._descendant_counts(nodes, succ, comps, part) == {
            r: c for r, c in _bfs_counts(nodes, succ).items() if r in part}


def test_plan_counts_unblocks_once_not_per_ready_step(repo, monkeypatch):
    """R6/F17: see INVARIANTS.md, sixth-review findings."""
    n = 400
    _task(repo, "TASK-A-001", [_step(_sid_r6(i), dep=[_sid_r6(i - 1)] if i else []) for i in range(n)])
    _task(repo, "TASK-A-002", [_step(_sid_r6(i)) for i in range(n)])
    t1 = repo / ".crossbox" / "queue" / "TASK-A-001.yaml"
    d = yaml.safe_load(t1.read_text())
    d["steps"][0]["depends_on"] = ["TASK-A-002"]
    t1.write_text(yaml.safe_dump(d, sort_keys=False))
    calls = []
    real = planmod._reach
    monkeypatch.setattr(planmod, "_reach", lambda *a: calls.append(1) or real(*a))
    p = json.loads(run(repo, "plan", "--json").out)
    assert len(p["next"]) == n and all(r["unblocks"] == n for r in p["next"])
    assert len(calls) <= 1, f"{len(calls)} searches for {n} ready steps"


def test_6000_steps_of_ready_roots_feeding_a_chain_plan_in_seconds(repo):
    """R6/F17: see INVARIANTS.md, sixth-review findings."""
    n = 3000
    chain = [_step(_sid_r6(i), dep=[_sid_r6(i - 1)] if i else ["TASK-A-002"]) for i in range(n)]
    _task(repo, "TASK-A-001", chain)
    _task(repo, "TASK-A-002", [_step(_sid_r6(i)) for i in range(n)])
    t = time.time()
    r = run(repo, "plan", "--json")
    took = time.time() - t
    p = json.loads(r.out)
    assert r.code == 0 and p["nodes"] == 2 * n and len(p["next"]) == n
    assert p["next"][0]["unblocks"] == n and len(p["critical_path"]) == n + 1
    assert took < 15, took


def test_plan_with_an_incomplete_claims_view_ranks_nothing(world):
    """R6/F20: see INVARIANTS.md, sixth-review findings."""
    a = world.laptop
    _task(a, "TASK-A-001", [_step("a"), _step("b", dep=["a"])])
    world.commit(a)
    (world.wt1 / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    world.commit(world.wt1)
    r = run(a, "plan", "--json")
    p = json.loads(r.out)
    assert r.code == 2 and p["next"] == [], p
    assert p["not_evaluable"] and "wt1" in p["not_evaluable"]
    assert {s["state"] for s in p["critical_path"]} == {"NOT EVALUABLE"}
    t = run(a, "plan")
    assert "READY" not in t.out and "NOT EVALUABLE" in t.out
    # the quiet direction: with a sound sibling the same queue ranks
    (world.wt1 / ".crossbox" / "claims.yaml").write_text("claims: []\n")
    world.commit(world.wt1, "fix")
    ok = json.loads(run(a, "plan", "--json").out)
    assert [n["ref"] for n in ok["next"]] == ["TASK-A-001.a"], ok
