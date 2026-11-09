"""Notes, status, show, JSON surfaces and the command line (invariants 77-80)."""
import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import run, task_path

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _notes(root):
    d = root / ".crossbox" / "notes"
    return sorted(p.name for p in d.glob("*.md"))


def test_note_help_and_unknown_flags_write_nothing(world):
    # invariant 77
    for args in (("note", "--help"), ("note", "-x", "msg"), ("note", "--to", "workstation")):
        r = run(world.laptop, *args)
        assert r.code in (0, 64), (args, r)
        assert _notes(world.laptop) == []
    r = run(world.laptop, "note", "--to", "workstation", "--", "-leading dash is fine")
    assert r.code == 0 and _notes(world.laptop) == ["laptop-to-workstation.md"]
    assert "-leading dash is fine" in (world.laptop / ".crossbox/notes/laptop-to-workstation.md").read_text()


def test_note_refuses_unknown_or_own_slot(world):
    for to in ("nosuchbox", "laptop/main", "laptop/wt7", "laptop"):
        r = run(world.laptop, "note", "--to", to, "hello")
        assert r.code == 1, (to, r)
    assert _notes(world.laptop) == []
    assert run(world.laptop, "note", "--to", "laptop/wt1", "hello slot").code == 0
    assert _notes(world.laptop) == ["laptop-to-laptop+wt1.md"]


def test_notes_are_read_and_cleared_by_the_addressee(world):
    run(world.laptop, "note", "--to", "laptop/wt1", "one\nsecond line")
    run(world.laptop, "note", "--to", "laptop/wt1", "two")
    src = world.laptop / ".crossbox" / "notes" / "laptop-to-laptop+wt1.md"
    dst = world.wt1 / ".crossbox" / "notes" / src.name
    dst.write_text(src.read_text())
    text = dst.read_text()
    assert text.count("- **[") == 2 and "  second line" in text
    r = run(world.wt1, "notes")
    assert "two" in r.out and "1 inbox file(s)" in r.out
    assert "0 inbox file(s)" in run(world.laptop, "notes").out
    assert run(world.wt1, "clear-notes").code == 0
    assert any(n.startswith("archive-laptop-to-laptop+wt1-") for n in _notes(world.wt1))
    assert "0 inbox" in run(world.wt1, "notes").out


def test_default_recipient_is_the_only_other_machine(repo):
    assert run(repo, "note", "hi").code == 0
    assert _notes(repo) == ["laptop-to-workstation.md"]


def test_summary_json_is_clean_from_byte_zero(repo):
    # invariant 78
    task_path(repo, "TASK-A-001").write_text("id: TASK-A-001\ntitle: t\nsteps: [\n")
    for args in (("status", "--summary", "--json"), ("ready", "--json"), ("claims", "--json"),
                 ("check-duplicate-ids", "--json"), ("needs", "audit", "--json")):
        r = run(repo, *args)
        assert r.out.startswith("{"), (args, r)
        json.loads(r.out)


def test_show_truncates_but_full_shows_everything(repo):
    # invariant 79
    notes = "\n".join(f"line {i}" for i in range(6))
    run(repo, "new", "x", "-d", "a")
    run(repo, "add", "TASK-A-001", "s", "--notes", notes)
    r = run(repo, "show", "TASK-A-001")
    assert "line 5" not in r.out and "(4 more lines; --full shows all)" in r.out
    assert "line 5" in run(repo, "show", "TASK-A-001", "--full").out


def test_every_count_prints_its_denominator(repo):
    # invariant 80
    run(repo, "new", "x", "-d", "a")
    run(repo, "add", "TASK-A-001", "s")
    task_path(repo, "TASK-A-002").write_text("id: TASK-A-002\nsteps: [\n")
    r = run(repo, "status", "--summary")
    assert r.code == 1 and "1 parsed of 2 queue file(s)" in r.out and "floor" in r.out
    assert "ready here: unknown" in r.out
    j = json.loads(run(repo, "status", "--json").out)
    assert j["ready"]["count"] is None and j["ready"]["cause"]
    assert j["tasks"] == {"parsed": 1, "of_files": 2, "unparseable": ["TASK-A-002.yaml"]}
    j = json.loads(run(repo, "status", "--json", "--ready").out)
    assert j["ready"]["count"] == 1 and j["ready"]["of_open_here"] == 1
    assert "of 1 open step(s)" in run(repo, "ready").out
    assert "among 0 of" in run(repo, "check-collisions", "--against", "HEAD").out


