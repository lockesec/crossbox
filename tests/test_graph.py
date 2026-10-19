"""`crossbox graph` (docs/views.md; views invariants V12-V22 in INVARIANTS.md)."""
import json
import re
import shutil
import time
from pathlib import Path

import pytest
from conftest import init_repo, run
from test_plan import _big, put
from test_plan import plan as plan_json
import yaml

ROOT = Path(__file__).resolve().parents[1]

CFG_GPU = """\
prefix: TASK
machines:
  laptop:      {provides: [docker]}
  workstation: {provides: [gpu, docker]}
"""
REG = """\
schema: 1
tokens:
  closed-probe: {kind: gate, probe: "exit 1"}
  broken-probe: {kind: gate, probe: "exit 7"}
"""


def gjson(root, *extra, machine=None):
    r = run(root, "graph", "--format", "json", *extra, machine=machine)
    return r, json.loads(r.out)


def states(j):
    return {n["ref"]: n["state"] for n in j["nodes"]}


# ---------------------------------------------------------------- the format grammar

STATE_RE = r"(?:done|skipped|ready|gated|claimed|waiting|elsewhere|malformed|cycle|not_evaluable)"
# one label: no raw quote, no raw character Mermaid gives a meaning, only entity codes
LABEL = r'(?:[^"<>#|\[\]{}`&%\\@\r\n]|#quot;|#lt;|#gt;|#\d+;)*'
MERMAID_LINES = [
    re.compile(r"^flowchart LR$"),
    re.compile(rf"^  classDef {STATE_RE} fill:#[0-9a-f]{{6}},stroke:#[0-9a-f]{{6}},"
               r"color:#[0-9a-f]{6}(?:,stroke-[a-z]+:[0-9a-z ]+)?$"),
    re.compile(rf'^  subgraph t\d+\["{LABEL}"\]$'),
    re.compile(rf'^    n\d+\["\S+ {LABEL}"\]:::{STATE_RE}$'),
    re.compile(r"^  end$"),
    re.compile(r"^  n\d+ (?:-->|==>) n\d+$"),
]


def check_mermaid(text):
    """Every line is one of the shapes the renderer emits, subgraph/end balance, ids are
    generated; nothing from a file can start a line or a directive."""
    depth = 0
    for line in text.split("\n"):
        assert any(rx.match(line) for rx in MERMAID_LINES), repr(line)
        assert not line.lstrip().startswith("%%")
        depth += line.startswith("  subgraph ")
        depth -= line == "  end"
        assert 0 <= depth <= 1
    assert depth == 0
    ids = set(re.findall(r"^    (n\d+)\[", text, re.M))
    for a, b in re.findall(r"^  (n\d+) (?:-->|==>) (n\d+)$", text, re.M):
        assert a in ids and b in ids
    used = set(re.findall(r":::(\w+)$", text, re.M))
    assert used == set(re.findall(r"^  classDef (\w+) ", text, re.M))


DOT_STR = r'"(?:[^"\\\r\n]|\\\\|\\")*"'
DOT_LINES = [
    re.compile(r"^digraph crossbox \{$"), re.compile(r"^  rankdir=LR;$"),
    re.compile(r'^  node \[shape=box, style="rounded,filled", fontname="Helvetica"\];$'),
    re.compile(r"^  subgraph cluster_\d+ \{$"),
    re.compile(rf"^    label={DOT_STR};$"),
    re.compile(rf'^    n\d+ \[label={DOT_STR}, fillcolor="#[0-9a-f]{{6}}", '
               rf'color="#[0-9a-f]{{6}}", fontcolor="#[0-9a-f]{{6}}", '
               rf'style="rounded,filled(?:,dashed)?"\];$'),
    re.compile(r"^  \}$"), re.compile(r"^\}$"),
    re.compile(r"^  n\d+ -> n\d+(?: \[penwidth=3\])?;$"),
]


def check_dot(text):
    for line in text.split("\n"):
        assert any(rx.match(line) for rx in DOT_LINES), repr(line)


