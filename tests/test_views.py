"""`crossbox progress` and `crossbox near-done` (docs/views.md; views invariants
V1-V9 in INVARIANTS.md)."""
import json

import pytest
from conftest import init_repo, run
from test_plan import REG, put
import yaml

CFG_GPU = """\
prefix: TASK
machines:
  laptop:      {provides: [docker]}
  workstation: {provides: [gpu, docker]}
"""


def jprogress(root, *extra):
    r = run(root, "progress", "--json", *extra)
    return r, json.loads(r.out)


def jnear(root, *extra, machine=None):
    r = run(root, "near-done", "--json", *extra, machine=machine)
    return r, json.loads(r.out)


def snapshot(root):
    return {p: p.read_bytes() for p in (root / ".crossbox").rglob("*") if p.is_file()}


def done(*ids):
    return [(i, {"status": "done"}) for i in ids]


# ---------------------------------------------------------------- progress

def test_progress_counts_sit_beside_their_denominators(repo):
    # V1
    put(repo, "TASK-A-001", done("a", "b") + [("c", {"status": "skipped"}), ("d", {})])
    put(repo, "TASK-B-001", [("a", {}), ("b", {}), ("c", {})])
    r = run(repo, "progress")
    assert r.code == 0
    lines = r.out.splitlines()
    assert "queue only" in lines[0] and "done + skipped" in lines[0]
    a = next(x for x in lines if x.startswith("TASK-A-001"))
    assert "3/4 closed" in a and "2 done" in a and "1 skipped" in a and "75%" in a
    b = next(x for x in lines if x.startswith("TASK-B-001"))
    assert "0/3 closed" in b and "0%" in b
    total = next(x for x in lines if x.startswith("all"))
    assert "3/7 closed" in total and "2 done" in total and "1 skipped" in total
    assert "42%" in total and "2 task(s)" in total
    _, j = jprogress(repo)
    assert j["total"] == {"tasks": 2, "steps": 7, "done": 2, "skipped": 1, "closed": 3,
                          "pct": 42}


def test_percent_rounds_down_so_only_a_finished_task_reads_100(repo):
    # V1
    put(repo, "TASK-A-001", done("a", "b") + [("c", {})])
    put(repo, "TASK-B-001", done(*[f"x{chr(97 + i // 26)}{chr(97 + i % 26)}" for i in range(199)]) + [("zzz", {})])
    _, j = jprogress(repo)
    rows = {g["key"]: g for g in j["groups"] if g["kind"] == "task"}
    assert rows["TASK-A-001"]["pct"] == 66
    assert rows["TASK-B-001"]["pct"] == 99
    assert "99%" in run(repo, "progress").out


def test_zero_step_task_is_zero_over_zero_never_a_hundred_percent(repo):
    # V2
    put(repo, "TASK-A-001", [])
    r = run(repo, "progress")
    row = next(x for x in r.out.splitlines() if x.startswith("TASK-A-001"))
    assert "0/0 closed" in row and "—" in row and "100%" not in row
    total = next(x for x in r.out.splitlines() if x.startswith("all"))
    assert "0/0 closed" in total and "100%" not in total
    _, j = jprogress(repo)
    assert j["total"]["pct"] is None and j["groups"][0]["pct"] is None
    assert r.code == 0


def test_by_domain_and_priority_group_and_still_total(repo):
    # V1
    for tid, dom, prio, n_done in (("TASK-A-001", "web", "high", 1), ("TASK-B-001", "web", "low", 0),
                                   ("TASK-C-001", "ml", "high", 2)):
        put(repo, tid, done(*"abc"[:n_done]) + [("z", {})])
        p = repo / ".crossbox" / "queue" / f"{tid}.yaml"
        d = json.loads(p.read_text())
        d.update(domain=dom, priority=prio)
        p.write_text(json.dumps(d))
    r, j = jprogress(repo, "--by", "domain")
    assert r.code == 0 and [(g["key"], g["closed"], g["steps"]) for g in j["groups"]] == \
        [("ml", 2, 3), ("web", 1, 3)]
    r, j = jprogress(repo, "--by", "priority")
    assert [(g["key"], g["tasks"]) for g in j["groups"]] == [("high", 2), ("low", 1)]
    text = run(repo, "progress", "--by", "priority").out
    assert "priority high" in text and text.splitlines()[-1].startswith("all")
    r, j = jprogress(repo, "--by", "task")
    assert {g["kind"] for g in j["groups"]} == {"task"}
    _, j = jprogress(repo)                      # default: tasks, then domains
    assert [g["kind"] for g in j["groups"]] == ["task"] * 3 + ["domain"] * 2