def test_status_summary_absent_queue_is_not_evaluable(tmp_path):
    (tmp_path / "empty").mkdir()
    r = run(tmp_path / "empty", "status", "--summary")
    assert r.code == 2


def test_status_rejects_unknown_flags(repo):
    assert run(repo, "status", "--bogus").code == 64


def test_usage_errors_exit_64_not_2(repo):
    for args in (("done",), ("frobnicate",), ("add", "TASK-A-001"), ("reorder", "TASK-A-001.a")):
        assert run(repo, *args).code == 64, args


def test_new_validates_priority_and_domain(repo):
    assert run(repo, "new", "x", "-p", "urgent").code == 1
    assert run(repo, "new", "x", "-d", "a1").code == 64
    r = run(repo, "new", "  x\n  y ", "-d", "api", "-p", "high")
    assert r.code == 0
    text = task_path(repo, "TASK-API-001").read_text()
    assert "title: x y" in text and "created: 20" in text and "created_by: laptop" in text


def test_python_dash_m_entry_point(repo):
    env = dict(os.environ, PYTHONPATH=SRC)
    cp = subprocess.run([sys.executable, "-m", "crossbox", "--root", str(repo), "status",
                         "--summary", "--json"], capture_output=True, text=True, env=env)
    assert cp.returncode == 0, cp.stderr
    assert json.loads(cp.stdout)["tasks"]["of_files"] == 0
    cp = subprocess.run([sys.executable, "-m", "crossbox", "done"], capture_output=True,
                        text=True, env=env)
    assert cp.returncode == 64


def test_verbs_are_callable_in_process(repo):
    import io

    from crossbox import verbs
    from crossbox.context import Ctx
    out = io.StringIO()
    ctx = Ctx(root=str(repo), out=out, err=io.StringIO())
    assert verbs.cmd_new(ctx, "in process", "api") == 0
    assert verbs.cmd_add(ctx, "TASK-API-001", "first") == 0
    assert "added TASK-API-001.a" in out.getvalue()


def test_root_env_var(repo, monkeypatch, tmp_path):
    monkeypatch.setenv("CROSSBOX_ROOT", str(repo))
    monkeypatch.chdir(tmp_path)
    import io

    from crossbox.cli import main
    out = io.StringIO()
    assert main(["status", "--json"], out=out, err=io.StringIO()) == 0
    assert json.loads(out.getvalue())["tasks"]["of_files"] == 0


def test_slot_detection_names_worktree_and_main(world):
    from crossbox.config import detect_slot
    assert detect_slot(world.laptop) == "main"
    assert detect_slot(world.wt1) == "wt1"


def test_machine_identity_from_hostname_mapping(repo, monkeypatch):
    import socket

    from crossbox.config import detect_machine, load_config
    monkeypatch.delenv("CROSSBOX_MACHINE")
    (repo / ".crossbox" / "config.yaml").write_text(
        "machines:\n  workstation:\n    hostnames: [big-box]\n")
    monkeypatch.setattr(socket, "gethostname", lambda: "Big-Box.local")
    assert detect_machine(load_config(repo)) == "workstation"
    monkeypatch.setattr(socket, "gethostname", lambda: "other.example")
    unnamed = detect_machine(load_config(repo))
    assert unnamed.startswith("host-") and "other" not in unnamed      # never the host name