HOSTILE = [
    '"]); click n0 call x(',
    "</script><img src=x onerror=alert(1)>",
    '%%{init: {"securityLevel":"loose"}}%%',
    "`code` and ```fence```",
    "a|b|c",
    "line1\nline2\r\nline3\rline4",
    "nul\x00 esc\x1b[31m bell\x07 del\x7f c1\x85 us\x1f",
    "x" * 10240,
    "rtl ‮ gnirts ‭ bidi ⁦ zero​width",
    "end\n  end\nsubgraph evil[\"x\"]",
    "n0 --> n0\n  n0:::done",
    "%% comment start",
    "& &amp; &#35; #quot; #35; \\ \\\" @ {x} [y] <z>",
    "style n0 fill:red;click n0 href \"javascript:alert(1)\"",
    "﻿bom   line-sep   para-sep \x0b vt \x0c ff",
]


def hostile_queue(root, title):
    put(root, "TASK-A-001", [("a", {"title": title}),
                             ("b", {"title": title, "depends_on": ["a"]})])
    p = root / ".crossbox" / "queue" / "TASK-A-001.yaml"
    d = json.loads(p.read_text())
    d["title"] = title
    p.write_text(json.dumps(d))


# ---------------------------------------------------------------- states, edges, path

def test_every_node_state_is_named_and_classed(repo):
    # V12
    (repo / ".crossbox" / "config.yaml").write_text(CFG_GPU)
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", [
        ("a", {"status": "done"}), ("b", {"status": "skipped"}), ("c", {}),
        ("d", {"needs": ["closed-probe"]}), ("e", {}), ("f", {"depends_on": ["c"]}),
        ("g", {"target": "gpu"}), ("h", {"target": "tpu"}),
        ("i", {"depends_on": ["j"]}), ("j", {"depends_on": ["i"]})])
    assert run(repo, "claim", "TASK-A-001.e").code == 0
    r, j = gjson(repo)
    assert r.code == 1                                   # malformed + cycle are findings
    st = {k.split(".")[1]: v for k, v in states(j).items()}
    assert st == {"a": "done", "b": "skipped", "c": "ready", "d": "gated", "e": "claimed",
                  "f": "waiting", "g": "elsewhere", "h": "malformed", "i": "cycle",
                  "j": "cycle"}
    text = run(repo, "graph").out
    for cls in set(st.values()):
        assert f":::{cls}" in text
    check_mermaid(text.rstrip("\n"))


def test_edges_run_from_dependency_to_dependent_and_fan_out_for_task_refs(repo):
    # V13
    put(repo, "TASK-A-001", [("a", {"status": "done"}), ("b", {"depends_on": ["a"]}),
                             ("c", {})])
    put(repo, "TASK-B-001", [("a", {"depends_on": ["TASK-A-001"]}),
                             ("b", {"depends_on": ["TASK-A-001.c"]})])
    _, j = gjson(repo)
    assert j["edges"] == [["TASK-A-001.a", "TASK-A-001.b"],
                          ["TASK-A-001.a", "TASK-B-001.a"],
                          ["TASK-A-001.b", "TASK-B-001.a"],
                          ["TASK-A-001.c", "TASK-B-001.a"],
                          ["TASK-A-001.c", "TASK-B-001.b"]]


def test_open_flag_draws_exactly_the_edges_plan_resolves(repo):
    # V13: the same dependency resolution as plan (one shared function)
    from crossbox import plan as P
    from crossbox.context import Ctx
    put(repo, "TASK-A-001", [("a", {"status": "done"}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["b"]})])
    put(repo, "TASK-B-001", [("a", {"depends_on": ["TASK-A-001"]}), ("b", {}),
                             ("c", {"depends_on": ["TASK-A-001.c", "a"]})])
    put(repo, "TASK-C-001", [("a", {"depends_on": ["TASK-B-001", "TASK-A-001.b"]})])
    an = P.analyse(Ctx(root=str(repo)))
    expect = sorted([d, r] for r, n in an.cands.items() for d in n.dep_refs)
    _, j = gjson(repo, "--open")
    assert j["edges"] == expect and expect
    assert all(n["state"] not in ("done", "skipped") for n in j["nodes"])
    _, full = gjson(repo)
    assert set(map(tuple, j["edges"])) < set(map(tuple, full["edges"]))     # done work adds edges


