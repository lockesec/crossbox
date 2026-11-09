"""Gaps found by an independent verification pass (2026-10-31); each test failed
before its fix. Fake values are built from parts."""
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from conftest import git, run

from crossbox import gitutil, model, needs


def _queue(repo, tid, sids="abc", status="pending", title="t"):
    body = "".join(f"- id: {s}\n  title: step {s}\n  status: {status}\n" for s in sids)
    (repo / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: {title}\npriority: normal\nsteps:\n{body}")


def _ids(repo, tid):
    import yaml
    d = yaml.safe_load((repo / ".crossbox" / "queue" / f"{tid}.yaml").read_text())
    return [(s["id"], s["title"]) for s in d["steps"]]


# ------------------------------------------------ swap / reorder keep to one task

def test_swap_refuses_a_ref_into_another_task(repo):
    _queue(repo, "TASK-A-001")
    _queue(repo, "TASK-A-002", "a")
    before = _ids(repo, "TASK-A-001")
    r = run(repo, "swap", "TASK-A-001.a", "TASK-A-002.b")
    assert r.code != 0 and "TASK-A-002" in r.err, r
    assert _ids(repo, "TASK-A-001") == before


def test_swap_refuses_a_ref_into_a_task_that_does_not_exist(repo):
    _queue(repo, "TASK-A-001")
    before = _ids(repo, "TASK-A-001")
    r = run(repo, "swap", "TASK-A-001.a", "NOPE-999.b")
    assert r.code != 0, r
    assert _ids(repo, "TASK-A-001") == before


def test_swap_still_takes_a_bare_id_or_a_ref_in_the_same_task(repo):
    _queue(repo, "TASK-A-001")
    assert run(repo, "swap", "TASK-A-001.a", "b").code == 0
    assert run(repo, "swap", "TASK-A-001.a", "TASK-A-001.c").code == 0
    assert _ids(repo, "TASK-A-001") == [("b", "step a"), ("c", "step b"), ("a", "step c")]


def test_reorder_anchor_in_another_task_is_refused(repo):
    _queue(repo, "TASK-A-001")
    _queue(repo, "TASK-A-002", "a")
    before = _ids(repo, "TASK-A-001")
    for anchor in ("TASK-A-002.a", "NOPE-999.a"):
        r = run(repo, "reorder", "TASK-A-001.c", "--before", anchor)
        assert r.code != 0, r
    assert _ids(repo, "TASK-A-001") == before


# ------------------------------------------------ batch-done does not count no-ops

def test_batch_done_does_not_call_an_already_done_step_closed(repo):
    _queue(repo, "TASK-A-001", "ab")
    assert run(repo, "done", "TASK-A-001.a").code == 0
    r = run(repo, "batch-done", "TASK-A-001.a", "TASK-A-001.b")
    assert r.code == 0, r
    assert "closed 1 of 2" in r.out and "1 already done" in r.out, r.out
    r = run(repo, "batch-done", "TASK-A-001.a", "TASK-A-001.b")
    assert "closed 0 of 2" in r.out and "2 already done" in r.out, r.out


# ------------------------------------------------ a probe that forks is still cut off

def test_a_forking_probe_is_cut_off_at_the_timeout(repo, monkeypatch):
    monkeypatch.setattr(needs, "PROBE_TIMEOUT", 1.0)
    (repo / ".crossbox" / "needs-registry.yaml").write_text(
        "schema: 1\ntokens:\n  slow: {kind: gate, probe: \"sleep 30; true\"}\n")
    reg = needs.Registry.load(repo / ".crossbox" / "needs-registry.yaml")
    t0 = time.monotonic()
    state, cause = reg.gate_state("slow", repo)
    assert time.monotonic() - t0 < 10, "the probe's child held the pipe past the timeout"
    assert state == needs.NE and "timed out" in cause


# ------------------------------------------------ git output that is not UTF-8

def test_git_output_that_is_not_utf8_is_decoded_not_raised(repo):
    """git re-encodes a latin-1 commit subject to UTF-8 on output, but `git show
    REF:path` hands a non-UTF-8 file (a sibling branch's claims.yaml) back as it
    is; strict decoding raised UnicodeDecodeError, which no caller catches."""
    (repo / "legacy.txt").write_bytes(b"caf\xe9\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "legacy")
    cp = gitutil.git(repo, "show", "HEAD:legacy.txt")
    assert cp.returncode == 0 and cp.stdout.startswith("caf")
    assert gitutil.show(repo, "HEAD", "legacy.txt").startswith("caf")


# ------------------------------------------------ no escape sequence reaches the terminal

def test_claims_prints_no_control_character_from_a_claim_row(repo):
    esc = chr(27)
    (repo / ".crossbox" / "claims.yaml").write_text(
        "claims:\n- id: CLM-other-001\n  machine: other\n  slot: main\n"
        "  subject: \"work\\x1b[2Jxx\"\n  created: '2026-10-30'\n  status: active\n")
    r = run(repo, "claims")
    assert esc not in r.out and esc not in r.err
    assert "work[2Jxx" in r.out


# ------------------------------------------------ archive never drops a different task

def test_archive_keeps_an_archived_copy_with_the_same_title_but_other_content(repo):
    _queue(repo, "TASK-A-001", "ab", status="done", title="same")
    arch = repo / ".crossbox" / "archive"
    arch.mkdir(exist_ok=True)
    (arch / "TASK-A-001.yaml").write_text(
        "id: TASK-A-001\ntitle: same\npriority: normal\nsteps:\n"
        "- id: a\n  title: other work\n  status: done\n")
    kept = (arch / "TASK-A-001.yaml").read_text()
    r = run(repo, "archive")
    assert r.code != 0, r
    assert (arch / "TASK-A-001.yaml").read_text() == kept
    assert (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").exists()


def test_archive_still_replaces_an_identical_archived_copy(repo):
    _queue(repo, "TASK-A-001", "a", status="done", title="same")
    arch = repo / ".crossbox" / "archive"
    arch.mkdir(exist_ok=True)
    (arch / "TASK-A-001.yaml").write_text(
        (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").read_text())
    assert run(repo, "archive").code == 0
    assert not (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").exists()


def test_archive_summary_names_unparseable_queue_files(repo):
    (repo / ".crossbox" / "queue" / "TASK-A-009.yaml").write_text("id: [unclosed\n")
    r = run(repo, "archive")
    assert "1 of them do not parse" in r.out, r


# ------------------------------------------------ status --summary --ready gives the real cause

def test_status_summary_ready_names_the_claims_view_as_the_cause(repo):
    _queue(repo, "TASK-A-001", "a")
    (repo / ".crossbox" / "claims.yaml").write_text("[]\n")
    r = run(repo, "status", "--summary", "--ready", "--json")
    j = json.loads(r.out)
    assert r.code == 2 and j["ready"]["count"] is None
    assert "pass --ready" not in j["ready"]["cause"] and "claims" in j["ready"]["cause"]


# ------------------------------------------------ renumber gates every file before moving

def test_renumber_checks_every_file_before_the_first_move(repo, monkeypatch):
    _queue(repo, "TASK-A-001", "a")
    (repo / ".crossbox" / "queue" / "TASK-A-002.yaml").write_text(
        "id: TASK-A-002\ntitle: t\npriority: normal\nsteps:\n"
        "- id: a\n  title: s\n  status: pending\n  depends_on: [TASK-A-001.a]\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "tasks")
    real = model.check_task

    def failing(name, data, expected_ids=None):
        if name.startswith("TASK-A-002"):
            raise model.CrossboxError(f"{name}: injected round-trip failure")
        return real(name, data, expected_ids)
    monkeypatch.setattr(model, "check_task", failing)
    r = run(repo, "renumber", "TASK-A-001", "7")
    assert r.code != 0, r
    q = repo / ".crossbox" / "queue"
    assert (q / "TASK-A-001.yaml").exists() and not (q / "TASK-A-007.yaml").exists()


# ------------------------------------------------ reopen on an archived task says so

def test_reopen_on_an_archived_task_says_it_is_archived(repo):
    _queue(repo, "TASK-A-001", "a", status="done")
    assert run(repo, "archive").code == 0
    r = run(repo, "reopen", "TASK-A-001.a", "--reason", "redo")
    assert r.code == 0 and "[archived task]" in r.out and "unarchive" in r.err, r


# ------------------------------------------------ the queue guard denies `>&file`

def _guard():
    path = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "queue-guard.py"
    spec = importlib.util.spec_from_file_location("queue_guard_gaps", path)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    return g


def test_guard_denies_the_both_streams_redirect():
    g = _guard()
    for cmd in ("echo x >&.crossbox/claims.yaml", "echo x >& .crossbox/claims.yaml",
                "echo x >&\".crossbox/claims.yaml\"", "cd .crossbox && echo x >&claims.yaml"):
        assert g.redirects_into(cmd, ".crossbox"), cmd
    for cmd in ("make 2>&1 | tee /tmp/log", "echo x >&2", "ls .crossbox 2>&1",
                "echo x >&/tmp/out"):
        assert not g.redirects_into(cmd, ".crossbox"), cmd


# ------------------------------------------------ rulings from the verification pass

def _claims_file(repo, status="active", ref="TASK-A-001.a"):
    (repo / ".crossbox" / "claims.yaml").write_text(
        "claims:\n- id: CLM-1\n  subject: s\n  machine: other\n  slot: main\n"
        f"  created: 2026-10-31\n  status: {status}\n  step_ref: {ref}\n")


@pytest.mark.parametrize("status", ["held", "actve", "42"])
def test_an_unknown_claim_status_is_refused_not_read_as_unclaimed(repo, status):
    _queue(repo, "TASK-A-001", "a")
    _claims_file(repo, status)
    r = run(repo, "ready", "--json")
    assert r.code == 2 and "unknown status" in r.out + r.err


@pytest.mark.parametrize("status,ref", [("Active", "TASK-A-001.a"), ("ACTIVE ", "TASK-A-001.a"),
                                        ("active", "task-a-001.A")])
def test_claim_status_and_step_ref_match_in_any_case(repo, status, ref):
    _queue(repo, "TASK-A-001", "a")
    _claims_file(repo, f"'{status}'", ref)
    j = json.loads(run(repo, "ready", "--json").out)
    assert not [x for x in j["ready"] if x["ref"] == "TASK-A-001.a"]


def _gated(repo):
    (repo / ".crossbox" / "needs-registry.yaml").write_text(
        "tokens:\n  staging: {kind: gate}\n  ok-probe: {kind: gate, probe: 'true'}\n")
    (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").write_text(
        "id: TASK-A-001\ntitle: t\npriority: normal\nsteps:\n"
        "- id: a\n  title: x\n  status: pending\n  needs: [staging]\n"
        "- id: b\n  title: y\n  status: pending\n  needs: [ok-probe]\n"
        "- id: c\n  title: z\n  status: pending\n  needs: [mystery]\n")


def test_done_on_a_closed_gate_warns_and_still_closes(repo):
    _gated(repo)
    r = run(repo, "done", "TASK-A-001.a")
    assert r.code == 0 and "gate" in r.err and "staging" in r.err
    assert "status: done" in (repo / ".crossbox" / "queue" / "TASK-A-001.yaml").read_text()


def test_done_on_an_open_gate_says_nothing(repo):
    _gated(repo)
    r = run(repo, "done", "TASK-A-001.b")
    assert r.code == 0 and "gate" not in r.err


def test_done_on_an_unknown_need_warns(repo):
    _gated(repo)
    r = run(repo, "done", "TASK-A-001.c")
    assert r.code == 0 and "mystery" in r.err


@pytest.mark.parametrize("verb", [("move-step", "TASK-A-001.a", "z"), ("swap", "TASK-A-001.a", "b")])
def test_renaming_a_closed_step_needs_force(repo, verb):
    _queue(repo, "TASK-A-001", "ab", status="done")
    r = run(repo, *verb)
    assert r.code != 0 and "cited elsewhere" in r.err
    assert [i for i, _ in _ids(repo, "TASK-A-001")] == ["a", "b"]
    assert run(repo, *verb, "--force").code == 0


def test_renaming_an_open_step_needs_no_force(repo):
    _queue(repo, "TASK-A-001", "ab")
    assert run(repo, "swap", "TASK-A-001.a", "b").code == 0


def test_archive_with_only_unparseable_files_exits_1(repo):
    (repo / ".crossbox" / "queue" / "TASK-A-009.yaml").write_text("id: [unclosed\n")
    assert run(repo, "archive").code == 1


def test_archive_that_moved_something_past_an_unparseable_file_exits_3(repo):
    _queue(repo, "TASK-A-001", "a", status="done")
    (repo / ".crossbox" / "queue" / "TASK-A-009.yaml").write_text("id: [unclosed\n")
    assert run(repo, "archive").code == 3


def test_archive_with_nothing_wrong_exits_0(repo):
    _queue(repo, "TASK-A-001", "a", status="done")
    assert run(repo, "archive").code == 0
