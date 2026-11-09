"""Absent, damaged and mis-shaped inputs are never READY and never clean.

Every reader states when it could not look (exit 2), and anything but the
declared shape is an error: a registry, a step's `needs`, a step id."""
import json

import pytest

from conftest import run, task_path
from conftest import init_repo
from pathlib import Path
import yaml
from crossbox import model

GATE_REG = "tokens: {release: {kind: gate}}\n"


def _reg(repo, text):
    p = repo / ".crossbox" / "needs-registry.yaml"
    if text is None:
        if p.exists():
            p.unlink()
    else:
        p.write_text(text)
    return p


def _task(repo, needs_yaml, tid="TASK-001"):
    task_path(repo, tid).write_text(
        f"id: {tid}\ntitle: t\nsteps:\n- id: a\n  title: deploy\n  status: pending\n"
        f"  needs: {needs_yaml}\n")


def _ready(repo):
    r = run(repo, "ready", "--json")
    return r, json.loads(r.out)


# ------------------------------------------------------------ the registry

def test_valid_gate_registry_gates_the_step(repo):
    # control
    _reg(repo, GATE_REG)
    _task(repo, "[release]")
    r, j = _ready(repo)
    assert r.code == 0 and j["counts"]["ready"] == 0 and j["counts"]["gated"] == 1


@pytest.mark.parametrize("registry", [
    "tokens: {release: gate}\n",                              # scalar definition
    "tokens: {release: {kind: gaet}}\n",                      # unknown kind
    "tokens: {release: {kind: Gate}}\n",                      # wrong case
    "tokens: {release: {}}\n",                                # no kind
    "tokens: [release]\n",                                    # list, not mapping
    "tokens: {release: {kind: gate, probe: 5}}\n",            # probe not a string
    "tokens: {release: {kind: gate, clearable: whoever}}\n",  # unknown clearable
    "tokens: {release: {kind: gate, prob: 'exit 0'}}\n",      # misspelt field
    "tokens: {release: {kind: gate}}\naliases: {rel: relese}\n",  # alias to nothing
    "tokens: {release: {kind: gate}}\naliases: [rel]\n",
    "tokens: {release: {kind: gate}}\nprefix_kinds: {'x-': gat}\n",
    "tokenz: {release: {kind: gate}}\n",                      # misspelt top-level key
    "- release\n",                                            # not a mapping at all
])
def test_a_malformed_registry_is_not_evaluable_never_ready(repo, registry):
    _reg(repo, registry)
    _task(repo, "[release]")
    r = run(repo, "ready", "--json")
    assert r.code == 2, r
    assert "Traceback" not in r.err
    assert "not_evaluable" in json.loads(r.out)
    assert run(repo, "needs", "audit").code == 2
    assert run(repo, "status", "--summary", "--ready").code == 2


def test_a_malformed_registry_never_crashes_a_writer(repo):
    _reg(repo, "tokens: [release]\n")
    assert run(repo, "new", "T").code == 0
    r = run(repo, "add", "TASK-001", "deploy", "-n", "release")
    assert r.code == 0 and "Traceback" not in r.err
    assert "NOT EVALUABLE" in r.err


def test_a_registry_that_is_a_directory_is_not_evaluable(repo):
    (repo / ".crossbox" / "needs-registry.yaml").mkdir()
    _task(repo, "[release]")
    assert run(repo, "ready").code == 2


def test_absent_registry_with_needs_is_not_evaluable(repo):
    _reg(repo, None)
    _task(repo, "[release]")
    r, j = _ready(repo)
    assert r.code == 2
    assert j["counts"]["ready"] == 0
    assert j["gated"][0]["gates"][0]["state"] == "not-evaluable"
    assert "registry" in run(repo, "ready").out
    assert run(repo, "needs", "audit").code == 2


def test_absent_registry_without_needs_stays_clean(repo):
    # control: no needs anywhere, no registry needed
    _reg(repo, None)
    _task(repo, "[]")
    r, j = _ready(repo)
    assert r.code == 0 and j["counts"]["ready"] == 1
    assert run(repo, "needs", "audit").code == 0


def test_builtin_date_gate_works_without_a_registry(repo):
    _reg(repo, None)
    _task(repo, "['date-reached:2000-01-01']")
    r, j = _ready(repo)
    assert r.code == 0 and j["counts"]["ready"] == 1