def test_critical_path_edges_are_thick_and_match_plan(repo):
    # V14
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["b"]}), ("d", {"depends_on": ["a"]})])
    _, p = plan_json(repo)
    path = [x["ref"] for x in p["critical_path"]]
    r, j = gjson(repo)
    assert j["critical_path"] == path == ["TASK-A-001.a", "TASK-A-001.b", "TASK-A-001.c"]
    text = run(repo, "graph").out
    ids = {n["ref"]: n["id"] for n in j["nodes"]}
    assert f"  {ids[path[0]]} ==> {ids[path[1]]}" in text
    assert f"  {ids[path[1]]} ==> {ids[path[2]]}" in text
    assert f"  {ids[path[0]]} --> {ids['TASK-A-001.d']}" in text      # quiet direction: not thick
    assert text.count("==>") == 2
    assert "[penwidth=3]" in run(repo, "graph", "--format", "dot").out


def test_machine_flag_changes_the_states_not_the_shape(repo):
    # V12
    (repo / ".crossbox" / "config.yaml").write_text(CFG_GPU)
    put(repo, "TASK-A-001", [("a", {"target": "gpu"}), ("b", {"depends_on": ["a"]})])
    _, lap = gjson(repo)
    _, ws = gjson(repo, machine="workstation")
    assert states(lap)["TASK-A-001.a"] == "elsewhere" and states(ws)["TASK-A-001.a"] == "ready"
    assert lap["edges"] == ws["edges"]


# ---------------------------------------------------------------- untrusted text

@pytest.mark.parametrize("title", HOSTILE)
def test_hostile_titles_stay_inside_their_label_in_every_format(repo, title):
    # V15
    hostile_queue(repo, title)
    for fmt, check in (("mermaid", check_mermaid), ("dot", check_dot)):
        r = run(repo, "graph", "--format", fmt)
        assert r.code == 0, r
        check(r.out.rstrip("\n"))
        assert "\x00" not in r.out and "\x1b" not in r.out and "‮" not in r.out
        assert "\r" not in r.out
    out = run(repo, "graph").out
    assert max(len(x) for x in out.split("\n")) < 400                     # the 10 KB title is cut
    r, j = gjson(repo)
    assert all(len(n["title"]) <= 60 and "\n" not in n["title"] for n in j["nodes"])


def test_mermaid_escapes_the_characters_that_mean_something(repo):
    # V15
    hostile_queue(repo, '"]); click n0 call x(')
    out = run(repo, "graph").out
    assert '#quot;#93;); click n0 call x(' in out
    hostile_queue(repo, "</script><img src=x onerror=alert(1)>")
    out = run(repo, "graph").out
    assert "</script>" not in out and "#lt;/script#gt;" in out
    hostile_queue(repo, '%%{init: {"securityLevel":"loose"}}%%')
    out = run(repo, "graph").out
    assert "%%" not in out and "#37;#37;#123;init: #123;#quot;securityLevel" in out
    hostile_queue(repo, "a`b|c#d[e]f{g}")
    out = run(repo, "graph").out
    assert "a#96;b#124;c#35;d#91;e#93;f#123;g#125;" in out


def test_a_hostile_task_id_and_task_title_get_the_same_escaping(repo):
    # V15: subgraph labels, and ids never taken from file text
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]})])
    p = repo / ".crossbox" / "queue" / "TASK-A-001.yaml"
    d = json.loads(p.read_text())
    d["title"] = '"]); click t0 call evil(\n%%{init}%% <b>'
    d["id"] = 'TASK-A-001"] --> x["<img src=x>'
    p.write_text(json.dumps(d))
    for fmt, check in (("mermaid", check_mermaid), ("dot", check_dot)):
        r = run(repo, "graph", "--format", fmt)
        assert r.code in (0, 1), r
        check(r.out.rstrip("\n"))
    out = run(repo, "graph").out
    sub = next(x for x in out.split("\n") if x.startswith("  subgraph"))
    assert "<" not in sub and "%%" not in sub and sub.count('"') == 2


