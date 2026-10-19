"""doctor check / fix / selftest / installers (invariants 73-76)."""
import os
import stat
import sys

import yaml
from conftest import git, run, task_path

from crossbox import doctor
from pathlib import Path

MIXED = ("id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: one\n  status: pending\n"
         "  notes: keep-1\n  - id: b\n    title: two\n    status: pending\n")
DANGLING = ('id: TASK-A-002\ntitle: t\nsteps:\n- id: a\n  title: one\n  status: pending\n'
            '  notes: "first line"\n    keep-2 orphan\n- id: b\n  title: two\n  status: pending\n')
ABSORBED = ("id: TASK-A-003\ntitle: t\nsteps:\n- id: a\n  title: one\n  status: pending\n"
            "  notes: |\n    keep-3\n    - id: b\n      title: two\n      status: pending\n")


def _w(repo, tid, text):
    p = task_path(repo, tid)
    p.write_text(text)
    return p


def test_fixtures_reproduce_their_failure_classes():
    for bad in (MIXED, DANGLING):
        try:
            yaml.safe_load(bad)
            raise AssertionError("fixture parses")
        except yaml.YAMLError:
            pass
    assert doctor.find_absorbed(yaml.safe_load(ABSORBED))


def test_check_names_unparseable_files_and_classifies(repo):
    # invariant 73: unparseable files are named by readers that count
    _w(repo, "TASK-A-001", MIXED)
    _w(repo, "TASK-A-004", "id: TASK-A-004\nsteps: [\n")
    r = run(repo, "doctor", "check")
    assert r.code == 1
    assert "TASK-A-001.yaml: L" in r.out and "[fixable]" in r.out
    assert "TASK-A-004.yaml" in r.out and "[manual]" in r.out
    assert "2 of 2 file(s)" in r.out
    s = run(repo, "status")
    assert "TASK-A-001.yaml does not parse" in s.err and "2 unparseable" in s.out


def test_task_level_status_and_notes_text_never_flagged(repo):
    # invariant 74
    _w(repo, "TASK-A-001", "id: TASK-A-001\ntitle: t\nstatus: frozen\nsteps:\n- id: a\n"
                           "  title: x\n  status: pending\n  notes: 'status: bogus'\n")
    assert run(repo, "doctor", "check").code == 0
    assert run(repo, "doctor", "fix", "--write").code == 0
    assert yaml.safe_load(task_path(repo, "TASK-A-001").read_text())["status"] == "frozen"


def test_fix_refused_when_ids_or_key_shape_would_change(repo):
    # invariant 75
    bad = ("id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: one\n  status: pending\n"
           "  - id: b\n    title: two\nBad Key: 1\n")
    p = _w(repo, "TASK-A-001", bad)
    r = run(repo, "doctor", "fix", "TASK-A-001", "--write")
    assert r.code == 1 and "manual" in r.out
    assert p.read_text() == bad
    # a dedent that would eat text aborts the re-indent
    eats = ("id: T-1\ntitle: t\nsteps:\n- id: a\n  title: x\n- id: b\n  title: y\n"
            "    - id: c\n  title: z\n")
    assert doctor.fix_mixed_indent(eats) is None
    assert doctor.repair(eats, "2000-01-01")["classes"] == []


def test_absorbed_step_needs_step_shaped_id_and_status(repo):
    # invariant 76
    example = ("id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: one\n  status: pending\n"
               "  notes: |\n    yaml example:\n    - id: foo-bar\n      status: done\n"
               "    - id: q\n      title: no status line\n")
    _w(repo, "TASK-A-001", example)
    assert run(repo, "doctor", "check").code == 0
    _w(repo, "TASK-A-003", ABSORBED)
    r = run(repo, "doctor", "check")
    assert r.code == 1 and "absorbed-step" in r.out


def test_fix_repairs_each_class_with_backup(repo):
    for tid, text in (("TASK-A-001", MIXED), ("TASK-A-002", DANGLING), ("TASK-A-003", ABSORBED)):
        _w(repo, tid, text)
    r = run(repo, "doctor", "fix")
    assert "dry run" in r.out
    assert task_path(repo, "TASK-A-001").read_text() == MIXED
    r = run(repo, "doctor", "fix", "--write")
    assert r.code == 0, r
    for tid, keep in (("TASK-A-001", "keep-1"), ("TASK-A-002", "keep-2"), ("TASK-A-003", "keep-3")):
        text = task_path(repo, tid).read_text()
        d = yaml.safe_load(text)
        assert [s["id"] for s in d["steps"]] == ["a", "b"] and keep in text
    notes = yaml.safe_load(task_path(repo, "TASK-A-002").read_text())["steps"][0]["notes"]
    assert notes == "first line\nkeep-2 orphan\n"
    assert (repo / ".crossbox" / "queue" / ".doctor-bak" / "TASK-A-001.yaml.bak").read_text() == MIXED
    assert run(repo, "doctor", "check").code == 0


def test_fix_normalizes_unknown_status_with_provenance(repo):
    _w(repo, "TASK-A-001", "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n  status: wip\n")
    assert run(repo, "doctor", "check").code == 1
    assert run(repo, "doctor", "fix", "--write").code == 0
    s = yaml.safe_load(task_path(repo, "TASK-A-001").read_text())["steps"][0]
    assert s["status"] == "pending" and "'wip'" in s["notes"]


