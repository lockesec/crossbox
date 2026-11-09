"""Property tests written from the README and docs/, not from the code.

They check readiness, planning, claims, step ids, the claims merge driver, the
rate gate, config validation and the graph exports against the documented
rules, using small independent models. Each queue lives in a temporary
directory, and HOME is a temporary directory too.
"""
import json
import os
import re
import sys
import time
from contextlib import contextmanager

import pytest
import yaml

pytest.importorskip("hypothesis")

from conftest import CONFIG, SRC, git, run  # noqa: E402
from hypothesis import example, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

PROP = settings(derandomize=True, database=None, deadline=None, max_examples=20)
TID = "TASK-API-001"


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / "state"))


@contextmanager
def env(**kw):
    old = {k: os.environ.get(k) for k in kw}
    try:
        for k, v in kw.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def letters(i):
    """Bijective base 26 for a 0-based index: a..z, aa, ab, ..."""
    i += 1
    s = ""
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(97 + r) + s
    return s


def ref(i, tid=TID):
    return f"{tid}.{letters(i)}"


class Q:
    def __init__(self, factory, config=CONFIG):
        self.root = factory.mktemp("q", numbered=True)
        assert run(self.root, "init").code == 0
        (self.root / ".crossbox" / "config.yaml").write_text(config)

    def run(self, *args, machine=None, slot=None):
        with env(CROSSBOX_SLOT=slot):
            return run(self.root, *args, machine=machine)

    def js(self, *args, **kw):
        r = self.run(*args, "--json", **kw)
        return r.code, json.loads(r.out)

    def path(self, tid=TID, tier="queue"):
        return self.root / ".crossbox" / tier / f"{tid}.yaml"

    def task(self, tid=TID):
        return yaml.safe_load(self.path(tid).read_text())

    def write_task(self, steps, tid=TID, **top):
        data = {"id": tid, "title": top.pop("title", "T"), "priority": top.pop("priority", "normal"),
                "domain": top.pop("domain", "api"), "created": "2026-09-24", "created_by": "laptop"}
        data.update(top)
        data["steps"] = [{"id": letters(i), "title": f"step {i}", "target": "any",
                          "status": "pending", **s} for i, s in enumerate(steps)]
        self.path(tid).write_text(yaml.safe_dump(data, sort_keys=False))

    def snapshot(self):
        base = self.root / ".crossbox"
        return {str(p.relative_to(base)): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}

    def claims(self):
        p = self.root / ".crossbox" / "claims.yaml"
        return (yaml.safe_load(p.read_text()) or {}).get("claims", []) if p.exists() else []

    def ready(self, bucket="ready", **kw):
        code, j = self.js("ready", **kw)
        return code, [r["ref"] for r in j[bucket]], j

    def registry(self, tokens, **top):
        reg = {"schema": 1, "tokens": tokens, **top}
        (self.root / ".crossbox" / "needs-registry.yaml").write_text(yaml.safe_dump(reg))


@pytest.fixture(scope="session")
def mk(tmp_path_factory):
    return lambda config=CONFIG: Q(tmp_path_factory, config)


def chain(n):
    return [{}] + [{"depends_on": [letters(i - 1)]} for i in range(1, n)]


@st.composite
def dags(draw, max_n=6):
    n = draw(st.integers(1, max_n))
    deps = [sorted(draw(st.sets(st.integers(0, i - 1), max_size=3))) if i else [] for i in range(n)]
    return n, deps


def dag_steps(deps, closed=(), skipped=()):
    out = []
    for i, d in enumerate(deps):
        st_ = "skipped" if i in skipped else "done" if i in closed else "pending"
        out.append({"status": st_, "depends_on": [letters(j) for j in d]})
    return out


# ------------------------------------------------------------------ readiness

@st.composite
def ready_cases(draw):
    n, deps = draw(dags())
    closed = draw(st.sets(st.integers(0, n - 1)))
    skipped = draw(st.sets(st.sampled_from(sorted(closed)))) if closed else set()
    claimed = draw(st.sets(st.integers(0, n - 1)))
    return n, deps, closed, skipped, claimed


@PROP
@given(ready_cases())
@example((3, [[], [0], [1]], set(), set(), set())).via("doc: a chain waits on its first step")
@example((3, [[], [0], [0, 1]], {0, 1}, {1}, set())).via("doc: skipped is terminal for dependents")
@example((2, [[], []], set(), set(), {0, 1})).via("doc: a claimed step is CLAIMED, not READY")
@example((3, [[], [], [0, 1]], {0, 1}, set(), {2})).via("doc: claimed beats ready")
@example((2, [[], [0]], set(), set(), {1})).via("doc: an active claim holds a step even while it waits")
def test_ready_buckets_match_the_documented_rules(mk, case):
    n, deps, closed, skipped, claimed = case
    q = mk()
    q.write_task(dag_steps(deps, closed, skipped))
    open_ = set(range(n)) - closed
    claimed = claimed & open_
    for c in sorted(claimed):
        assert q.run("claim", ref(c)).code == 0
    met = {i for i in open_ if all(d in closed for d in deps[i])}
    code, ready, j = q.ready()
    assert code == 0
    assert sorted(ready) == sorted(ref(i) for i in met - claimed)
    assert sorted(x["ref"] for x in j["claimed"]) == sorted(ref(i) for i in claimed)
    assert sorted(x["ref"] for x in j["waiting"]) == sorted(
        ref(i) for i in open_ - met - claimed)
    assert j["open_here"] == len(open_)
    assert j["counts"]["ready"] == len(ready)
    assert j["counts_are_floor"] is False


@PROP
@given(dags(), st.data())
@example((3, [[], [0], [1]]), None)
def test_done_succeeds_exactly_when_dependencies_are_closed(mk, dag, data):
    n, deps = dag
    if data is None:
        order = [2, 1, 0, 2, 1, 0, 2, 1, 0]
        skip_it = set()
    else:
        order = data.draw(st.permutations(range(n))) * n
        skip_it = data.draw(st.sets(st.integers(0, n - 1)))
    q = mk()
    q.write_task(dag_steps(deps))
    closed = set()
    for i in order:
        if i in closed:
            continue
        can = all(d in closed for d in deps[i])
        if i in skip_it:
            r = q.run("skip", ref(i), "--reason", "not needed")
            assert r.code == 0, r
            closed.add(i)
        else:
            r = q.run("done", ref(i))
            assert r.code == (0 if can else 1), r
            if can:
                closed.add(i)
        _, ready, j = q.ready()
        want = {ref(k) for k in range(n)
                if k not in closed and all(d in closed for d in deps[k])}
        assert set(ready) == want
        assert j["open_here"] == n - len(closed)
    assert closed == set(range(n))


@pytest.mark.parametrize("status,bucket", [
    ("pending", "ready"), ("blocked", "ready"), ("in_progress", "ready"),
    ("weird", "malformed")])
def test_each_documented_status_is_open_or_malformed(mk, status, bucket):
    q = mk()
    q.write_task([{"status": status}])
    _, got, j = q.ready(bucket)
    assert got == [ref(0)]
    assert j["counts_are_floor"] is (bucket == "malformed")


@pytest.mark.parametrize("status", ["done", "skipped"])
def test_terminal_statuses_are_in_no_bucket(mk, status):
    q = mk()
    q.write_task([{"status": status}])
    code, j = q.js("ready")
    assert code == 0 and j["open_here"] == 0
    assert all(j[b] == [] for b in ("ready", "gated", "claimed", "waiting", "malformed"))


@pytest.mark.parametrize("bad", ["a", [1], [["a"]]])
def test_depends_on_that_is_not_a_list_of_strings_is_malformed(mk, bad):
    q = mk()
    q.write_task([{}, {"depends_on": bad}])
    code, j = q.js("ready")
    assert [x["ref"] for x in j["malformed"]] == [ref(1)]
    assert ref(1) not in [x["ref"] for x in j["ready"]]
    assert j["counts_are_floor"] is True


def test_dangling_dependency_is_malformed_and_done_refuses(mk):
    q = mk()
    q.write_task([{}, {"depends_on": ["TASK-API-099.a"]}])
    _, ready, j = q.ready()
    assert ready == [ref(0)]
    assert [x["ref"] for x in j["malformed"]] == [ref(1)]
    assert j["waiting"] == []
    assert q.run("done", ref(1)).code != 0