def test_a_title_is_cut_to_60_characters_with_an_ellipsis(repo):
    # V15
    put(repo, "TASK-A-001", [("a", {"title": "w" * 100}), ("b", {"title": "short"})])
    _, j = gjson(repo)
    t = {n["ref"]: n["title"] for n in j["nodes"]}
    assert t["TASK-A-001.a"] == "w" * 59 + "…" and len(t["TASK-A-001.a"]) == 60
    assert t["TASK-A-001.b"] == "short"


def test_node_ids_are_generated_in_sorted_ref_order(repo):
    # V16
    put(repo, "TASK-B-001", [("a", {}), ("b", {})])
    put(repo, "TASK-A-001", [("b", {}), ("a", {})])
    _, j = gjson(repo)
    assert [(n["id"], n["ref"]) for n in j["nodes"]] == [
        ("n0", "TASK-A-001.a"), ("n1", "TASK-A-001.b"), ("n2", "TASK-B-001.a"),
        ("n3", "TASK-B-001.b")]
    out = run(repo, "graph").out
    assert "TASK-A-001.a" not in out.replace("TASK-A-001:", "")        # refs are not ids


# ---------------------------------------------------------------- size and scope

def test_over_max_nodes_refuses_names_the_count_and_prints_no_graph(repo):
    # V17
    put(repo, "TASK-A-001", [(c, {}) for c in "abcdef"])
    r = run(repo, "graph", "--max-nodes", "5")
    assert r.code == 1 and r.out == ""
    assert "REFUSED" in r.err and "6 nodes exceed --max-nodes 5" in r.err
    assert "--task" in r.err and "--open" in r.err and "silently" in r.err
    for fmt in ("dot", "json"):
        r = run(repo, "graph", "--format", fmt, "--max-nodes", "5")
        assert r.code == 1 and r.out == ""
    r = run(repo, "graph", "--max-nodes", "6")                           # exactly the limit
    assert r.code == 0 and r.out.count(":::") == 6
    put(repo, "TASK-B-001", [("a", {"status": "done"}), ("b", {})])
    assert run(repo, "graph", "--max-nodes", "7").code == 1
    assert run(repo, "graph", "--max-nodes", "7", "--open").code == 0     # 7 open, 8 in all


def test_task_flag_draws_the_task_and_its_dependencies_as_context(repo):
    # V18
    put(repo, "TASK-A-001", [("a", {"status": "done"}), ("b", {})])
    put(repo, "TASK-B-001", [("a", {"depends_on": ["TASK-A-001.b"]}), ("b", {})])
    put(repo, "TASK-C-001", [("a", {"depends_on": ["TASK-B-001.a"]})])
    r, j = gjson(repo, "--task", "TASK-B-001")
    assert [(n["ref"], n["context"]) for n in j["nodes"]] == [
        ("TASK-A-001.b", True), ("TASK-B-001.a", False), ("TASK-B-001.b", False)]
    assert j["edges"] == [["TASK-A-001.b", "TASK-B-001.a"]]
    assert j["scope"]["tasks"] == ["TASK-B-001"]
    out = run(repo, "graph", "--task", "TASK-B-001").out
    assert "(context)" in out and out.count("subgraph") == 2
    _, j2 = gjson(repo, "--task", "TASK-B-001", "--task", "TASK-C-001")
    assert [n["ref"] for n in j2["nodes"] if not n["context"]] == [
        "TASK-B-001.a", "TASK-B-001.b", "TASK-C-001.a"]
    check_mermaid(out.rstrip("\n"))