def test_writers_warn_on_needs_when_no_registry(repo):
    _reg(repo, None)
    assert run(repo, "new", "T").code == 0
    r = run(repo, "add", "TASK-001", "deploy", "-n", "release")
    assert r.code == 0 and "no needs registry" in r.err


# ------------------------------------------------------------ a step's needs

@pytest.mark.parametrize("needs_yaml", [
    "release",            # scalar
    "{release: 1}",       # mapping
    "[ [release] ]",      # nested list
    "[7]",                # non-string
    "[relase]",           # unknown token (typo)
    "['release now']",    # multi-word
    "['']",               # empty token
])
def test_a_needs_value_of_the_wrong_shape_is_malformed_never_ready(repo, needs_yaml):
    _reg(repo, GATE_REG)
    _task(repo, needs_yaml)
    r, j = _ready(repo)
    assert r.code == 1, r
    assert j["counts"]["ready"] == 0 and j["counts"]["malformed"] == 1
    assert j["counts_are_floor"] is True


def test_writers_refuse_an_unknown_need_when_a_registry_exists(repo):
    _reg(repo, GATE_REG)
    assert run(repo, "new", "T").code == 0
    before = task_path(repo, "TASK-001").read_text()
    r = run(repo, "add", "TASK-001", "deploy", "-n", "relase")
    assert r.code == 1 and "did you mean release" in r.err
    assert task_path(repo, "TASK-001").read_text() == before
    assert run(repo, "add", "TASK-001", "deploy", "-n", "release").code == 0
    r = run(repo, "edit-step", "TASK-001.a", "--needs", "relase")
    assert r.code == 1


# ------------------------------------------------------------ step ids

@pytest.mark.parametrize("steps", [
    "- {id: a, title: one, status: pending}\n- {id: a, title: two, status: pending}\n",
    "- &s {id: a, title: one, status: pending}\n- *s\n",
    "- {title: no id, status: pending}\n",
    "- {id: 1, title: int id, status: pending}\n",
    "- {id: A, title: upper, status: pending}\n",
])
def test_duplicate_or_invalid_step_ids_are_malformed_not_ready(repo, steps):
    task_path(repo, "TASK-001").write_text(f"id: TASK-001\ntitle: t\nsteps:\n{steps}")
    r, j = _ready(repo)
    assert r.code == 1, r
    assert j["counts"]["ready"] == 0 and j["counts"]["malformed"] >= 1
    assert run(repo, "doctor", "check").code == 1


def test_distinct_step_ids_stay_ready(repo):
    # control
    task_path(repo, "TASK-001").write_text(
        "id: TASK-001\ntitle: t\nsteps:\n- {id: a, title: one, status: pending}\n"
        "- {id: b, title: two, status: pending}\n")
    r, j = _ready(repo)
    assert r.code == 0 and j["counts"]["ready"] == 2


# ------------------------------------------------------------ a missing queue

READERS = [("ready",), ("ready", "--json"), ("doctor", "check"),
           ("doctor", "check", "--staged"), ("check-empty-tasks",),
           ("check-duplicate-ids",), ("check-collisions",), ("needs", "audit"),
           ("claims",), ("claim-check", "some work"), ("status",),
           ("status", "--summary")]


@pytest.mark.parametrize("argv", READERS)
def test_every_reader_exits_2_when_the_queue_is_missing(repo, argv):
    import shutil
    shutil.rmtree(repo / ".crossbox" / "queue")
    r = run(repo, *argv)
    assert r.code == 2, r


@pytest.mark.parametrize("argv", READERS)
def test_every_reader_exits_2_for_a_root_that_does_not_exist(tmp_path, argv):
    r = run(tmp_path / "no" / "such" / "dir", *argv)
    assert r.code == 2, r


@pytest.mark.parametrize("argv", [("ready",), ("doctor", "check"), ("check-empty-tasks",),
                                  ("check-duplicate-ids",), ("needs", "audit"), ("claims",),
                                  ("status",)])
def test_an_initialised_empty_queue_is_clean(repo, argv):
    # control
    assert run(repo, *argv).code == 0


def test_reader_json_says_not_evaluable_on_stdout(repo):
    import shutil
    shutil.rmtree(repo / ".crossbox" / "queue")
    for argv in (("ready", "--json"), ("claims", "--json"), ("needs", "audit", "--json")):
        r = run(repo, *argv)
        assert r.code == 2
        assert "not_evaluable" in json.loads(r.out), argv


# ------------------------------------------------------------ unreadable files