@pytest.mark.parametrize("other,expect_met", [
    ("archived", True), ("all_terminal", True), ("one_open", False), ("no_steps", False)])
def test_task_level_dependency_rules(mk, other, expect_met):
    q = mk()
    other_id = "TASK-API-002"
    if other == "no_steps":
        q.write_task([], tid=other_id)
    else:
        sts = {"archived": ["done"], "all_terminal": ["done", "skipped"],
               "one_open": ["done", "pending"]}[other]
        q.write_task([{"status": s} for s in sts], tid=other_id)
    q.write_task([{"depends_on": [other_id]}])
    if other == "archived":
        assert q.run("archive").code == 0
        assert q.path(other_id, "archive").exists()
    ready = q.ready()[1]
    assert (ref(0) in ready) is expect_met


def test_step_dependency_on_an_archived_task_is_met(mk):
    q = mk()
    q.write_task([{"status": "done"}], tid="TASK-API-002")
    q.write_task([{"depends_on": ["TASK-API-002.a"]}])
    assert q.run("archive").code == 0
    assert q.ready()[1] == [ref(0)]


# -------------------------------------------------------------- targets, gates

TARGETS = ["any", "docker", "gpu", "laptop", "workstation"]
PROVIDES = {"laptop": {"docker"}, "workstation": {"gpu", "docker"}}


@PROP
@given(st.sampled_from(TARGETS), st.sampled_from(sorted(PROVIDES)))
@example("gpu", "laptop").via("doc: a capability the machine lacks is for other machines")
@example("gpu", "workstation").via("doc: a capability the machine provides is workable")
@example("workstation", "laptop").via("doc: a machine name is a target")
@example("laptop", "laptop").via("doc: a machine name is a target")
@example("any", "laptop").via("doc: any means anywhere")
@example("docker", "workstation").via("doc: both machines provide docker")
def test_target_routes_by_capability_or_machine_name(mk, target, machine):
    q = mk()
    q.write_task([{"target": target}])
    workable = target == "any" or target in PROVIDES[machine] or target == machine
    code, ready, j = q.ready(machine=machine)
    assert code == 0
    assert ready == ([ref(0)] if workable else [])
    assert j["elsewhere"] == (0 if workable else 1)
    assert j["open_here"] == (1 if workable else 0)


def test_unknown_target_is_malformed_never_widened_and_writers_refuse_it(mk):
    q = mk()
    q.write_task([{"target": "tpu"}, {}])
    _, ready, j = q.ready()
    assert ready == [ref(1)]
    assert [x["ref"] for x in j["malformed"]] == [ref(0)]
    assert j["elsewhere"] == 0 and j["counts_are_floor"] is True
    before = q.snapshot()
    assert q.run("add", TID, "x", "-t", "tpu").code == 1
    assert q.snapshot() == before


def test_target_alias_is_read_but_never_written(mk):
    q = mk(CONFIG + "target_aliases: {either: any}\n")
    q.write_task([{"target": "either"}])
    assert q.ready()[1] == [ref(0)]
    assert q.run("add", TID, "x", "-t", "either").code == 1


@PROP
@given(st.integers(-400, 400))
@example(-1).via("doc: the UTC day has come, day before")
@example(0).via("doc: the UTC day has come, that day")
@example(1).via("doc: the UTC day has not come, day after")
def test_date_reached_gate_opens_on_that_utc_day(mk, offset):
    import datetime as dt
    q = mk()
    today = dt.datetime.now(dt.timezone.utc).date()
    day = today + dt.timedelta(days=offset)
    q.write_task([{"needs": [f"date-reached:{day.isoformat()}"]}])
    code, ready, j = q.ready()
    if dt.datetime.now(dt.timezone.utc).date() != today:
        return
    assert code == 0
    if offset <= 0:
        assert ready == [ref(0)]
    else:
        assert ready == [] and [x["ref"] for x in j["gated"]] == [ref(0)]


@PROP
@given(st.integers(0, 255))
@example(0).via("doc: exit 0 is open")
@example(1).via("doc: exit 1 is closed")
@example(2).via("doc: anything else is not evaluable")
@example(255).via("doc: anything else is not evaluable")
def test_probe_exit_status_decides_the_gate(mk, status):
    q = mk()
    q.registry({"g": {"kind": "gate", "probe": f"exit {status}"}})
    q.write_task([{"needs": ["g"]}])
    code, ready, j = q.ready()
    if status == 0:
        assert (code, ready) == (0, [ref(0)])
    elif status == 1:
        assert (code, ready) == (0, [])
        assert [x["ref"] for x in j["gated"]] == [ref(0)]
        assert j["gates_not_evaluable"] == 0
    else:
        assert (code, ready) == (2, [])
        assert [x["ref"] for x in j["gated"]] == [ref(0)]
        assert j["gates_not_evaluable"] == 1 and j["counts_are_floor"] is True


def test_probe_runs_with_the_repository_root_as_working_directory(mk):
    q = mk()
    q.registry({"g": {"kind": "gate", "probe": "test -f .crossbox/config.yaml"}})
    q.write_task([{"needs": ["g"]}])
    assert q.ready()[1] == [ref(0)]


def test_no_probes_never_runs_the_probe_and_is_not_evaluable(mk):
    q = mk()
    marker = q.root / "ran"
    q.registry({"g": {"kind": "gate", "probe": f"touch {marker}"}})
    q.write_task([{"needs": ["g"]}])
    assert q.ready()[0] == 0 and marker.exists(), "without the flag the probe runs"
    marker.unlink()
    r = q.run("ready", "--no-probes", "--json")
    assert r.code == 2
    assert json.loads(r.out)["ready"] == []
    assert not marker.exists()


@pytest.mark.parametrize("kind", ["capability", "annotation", "estimate"])
def test_only_gate_tokens_affect_readiness(mk, kind):
    q = mk()
    q.registry({"t": {"kind": kind}})
    q.write_task([{"needs": ["t"]}])
    assert q.ready()[:2] == (0, [ref(0)])


def test_gate_without_probe_stays_closed_and_operator_flag_opens_only_operator_gates(mk):
    q = mk()
    q.registry({"manual": {"kind": "gate"},
                "op": {"kind": "gate", "clearable": "operator"},
                "ext": {"kind": "gate", "clearable": "external"}})
    q.write_task([{"needs": ["op"]}, {"needs": ["manual"]}, {"needs": ["ext"]}])
    assert q.ready()[1] == []
    r = q.run("ready", "--operator", "--json")
    j = json.loads(r.out)
    assert [x["ref"] for x in j["ready"]] == [ref(0)]
    assert sorted(x["ref"] for x in j["gated"]) == [ref(1), ref(2)]
    assert r.code == 0


BAD_REGISTRIES = [
    {"schema": 1, "tokens": {"t": {"kind": "other"}}},
    {"schema": 1, "tokens": {"t": {}}},
    {"schema": 1, "tokens": {"t": "gate"}},
    {"schema": 1, "tokens": {"t": {"kind": "gate", "probe": ""}}},
    {"schema": 1, "tokens": {"t": {"kind": "gate", "probe": 5}}},
    {"schema": 1, "tokens": {"t": {"kind": "capability", "probe": "true"}}},
    {"schema": 1, "tokens": {"t": {"kind": "gate", "clearable": "nobody"}}},
    {"schema": 1, "tokens": {"t": {"kind": "estimate", "clearable": "operator"}}},
    {"schema": 1, "tokens": {"t": {"kind": "gate", "desc": 5}}},
    {"schema": 1, "tokens": {"t": {"kind": "gate", "colour": "red"}}},
    {"schema": 1, "tokens": {"t": {"kind": "gate"}}, "aliases": {"x": "missing"}},
]


@pytest.mark.parametrize("reg", BAD_REGISTRIES)
def test_a_malformed_registry_makes_ready_not_evaluable(mk, reg):
    q = mk()
    (q.root / ".crossbox" / "needs-registry.yaml").write_text(yaml.safe_dump(reg))
    q.write_task([{}])
    r = q.run("ready", "--json")
    assert r.code == 2


def test_with_a_registry_an_unknown_token_is_refused_by_writers_and_malformed_on_read(mk):
    q = mk()
    q.registry({"known": {"kind": "gate", "probe": "exit 0"}})
    q.write_task([{}])
    before = q.snapshot()
    assert q.run("add", TID, "x", "--needs", "nosuchtoken").code != 0
    assert q.snapshot() == before
    q.write_task([{"needs": ["nosuchtoken"]}])
    _, _, j = q.ready()
    assert [x["ref"] for x in j["malformed"]] == [ref(0)]