def test_unreadable_files_are_listed_and_make_the_counts_a_floor(repo):
    # V3
    put(repo, "TASK-A-001", done("a") + [("b", {})])
    q = repo / ".crossbox" / "queue"
    (q / "TASK-B-001.yaml").write_text("id: TASK-B-001\nsteps: [\n")
    (q / "TASK-C-001.yaml").write_text("id: TASK-C-001\nsteps: not-a-list\n")
    r = run(repo, "progress")
    assert r.code == 1
    assert "UNREADABLE TASK-B-001 (TASK-B-001.yaml): task file does not parse" in r.out
    assert "UNREADABLE TASK-C-001" in r.out and "`steps` is not a list" in r.out
    total = next(x for x in r.out.splitlines() if x.startswith("all"))
    assert "1/2 closed" in total                       # unreadable steps are NOT counted
    assert "counts are a floor: 2 task file(s) unreadable" in r.out
    r, j = jprogress(repo)
    assert r.code == 1 and j["counts_are_floor"] is True
    assert [u["id"] for u in j["unreadable"]] == ["TASK-B-001", "TASK-C-001"]
    assert all(u["cause"] for u in j["unreadable"])


def test_a_clean_queue_exits_0_and_is_not_a_floor(repo):
    # V3 (the quiet direction)
    put(repo, "TASK-A-001", [("a", {})])
    r, j = jprogress(repo)
    assert r.code == 0 and j["counts_are_floor"] is False and j["unreadable"] == []
    assert "floor" not in run(repo, "progress").out


def test_archive_flag_adds_archived_tasks_and_says_what_it_counted(repo):
    # V4
    put(repo, "TASK-A-001", [("a", {})])
    put(repo, "TASK-Z-001", done("a", "b"), tier="archive")
    r, j = jprogress(repo)
    assert j["scope"] == "queue" and j["total"]["steps"] == 1
    assert "queue only" in run(repo, "progress").out
    r, j = jprogress(repo, "--archive")
    assert j["scope"] == "queue+archive" and j["total"] == {
        "tasks": 2, "steps": 3, "done": 2, "skipped": 0, "closed": 2, "pct": 66}
    text = run(repo, "progress", "--archive").out
    assert "queue and archive" in text and "TASK-Z-001 (archived)" in text


def test_archive_flag_with_a_missing_archive_dir_is_not_evaluable(repo):
    # V4: an absent archive must not read as "zero archived tasks"
    put(repo, "TASK-A-001", [("a", {})])
    (repo / ".crossbox" / "archive" / ".gitkeep").unlink()
    (repo / ".crossbox" / "archive").rmdir()
    assert run(repo, "progress").code == 0
    r = run(repo, "progress", "--archive")
    assert r.code == 2 and "archive" in r.err and "NOT EVALUABLE" in r.err


def test_a_task_id_held_by_two_files_is_excluded_not_double_counted(repo):
    # V3
    put(repo, "TASK-A-001", done("a", "b"))
    put(repo, "TASK-A-001", [("a", {})], tier="archive")        # shadowed queue copy
    r, j = jprogress(repo, "--archive")
    assert r.code == 1 and j["total"]["steps"] == 0
    assert all("more than one file" in u["cause"] for u in j["unreadable"])
    assert len(j["unreadable"]) == 2


def test_progress_missing_queue_is_not_evaluable(plain):
    # V5
    q = plain / ".crossbox" / "queue"
    for f in q.iterdir():
        f.unlink()
    q.rmdir()
    r = run(plain, "progress", "--json")
    assert r.code == 2 and "not_evaluable" in json.loads(r.out)
    assert run(plain, "progress").code == 2