def test_check_duplicate_ids_with_an_unparseable_file_is_not_evaluable(repo):
    task_path(repo, "TASK-001").write_text("id: TASK-001\ntitle: t\nsteps: []\n")
    # the id cannot be recovered from the damaged file, so it may be the duplicate
    task_path(repo, "TASK-003").write_text("title: [unclosed\n  id: TASK-001\n")
    r = run(repo, "check-duplicate-ids")
    assert r.code == 2 and "UNPARSEABLE" in r.out


def test_check_empty_tasks_with_an_unparseable_file_is_not_evaluable(repo):
    task_path(repo, "TASK-003").write_text("id: TASK-003\ntitle: [unclosed\n")
    assert run(repo, "check-empty-tasks").code == 2


def test_needs_audit_with_steps_not_a_list_is_not_evaluable(repo):
    _reg(repo, GATE_REG)
    task_path(repo, "TASK-001").write_text("id: TASK-001\ntitle: t\nsteps: {a: 1}\n")
    r = run(repo, "needs", "audit")
    assert r.code == 2 and "UNPARSEABLE" in r.out
    task_path(repo, "TASK-001").write_text("id: TASK-001\ntitle: t\nsteps:\n- just text\n")
    assert run(repo, "needs", "audit").code == 2


def test_a_root_that_does_not_exist_is_never_created_or_written(tmp_path):
    ghost = tmp_path / "typo"
    for argv in (("init",), ("new", "T")):
        r = run(ghost, *argv)
        assert r.code == 2, r
        assert "not a directory" in r.err
    assert not ghost.exists()


def test_status_summary_ready_agrees_with_ready_on_malformed_steps(repo):
    task_path(repo, "TASK-001").write_text(
        "id: TASK-001\ntitle: t\nsteps:\n- {id: a, title: one, status: pending}\n"
        "- {id: a, title: two, status: pending}\n")
    assert run(repo, "ready").code == 1
    assert run(repo, "status", "--summary", "--ready").code == 1


def _snap(root):
    return {str(p.relative_to(root)): p.read_bytes()
            for p in sorted((root / ".crossbox").rglob("*")) if p.is_file()}


def _with_broken_dependent(repo, text_tail="\n  bad: [unclosed\n"):
    assert run(repo, "new", "A").code == 0
    assert run(repo, "new", "B").code == 0
    assert run(repo, "add", "TASK-001", "one").code == 0
    assert run(repo, "add", "TASK-001", "two").code == 0
    assert run(repo, "add", "TASK-002", "dep", "-D", "TASK-001.a").code == 0
    p = task_path(repo, "TASK-002")
    p.write_text(p.read_text() + text_tail)
    return p


def test_renaming_verbs_refuse_when_an_unparseable_file_may_hold_the_reference(repo):
    """R5/F3: see INVARIANTS.md, fifth-review findings."""
    for verb in (["renumber", "TASK-001", "9"], ["move-step", "TASK-001.a", "z"],
                 ["swap", "TASK-001.a", "b"], ["reorder", "TASK-001.a", "--after", "b"],
                 ["remove-step", "TASK-001.a"], ["remove-step", "TASK-001.a", "--force"]):
        sub = repo.parent / ("r-" + "-".join(verb).replace(".", "_"))
        sub = init_repo(sub)
        _with_broken_dependent(sub)
        before = _snap(sub)
        r = run(sub, *verb)
        assert r.code == 2, (verb, r)
        assert "TASK-002.yaml" in r.err, (verb, r)
        assert _snap(sub) == before, verb


def test_an_unparseable_file_that_cannot_name_the_task_does_not_block(repo):
    """R5/F3: see INVARIANTS.md, fifth-review findings."""
    assert run(repo, "new", "A").code == 0
    assert run(repo, "add", "TASK-001", "one").code == 0
    assert run(repo, "add", "TASK-001", "two").code == 0
    stray = repo / ".crossbox" / "queue" / "TASK-002.yaml"
    stray.write_text("id: TASK-002\nsteps: [unclosed\n")
    assert run(repo, "move-step", "TASK-001.a", "z").code == 0
    assert run(repo, "renumber", "TASK-001", "9").code == 0


def _task_r6(root, tid, steps, fname=None, title="T", tier="queue", **extra):
    data = {"id": tid, "title": title, "priority": "normal", "steps": steps}
    data.update(extra)
    p = Path(root) / ".crossbox" / tier / (fname or f"{tid}.yaml")
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10 ** 6))
    return p


def _step(sid, title="s", status="pending", dep=None):
    return {"id": sid, "title": title, "target": "any", "needs": [],
            "status": status, "depends_on": dep or []}