def test_without_a_registry_only_date_reached_can_be_evaluated(mk):
    q = mk()
    q.write_task([{"needs": ["staging-up"]}])
    code, ready, j = q.ready()
    assert code == 2 and ready == []
    assert [x["ref"] for x in j["gated"]] == [ref(0)]


def test_done_is_not_blocked_by_a_closed_gate(mk):
    q = mk()
    q.registry({"g": {"kind": "gate", "probe": "exit 1"}})
    q.write_task([{"needs": ["g"]}])
    assert q.run("done", ref(0)).code == 0
    assert q.task()["steps"][0]["status"] == "done"


# ------------------------------------------------------------------- the plan

@st.composite
def plan_cases(draw):
    n, deps = draw(dags(7))
    return n, deps, draw(st.integers(0, n))


@PROP
@given(plan_cases())
@example((4, [[], [0], [0], [1, 2]], 0)).via("doc: waves of a diamond")
@example((4, [[], [0], [0], [1, 2]], 1)).via("doc: a closed dependency adds no edge")
@example((3, [[], [0], [1]], 3)).via("doc: nothing open")
@example((3, [[], [], []], 0)).via("doc: independent steps share wave 0")
def test_plan_matches_an_independent_graph_model(mk, case):
    n, deps, k = case
    q = mk()
    q.write_task(dag_steps(deps, closed=set(range(k))))
    opn = list(range(k, n))
    up = {i: [d for d in deps[i] if d >= k] for i in opn}
    down = {i: [j for j in opn if i in up[j]] for i in opn}
    wave = {}
    for i in opn:
        wave[i] = 1 + max((wave[d] for d in up[i]), default=-1)

    def below(i):
        seen, todo = set(), list(down[i])
        while todo:
            x = todo.pop()
            if x not in seen:
                seen.add(x)
                todo.extend(down[x])
        return seen

    code, p = q.js("plan")
    assert code == 0
    assert p["open_steps"] == len(opn) and p["nodes"] == len(opn)
    assert p["cycles"] == [] and p["malformed"] == []
    first = [i for i in opn if not up[i]]
    want = sorted(first, key=lambda i: (-len(below(i)), ref(i)))
    assert [x["ref"] for x in p["next"]] == [ref(i) for i in want]
    for row in p["next"]:
        i = int(sum((ord(c) - 96) * 26 ** e for e, c in enumerate(reversed(row["ref"].split(".")[1])))) - 1
        assert row["unblocks"] == len(below(i))
        assert row["dependents"] == len(down[i])
    got_wave = {x["ref"]: w["wave"] for w in p["waves"] for x in w["steps"]}
    assert got_wave == {ref(i): wave[i] for i in opn}
    depth = max(wave.values()) + 1 if opn else 0
    assert len(p["waves"]) == depth and len(p["critical_path"]) == depth
    path = [x["ref"] for x in p["critical_path"]]
    for a, b in zip(path, path[1:]):
        bi = [i for i in opn if ref(i) == b][0]
        assert any(ref(d) == a for d in up[bi])
    pos = {r: x for x, r in enumerate(p["order"])}
    assert set(pos) == {ref(i) for i in opn} and len(p["order"]) == len(opn)
    for i in opn:
        for d in up[i]:
            assert pos[ref(d)] < pos[ref(i)]
    waves_in_order = [got_wave[r] for r in p["order"]]
    assert waves_in_order == sorted(waves_in_order)


@PROP
@given(st.integers(1, 5))
@example(1).via("doc: a step that waits on its own task is a cycle of one")
@example(2).via("doc: a cycle of two")
def test_plan_names_a_cycle_once_and_keeps_its_steps_out_of_every_ranking(mk, m):
    q = mk()
    steps = [{"depends_on": [letters((i - 1) % m)]} for i in range(m)]
    steps.append({"depends_on": [letters(0)]})
    q.write_task(steps)
    code, p = q.js("plan")
    assert code == 1
    assert p["cycles"] == [[ref(i) for i in range(m)]]
    assert p["blocked_by_cycle"] == [ref(m)]
    placed = {x["ref"] for x in p["next"]} | set(p["order"]) | {x["ref"] for x in p["critical_path"]}
    placed |= {x["ref"] for w in p["waves"] for x in w["steps"]}
    assert placed == set()
    assert p["counts_are_floor"] is True


@PROP
@given(st.integers(2, 5))
@example(2).via("doc: edit-step refuses a cycle through the edited step")
def test_edit_step_refuses_cycles_and_self_dependency_and_writes_nothing(mk, m):
    q = mk()
    q.write_task(chain(m))
    before = q.snapshot()
    assert q.run("edit-step", ref(0), "--depends", letters(m - 1)).code == 1
    assert q.snapshot() == before
    assert q.run("edit-step", ref(1), "--depends", letters(1)).code == 1
    assert q.snapshot() == before


PRIORITIES = ["critical", "high", "normal", "low", "weird"]


@PROP
@given(st.permutations(PRIORITIES + ["medium"]))
@example(["low", "weird", "normal", "critical", "medium", "high"]).via("doc: priority order")
def test_plan_ranks_equal_unblocks_by_priority_then_ref(mk, assigned):
    q = mk()
    rank = {"critical": 0, "high": 1, "normal": 2, "medium": 2, "low": 3, "weird": 4}
    for k, pr in enumerate(assigned):
        q.write_task([{}], tid=f"TASK-API-{k + 1:03d}", priority=pr)
    code, p = q.js("plan")
    assert code == 0
    refs = [f"TASK-API-{k + 1:03d}.a" for k in range(len(assigned))]
    prio = {refs[k]: assigned[k] for k in range(len(assigned))}
    want = sorted(refs, key=lambda r: (rank[prio[r]], r))
    assert [x["ref"] for x in p["next"]] == want


def test_plan_on_a_long_chain_is_complete(mk):
    q = mk()
    q.write_task(chain(300))
    code, p = q.js("plan")
    assert code == 0 and len(p["critical_path"]) == 300 and len(p["waves"]) == 300
    r = q.run("why", ref(299))
    assert r.code == 0
    assert r.out.strip().splitlines()[-1].startswith(f"root: {ref(0)}")


# -------------------------------------------------------- missing queue and why

READERS = [("ready",), ("plan",), ("progress",), ("near-done",), ("graph",), ("status",),
           ("claims",), ("doctor", "check")]


@pytest.mark.parametrize("verb", READERS, ids=lambda v: "-".join(v))
def test_a_missing_queue_is_not_evaluable_for_every_reader(tmp_path, verb):
    root = tmp_path / "empty"
    root.mkdir()
    assert run(root, *verb).code == 2


@pytest.mark.parametrize("verb", READERS, ids=lambda v: "-".join(v))
def test_a_root_that_is_not_a_directory_is_not_evaluable(tmp_path, verb):
    f = tmp_path / "file"
    f.write_text("x")
    assert run(f, *verb).code == 2