def test_progress_json_keys_are_exactly_the_contract(repo):
    # V6
    put(repo, "TASK-A-001", [("a", {})])
    _, j = jprogress(repo)
    assert set(j) == {"scope", "by", "groups", "total", "unreadable", "counts_are_floor"}
    assert set(j["total"]) == {"tasks", "steps", "done", "skipped", "closed", "pct"}
    assert set(j["groups"][0]) == {"kind", "key", "title", "archived", "tasks", "steps",
                                   "done", "skipped", "closed", "pct"}


# ---------------------------------------------------------------- near-done

def test_near_done_lists_a_task_with_its_remaining_step_state_and_machines(repo):
    # V7
    (repo / ".crossbox" / "config.yaml").write_text(CFG_GPU)
    put(repo, "TASK-A-001", done("a", "b", "c", "d") + [("e", {"target": "gpu"})])
    r = run(repo, "near-done")
    assert r.code == 0
    assert "TASK-A-001  4/5  80%  1 open" in r.out
    assert "  .e  elsewhere (target gpu)   workable on: workstation" in r.out
    assert "near-done: 1 of 1 task(s) with open steps listed" in r.out
    r = run(repo, "near-done", machine="workstation")
    assert "  .e  READY here   workable on: workstation" in r.out


def test_near_done_every_state_reads_in_words(repo):
    # V7
    (repo / ".crossbox" / "config.yaml").write_text(CFG_GPU)
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", done("a", "b", "c", "d", "e", "f", "g")
        + [("h", {})])                                                   # READY
    put(repo, "TASK-B-001", done("a", "b") + [("c", {"needs": ["closed-probe"]})])  # GATED
    put(repo, "TASK-C-001", done("a", "b") + [("c", {})])
    assert run(repo, "claim", "TASK-C-001.c").code == 0                  # CLAIMED
    put(repo, "TASK-D-001", done("a", "b") + [("c", {"depends_on": ["TASK-E-001.a"]})])
    put(repo, "TASK-E-001", [("a", {})])                                 # D.c WAITING
    put(repo, "TASK-F-001", done("a", "b", "e") + [("c", {"depends_on": ["d"]}),
                                              ("d", {"depends_on": ["c"]})])  # CYCLE
    r, j = jnear(repo)
    state = {s["ref"]: s["state"] for t in j["tasks"] for s in t["steps"]}
    assert state["TASK-A-001.h"] == "ready" and state["TASK-B-001.c"] == "gated"
    assert state["TASK-C-001.c"] == "claimed" and state["TASK-D-001.c"] == "waiting"
    assert state["TASK-F-001.c"] == "cycle"
    assert r.code == 1                                   # a cycle is a finding
    text = run(repo, "near-done", "--max-open", "2").out
    assert "GATED here (closed-probe closed)" in text and "CLAIMED (CLM-laptop-001" in text
    assert "WAITING on TASK-E-001.a" in text and "CYCLE (" in text and "READY here" in text


def test_thresholds_are_inclusive_and_both_must_hold(repo):
    # V7
    put(repo, "TASK-A-001", done("a", "b", "c") + [("d", {}), ("e", {})])       # 60%, 2 open
    put(repo, "TASK-B-001", done("a", "b") + [("c", {}), ("d", {}), ("e", {})])  # 40%, 3 open
    put(repo, "TASK-C-001", done(*"abcdefghi") + [("j", {})])                   # 90%, 1 open
    put(repo, "TASK-D-001", done("a", "b", "c") + [("d", {}), ("e", {}), ("f", {})])  # 50%, 3 open
    _, j = jnear(repo)
    assert [t["id"] for t in j["tasks"]] == ["TASK-C-001", "TASK-A-001"]
    _, j = jnear(repo, "--min-pct", "61")
    assert [t["id"] for t in j["tasks"]] == ["TASK-C-001"]
    _, j = jnear(repo, "--max-open", "1")
    assert [t["id"] for t in j["tasks"]] == ["TASK-C-001"]
    _, j = jnear(repo, "--max-open", "3", "--min-pct", "50")
    assert [t["id"] for t in j["tasks"]] == ["TASK-C-001", "TASK-A-001", "TASK-D-001"]
    assert j["tasks_with_open_steps"] == 4        # the denominator of "N listed"