def test_unknown_or_archived_task_is_a_usage_error(repo):
    # V18
    put(repo, "TASK-A-001", [("a", {})])
    put(repo, "TASK-Z-001", [("a", {"status": "done"})], tier="archive")
    for bad in ("TASK-NOPE-001", "TASK-Z-001"):
        r = run(repo, "graph", "--task", bad)
        assert r.code == 64 and "--task" in r.err and r.out == ""


def test_an_empty_selection_is_an_empty_graph_that_says_so(repo):
    # V18
    r = run(repo, "graph")
    assert r.code == 0 and r.out.strip() == "flowchart LR" and "nothing to draw" in r.err
    put(repo, "TASK-A-001", [("a", {"status": "done"})])
    r = run(repo, "graph", "--open")
    assert r.code == 0 and "0 of 1 step(s) selected" in r.err


# ---------------------------------------------------------------- floors and exit codes

def test_malformed_steps_are_drawn_malformed_and_exit_1(repo):
    # V19
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["nope"]})])
    r, j = gjson(repo)
    assert r.code == 1 and states(j)["TASK-A-001.b"] == "malformed"
    assert j["malformed"] and "dangling" in j["malformed"][0]["cause"]
    t = run(repo, "graph")
    assert t.code == 1 and ":::malformed" in t.out and "MALFORMED TASK-A-001.b" in t.err


def test_an_unreadable_task_file_is_named_and_exits_1_whatever_the_scope(repo):
    # V19
    put(repo, "TASK-A-001", [("a", {})])
    (repo / ".crossbox" / "queue" / "TASK-B-001.yaml").write_text("id: TASK-B-001\nsteps: [\n")
    r = run(repo, "graph", "--task", "TASK-A-001")
    assert r.code == 1 and "MALFORMED TASK-B-001" in r.err and "flowchart LR" in r.out


def test_a_malformed_step_outside_the_drawn_task_does_not_fail_the_drawing(repo):
    # V19 (quiet direction)
    put(repo, "TASK-A-001", [("a", {})])
    put(repo, "TASK-B-001", [("a", {"depends_on": ["nope"]})])
    assert run(repo, "graph", "--task", "TASK-A-001").code == 0
    assert run(repo, "graph").code == 1


def test_a_clean_queue_exits_0_with_nothing_on_stderr(repo):
    # V19 (quiet direction)
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]})])
    r = run(repo, "graph")
    assert r.code == 0 and r.err == ""


def test_cycle_steps_are_drawn_with_the_cycle_class_and_exit_1(repo):
    # V19
    put(repo, "TASK-A-001", [("a", {"depends_on": ["b"]}), ("b", {"depends_on": ["a"]}),
                             ("c", {"depends_on": ["a"]})])
    r = run(repo, "graph")
    assert r.code == 1 and r.out.count(":::cycle") == 2 and "CYCLE" in r.err
    check_mermaid(r.out.rstrip("\n"))