def test_json_readers_print_not_evaluable_for_a_missing_queue(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    for verb in ("ready", "plan", "progress"):
        r = run(root, verb, "--json")
        assert r.code == 2 and "not_evaluable" in json.loads(r.out)


WHY_CASES = {"ready": "READY", "waiting": "WAITING", "claimed": "CLAIMED", "done": "DONE",
             "skipped": "SKIPPED", "elsewhere": "ELSEWHERE", "gated": "GATED",
             "malformed": "MALFORMED"}


@pytest.fixture
def why_queue(mk):
    q = mk()
    q.registry({"g": {"kind": "gate"}})
    q.write_task([
        {}, {"depends_on": ["a"]}, {}, {"status": "done"}, {"status": "skipped"},
        {"target": "gpu"}, {"needs": ["g"]}, {"status": "weird"}])
    assert q.run("claim", ref(2)).code == 0
    return q, dict(zip(WHY_CASES, range(8)))


@pytest.mark.parametrize("name", list(WHY_CASES))
def test_why_reports_the_code_that_ready_uses(why_queue, name):
    q, idx = why_queue
    code, w = q.js("why", ref(idx[name]))
    assert w["code"] == WHY_CASES[name]
    assert code in (0, 2)


def test_why_for_a_ref_that_names_nothing_exits_1(why_queue):
    q, _ = why_queue
    assert q.run("why", f"{TID}.zz").code == 1


# ---------------------------------------------------------------------- claims

SLOTS = [None, "wt1", "wt2"]


@PROP
@given(st.lists(st.tuples(st.sampled_from(["laptop", "workstation"]), st.sampled_from(SLOTS)),
                min_size=1, max_size=6))
@example([("laptop", None), ("laptop", None), ("laptop", "wt1"), ("workstation", "wt1")]).via(
    "doc: ids are CLM-machine-NNN, CLM-machine-slot-NNN, one counter per pair")
def test_claim_ids_count_up_per_machine_and_slot(mk, owners):
    q = mk()
    q.write_task([{} for _ in owners])
    seen = {}
    for i, (m, s) in enumerate(owners):
        r = q.run("claim", ref(i), machine=m, slot=s)
        assert r.code == 0, r
        seen[(m, s)] = seen.get((m, s), 0) + 1
        want = f"CLM-{m}-{seen[(m, s)]:03d}" if s is None else f"CLM-{m}-{s}-{seen[(m, s)]:03d}"
        assert want in r.out
    rows = q.claims()
    assert len({c["id"] for c in rows}) == len(owners)
    assert [(c["machine"], c["slot"]) for c in rows] == [(m, s or "main") for m, s in owners]
    assert all(c["status"] == "active" for c in rows)


@PROP
@given(st.lists(st.tuples(st.sampled_from(["laptop", "workstation"]), st.sampled_from(SLOTS)),
                min_size=1, max_size=6))
@example([("laptop", "wt1"), ("laptop", None), ("workstation", "wt1")]).via(
    "doc: release --all-mine is this machine and this slot only")
def test_release_all_mine_releases_exactly_my_machine_and_slot(mk, owners):
    q = mk()
    q.write_task([{} for _ in owners])
    for i, (m, s) in enumerate(owners):
        assert q.run("claim", ref(i), machine=m, slot=s).code == 0
    q.run("release", "--all-mine", machine="laptop", slot="wt1")
    for c, (m, s) in zip(q.claims(), owners):
        mine = (m, s) == ("laptop", "wt1")
        assert c["status"] == ("released" if mine else "active")
        assert ("released" in c) == mine


@PROP
@given(st.integers(1, 4))
def test_release_frees_the_step_and_a_new_claim_gets_a_new_id(mk, cycles):
    q = mk()
    q.write_task([{}])
    ids = []
    for _ in range(cycles):
        assert q.ready()[1] == [ref(0)]
        out = q.run("claim", ref(0)).out
        cid = re.search(r"CLM-\S+", out).group(0)
        assert cid not in ids
        ids.append(cid)
        assert q.ready("claimed")[1] == [ref(0)] and q.ready()[1] == []
        assert q.run("release", cid).code == 0
    assert [c["id"] for c in q.claims()] == ids
    assert {c["status"] for c in q.claims()} == {"released"}
    assert q.ready()[1] == [ref(0)]


def test_a_subject_that_only_mentions_a_step_binds_nothing(mk):
    q = mk()
    q.write_task([{}])
    assert q.run("claim", f"work on {ref(0)} soon").code == 0
    assert not q.claims()[0].get("step_ref")
    assert q.ready()[1] == [ref(0)]


def test_repeated_step_flag_makes_one_claim_per_step_each_releasable(mk):
    q = mk()
    q.write_task([{}, {}, {}])
    assert q.run("claim", "batch", "--step", ref(0), "--step", ref(2)).code == 0
    rows = q.claims()
    assert sorted(c["step_ref"] for c in rows) == [ref(0), ref(2)]
    assert q.run("release", rows[0]["id"]).code == 0
    assert sorted(c["status"] for c in q.claims()) == ["active", "released"]
    assert q.ready("claimed")[1] == [q.claims()[1]["step_ref"]]


def test_claiming_a_step_held_elsewhere_warns_naming_the_holder_and_keeps_both(mk):
    q = mk()
    q.write_task([{}])
    q.run("claim", ref(0), slot="wt1")
    r = q.run("claim", ref(0), slot="wt2")
    assert r.code == 0 and "CLM-laptop-wt1-001" in r.out + r.err
    assert len(q.claims()) == 2


def test_done_closes_a_step_another_session_claims_and_names_the_claim(mk):
    q = mk()
    q.write_task([{}])
    q.run("claim", ref(0), machine="workstation", slot="wt9")
    r = q.run("done", ref(0))
    assert r.code == 0 and "CLM-workstation-wt9-001" in r.out + r.err
    assert q.task()["steps"][0]["status"] == "done"


def test_a_claim_on_a_removed_step_is_orphaned_and_ready_exits_1(mk):
    q = mk()
    q.write_task([{}, {}])
    q.run("claim", ref(1), slot="wt1")
    t = q.task()
    t["steps"].pop()
    q.path().write_text(yaml.safe_dump(t, sort_keys=False))
    code, _, j = q.ready()
    assert code == 1 and j["counts_are_floor"] is True
    assert len(j["orphaned_claims"]) == 1


@PROP
@given(st.integers(1, 6), st.integers(0, 9))
@example(3, 3).via("doc: stale_days 3, age 3 is not stale")
@example(3, 4).via("doc: stale_days 3, age 4 is stale")
@example(1, 1).via("doc: stale_days 1, age 1 is not stale")
@example(1, 2).via("doc: stale_days 1, age 2 is stale")
def test_a_claim_is_stale_only_when_older_than_stale_days_and_never_released(mk, days, age):
    import datetime as dt
    q = mk(CONFIG + f"stale_days: {days}\n")
    q.write_task([{}])
    q.run("claim", ref(0))
    p = q.root / ".crossbox" / "claims.yaml"
    data = yaml.safe_load(p.read_text())
    data["claims"][0]["created"] = (dt.date.today() - dt.timedelta(days=age)).isoformat()
    p.write_text(yaml.safe_dump(data))
    code, j = q.js("claims")
    assert code == 0
    assert (j["stale"] != []) is (age > days)
    assert q.claims()[0]["status"] == "active"
    assert q.ready()[1] == []


def test_default_stale_days_is_three(mk):
    import datetime as dt
    q = mk()
    q.write_task([{}, {}])
    q.run("claim", ref(0))
    q.run("claim", ref(1))
    p = q.root / ".crossbox" / "claims.yaml"
    data = yaml.safe_load(p.read_text())
    for c, age in zip(data["claims"], (3, 4)):
        c["created"] = (dt.date.today() - dt.timedelta(days=age)).isoformat()
    p.write_text(yaml.safe_dump(data))
    assert q.js("claims")[1]["stale"] == [data["claims"][1]["id"]]


WORDS = ["parser", "grammar", "lexer", "tokens", "schema", "router", "widget", "binder"]


@PROP
@given(st.sets(st.sampled_from(WORDS), min_size=1, max_size=4),
       st.sets(st.sampled_from(WORDS), min_size=1, max_size=4))
@example({"parser", "grammar"}, {"parser", "lexer"}).via("doc: one shared word is no overlap")
@example({"parser", "grammar"}, {"grammar", "parser", "lexer"}).via("doc: two shared words overlap")
def test_claim_check_flags_two_or_more_shared_words(mk, held, planned):
    q = mk()
    q.write_task([{}])
    assert q.run("claim", " ".join(sorted(held)), slot="wt1").code == 0
    r = q.run("claim-check", " ".join(sorted(planned)), slot="wt2")
    assert r.code == (1 if len(held & planned) >= 2 else 0), r
    assert q.run("claim-check", " ".join(sorted(planned)), slot="wt1").code == 0


def test_claim_check_flags_the_same_source_file(mk):
    q = mk()
    q.write_task([{}])
    assert q.run("claim", "alpha beta", "--source", "src/a.py", slot="wt1").code == 0
    assert q.run("claim-check", "gamma delta", "--source", "src/a.py", slot="wt2").code == 1
    assert q.run("claim-check", "gamma delta", "--source", "src/b.py", slot="wt2").code == 0


# --------------------------------------------------------------- merge driver

def claim_row(machine, slot, n, step, created, status):
    row = {"id": f"CLM-{machine}-{slot}-{n:03d}", "machine": machine, "slot": slot,
           "subject": ref(step), "source": "", "intent": "", "created": created,
           "status": status, "step_ref": ref(step)}
    if status == "released":
        row["released"] = "2026-10-09"
    return row


rows_st = st.lists(st.builds(
    claim_row, st.sampled_from(["laptop", "workstation"]), st.sampled_from(["main", "wt1"]),
    st.integers(1, 2), st.integers(0, 2), st.sampled_from(["2026-10-01", "2026-10-02"]),
    st.sampled_from(["active", "released"])), max_size=5)


def merge(tmp, ours, theirs, name="m"):
    d = tmp.mktemp(name, numbered=True)
    paths = []
    for nm, rows in (("base", []), ("ours", ours), ("theirs", theirs)):
        p = d / nm
        p.write_text(yaml.safe_dump({"claims": rows}))
        paths.append(str(p))
    rc = run(d, "merge-driver", *paths).code
    return rc, yaml.safe_load((d / "ours").read_text())["claims"], d


def ident(row):
    return (row.get("renumbered_from") or row["id"], row["machine"], row["slot"],
            row["step_ref"], row["created"], row["subject"])


@PROP
@given(rows_st, rows_st)
@example([claim_row("laptop", "main", 1, 0, "2026-10-01", "active")],
         [claim_row("laptop", "main", 1, 0, "2026-10-01", "released")]).via(
    "doc: released in either copy means released")
@example([claim_row("laptop", "main", 1, 0, "2026-10-01", "active")],
         [claim_row("laptop", "main", 1, 1, "2026-10-01", "active")]).via(
    "doc: two different claims minted with one id are both kept")
def test_merge_driver_unions_by_identity_and_release_is_monotonic(tmp_path_factory, a, b):
    rc, ab, _ = merge(tmp_path_factory, a, b)
    rc2, ba, _ = merge(tmp_path_factory, b, a)
    assert rc == 0 and rc2 == 0
    want = {}
    for r in a + b:
        k = ident(r)
        want[k] = "released" if "released" in (want.get(k), r["status"]) else "active"
    assert {ident(r): r["status"] for r in ab} == want
    assert len(ab) == len(want)
    assert len({r["id"] for r in ab}) == len(ab)
    key = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)  # noqa: E731
    assert key(ab) == key(ba)
    rc3, again, _ = merge(tmp_path_factory, ab, b)
    assert rc3 == 0 and key(again) == key(ab)