def test_order_is_open_steps_then_percent_descending_then_id(repo):
    # V7
    put(repo, "TASK-B-001", done(*"abcd") + [("e", {})])                       # 1 open, 80%
    put(repo, "TASK-A-001", done(*"abcd") + [("e", {})])                       # tie -> id
    put(repo, "TASK-C-001", done(*"abcdefghi") + [("j", {})])                  # 1 open, 90%
    put(repo, "TASK-D-001", done(*"abcdefgh") + [("i", {}), ("j", {})])        # 2 open, 80%
    _, j = jnear(repo)
    assert [t["id"] for t in j["tasks"]] == ["TASK-C-001", "TASK-A-001", "TASK-B-001",
                                             "TASK-D-001"]


def test_complete_and_stepless_tasks_are_not_near_done(repo):
    # V7: nothing left to do is not "near done"; 0/0 is not 100%
    put(repo, "TASK-A-001", done("a", "b"))
    put(repo, "TASK-B-001", [])
    put(repo, "TASK-C-001", done("a", "b") + [("c", {})])
    _, j = jnear(repo)
    assert [t["id"] for t in j["tasks"]] == ["TASK-C-001"] and j["tasks_with_open_steps"] == 1


def test_a_step_no_configured_machine_provides_says_none_and_is_counted(repo):
    # V7
    put(repo, "TASK-A-001", done("a", "b", "x") + [("c", {"target": "docker"}),
                                                   ("d", {"target": "tpu"})])
    r, j = jnear(repo)
    by = {s["id"]: s for t in j["tasks"] for s in t["steps"]}
    assert by["c"]["workable_on"] == ["laptop", "workstation"]
    assert by["d"]["workable_on"] == [] and by["d"]["state"] == "malformed"
    assert j["unworkable_steps"] == 1 and r.code == 1
    text = run(repo, "near-done").out
    assert "workable on: none" in text and "contains malformed steps" in text
    assert "1 listed step(s) workable on no configured machine" in text
    assert "workable on: laptop, workstation" in text


def test_target_any_is_workable_on_every_machine(repo):
    # V7
    put(repo, "TASK-A-001", done("a", "b") + [("c", {})])
    text = run(repo, "near-done").out
    assert "workable on: any machine" in text
    _, j = jnear(repo)
    assert j["tasks"][0]["steps"][0]["workable_on"] == ["any"]


