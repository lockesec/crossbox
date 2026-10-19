"""add, batch-add, edit, edit-step and the step-structure verbs (invariants 13-22)."""
import yaml
from conftest import run, task_path


def _task(repo, steps="", tid="TASK-A-001"):
    p = task_path(repo, tid)
    p.write_text(f"id: {tid}\ntitle: t\npriority: normal\nsteps:\n{steps}" if steps
                 else f"id: {tid}\ntitle: t\npriority: normal\nsteps: []\n")
    return p


def _steps(p):
    return yaml.safe_load(p.read_text())["steps"]


STEP = "- id: {i}\n  title: s{i}\n  target: any\n  status: {st}\n  depends_on: [{d}]\n"


def test_notes_over_existing_notes_refuse_without_replace(repo):
    # invariant 13
    p = _task(repo)
    run(repo, "add", "TASK-A-001", "x", "--notes", "first")
    r = run(repo, "edit-step", "TASK-A-001.a", "--notes", "second")
    assert r.code == 1 and "--replace-notes" in r.err
    assert _steps(p)[0]["notes"] == "first"
    assert run(repo, "edit-step", "TASK-A-001.a", "--notes", "second", "--replace-notes").code == 0
    assert _steps(p)[0]["notes"] == "second"


def test_append_notes_preserves_block_and_escaped_scalars(repo):
    # invariant 14
    p = task_path(repo, "TASK-A-001")
    p.write_text('id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n'
                 '  notes: "esc\\nline two\\ttab"\n- id: b\n  title: y\n  status: pending\n'
                 '  notes: |\n    block one\n    block two\n')
    assert run(repo, "es", "TASK-A-001.a", "--append-notes", "more").code == 0
    assert run(repo, "es", "TASK-A-001.b", "--append-notes", "more").code == 0
    s = _steps(p)
    assert s[0]["notes"] == "esc\nline two\ttab\n\nmore"
    assert s[1]["notes"] == "block one\nblock two\n\nmore"


def test_empty_or_whitespace_payloads_are_refused(repo):
    # invariant 15
    p = _task(repo)
    assert run(repo, "add", "TASK-A-001", "   ").code == 1
    assert run(repo, "add", "TASK-A-001", "ok").code == 0
    for flag in ("--title", "--notes", "--append-notes"):
        r = run(repo, "edit-step", "TASK-A-001.a", flag, " \n ")
        assert r.code == 1, (flag, r)
    assert run(repo, "add", "TASK-A-001", "x", "--notes", "  ").code == 1
    assert len(_steps(p)) == 1


def test_batch_escaped_pipe_overflow_and_apostrophes(repo, tmp_path):
    # invariant 17
    p = _task(repo)
    f = tmp_path / "rows.txt"
    f.write_text("# comment\n\nit's a \\| literal | any |  |  | O'Brien's note\n"
                 "second row\n")
    r = run(repo, "batch-add", "TASK-A-001", f)
    assert r.code == 0, r
    s = _steps(p)
    assert s[0]["title"] == "it's a | literal" and s[0]["notes"] == "O'Brien's note"
    f.write_text("fine\na | any | | | | normal | overflow\n")
    before = p.read_text()
    r = run(repo, "ba", "TASK-A-001", f)
    assert r.code == 1 and "columns" in r.err
    assert p.read_text() == before


def test_batch_reports_attempted_added_and_delta(repo, tmp_path):
    # invariant 18
    p = _task(repo)
    f = tmp_path / "rows.txt"
    f.write_text("one\ntwo | nosuchtarget\nthree\n")
    r = run(repo, "batch-add", "TASK-A-001", f)
    assert "Added 2 of 3 (yaml delta: 2)" in r.out
    assert r.code == 3
    f.write_text("bad | nosuchtarget\n")
    r = run(repo, "batch-add", "TASK-A-001", f)
    assert r.code == 1 and "Added 0 of 1" in r.out
    assert len(_steps(p)) == 2