def test_merge_driver_exits_1_on_unparseable_input_and_leaves_ours(tmp_path):
    for nm, txt in (("base", ""), ("ours", "claims: []\n"), ("theirs", "claims: [: :\n  - {")):
        (tmp_path / nm).write_text(txt)
    assert run(tmp_path, "merge-driver", *(str(tmp_path / n) for n in ("base", "ours", "theirs"))).code == 1
    assert (tmp_path / "ours").read_text() == "claims: []\n"


# ------------------------------------------------------------------- rate gate

def gate_env(q, cap, **extra):
    kw = {"CROSSBOX_RATE_DISABLE": None, "CROSSBOX_RATE_CAP": str(cap),
          "CROSSBOX_RATE_STATE": str(q.root.with_name(q.root.name + "-rate.json"))}
    kw.update(extra)
    return env(**kw)


@PROP
@given(st.integers(1, 5))
@example(1).via("doc: the call past the cap is refused")
def test_rate_gate_refuses_past_the_cap_without_using_budget_and_spares_reads(mk, cap):
    q = mk()
    q.write_task([])
    with gate_env(q, cap):
        for i in range(cap):
            assert q.run("add", TID, f"s{i}").code == 0
        before = q.snapshot()
        for _ in range(3):
            assert q.run("add", TID, "over").code == 1
            assert q.snapshot() == before
        for _ in range(cap + 3):
            assert q.run("ready").code == 0
            assert q.run("claims").code == 0
            assert q.run("plan").code == 0
        assert q.run("add", TID, "still over").code == 1


def test_default_cap_is_thirty_calls(mk):
    q = mk()
    q.write_task([])
    with env(CROSSBOX_RATE_DISABLE=None, CROSSBOX_RATE_CAP=None,
             CROSSBOX_RATE_STATE=str(q.root.with_name("rate-default.json"))):
        codes = [q.run("add", TID, f"s{i}").code for i in range(31)]
    assert codes == [0] * 30 + [1]


@pytest.mark.parametrize("cap", ["0", "-1"])
def test_cap_of_zero_or_less_turns_the_gate_off(mk, cap):
    q = mk()
    q.write_task([])
    with gate_env(q, cap):
        assert [q.run("add", TID, f"s{i}").code for i in range(35)] == [0] * 35


@pytest.mark.parametrize("var,value", [
    ("CROSSBOX_RATE_CAP", "lots"), ("CROSSBOX_RATE_CAP", "1.5"),
    ("CROSSBOX_RATE_WINDOW", "0"), ("CROSSBOX_RATE_WINDOW", "-5"),
    ("CROSSBOX_RATE_WINDOW", "soon")])
def test_an_invalid_rate_variable_is_not_evaluable_and_names_itself(mk, var, value):
    q = mk()
    q.write_task([])
    with gate_env(q, 5, **{var: value}):
        r = q.run("add", TID, "x")
    assert r.code == 2 and var in r.out + r.err
    assert q.task()["steps"] == []


def test_the_window_reopens_after_it_expires(mk):
    q = mk()
    q.write_task([])
    with gate_env(q, 1, CROSSBOX_RATE_WINDOW="3"):       # long enough that a slow runner still lands inside it
        assert q.run("add", TID, "a").code == 0
        assert q.run("add", TID, "b").code == 1
        time.sleep(3.5)
        assert q.run("add", TID, "c").code == 0


def test_the_gate_can_be_set_in_the_config(mk):
    q = mk(CONFIG + "rate_gate: {cap: 2, window: 600}\n")
    q.write_task([])
    with env(CROSSBOX_RATE_DISABLE=None, CROSSBOX_RATE_CAP=None,
             CROSSBOX_RATE_STATE=str(q.root.with_name("rate-cfg.json"))):
        assert [q.run("add", TID, f"s{i}").code for i in range(3)] == [0, 0, 1]


def test_closing_work_this_slot_claimed_is_exempt_and_everything_else_is_metered(mk):
    q = mk()
    q.write_task([{}, {}, {}, {}, {}])
    assert q.run("claim", ref(0)).code == 0
    assert q.run("claim", ref(2), slot="wt1").code == 0
    with gate_env(q, 1):
        assert q.run("add", TID, "uses the one call").code == 0
        assert q.run("add", TID, "refused").code == 1
        assert q.run("done", ref(0)).code == 0, "own claim: exempt"
        assert q.run("done", ref(1)).code == 1, "unclaimed: metered"
        assert q.run("done", ref(2)).code == 1, "another slot's claim: metered"
        assert q.run("reopen", ref(0), "--reason", "x").code == 1, "reopen: never exempt"


def test_batch_add_and_its_short_name_are_one_verb_for_the_gate(mk):
    q = mk()
    q.write_task([])
    f = q.root / "steps.txt"
    f.write_text("one\ntwo\n")
    with gate_env(q, 2):
        assert q.run("batch-add", TID, str(f)).code == 0
        assert q.run("ba", TID, str(f)).code == 0
        assert q.run("batch-add", TID, str(f)).code == 1


# -------------------------------------------------------------------- step ids

@settings(max_examples=8, derandomize=True, database=None, deadline=None)
@given(st.integers(1, 30))
@example(26).via("doc: a to z")
@example(27).via("doc: then aa")
def test_batch_add_mints_bijective_base_26_ids_in_order(mk, n):
    q = mk()
    q.write_task([])
    f = q.root / "steps.txt"
    f.write_text("".join(f"s{i}\n" for i in range(n)))
    r = q.run("batch-add", TID, str(f))
    assert r.code == 0, r
    assert f"Added {n} of {n}" in r.out
    assert [s["id"] for s in q.task()["steps"]] == [letters(i) for i in range(n)]


@PROP
@given(st.sets(st.integers(1, 80), min_size=1, max_size=6))
@example({1, 3}).via("doc: gaps are never reused")
@example({26}).via("doc: one past z is aa")
@example({52}).via("doc: one past az is ba")
@example({702}).via("doc: one past zz is aaa")
def test_the_next_step_id_is_one_past_the_highest(mk, ords):
    q = mk()
    q.write_task([{"id": letters(o - 1)} for o in sorted(ords)])
    r = q.run("add", TID, "next")
    assert r.code == 0
    assert r.out.strip() == f"added {TID}.{letters(max(ords))}"