def test_near_done_with_a_damaged_claims_file_is_not_evaluable(repo):
    # V8
    put(repo, "TASK-A-001", done("a", "b") + [("c", {})])
    (repo / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    r, j = jnear(repo)
    assert r.code == 2 and "claims file damaged" in j["not_evaluable"]
    assert j["tasks"][0]["steps"][0]["state"] == "not_evaluable"
    out = run(repo, "near-done")
    assert out.code == 2 and "NOT EVALUABLE (" in out.out and "counts are a floor" in out.out


def test_near_done_with_an_incomplete_claims_view_never_reads_ready(world):
    # V8
    (world.wt1 / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    world.commit(world.wt1)
    put(world.laptop, "TASK-A-001", done("a", "b") + [("c", {})])
    r, j = jnear(world.laptop)
    assert r.code == 2 and j["tasks"][0]["steps"][0]["state"] == "not_evaluable"


def test_near_done_gate_probe_with_no_answer_is_not_evaluable(repo):
    # V8
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", done("a", "b") + [("c", {"needs": ["broken-probe"]})])
    r, j = jnear(repo)
    assert r.code == 2 and "probe" in j["not_evaluable"]
    assert j["tasks"][0]["steps"][0]["state"] == "gated"


def test_near_done_unreadable_file_is_listed_and_exits_1(repo):
    # V3
    put(repo, "TASK-A-001", done("a", "b") + [("c", {})])
    (repo / ".crossbox" / "queue" / "TASK-B-001.yaml").write_text("id: TASK-B-001\nsteps: [\n")
    r, j = jnear(repo)
    assert r.code == 1 and j["counts_are_floor"] is True
    assert [u["id"] for u in j["unreadable"]] == ["TASK-B-001"]
    assert "UNREADABLE TASK-B-001" in run(repo, "near-done").out


def test_near_done_missing_queue_is_not_evaluable(plain):
    # V8
    q = plain / ".crossbox" / "queue"
    for f in q.iterdir():
        f.unlink()
    q.rmdir()
    r = run(plain, "near-done", "--json")
    assert r.code == 2 and "not_evaluable" in json.loads(r.out)


def test_near_done_json_keys_are_exactly_the_contract(repo):
    # V6
    put(repo, "TASK-A-001", done("a", "b") + [("c", {})])
    _, j = jnear(repo)
    assert set(j) == {"machine", "slot", "criteria", "tasks_with_open_steps", "tasks",
                      "unworkable_steps", "problem_steps", "unreadable", "not_evaluable",
                      "counts_are_floor"}
    assert set(j["tasks"][0]) == {"id", "title", "closed", "total", "open", "pct", "steps"}
    assert set(j["tasks"][0]["steps"][0]) == {"ref", "id", "title", "state", "detail",
                                              "target", "workable_on"}


# ---------------------------------------------------------------- shared

@pytest.mark.parametrize("args", [("progress",), ("progress", "--by", "domain"),
                                  ("progress", "--json"), ("near-done",),
                                  ("near-done", "--json")])
def test_output_is_byte_identical_whatever_the_file_creation_order(tmp_path, args):
    # V9
    outs = []
    for i, order in enumerate((("A", "B", "C"), ("C", "A", "B"))):
        root = init_repo(tmp_path / f"r{i}")
        for k in order:
            put(root, f"TASK-{k}-001", done("a", "b") + [("c", {"depends_on": ["a"]})])
        outs.append(run(root, *args))
    assert outs[0].code == outs[1].code and outs[0].out == outs[1].out and outs[0].out


def test_views_are_read_only_unmetered_and_take_no_lock(repo, monkeypatch, tmp_path):
    # V10
    from crossbox import lock
    put(repo, "TASK-A-001", done("a", "b") + [("c", {})])
    before = snapshot(repo)
    monkeypatch.setenv("CROSSBOX_RATE_DISABLE", "0")
    monkeypatch.setenv("CROSSBOX_RATE_CAP", "1")
    monkeypatch.setenv("CROSSBOX_LOCK_TIMEOUT", "0.2")
    with lock.hold(repo):
        for _ in range(3):
            for v in (("progress",), ("near-done",), ("graph",), ("progress", "--json"),
                      ("near-done", "--json"), ("graph", "--format", "json")):
                assert run(repo, *v).code == 0, v
    assert not (tmp_path / "rate" / "state.json").exists()
    assert snapshot(repo) == before


@pytest.mark.parametrize("args", [("progress", "--by", "nope"), ("near-done", "--min-pct", "101"),
                                  ("near-done", "--min-pct", "x"), ("near-done", "--max-open", "0"),
                                  ("graph", "--format", "svg"), ("graph", "--max-nodes", "0"),
                                  ("progress", "--bogus")])
def test_bad_arguments_are_usage_errors_not_not_evaluable(repo, args):
    # V10
    assert run(repo, *args).code == 64


def test_hostile_titles_cannot_inject_terminal_escapes(repo):
    # V11: titles are untrusted in the text views as well
    put(repo, "TASK-A-001", done("a", "b") + [("c", {"title": "x\x1b[31mred\x07\x00‮ end"})])
    p = repo / ".crossbox" / "queue" / "TASK-A-001.yaml"
    d = json.loads(p.read_text())
    d["title"] = "t\x1b]0;owned\x07\nline2"
    p.write_text(json.dumps(d))
    for v in (("progress",), ("near-done",)):
        out = run(repo, *v).out
        assert "\x1b" not in out and "\x07" not in out and "\x00" not in out
        assert "‮" not in out
        assert out.count("\n") == len(out.splitlines())


def test_the_documented_sample_outputs_are_what_the_views_print(tmp_path):
    # the samples in docs/views.md are regenerated from a copy of examples/sample-queue/
    # as laptop and compared line for line, so they cannot drift from the tool
    import shutil
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    sample = tmp_path / "sample"
    shutil.copytree(root / "examples" / "sample-queue", sample)
    doc = (root / "docs" / "views.md").read_text().splitlines()
    for args in (("progress",), ("near-done", "--max-open", "3", "--min-pct", "25")):
        r = run(sample, *args, machine="laptop")
        assert r.code == 0, r
        cmd = "    $ crossbox " + " ".join(args)
        assert cmd in doc, cmd
        at = doc.index(cmd) + 1
        want = ["    " + ln if ln else "" for ln in r.out.splitlines()]
        assert doc[at:at + len(want)] == want


def _task(root, tid, steps, fname=None, title="T"):
    data = {"id": tid, "title": title, "priority": "normal", "steps": steps}
    p = root / ".crossbox" / "queue" / (fname or f"{tid}.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10 ** 6))
    return p


def _step(sid, title="s", status="pending", dep=None, needs=None, target="any"):
    return {"id": sid, "title": title, "target": target, "needs": needs or [],
            "status": status, "depends_on": dep or []}


@pytest.fixture
def probed(repo, tmp_path):
    marker = tmp_path / "marker"
    (repo / ".crossbox" / "needs-registry.yaml").write_text(
        f'schema: 1\ntokens:\n  boom: {{kind: gate, probe: "echo x >> {marker}; exit 1"}}\n')
    _task(repo, "TASK-001", [_step("a", needs=["boom"])])
    return repo, marker


@pytest.mark.parametrize("verb", [["plan"], ["near-done", "--min-pct", "0"], ["graph"]])
def test_the_views_run_the_probe_unless_told_not_to(probed, verb):
    """R6/F1: see INVARIANTS.md, sixth-review findings."""
    repo, marker = probed
    run(repo, *verb)
    assert marker.exists()                       # the control: without the flag it runs
    marker.unlink()
    r = run(repo, *verb, "--no-probes")
    assert not marker.exists(), r
    assert r.code == 2, r                        # a probed gate is NOT EVALUABLE, never open
    assert "--no-probes" in r.out + r.err


def test_no_probes_leaves_an_unprobed_queue_alone(repo):
    """R6/F1: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-001", [_step("a"), _step("b", dep=["a"])])
    for verb in (["plan"], ["near-done", "--min-pct", "0"], ["graph"]):
        a, b = run(repo, *verb), run(repo, *verb, "--no-probes")
        assert (a.code, a.out, a.err) == (b.code, b.out, b.err) and a.code == 0, verb


def test_near_done_cleans_machine_names(repo):
    """R6/F9: see INVARIANTS.md, sixth-review findings."""
    cfg = repo / ".crossbox" / "config.yaml"
    cfg.write_text("prefix: TASK\nmachines:\n  \"lap‮top\": {provides: [docker]}\n")
    _task(repo, "TASK-A-001", [_step("a", status="done"), _step("b", target="docker")])
    r = run(repo, "near-done", "--min-pct", "0", machine="x")
    assert "‮" not in r.out, r.out
    assert "laptop" in r.out


def test_an_empty_machine_flag_is_a_usage_error(repo):
    """R6/F16: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-A-001", [_step("a")])
    for verb in ("plan", "ready", "near-done"):
        r = run(repo, "--machine", "", verb)
        assert r.code == 64, (verb, r)
        assert "--machine" in r.err and "empty" in r.err


def test_a_bad_machine_flag_names_the_flag_not_the_environment(repo, monkeypatch):
    """R6/F16: see INVARIANTS.md, sixth-review findings."""
    monkeypatch.setenv("CROSSBOX_MACHINE", "laptop")
    r = run(repo, "--machine", "a/b", "plan")
    assert r.code == 64 and "--machine 'a/b'" in r.err and "$CROSSBOX_MACHINE" not in r.err, r


def test_a_bad_environment_machine_names_the_environment(repo, monkeypatch):
    """R6/F16: see INVARIANTS.md, sixth-review findings."""
    monkeypatch.setenv("CROSSBOX_MACHINE", "a/b")
    r = run(repo, "plan")
    assert r.code == 2 and "$CROSSBOX_MACHINE" in r.err, r


def test_progress_json_groups_all_carry_the_documented_keys(repo):
    """R6/F18: see INVARIANTS.md, sixth-review findings."""
    _task(repo, "TASK-A-001", [_step("a")])
    j = json.loads(run(repo, "progress", "--json").out)
    keys = {"kind", "key", "title", "archived", "tasks", "steps", "done", "skipped",
            "closed", "pct"}
    assert {g["kind"] for g in j["groups"]} == {"task", "domain"}
    for g in j["groups"]:
        assert set(g) == keys, g
        if g["kind"] != "task":
            assert g["title"] is None and g["archived"] is None
    assert j["groups"][0]["archived"] is False