def test_batch_jsonl_form_carries_multiline_notes(repo, tmp_path):
    p = _task(repo)
    f = tmp_path / "rows.jsonl"
    f.write_text('{"title": "a | b", "notes": "l1\\nl2", "needs": ["x:y"]}\n')
    assert run(repo, "batch-add", "TASK-A-001", f).code == 0
    s = _steps(p)[0]
    assert s["title"] == "a | b" and s["notes"] == "l1\nl2" and s["needs"] == ["x:y"]


def test_add_defaults_target_any_and_refuses_unknown(repo):
    # invariant 19
    p = _task(repo)
    assert run(repo, "add", "TASK-A-001", "x").code == 0
    assert _steps(p)[0]["target"] == "any"
    assert run(repo, "add", "TASK-A-001", "y", "-t", "gpu").code == 0
    r = run(repo, "add", "TASK-A-001", "z", "-t", "either")
    assert r.code == 1 and "unknown target" in r.err
    assert len(_steps(p)) == 2


def test_legacy_target_alias_is_read_but_not_written(tmp_path):
    from conftest import CONFIG, init_repo
    repo = init_repo(tmp_path / "r", CONFIG + "target_aliases:\n  either: any\n")
    p = _task(repo, STEP.format(i="a", st="pending", d="").replace("any", "either"))
    assert "READY" in run(repo, "ready").out and "TASK-A-001.a" in run(repo, "ready").out
    r = run(repo, "edit-step", "TASK-A-001.a", "--target", "either")
    assert r.code == 1 and "legacy" in r.err
    assert _steps(p)[0]["target"] == "either"


def test_self_dependency_refused_in_add_and_edit_step(repo):
    # invariant 20
    p = _task(repo)
    r = run(repo, "add", "TASK-A-001", "x", "-D", "a")
    assert r.code == 1 and "itself" in r.err
    run(repo, "add", "TASK-A-001", "x")
    for dep in ("a", "TASK-A-001.a"):
        r = run(repo, "edit-step", "TASK-A-001.a", "--depends", dep)
        assert r.code == 1 and "itself" in r.err
    assert _steps(p)[0]["depends_on"] == []


def test_depends_refuses_cycles_through_edited_step_only(repo):
    # invariant 21
    p = _task(repo, STEP.format(i="a", st="pending", d="b") + STEP.format(i="b", st="pending", d="a")
              + STEP.format(i="c", st="pending", d="") + STEP.format(i="d", st="pending", d="c"))
    r = run(repo, "edit-step", "TASK-A-001.c", "--depends", "d")
    assert r.code == 1 and "cycle" in r.err
    r = run(repo, "edit-step", "TASK-A-001.d", "--depends", "c")  # repair edit elsewhere
    assert r.code == 0 and "pre-existing cycle" in r.err
    r = run(repo, "edit-step", "TASK-A-001.a", "--depends", "")  # breaking the cycle works
    assert r.code == 0
    assert _steps(p)[0]["depends_on"] == []


def test_whole_task_deps_writable_only_when_task_exists(repo):
    # invariant 22
    _task(repo, STEP.format(i="a", st="pending", d=""))
    _task(repo, STEP.format(i="a", st="done", d=""), tid="TASK-B-001")
    arch = repo / ".crossbox" / "archive" / "TASK-C-001.yaml"
    arch.write_text("id: TASK-C-001\ntitle: c\nsteps:\n- id: a\n  title: x\n  status: done\n")
    assert run(repo, "es", "TASK-A-001.a", "--depends", "TASK-B-001,TASK-C-001").code == 0
    r = run(repo, "es", "TASK-A-001.a", "--depends", "TASK-Z-009")
    assert r.code == 1 and "no such task" in r.err
    r = run(repo, "add", "TASK-A-001", "x", "-D", "TASK-Z-009")
    assert r.code == 1
    r = run(repo, "add", "TASK-A-001", "x", "-D", "q")
    assert r.code == 1 and "no step" in r.err


def test_edit_task_fields_and_noop(repo):
    p = _task(repo)
    assert run(repo, "edit", "TASK-A-001", "-p", "high", "--phase", "two").code == 0
    d = yaml.safe_load(p.read_text())
    assert d["priority"] == "high" and d["phase"] == "two"
    r = run(repo, "edit", "TASK-A-001", "-p", "high")
    assert r.code == 0 and "no change" in r.out
    assert run(repo, "edit", "TASK-A-001", "-p", "urgent").code == 1