def test_removing_the_last_step_and_adding_again_reuses_its_letter(mk):
    q = mk()
    q.write_task([{}, {}, {}])
    assert q.run("remove-step", ref(2)).code == 0
    assert q.run("add", TID, "again").out.strip() == f"added {ref(2)}"


# ------------------------------------------------------------ writes round-trip

WORD = st.text(alphabet=st.characters(min_codepoint=33, max_codepoint=0x24F,
                                      exclude_categories=["Cc", "Cs", "Cf", "Z"]),
               min_size=1, max_size=10)
PHRASE = st.lists(WORD, min_size=1, max_size=5).map(" ".join)

HOSTILE = ["x: y", "- a", "'q'", '"d"', "#c", "a: b: c", "{k: v}", "[1, 2]", "yes", "null", "~",
           "123", "0x1f", "1e3", "&anc", "*ali", "!tag", "%dir", "@at", "`bt", "|", ">"]


@PROP
@given(PHRASE)
@example("not-a-flag").via("doc: titles are kept exactly")
def test_a_title_round_trips_through_add(mk, title):
    if title.startswith("-"):
        title = "t " + title
    q = mk()
    q.write_task([])
    r = q.run("add", TID, title)
    assert r.code == 0, r
    assert q.task()["steps"][0]["title"] == title


@pytest.mark.parametrize("title", HOSTILE)
def test_yaml_looking_titles_stay_strings_and_structure_is_intact(mk, title):
    q = mk()
    q.write_task([{}])
    r = q.run("add", TID, title, "-D", "a")
    assert r.code == 0, r
    steps = q.task()["steps"]
    assert [s["id"] for s in steps] == ["a", "b"]
    assert steps[1]["title"] == title and steps[1]["depends_on"] == ["a"]


@PROP
@given(st.lists(PHRASE, min_size=1, max_size=4))
@example(["one", "two", "three"]).via("doc: multi-line notes are kept line for line")
def test_multi_line_notes_survive_line_for_line(mk, lines):
    q = mk()
    q.write_task([{}])
    notes = "\n".join(lines)
    r = q.run("edit-step", ref(0), "--notes", notes)
    assert r.code == 0, r
    assert q.task()["steps"][0]["notes"].splitlines() == lines


def test_unknown_keys_survive_writes_after_the_known_ones(mk):
    q = mk()
    q.write_task([{"custom": {"k": [1, 2]}}, {}], bucket="later")
    q.run("add", TID, "third")
    q.run("edit-step", ref(1), "--append-notes", "hello")
    t = q.task()
    assert t["bucket"] == "later" and t["steps"][0]["custom"] == {"k": [1, 2]}
    keys = list(t)
    assert keys.index("id") < keys.index("steps") < keys.index("bucket")
    assert [s["id"] for s in t["steps"]] == ["a", "b", "c"]


def test_a_write_that_drops_a_comment_says_so_on_stderr(mk):
    q = mk()
    q.write_task([{}])
    q.path().write_text("# keep me\n" + q.path().read_text())
    r = q.run("add", TID, "x")
    assert r.code == 0 and "comment" in r.err.lower()


NOTES_SETUP = [{"notes": "old"}, {}]
REFUSED = [
    ("done unmet dependency", ["done", ref(1)], [{}, {"depends_on": ["a"]}]),
    ("done unknown step", ["done", f"{TID}.zz"], [{}]),
    ("done unknown task", ["done", "TASK-API-777.a"], [{}]),
    ("add unknown task", ["add", "TASK-API-777", "x"], [{}]),
    ("add unknown target", ["add", TID, "x", "-t", "tpu"], [{}]),
    ("add dangling dependency", ["add", TID, "x", "-D", "zz"], [{}]),
    ("done a skipped step", ["done", ref(0)], [{"status": "skipped"}]),
    ("remove a step others depend on", ["remove-step", ref(0)], [{}, {"depends_on": ["a"]}]),
    ("remove a closed step", ["remove-step", ref(0)], [{"status": "done"}]),
    ("notes over notes", ["edit-step", ref(0), "--notes", "new"], NOTES_SETUP),
    ("empty notes", ["edit-step", ref(1), "--notes", "  "], NOTES_SETUP),
    ("self dependency", ["edit-step", ref(0), "--depends", "a"], [{}]),
    ("skip without a reason", ["skip", ref(0)], [{}]),
    ("unresolved superseded-by", ["skip", ref(0), "--reason", "r", "--superseded-by", "TASK-API-777.a"], [{}]),
    ("unknown flag", ["done", ref(0), "--frobnicate"], [{}]),
]


@pytest.mark.parametrize("name,args,steps", REFUSED, ids=[r[0] for r in REFUSED])
def test_a_refused_verb_exits_nonzero_and_changes_no_file(mk, name, args, steps):
    q = mk()
    q.write_task(steps)
    before = q.snapshot()
    assert q.run(*args).code != 0
    assert q.snapshot() == before


def test_done_never_overwrites_attribution_and_a_second_done_is_a_no_op(mk):
    q = mk()
    q.write_task([{}])
    assert q.run("done", ref(0), machine="laptop").code == 0
    first = {k: q.task()["steps"][0][k] for k in ("completed_by", "completed")}
    assert first["completed_by"] == "laptop"
    before = q.snapshot()
    assert q.run("done", ref(0), machine="workstation").code == 0
    assert q.snapshot() == before


def test_done_force_records_what_was_unmet_and_skip_records_its_reason(mk):
    q = mk()
    q.write_task([{}, {"depends_on": ["a"]}, {}])
    assert q.run("done", ref(1)).code == 1
    assert q.run("done", ref(1), "--force").code == 0
    assert f"unmet: {ref(0)}" in q.task()["steps"][1]["notes"]
    assert q.run("skip", ref(2), "--reason", "gone away").code == 0
    s = q.task()["steps"][2]
    assert s["status"] == "skipped" and "gone away" in s["notes"]


def test_reopen_returns_a_step_to_pending_and_ready_with_the_reason_in_notes(mk):
    q = mk()
    q.write_task([{"status": "done", "completed_by": "laptop", "completed": "2026-09-24"}])
    assert q.ready()[1] == []
    assert q.run("reopen", ref(0), "--reason", "oops").code == 0
    s = q.task()["steps"][0]
    assert s["status"] == "pending" and "oops" in s["notes"]
    assert q.ready()[1] == [ref(0)]


@PROP
@given(st.lists(st.booleans(), min_size=1, max_size=6))
@example([True, False, True]).via("doc: N counts only steps this call closed")
@example([True, True]).via("doc: nothing written when all are done")
def test_batch_done_counts_only_what_it_closed(mk, already):
    q = mk()
    q.write_task([{"status": "done"} if a else {} for a in already])
    r = q.run("batch-done", *[ref(i) for i in range(len(already))])
    closed = already.count(False)
    assert r.code == 0
    assert f"closed {closed} of {len(already)}" in r.out
    if closed < len(already):
        assert f"{already.count(True)} already done" in r.out
    assert all(s["status"] == "done" for s in q.task()["steps"])


# -------------------------------------------------------------------- progress

STATUSES = ["pending", "blocked", "in_progress", "done", "skipped", "weird"]


@PROP
@given(st.lists(st.sampled_from(STATUSES), max_size=8))
@example([]).via("doc: a task with no steps is 0/0, never 100%")
@example(["done", "pending", "pending"]).via("doc: 1 of 3 rounds down to 33")
@example(["done", "skipped", "pending"]).via("doc: 2 of 3 rounds down to 66")
@example(["done", "skipped"]).via("doc: only a finished task reads 100")
@example(["weird", "done"]).via("doc: an unknown status is not closed")
def test_progress_counts_beside_denominators_and_rounds_down(mk, statuses):
    q = mk()
    q.write_task([{"status": s} for s in statuses])
    code, j = q.js("progress")
    assert code == 0
    g = [x for x in j["groups"] if x["kind"] == "task"][0]
    done, skipped = statuses.count("done"), statuses.count("skipped")
    assert (g["steps"], g["done"], g["skipped"], g["closed"]) == (
        len(statuses), done, skipped, done + skipped)
    if statuses:
        assert g["pct"] == (100 * (done + skipped)) // len(statuses)
        assert (g["pct"] == 100) == (done + skipped == len(statuses))
    else:
        assert g["pct"] is None


