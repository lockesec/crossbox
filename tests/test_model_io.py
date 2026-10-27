"""The one parser and the one canonical writer (invariants 1-12, 16)."""
import yaml
from conftest import run, task_path

from crossbox import model


def _mk(repo, text, tid="TASK-A-001"):
    p = task_path(repo, tid)
    p.write_text(text)
    return p


def test_one_parser_shared_by_readers_and_writers(repo):
    # invariant 1: show, done, add and doctor agree on the step list of one file
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n"
                  "  notes: |\n    - id: zz\n      status: pending\n")
    assert "  [ ] a  x" in run(repo, "show", "TASK-A-001").out
    assert run(repo, "add", "TASK-A-001", "second").out.strip() == "added TASK-A-001.b"
    data = yaml.safe_load(p.read_text())
    assert [s["id"] for s in data["steps"]] == ["a", "b"]


def test_step_markers_count_only_under_steps_key(repo):
    # invariant 2: a list with `- id:` items under another key is not steps
    _mk(repo, "id: TASK-A-001\ntitle: t\nlinks:\n- id: q\n  status: done\nsteps:\n"
              "- id: a\n  title: x\n  status: pending\n")
    r = run(repo, "add", "TASK-A-001", "next")
    assert r.out.strip() == "added TASK-A-001.b"


def test_decoy_ids_inside_block_and_quoted_scalars_are_content(repo):
    # invariant 3
    p = _mk(repo, 'id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n'
                  '  notes: |\n    example:\n    - id: q\n      status: done\n'
                  '- id: b\n  title: y\n  status: pending\n  notes: "multi\n    - id: r\n    line"\n')
    r = run(repo, "add", "TASK-A-001", "third")
    assert r.out.strip() == "added TASK-A-001.c"
    data = yaml.safe_load(p.read_text())
    assert [s["id"] for s in data["steps"]] == ["a", "b", "c"]
    assert "- id: q" in data["steps"][0]["notes"]


def test_single_quote_closed_only_by_unpaired_quote(repo):
    # invariant 4: '' inside a single-quoted scalar is an escaped quote
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: 'it''s\n"
                  "    - id: z\n    fine'\n  status: pending\n")
    assert run(repo, "add", "TASK-A-001", "next").code == 0
    data = yaml.safe_load(p.read_text())
    assert [s["id"] for s in data["steps"]] == ["a", "b"]
    assert data["steps"][0]["title"].startswith("it's")


def test_new_steps_insert_before_trailing_top_level_keys(repo):
    # invariant 5: keys after steps: stay top-level and are not absorbed
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n"
                  "bucket: later\nextra:\n  k: v\n")
    assert run(repo, "add", "TASK-A-001", "next").code == 0
    data = yaml.safe_load(p.read_text())
    assert data["bucket"] == "later" and data["extra"] == {"k": "v"}
    assert [s["id"] for s in data["steps"]] == ["a", "b"]


def test_mixed_indent_is_refused_by_every_writer(repo):
    # invariant 6: a mixed-indent file does not parse; writers refuse, doctor fixes
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n"
                  "  - id: b\n    title: y\n    status: pending\n")
    before = p.read_text()
    for args in (("add", "TASK-A-001", "c"), ("edit", "TASK-A-001", "--phase", "x"),
                 ("edit-step", "TASK-A-001.a", "--title", "z")):
        r = run(repo, *args)
        assert r.code == 1, r
        assert p.read_text() == before
    assert run(repo, "doctor", "fix", "TASK-A-001", "--write").code == 0
    assert [s["id"] for s in yaml.safe_load(p.read_text())["steps"]] == ["a", "b"]


def test_new_step_uses_one_consistent_indent(repo):
    # invariant 7: a file with indented markers is re-emitted at one indent
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n    - id: a\n      title: x\n"
                  "      status: pending\n")
    assert run(repo, "add", "TASK-A-001", "next").code == 0
    lines = [ln for ln in p.read_text().splitlines() if ln.lstrip().startswith("- id:")]
    assert len({len(ln) - len(ln.lstrip()) for ln in lines}) == 1


def test_round_trip_gate_writes_nothing_on_mismatch(repo, monkeypatch):
    # invariant 8
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps: []\n")
    before = p.read_text()
    real = model.render
    monkeypatch.setattr(model, "render", lambda d: real(d).replace("- id: a", "- id: q"))
    r = run(repo, "add", "TASK-A-001", "x")
    assert r.code == 1 and "round-trip gate" in r.err
    assert p.read_text() == before


def test_post_write_check_restores_original(repo, monkeypatch, tmp_path):
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps: []\n")
    before = p.read_text()
    real_write = model.atomic_write_text
    calls = []

    def bad_write(path, text):
        calls.append(1)
        real_write(path, text if len(calls) > 1 else "garbage: [\n")
    monkeypatch.setattr(model, "atomic_write_text", bad_write)
    r = run(repo, "add", "TASK-A-001", "x")
    assert r.code == 1 and "restored" in r.err
    assert p.read_text() == before