def test_remove_step_guards_and_force(repo):
    p = _task(repo, STEP.format(i="a", st="pending", d="") + STEP.format(i="b", st="pending", d="a")
              + STEP.format(i="c", st="done", d=""))
    assert run(repo, "remove-step", "TASK-A-001.a").code == 1
    assert run(repo, "rm-step", "TASK-A-001.c").code == 1
    assert run(repo, "remove-step", "TASK-A-001.a", "--force").code == 0
    s = _steps(p)
    assert [x["id"] for x in s] == ["b", "c"] and s[0]["depends_on"] == []


def test_swap_and_reorder_rewrite_deps_and_cross_task_refs(repo):
    p = _task(repo, STEP.format(i="a", st="pending", d="") + STEP.format(i="b", st="pending", d="a")
              + STEP.format(i="c", st="pending", d="b"))
    q = _task(repo, STEP.format(i="a", st="pending", d="TASK-A-001.c"), tid="TASK-B-001")
    assert run(repo, "swap", "TASK-A-001.a", "c").code == 0
    s = _steps(p)
    assert [x["id"] for x in s] == ["c", "b", "a"]
    assert s[1]["depends_on"] == ["c"] and s[2]["depends_on"] == ["b"]
    assert _steps(q)[0]["depends_on"] == ["TASK-A-001.a"]
    r = run(repo, "reorder", "TASK-A-001.a", "--to", "1")
    assert r.code == 0, r
    s = _steps(p)
    assert [x["id"] for x in s] == ["a", "b", "c"]
    # physical order after the swap was [c:sa, b:sb, a:sc]; moving a to the
    # front gives [sc, sa, sb], renumbered a, b, c, with deps following
    assert [x["title"] for x in s] == ["sc", "sa", "sb"]
    assert s[0]["depends_on"] == ["c"] and s[2]["depends_on"] == ["b"]
    assert _steps(q)[0]["depends_on"] == ["TASK-A-001.a"]


def test_reorder_noop_exits_zero_with_message(repo):
    _task(repo, STEP.format(i="a", st="pending", d="") + STEP.format(i="b", st="pending", d=""))
    r = run(repo, "ro", "TASK-A-001.a", "--before", "b")
    assert r.code == 0 and "no change" in r.out


def test_reorder_refuses_renaming_closed_steps_without_force(repo):
    _task(repo, STEP.format(i="a", st="done", d="") + STEP.format(i="b", st="pending", d=""))
    assert run(repo, "reorder", "TASK-A-001.b", "--to", "1").code == 1
    assert run(repo, "reorder", "TASK-A-001.b", "--to", "1", "--force").code == 0


def test_move_step_refuses_used_id_and_bad_grammar(repo):
    _task(repo, STEP.format(i="a", st="pending", d="") + STEP.format(i="b", st="pending", d=""))
    assert run(repo, "move-step", "TASK-A-001.a", "b").code == 1
    assert run(repo, "mv", "TASK-A-001.a", "B2").code == 64


def test_duplicate_step_ids_refused_by_structure_verbs(repo):
    _task(repo, STEP.format(i="a", st="pending", d="") + STEP.format(i="a", st="pending", d=""))
    for args in (("move-step", "TASK-A-001.a", "z"), ("swap", "TASK-A-001.a", "a"),
                 ("reorder", "TASK-A-001.a", "--to", "2"), ("add", "TASK-A-001", "x")):
        assert run(repo, *args).code == 1, args


def test_step_id_minting_is_bijective_base26(repo):
    from crossbox.ids import next_step_id, step_chr, step_ord
    assert [step_chr(n) for n in (1, 26, 27, 52, 53)] == ["a", "z", "aa", "az", "ba"]
    assert all(step_ord(step_chr(n)) == n for n in range(1, 2000))
    assert next_step_id(["a", "c", "1x"]) == "d"
    p = _task(repo)
    for _ in range(27):
        run(repo, "add", "TASK-A-001", "x")
    assert _steps(p)[-1]["id"] == "aa"