@PROP
@given(st.integers(0, 8), st.integers(0, 8), st.integers(1, 4), st.integers(0, 100))
@example(4, 3, 2, 75).via("doc: --min-pct is inclusive")
@example(4, 3, 2, 76).via("doc: just above --min-pct is not listed")
@example(4, 2, 2, 50).via("doc: --max-open is inclusive")
@example(4, 2, 1, 50).via("doc: one above --max-open is not listed")
@example(2, 2, 2, 0).via("doc: a complete task is not listed")
@example(0, 0, 2, 0).via("doc: a task with no steps is not listed")
def test_near_done_lists_tasks_inside_both_limits(mk, total, closed, max_open, min_pct):
    closed = min(closed, total)
    q = mk()
    q.write_task([{"status": "done"} if i < closed else {} for i in range(total)])
    code, j = q.js("near-done", "--max-open", str(max_open), "--min-pct", str(min_pct))
    opn = total - closed
    listed = 1 <= opn <= max_open and closed * 100 >= min_pct * total
    assert [t["id"] for t in j["tasks"]] == ([TID] if listed else [])
    assert code == 0


def test_near_done_defaults_are_two_open_and_sixty_percent(mk):
    q = mk()
    q.write_task([{"status": "done"}] * 3 + [{}, {}])
    code, j = q.js("near-done")
    assert j["criteria"] == {"max_open": 2, "min_pct": 60}
    assert [t["id"] for t in j["tasks"]] == [TID]


def test_progress_groups_by_domain_and_none(mk):
    q = mk()
    q.write_task([{"status": "done"}, {}], tid="TASK-API-001")
    q.write_task([{"status": "done"}], tid="TASK-WEB-001", domain="web")
    code, j = q.js("progress", "--by", "domain")
    got = {g["key"]: (g["closed"], g["steps"]) for g in j["groups"] if g["kind"] == "domain"}
    assert got == {"api": (1, 2), "web": (1, 1)}


# ---------------------------------------------------------------------- archive

@pytest.mark.parametrize("statuses,archived", [
    (["done", "done"], True), (["skipped"], True), (["done", "skipped"], True),
    (["done", "pending"], False), (["blocked"], False), ([], False)])
def test_archive_moves_a_task_only_when_it_has_steps_and_all_are_terminal(mk, statuses, archived):
    q = mk()
    q.write_task([{"status": s} for s in statuses])
    q.run("archive")
    assert q.path(tier="archive").exists() is archived
    assert q.path().exists() is not archived


def test_unarchive_refuses_when_both_tiers_hold_the_id(mk):
    q = mk()
    q.write_task([{"status": "done"}])
    assert q.run("archive").code == 0
    q.write_task([{}])
    before = q.snapshot()
    assert q.run("unarchive", TID).code != 0
    assert q.snapshot() == before


# ----------------------------------------------------------------------- views

def gjson(q, *args):
    r = q.run("graph", "--format", "json", *args)
    return r.code, json.loads(r.out)


def label_of(mermaid, node="n0"):
    line = [x.strip() for x in mermaid.splitlines() if x.strip().startswith(f'{node}["')][0]
    return line[len(node) + 2:line.rindex('"]:::')]


HOSTILE_TITLES = ['say "hi"', "<b>bold</b>", "a|b", "[x] {y}", "`tick`", "50% & more", "a\\b",
                  "@user #tag", "%%{init: {}}", "click n0 call f()", "line\nbreak", "tab\there"]
FORBIDDEN = set('"<>|[]{}`&%\\@#')


@pytest.mark.parametrize("title", HOSTILE_TITLES)
def test_graph_labels_are_escaped_so_no_title_can_break_out(mk, title):
    q = mk()
    q.write_task([{"title": title}])
    out = q.run("graph", "--format", "mermaid")
    assert out.code == 0
    label = label_of(out.out)
    bare = re.sub(r"#(quot|lt|gt|\d+);", "", label)
    assert not (FORBIDDEN & set(bare)), label
    assert [x for x in out.out.splitlines() if x.strip().startswith("n0")] != []
    dot = q.run("graph", "--format", "dot").out
    assert "\n" not in re.findall(r'label="(.*?)", fill', dot)[0]


@PROP
@given(st.text(alphabet=st.characters(exclude_categories=["Cs"]), max_size=40))
def test_graph_labels_never_hold_a_raw_syntax_character(mk, title):
    q = mk()
    q.write_task([{"title": title or "x"}])
    out = q.run("graph", "--format", "mermaid")
    assert out.code == 0, out
    bare = re.sub(r"#(quot|lt|gt|\d+);", "", label_of(out.out))
    assert not (FORBIDDEN & set(bare))
    assert "\n" not in label_of(out.out)


@pytest.mark.parametrize("n,cut", [(59, False), (60, False), (61, True), (200, True)])
def test_titles_are_cut_to_sixty_characters_with_an_ellipsis(mk, n, cut):
    q = mk()
    q.write_task([{"title": "x" * n}])
    title = gjson(q)[1]["nodes"][0]["title"]
    if cut:
        assert len(title) == 60 and title.endswith("…")
    else:
        assert title == "x" * n


@pytest.mark.parametrize("raw,clean", [("a\nb", "a b"), ("a\tb", "a b"), ("a\x00b", "ab"),
                                      ("a\u202eb", "ab"), ("a\x1bb", "ab")])
def test_titles_are_printed_without_control_and_bidirectional_characters(mk, raw, clean):
    q = mk()
    q.write_task([{"title": raw}])
    assert gjson(q)[1]["nodes"][0]["title"] == clean
    q.write_task([{}], title=raw)
    out = q.run("progress").out
    assert f"  {clean}\n" in out and raw not in out


@PROP
@given(st.integers(1, 8), st.integers(1, 8))
@example(5, 5).via("doc: at --max-nodes is drawn")
@example(6, 5).via("doc: over --max-nodes refuses and prints nothing")
def test_graph_refuses_over_max_nodes_instead_of_dropping_nodes(mk, n, limit):
    q = mk()
    q.write_task([{} for _ in range(n)])
    r = q.run("graph", "--format", "json", "--max-nodes", str(limit))
    if n > limit:
        assert r.code == 1 and r.out == ""
    else:
        assert r.code == 0 and len(json.loads(r.out)["nodes"]) == n


@PROP
@given(st.integers(2, 8), st.integers(1, 8))
@example(4, 3).via("doc: at --max-edges is drawn")
@example(4, 2).via("doc: over --max-edges refuses and prints nothing")
def test_mermaid_refuses_over_max_edges(mk, n, limit):
    q = mk()
    q.write_task(chain(n))
    r = q.run("graph", "--format", "mermaid", "--max-edges", str(limit))
    if n - 1 > limit:
        assert r.code == 1 and r.out == ""
    else:
        assert r.code == 0 and r.out.count("==>") + r.out.count("-->") == n - 1


def test_mermaid_refuses_over_max_chars_and_json_has_no_default_limit(mk):
    q = mk()
    q.write_task([{} for _ in range(3)])
    r = q.run("graph", "--format", "mermaid", "--max-chars", "10")
    assert r.code == 1 and r.out == ""
    assert q.run("graph", "--format", "mermaid", "--max-chars", "100000").code == 0
    assert q.run("graph", "--format", "json").code == 0


@PROP
@given(dags(6), st.data())
def test_graph_json_states_agree_with_ready_and_edges_with_the_dependencies(mk, dag, data):
    n, deps = dag
    closed = data.draw(st.sets(st.integers(0, n - 1)))
    q = mk()
    q.write_task(dag_steps(deps, closed))
    code, g = gjson(q)
    assert code == 0
    ready = set(q.ready()[1])
    states = {x["ref"]: x["state"] for x in g["nodes"]}
    assert set(states) == {ref(i) for i in range(n)}
    for i in range(n):
        if i in closed:
            assert states[ref(i)] == "done"
        else:
            assert (states[ref(i)] == "ready") == (ref(i) in ready)
    assert sorted(map(tuple, g["edges"])) == sorted(
        (ref(d), ref(i)) for i in range(n) for d in deps[i])
    assert [x["id"] for x in g["nodes"]] == [f"n{i}" for i in range(n)]


def test_graph_unknown_task_is_a_usage_error(mk):
    q = mk()
    q.write_task([{}])
    assert q.run("graph", "--task", "TASK-API-099").code == 64