def test_field_edit_replaces_the_whole_construct(repo):
    # invariant 9: a multi-line notes block is replaced entirely
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n"
                  "  notes: |\n    old one\n    old two\n  depends_on:\n  - q\n")
    assert run(repo, "edit-step", "TASK-A-001.a", "--notes", "new", "--replace-notes").code == 0
    assert run(repo, "edit-step", "TASK-A-001.a", "--depends", "").code == 0
    text = p.read_text()
    assert "old" not in text and "- q" not in text
    s = yaml.safe_load(text)["steps"][0]
    assert s["notes"] == "new" and s["depends_on"] == []


def test_inline_trailing_comments_never_become_values(repo):
    # invariant 10
    _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: done  # finished\n"
              "- id: b\n  title: y\n  status: pending\n  depends_on: [a]  # after a\n")
    r = run(repo, "done", "TASK-A-001.b")
    assert r.code == 0, r


def test_block_and_inline_depends_both_parse_and_rewrite(repo):
    # invariant 11
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: pending\n"
                  "- id: b\n  title: y\n  status: pending\n  depends_on:\n  - a\n"
                  "- id: c\n  title: z\n  status: pending\n  depends_on: [a]\n")
    assert run(repo, "move-step", "TASK-A-001.a", "q").code == 0
    steps = yaml.safe_load(p.read_text())["steps"]
    assert steps[1]["depends_on"] == ["q"] and steps[2]["depends_on"] == ["q"]


def test_prose_with_colon_never_becomes_a_mapping(repo):
    # invariant 12
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps: []\n")
    # a multi-word need is refused outright (ready would call it malformed) ...
    before = p.read_text()
    assert run(repo, "add", "TASK-A-001", "x", "-n", "note: this").code == 1
    assert p.read_text() == before
    # ... and a colon token that is accepted stays a string, never a mapping
    assert run(repo, "add", "TASK-A-001", "x", "-n", "note:this").code == 0
    s = yaml.safe_load(p.read_text())["steps"][0]
    assert s["needs"] == ["note:this"]
    assert run(repo, "add", "TASK-A-001", "y", "--notes", "key: value").code == 0
    assert yaml.safe_load(p.read_text())["steps"][1]["notes"] == "key: value"


def test_multiline_notes_survive_add_and_edit(repo):
    # invariant 16 (open in the original design)
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nsteps: []\n")
    notes = "line one\n  indented two\n\nafter blank\n- id: z\nstatus: done\n"
    assert run(repo, "add", "TASK-A-001", "x", "--notes", notes).code == 0
    assert yaml.safe_load(p.read_text())["steps"][0]["notes"] == notes
    more = "second block\nwith `backticks` and $HOME"
    assert run(repo, "edit-step", "TASK-A-001.a", "--append-notes", more).code == 0
    assert yaml.safe_load(p.read_text())["steps"][0]["notes"] == notes.rstrip("\n") + "\n\n" + more


def test_unknown_keys_round_trip_in_order(repo):
    p = _mk(repo, "id: TASK-A-001\ntitle: t\nzeta: 1\nsteps:\n- id: a\n  title: x\n"
                  "  status: pending\n  custom: {k: [1, 2]}\nbucket: b\ntype: research\n"
                  "evidence:\n  - one\n")
    assert run(repo, "add", "TASK-A-001", "next").code == 0
    data = yaml.safe_load(p.read_text())
    assert data["zeta"] == 1 and data["bucket"] == "b" and data["type"] == "research"
    assert data["evidence"] == ["one"]
    assert data["steps"][0]["custom"] == {"k": [1, 2]}
    assert list(data)[-3:] == ["bucket", "type", "evidence"]


def test_comments_are_not_preserved_and_the_write_says_so(repo):
    p = _mk(repo, "# my comment\nid: TASK-A-001\ntitle: t\nsteps: []\n")
    r = run(repo, "add", "TASK-A-001", "x")
    assert r.code == 0 and "comment" in r.err
    assert "# my comment" not in p.read_text()


def test_canonical_emitter_never_folds_long_lines():
    long = "word " * 400
    text = model.render({"id": "T-1", "title": long.strip(), "steps": []})
    assert len(text.splitlines()) == 3


def test_an_atomic_write_syncs_the_directory_entry(tmp_path, monkeypatch):
    # review hardening: after os.replace the new directory entry is itself
    # flushed, so a power loss cannot leave the old name pointing at nothing
    seen = []
    monkeypatch.setattr(model, "fsync_dir", lambda d: seen.append(d))
    p = tmp_path / "sub" / "f.yaml"
    model.atomic_write_text(p, "a: 1\n")
    assert seen == [p.parent] and p.read_text() == "a: 1\n"


def test_fsync_dir_really_opens_and_syncs_on_posix(tmp_path):
    model.fsync_dir(tmp_path)          # must not raise on a real directory