def test_an_unnamed_machine_is_a_stable_hash_and_writes_say_so(repo, monkeypatch):
    """Dogfooding this repository wrote the maintainer's host name into
    .crossbox/queue/ (`completed_by`, a skip note): an unconfigured machine fell back
    to the host name, and the queue is committed and pushed."""
    import socket

    from crossbox.config import detect_machine, load_config
    monkeypatch.delenv("CROSSBOX_MACHINE")
    monkeypatch.setattr(socket, "gethostname", lambda: "my-laptop")
    a = detect_machine(load_config(repo))
    assert a == detect_machine(load_config(repo)) and a.startswith("host-") and len(a) == 11
    monkeypatch.setattr(socket, "gethostname", lambda: "another-box")
    assert detect_machine(load_config(repo)) != a                       # still distinct per host
    monkeypatch.setattr(socket, "gethostname", lambda: "my-laptop")
    r = run(repo, "new", "T")
    assert r.code == 0 and "has no name in the config" in r.err and "my-laptop" not in r.err
    assert "my-laptop" not in "".join(p.read_text() for p in (repo / ".crossbox").rglob("*.yaml"))
    assert "has no name" not in run(repo, "status").err                 # reads stay quiet


def test_a_named_or_env_machine_is_used_and_writes_stay_quiet(repo, monkeypatch):
    import socket
    monkeypatch.setattr(socket, "gethostname", lambda: "my-laptop")
    monkeypatch.setenv("CROSSBOX_MACHINE", "laptop")
    r = run(repo, "new", "T")
    assert r.code == 0 and "has no name" not in r.err


def test_a_flag_abbreviation_is_a_usage_error_not_the_long_flag(repo):
    """R5/F4: see INVARIANTS.md, fifth-review findings."""
    assert run(repo, "new", "T").code == 0
    assert run(repo, "add", "TASK-001", "one").code == 0
    for args in (["remove-step", "TASK-001.a", "--for"],
                 ["remove-step", "TASK-001.a", "--forc"],
                 ["doctor", "fix", "--writ"],
                 ["doctor", "check", "--stag"],
                 ["status", "--summ"]):
        r = run(repo, *args)
        assert r.code == 64, (args, r)
    assert task_path(repo, "TASK-001").read_text().count("id: a") == 1
    # the full spelling still works
    assert run(repo, "remove-step", "TASK-001.a", "--force").code == 0


def test_an_abbreviated_top_level_flag_is_a_usage_error(repo):
    """R5/F4: see INVARIANTS.md, fifth-review findings."""
    out = run(repo, "--vers")
    assert out.code == 64, out


def test_demo_ignores_the_users_global_git_config(tmp_path, monkeypatch):
    """R5/F8: see INVARIANTS.md, fifth-review findings."""
    import shutil
    import subprocess
    if shutil.which("sh") is None:
        import pytest
        pytest.skip("no sh")
    hostile = tmp_path / "hostile-gitconfig"
    hostile.write_text("[commit]\n\tgpgsign = true\n[gpg]\n\tprogram = /bin/false\n"
                       "[init]\n\tdefaultBranch = nonsense\n")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "crossbox").write_text(f"#!/bin/sh\nexec {sys.executable} -m crossbox \"$@\"\n")
    (bindir / "crossbox").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("PYTHONPATH", SRC)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    demo = Path(__file__).resolve().parents[1] / "examples" / "demo.sh"
    cp = subprocess.run(["sh", str(demo)], capture_output=True, text=True, timeout=120)
    assert cp.returncode == 0, cp.stderr + cp.stdout[-400:]


def test_plain_status_with_an_unparseable_file_exits_1_like_summary(repo):
    """R5/F6: F6: the listing skipped the file and exited 0. It now matches
    `status --summary` and `ready`: exit 1, the listing marked as a floor."""
    assert run(repo, "new", "ok", "--domain", "A").code == 0
    (repo / ".crossbox" / "queue" / "TASK-A-002.yaml").write_text("id: TASK-A-002\nsteps: [\n")
    r = run(repo, "status")
    assert r.code == 1, r
    assert "floor" in r.out and "TASK-A-002.yaml" in r.err


def test_plain_status_on_a_clean_queue_exits_0(repo):
    """R5/F6: see INVARIANTS.md, fifth-review findings."""
    assert run(repo, "new", "ok", "--domain", "A").code == 0
    assert run(repo, "status").code == 0