# ------------------------------------------------------------- config and names

@pytest.mark.parametrize("extra", [
    "stale_days: many\n", "priorities: high\n", "machines: [laptop]\n", "rate_gate: 3\n",
    "extra_refs: 3\n", "target_aliases: [a]\n"])
def test_a_config_field_of_the_wrong_type_makes_verbs_exit_2_naming_the_field(mk, extra):
    q = mk(CONFIG + extra)
    q.write_task([{}])
    r = q.run("ready")
    assert r.code == 2
    assert extra.split(":")[0] in r.out + r.err


def test_an_unknown_config_key_is_reported_on_stderr(mk):
    q = mk(CONFIG + "stale_day: 5\n")
    q.write_task([{}])
    r = q.run("ready")
    assert r.code == 0 and "stale_day" in r.err


@pytest.mark.parametrize("key,kind", [
    ("queue", "absolute"), ("queue", "dotdot"), ("queue", "git"), ("claims", "dotdot"),
    ("notes", "nested_dotdot"), ("archive", "absolute")])
def test_data_paths_that_leave_the_repository_are_not_evaluable_and_nothing_is_written(
        mk, tmp_path_factory, key, kind):
    outside = tmp_path_factory.mktemp("outside", numbered=True)
    value = {"absolute": str(outside / "q"), "dotdot": "../outside-q", "git": ".git/q",
             "nested_dotdot": "a/../../n"}[kind]
    q = mk(CONFIG + f"{key}: {value}\n")
    before = set(outside.parent.rglob("*"))
    assert q.run("ready").code == 2
    assert q.run("new", "T", "-d", "api").code == 2
    assert set(outside.parent.rglob("*")) == before


def test_a_symlink_that_leaves_the_repository_is_not_evaluable(mk, tmp_path_factory):
    outside = tmp_path_factory.mktemp("outside", numbered=True)
    q = mk(CONFIG + "queue: linked\n")
    (q.root / "linked").symlink_to(outside, target_is_directory=True)
    assert q.run("ready").code == 2
    assert q.run("new", "T", "-d", "api").code == 2
    assert list(outside.iterdir()) == []


def test_a_data_path_inside_the_repository_works(mk):
    q = mk(CONFIG + "queue: work/q\n")
    (q.root / "work" / "q").mkdir(parents=True)
    assert q.run("new", "T", "-d", "api").code == 0
    assert (q.root / "work" / "q" / f"{TID}.yaml").exists()


@PROP
@given(st.from_regex(r"[a-z][a-z0-9_]{0,10}", fullmatch=True))
@example("a-b").via("doc: a hyphen alone is allowed")
@example("a-to").via("doc: only the whole -to- separator is refused")
@example("to-a").via("doc: only the whole -to- separator is refused")
def test_safe_machine_names_are_accepted(mk, name):
    q = mk()
    q.write_task([{}])
    code, j = q.js("ready", machine=name)
    assert code == 0 and j["machine"] == name


UNSAFE = [".", "..", "a/b", "a\\b", "a+b", "a-to-b", "-to-", "a\x01b", "a\nb"]


@pytest.mark.parametrize("name", UNSAFE + [""])
def test_unsafe_recipient_names_are_refused_before_any_path_is_built(mk, name):
    q = mk()
    q.write_task([{}])
    before = q.snapshot()
    assert q.run("note", "--to", name, "hi").code != 0
    assert q.snapshot() == before


@pytest.mark.parametrize("name", UNSAFE)
def test_unsafe_machine_names_from_the_environment_are_refused(mk, name):
    q = mk()
    q.write_task([{}])
    before = q.snapshot()
    with env(CROSSBOX_MACHINE=name):
        assert run(q.root, "claim", "some work").code != 0
    assert q.snapshot() == before


@pytest.mark.parametrize("name", [".", "..", "a/b", "a\\b", "a+b", "a-to-b"])
def test_unsafe_slot_names_are_refused(mk, name):
    q = mk()
    q.write_task([{}])
    before = q.snapshot()
    assert q.run("claim", ref(0), slot=name).code != 0
    assert q.snapshot() == before


# --------------------------------------------------------------- locks, threads

def test_a_writer_that_cannot_get_the_lock_exits_2_without_writing(mk):
    fcntl = pytest.importorskip("fcntl")
    q = mk()
    q.write_task([{}])
    before = q.snapshot()
    fd = os.open(q.root, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        with env(CROSSBOX_LOCK_TIMEOUT="0.3"):
            assert q.run("add", TID, "x").code == 2
            assert q.run("ready").code == 0, "readers take no lock"
        assert q.snapshot() == before
    finally:
        os.close(fd)
    assert q.run("add", TID, "x").code == 0


def test_parallel_writers_lose_no_writes(mk):
    import subprocess
    q = mk()
    q.write_task([])
    envv = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]), CROSSBOX_RATE_DISABLE="1", CROSSBOX_MACHINE="laptop")
    procs = [subprocess.Popen([sys.executable, "-B", "-m", "crossbox", "--root", str(q.root),
                               "add", TID, f"s{i}"], env=envv, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for i in range(8)]
    assert [p.wait() for p in procs] == [0] * 8
    steps = q.task()["steps"]
    assert [s["id"] for s in steps] == list("abcdefgh")
    assert sorted(s["title"] for s in steps) == [f"s{i}" for i in range(8)]
    for p in procs:
        p.stdout.close()
        p.stderr.close()


def test_parallel_claims_mint_distinct_ids(mk):
    import subprocess
    q = mk()
    q.write_task([{} for _ in range(6)])
    envv = dict(os.environ, PYTHONPATH=os.pathsep.join([str(SRC), os.environ.get("PYTHONPATH", "")]), CROSSBOX_RATE_DISABLE="1", CROSSBOX_MACHINE="laptop")
    procs = [subprocess.Popen([sys.executable, "-B", "-m", "crossbox", "--root", str(q.root),
                               "claim", ref(i)], env=envv, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) for i in range(6)]
    assert [p.wait() for p in procs] == [0] * 6
    assert sorted(c["id"] for c in q.claims()) == [f"CLM-laptop-{i:03d}" for i in range(1, 7)]
    for p in procs:
        p.stdout.close()
        p.stderr.close()


# ---------------------------------------------------------------- git worktrees

def test_a_committed_claim_is_visible_from_a_sibling_worktree_before_any_push(repo, tmp_path):
    run(repo, "new", "T", "-d", "api")
    run(repo, "add", TID, "one")
    run(repo, "add", TID, "two")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")
    assert run(repo, "claim", ref(0)).code == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "claim")
    wt = tmp_path / "repo-feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    r = run(wt, "ready", "--json")
    j = json.loads(r.out)
    assert [x["ref"] for x in j["claimed"]] == [ref(0)]
    assert [x["ref"] for x in j["ready"]] == [ref(1)]


def test_the_origin_guard_refuses_an_id_a_sibling_branch_already_holds(repo, tmp_path):
    run(repo, "new", "T", "-d", "api")
    run(repo, "add", TID, "one")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")
    wt = tmp_path / "repo-feature"
    git(repo, "worktree", "add", "-q", "-b", "feature", str(wt))
    assert run(wt, "add", TID, "doc").out.strip() == f"added {ref(1)}"
    git(wt, "add", "-A")
    git(wt, "commit", "-q", "-m", "doc step")
    before = (repo / ".crossbox" / "queue" / f"{TID}.yaml").read_bytes()
    r = run(repo, "add", TID, "lexer")
    assert r.code == 1 and "CROSSBOX_SKIP_ORIGIN_GUARD" in r.out + r.err
    assert "feature" in r.out + r.err
    assert (repo / ".crossbox" / "queue" / f"{TID}.yaml").read_bytes() == before
    git(repo, "merge", "-q", "feature")
    assert run(repo, "add", TID, "lexer").out.strip() == f"added {ref(2)}"


def test_a_remote_named_in_the_config_that_does_not_exist_is_not_evaluable(tmp_path):
    root = tmp_path / "r"
    git(tmp_path, "init", "-q", "-b", "main", str(root))
    assert run(root, "init").code == 0
    (root / ".crossbox" / "config.yaml").write_text(CONFIG + "remote: nope\n")
    r = run(root, "new", "T", "-d", "api")
    assert r.code == 2
    assert not (root / ".crossbox" / "queue" / f"{TID}.yaml").exists()