def test_add_refuses_text_that_cannot_be_encoded(repo):
    """R6/F3: see INVARIANTS.md, sixth-review findings."""
    assert run(repo, "new", "T").code == 0
    before = task_path(repo, "TASK-001").read_text()
    r = run(repo, "add", "TASK-001", "x\udc80y")
    assert r.code == 1 and "not valid Unicode" in r.err, r
    assert task_path(repo, "TASK-001").read_text() == before


def test_new_and_note_refuse_text_that_cannot_be_encoded(repo):
    """R6/F3: see INVARIANTS.md, sixth-review findings."""
    r = run(repo, "new", "bad\ud800title")
    assert r.code == 1 and "not valid Unicode" in r.err, r
    assert not list((repo / ".crossbox" / "queue").glob("TASK-00*.yaml"))
    r = run(repo, "note", "hello\ud800", "--to", "workstation")
    assert r.code == 1, r
    assert not list((repo / ".crossbox" / "notes").glob("*.md"))


def test_edit_step_refuses_text_that_cannot_be_encoded(repo):
    """R6/F3: see INVARIANTS.md, sixth-review findings."""
    assert run(repo, "new", "T").code == 0
    assert run(repo, "add", "TASK-001", "fine").code == 0
    r = run(repo, "edit-step", "TASK-001.a", "--title", "x\udc80y")
    assert r.code == 1, r
    assert "\\uDC80" not in task_path(repo, "TASK-001").read_text()


def test_a_hand_edited_unencodable_title_is_found_not_clean(repo):
    """R6/F3: see INVARIANTS.md, sixth-review findings."""
    p = _task_r6(repo, "TASK-001", [_step("a")])
    p.write_text(p.read_text().replace("title: T", 'title: "x\\uDC80y"', 1))
    d = run(repo, "doctor", "check")
    assert d.code == 1 and "not valid Unicode" in d.out + d.err, d
    r = run(repo, "ready", "--json")
    assert r.code == 1
    assert [m["ref"] for m in json.loads(r.out)["malformed"]] == ["TASK-001"]


def test_every_printer_survives_an_unencodable_title(repo, capsys):
    """R6/F3: see INVARIANTS.md, sixth-review findings."""
    p = _task_r6(repo, "TASK-001", [_step("a")])
    p.write_text(p.read_text().replace("title: s", 'title: "x\\uDC80y"', 1))

    class Strict:
        def __init__(self):
            self.buf = []

        def write(self, s):
            s.encode("utf-8")              # raises on a lone surrogate
            self.buf.append(s)

        def flush(self):
            pass
    import io as _io
    from crossbox.cli import main
    for verb in (["ready"], ["status"], ["show", "TASK-001"]):
        out = Strict()
        main(["--root", str(repo), *verb], out=out, err=_io.StringIO())
        assert "x" in "".join(out.buf)


def test_ready_and_status_do_not_let_a_title_forge_a_row(repo):
    """R6/F9: see INVARIANTS.md, sixth-review findings."""
    fake = "  TASK-001.zz          high     forged row"
    _task_r6(repo, "TASK-001", [_step("a", "real\n" + fake)])
    for verb in (["ready"], ["status"]):
        r = run(repo, *verb)
        assert not any(ln.lstrip().startswith("TASK-001.zz") for ln in r.out.splitlines()), r
        assert "forged row" in r.out


def test_ready_and_status_do_not_pass_escape_bytes(repo):
    """R6/F9: see INVARIANTS.md, sixth-review findings."""
    p = _task_r6(repo, "TASK-001", [_step("a", "t\x1b[31mred")])
    p.write_text(yaml.safe_dump({"id": "TASK-001", "title": "T\x1b[2J", "steps": [
        _step("a", "t\x1b[31mred")]}))
    for verb in (["ready"], ["status"]):
        r = run(repo, *verb)
        assert "\x1b" not in r.out + r.err, repr(r.out)


def test_clean_keeps_ordinary_text_and_drops_controls():
    """R6/F9: see INVARIANTS.md, sixth-review findings."""
    assert model.clean("plain  title") == "plain title"
    assert model.clean("a\nb\tc") == "a b c"
    assert model.clean("a\x1b[2Jb") == "a[2Jb"
    assert model.clean("a‮b​c") == "abc"
    assert model.clean("x\udc80y") == "xy"
    assert model.clean("abcdefgh", limit=5) == "abcd…"