def test_fix_exit_codes(repo):
    assert run(repo, "doctor", "fix", "TASK-Z-001").code == 2
    _w(repo, "TASK-A-001", MIXED)
    _w(repo, "TASK-A-004", "id: TASK-A-004\nsteps: [\n")
    assert run(repo, "doctor", "fix", "--write").code == 3


def test_check_bad_deps_against_global_index(repo):
    _w(repo, "TASK-A-001", "id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: x\n"
                           "  status: pending\n  depends_on: [TASK-B-009.a, zz, 4]\n")
    r = run(repo, "doctor", "check")
    assert r.code == 1 and r.out.count("[manual]") == 3


def test_check_staged_reads_the_index_not_the_worktree(repo):
    p = _w(repo, "TASK-A-001", MIXED)
    git(repo, "add", str(p))
    p.write_text("id: TASK-A-001\ntitle: t\nsteps: []\n")         # clean on disk, broken staged
    assert run(repo, "doctor", "check", "--staged").code == 1
    git(repo, "add", str(p))
    assert run(repo, "doctor", "check", "--staged", "--quiet").code == 0


def test_selftest_passes():
    import io

    from crossbox.context import Ctx
    ctx = Ctx.__new__(Ctx)
    ctx.out, ctx.err = io.StringIO(), io.StringIO()
    assert doctor.cmd_selftest(ctx) == 0, ctx.out.getvalue()


def _cmd():
    return f"{sys.executable} -m crossbox"


def test_install_hook_works_in_a_linked_worktree_and_blocks_bad_commits(world, monkeypatch):
    # the hook installer must not assume .git is a directory
    src = str(__import__("pathlib").Path(__file__).resolve().parents[1] / "src")
    monkeypatch.setenv("PYTHONPATH", src)
    assert (world.wt1 / ".git").is_file()
    r = run(world.wt1, "doctor", "install-hook", "--command", _cmd())
    assert r.code == 0, r
    hookdir = git(world.wt1, "rev-parse", "--git-path", "hooks").stdout.strip()
    hook = (world.wt1 / hookdir).resolve() / "pre-commit"
    assert hook.exists() and os.stat(hook).st_mode & stat.S_IXUSR
    _w(world.wt1, "TASK-A-001", MIXED)
    git(world.wt1, "add", "-A")
    cp = git(world.wt1, "commit", "-q", "-m", "bad", check=False)
    assert cp.returncode != 0
    assert run(world.wt1, "doctor", "fix", "--write").code == 0
    git(world.wt1, "add", "-A")
    assert git(world.wt1, "commit", "-q", "-m", "good", check=False).returncode == 0


def test_install_hook_refuses_a_foreign_hook(repo):
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n")
    r = run(repo, "doctor", "install-hook")
    assert r.code == 1 and "refusing" in r.err
    assert (hooks / "pre-commit").read_text() == "#!/bin/sh\necho mine\n"


def test_install_merge_driver_verifies_with_check_attr(repo):
    (repo / ".gitattributes").write_text("")
    r = run(repo, "doctor", "install-merge-driver", "--command", _cmd())
    assert r.code == 0 and "verified" in r.out
    assert "merge-driver %O %A %B" in git(repo, "config", "merge.crossbox-claims.driver").stdout
    assert "merge=crossbox-claims" in (repo / ".gitattributes").read_text()


def test_selftest_covers_every_repair_class():
    import io
    from crossbox import doctor as D
    classes = {"mixed-indent", "dangling-notes", "absorbed-step", "unknown-status"}
    assert set(D.SELFTEST) == classes

    class _C:
        out = io.StringIO()

        def say(self, m=""):
            print(m, file=self.out)
    c = _C()
    assert D.cmd_selftest(c) == 0
    assert "4 of 4 passed" in c.out.getvalue()


def test_installed_command_is_absolute_and_quoted(monkeypatch):
    import shlex
    from crossbox import doctor as D
    monkeypatch.setattr(D.shutil, "which", lambda name: "/Users/Jane Doe/venv/bin/crossbox")
    cmd = D.crossbox_command()
    assert shlex.split(cmd) == ["/Users/Jane Doe/venv/bin/crossbox"]
    monkeypatch.setattr(D.shutil, "which", lambda name: None)
    monkeypatch.setattr(D.sys, "executable", "/opt/my python/bin/python3")
    assert shlex.split(D.crossbox_command()) == ["/opt/my python/bin/python3", "-m", "crossbox"]


def test_doctor_absorbed_scan_is_not_quadratic_on_hostile_notes(repo):
    """R5/doctor: Notes holding thousands of step-shaped lines and one unparseable tail
    used to cost one full YAML parse per line (2,000 lines: about 90 s), and
    `^\\s*status:` under re.M is quadratic on blank lines: a small task file
    could stall the pre-commit hook."""
    import time
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from crossbox import doctor
    notes = "x\n" + "- id: z\n  status: done\n" * 2000 + "  : : bad [\n" + "\n" * 50000
    data = {"id": "TASK-001", "steps": [{"id": "a", "status": "pending", "notes": notes}]}
    t = time.monotonic()
    assert doctor.find_absorbed(data) == []
    assert time.monotonic() - t < 5


def test_doctor_still_finds_a_swallowed_step_after_decoy_lines():
    """R5/doctor: see INVARIANTS.md, fifth-review findings."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from crossbox import doctor
    notes = "x\n- id: q\n  : broken [\nmore text\n- id: b\n  title: real\n  status: pending\n"
    data = {"id": "TASK-001", "steps": [{"id": "a", "status": "pending", "notes": notes}]}
    hits = doctor.find_absorbed(data)
    assert [h[3] for h in hits] == [["b"]]