def test_damaged_claims_is_not_evaluable_and_ready_derived_states_say_so(repo):
    # V20
    put(repo, "TASK-A-001", [("a", {"status": "done"}), ("b", {}), ("c", {"depends_on": ["nope"]})])
    (repo / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    r, j = gjson(repo)
    assert r.code == 2 and "claims file damaged" in j["not_evaluable"]
    st = states(j)
    assert st["TASK-A-001.a"] == "done" and st["TASK-A-001.b"] == "not_evaluable"
    assert st["TASK-A-001.c"] == "malformed"                       # structural: still known
    t = run(repo, "graph")
    assert t.code == 2 and ":::not_evaluable" in t.out and "NOT EVALUABLE" in t.err


def test_an_incomplete_claims_view_never_reads_ready(world):
    # V20: a sibling's unparseable claims file might hide a claim on this step
    (world.wt1 / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    world.commit(world.wt1)
    put(world.laptop, "TASK-A-001", [("a", {}), ("b", {"status": "done"})])
    r, j = gjson(world.laptop)
    assert r.code == 2 and "claims view" in j["not_evaluable"]
    assert states(j) == {"TASK-A-001.a": "not_evaluable", "TASK-A-001.b": "done"}


def test_a_gate_probe_with_no_answer_is_not_evaluable(repo):
    # V20
    (repo / ".crossbox" / "needs-registry.yaml").write_text(REG)
    put(repo, "TASK-A-001", [("a", {"needs": ["broken-probe"]}), ("b", {})])
    r, j = gjson(repo)
    assert r.code == 2 and "probe" in j["not_evaluable"]
    assert states(j)["TASK-A-001.b"] == "ready"                    # the answer we do have


def test_graph_not_evaluable_wins_over_a_size_refusal(repo):
    # V20
    put(repo, "TASK-A-001", [(c, {}) for c in "abcd"])
    (repo / ".crossbox" / "claims.yaml").write_text("claims: [\n")
    r = run(repo, "graph", "--max-nodes", "2")
    assert r.code == 2 and "REFUSED" in r.err and r.out == ""


def test_missing_queue_is_not_evaluable_in_every_format(plain):
    # V20
    q = plain / ".crossbox" / "queue"
    for f in q.iterdir():
        f.unlink()
    q.rmdir()
    r = run(plain, "graph", "--format", "json")
    assert r.code == 2 and "not_evaluable" in json.loads(r.out)
    for fmt in ("mermaid", "dot"):
        r = run(plain, "graph", "--format", fmt)
        assert r.code == 2 and r.out == "" and "NOT EVALUABLE" in r.err


# ---------------------------------------------------------------- shape, order, speed

def test_json_keys_are_exactly_the_contract(repo):
    # V21
    put(repo, "TASK-A-001", [("a", {}), ("b", {"depends_on": ["a"]})])
    _, j = gjson(repo)
    assert set(j) == {"machine", "slot", "scope", "nodes", "edges", "critical_path",
                      "counts", "malformed", "cycles", "not_evaluable"}
    assert set(j["nodes"][0]) == {"id", "ref", "task", "title", "state", "detail", "context"}
    assert j["counts"] == {"nodes": 2, "queue_steps": 2, "edges": 1}


@pytest.mark.parametrize("fmt", ["mermaid", "dot", "json"])
def test_output_is_byte_identical_whatever_the_file_creation_order(tmp_path, fmt):
    # V22
    outs = []
    for i, order in enumerate((("A", "B", "C"), ("C", "A", "B"))):
        root = init_repo(tmp_path / f"r{i}")
        for k in order:
            put(root, f"TASK-{k}-001", [("a", {"status": "done"}), ("b", {"depends_on": ["a"]}),
                                        ("c", {"depends_on": ["TASK-A-001.b"]})])
        outs.append(run(root, "graph", "--format", fmt))
    assert outs[0].code == outs[1].code == 0 and outs[0].out == outs[1].out


def test_3000_steps_render_in_seconds_with_a_raised_cap(repo):
    # V22
    assert _big(repo) == 3000
    for fmt in ("json", "mermaid", "dot"):
        t = time.time()
        r = run(repo, "graph", "--format", fmt, "--max-nodes", "5000",
                "--max-edges", "5000", "--max-chars", "5000000")
        took = time.time() - t
        assert r.code == 0 and took < 20, (fmt, took)
    assert len(json.loads(run(repo, "graph", "--format", "json", "--max-nodes", "5000").out)
               ["nodes"]) == 3000
    assert run(repo, "graph").code == 1                                  # default cap 150 refuses
    check_mermaid(run(repo, "graph", "--max-nodes", "5000", "--max-edges", "5000",
                      "--max-chars", "5000000").out.rstrip("\n"))


# ---------------------------------------------------------------- the checked-in graph

README_FENCE = re.compile(r"```mermaid\n(.*?)\n```", re.S)


def test_the_walkthroughs_graph_is_what_crossbox_graph_prints(tmp_path):
    # V23: regenerated from a copy of examples/sample-queue/ as laptop and compared byte
    # for byte, so the picture cannot drift from the tool
    sample = tmp_path / "sample"
    shutil.copytree(ROOT / "examples" / "sample-queue", sample)
    r = run(sample, "graph", machine="laptop")
    assert r.code == 0 and r.err == ""
    check_mermaid(r.out.rstrip("\n"))
    doc = (ROOT / "docs" / "walkthrough.md").read_text()
    blocks = README_FENCE.findall(doc)
    assert len(blocks) == 1, "docs/walkthrough.md must carry exactly one mermaid block"
    assert blocks[0] == r.out.rstrip("\n")


def test_the_readme_points_at_the_graph_and_the_views():
    readme = (ROOT / "README.md").read_text()
    assert "walkthrough.md#the-queue-as-a-graph" in readme
    assert "views.md" in readme
    assert len(readme.splitlines()) <= 124


def _sid(i):
    n, out = i + 1, ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(97 + r) + out
    return out


def _step(sid, title="s", status="pending", dep=None, needs=None, target="any"):
    return {"id": sid, "title": title, "target": target, "needs": needs or [],
            "status": status, "depends_on": dep or []}


def _task(root, tid, steps, fname=None, title="T"):
    data = {"id": tid, "title": title, "priority": "normal", "steps": steps}
    p = root / ".crossbox" / "queue" / (fname or f"{tid}.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10 ** 6))
    return p


def _fan(root, n):
    _task(root, "TASK-A-001", [_step(_sid(i)) for i in range(n)])
    _task(root, "TASK-A-002", [_step(_sid(i), dep=["TASK-A-001"]) for i in range(n)])


@pytest.fixture
def probed(repo, tmp_path):
    marker = tmp_path / "marker"
    (repo / ".crossbox" / "needs-registry.yaml").write_text(
        f'schema: 1\ntokens:\n  boom: {{kind: gate, probe: "echo x >> {marker}; exit 1"}}\n')
    _task(repo, "TASK-001", [_step("a", needs=["boom"])])
    return repo, marker


def test_graph_refuses_more_edges_than_mermaid_accepts(repo):
    """R6/F4: see INVARIANTS.md, sixth-review findings."""
    _fan(repo, 60)                               # 120 nodes (under 150), 3,600 edges
    r = run(repo, "graph")
    assert r.code == 1 and r.out == "", r
    assert "3600 edges" in r.err and "500" in r.err and "--max-edges" in r.err, r.err
    assert "Nothing was dropped silently" in r.err


def test_graph_edge_and_char_limits_are_flags_and_other_formats_have_none(repo):
    """R6/F4: see INVARIANTS.md, sixth-review findings."""
    _fan(repo, 60)
    assert run(repo, "graph", "--format", "dot").code == 0
    assert run(repo, "graph", "--format", "json").code == 0
    r = run(repo, "graph", "--max-edges", "4000", "--max-chars", "1000000")
    assert r.code == 0 and r.out.count("-->") + r.out.count("==>") == 3600, r.err
    assert run(repo, "graph", "--format", "dot", "--max-edges", "100").code == 1


def test_graph_refuses_more_characters_than_mermaid_accepts(repo):
    """R6/F4: see INVARIANTS.md, sixth-review findings."""
    _fan(repo, 5)
    ok = run(repo, "graph")
    assert ok.code == 0 and ok.out
    r = run(repo, "graph", "--max-chars", str(len(ok.out.rstrip("\n")) - 1))
    assert r.code == 1 and r.out == "", r
    assert "--max-chars" in r.err and "characters" in r.err
    edge = run(repo, "graph", "--max-edges", "25")      # exactly 25 edges: allowed
    assert edge.code == 0, edge
    assert run(repo, "graph", "--max-edges", "24").code == 1


def test_a_size_refusal_does_not_hide_not_evaluable(probed):
    """R6/F4: see INVARIANTS.md, sixth-review findings."""
    repo, _ = probed
    _fan(repo, 60)
    assert run(repo, "graph", "--no-probes").code == 2
