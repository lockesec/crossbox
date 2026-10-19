"""`crossbox closures`: is each closure the queue records true, read from git.

Every commit is a real one in a throwaway repository."""
import json
import os
import subprocess
import time

import pytest
from conftest import git, run

DAY = 86400


def _task(repo, sids="ab", status="pending", notes=None, tid="TASK-A-001", extra=""):
    body = ""
    for s in sids:
        body += f"- id: {s}\n  title: step {s}\n  status: {status}\n{extra}"
        if notes:
            body += f"  notes: |\n    {notes}\n"
    (repo / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: t\npriority: normal\nsteps:\n{body}")


def _commit(where, msg, ago=0, path="work.txt"):
    f = where / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text((f.read_text() if f.exists() else "") + msg + "\n")
    git(where, "add", "-A")
    when = f"@{int(time.time() - ago)} +0000"
    env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
    cp = subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(where), env=env,
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return git(where, "rev-parse", "HEAD").stdout.strip()


def _cl(repo, *args):
    r = run(repo, "closures", "--json", *args)
    return r, json.loads(r.out)


def _rows(j, cls):
    return j["classes"][cls]["rows"]


def _set_commits(repo, shas, sid="a", tid="TASK-A-001"):
    p = repo / ".crossbox" / "queue" / f"{tid}.yaml"
    txt = p.read_text().replace(f"- id: {sid}\n", f"- id: {sid}\n  commits: [{', '.join(shas)}]\n", 1)
    p.write_text(txt)


def _clean(repo):
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")


# ---------------------------------------------------------------- (a) unevidenced

def test_a_done_step_without_commits_is_unevidenced_with_its_denominator(repo):
    _task(repo, "ab", status="done")
    _set_commits(repo, ["deadbeef"], "b")
    _clean(repo)
    r, j = _cl(repo)
    a = j["classes"]["unevidenced"]
    assert [x["ref"] for x in a["rows"]] == ["TASK-A-001.a"]
    assert a["count"] == 1 and a["of"] == 2
    text = run(repo, "closures").out
    assert "unevidenced: 1 of 2 done step(s)" in text


def test_unevidenced_alone_is_informational_exit_0(repo):
    _task(repo, "a", status="done")
    _clean(repo)
    r, j = _cl(repo)
    assert r.code == 0, r
    assert j["classes"]["unevidenced"]["count"] == 1


# ---------------------------------------------------------------- (b) unreachable

def test_a_recorded_commit_not_in_main_is_unreachable_and_a_merged_one_is_not(repo):
    _task(repo, "ab", status="done")
    _clean(repo)
    good = _commit(repo, "TASK-A-001.a: merged work")
    git(repo, "checkout", "-q", "-b", "side")
    bad = _commit(repo, "TASK-A-001.b: side work", path="s.txt", ago=2 * DAY)
    git(repo, "checkout", "-q", "main")
    _set_commits(repo, [good], "a")
    _set_commits(repo, [bad], "b")
    _clean(repo)
    r, j = _cl(repo)
    rows = _rows(j, "unreachable")
    assert r.code == 1
    assert [(x["ref"], x["sha"], x["cause"]) for x in rows] == [
        ("TASK-A-001.b", bad, "UNMERGED")]
    assert rows[0]["age_seconds"] >= 2 * DAY - 60
    assert j["classes"]["unreachable"]["of"] == 2


def test_a_recorded_commit_that_no_longer_exists_is_unreachable(repo):
    _task(repo, "a", status="done")
    _set_commits(repo, ["1234567890abcdef1234567890abcdef12345678"])
    _clean(repo)
    r, j = _cl(repo)
    (row,) = _rows(j, "unreachable")
    assert row["cause"] == "MISSING" and r.code == 1


def test_all_reachable_is_clean_exit_0(repo):
    _task(repo, "a", status="done")
    _clean(repo)
    sha = _commit(repo, "TASK-A-001.a: work")
    _set_commits(repo, [sha])
    _clean(repo)
    r, j = _cl(repo)
    assert r.code == 0, r
    assert _rows(j, "unreachable") == []


# ---------------------------------------------------------------- (c) claimed closed but open

@pytest.mark.parametrize("subject", [
    "close TASK-A-001.a", "Closes TASK-A-001.a", "closed TASK-A-001.a: shipped",
    "done TASK-A-001.a", "fix TASK-A-001.a", "fixes TASK-A-001.a", "Fixed TASK-A-001.a",
    "finish TASK-A-001.a", "finishes TASK-A-001.a", "finished TASK-A-001.a",
    "complete TASK-A-001.a", "completes TASK-A-001.a", "completed TASK-A-001.a",
    "feat(x): close TASK-A-001.a", "docs: done TASK-A-001.a", "wip. Close TASK-A-001.a",
])
def test_a_subject_that_closes_an_open_step_is_flagged(repo, subject):
    _task(repo, "ab")
    _clean(repo)
    sha = _commit(repo, subject, ago=3600)
    r, j = _cl(repo)
    (row,) = _rows(j, "claimed_closed_but_open")
    assert row["ref"] == "TASK-A-001.a" and row["sha"] == sha and row["subject"] == subject
    assert r.code == 1


@pytest.mark.parametrize("subject", [
    "start TASK-A-001.a", "TASK-A-001.a: wip", "refactor around TASK-A-001.a",
    "close TASK-A-001.ab", "close the TASK-A-001.a gap", "enclose TASK-A-001.a",
    "prefix-closes TASK-A-001.a", "unfinished TASK-A-001.a", "TASK-A-001.a done",
])
def test_a_subject_that_only_mentions_the_step_is_not_flagged(repo, subject):
    _task(repo, "ab")
    _clean(repo)
    _commit(repo, subject)
    r, j = _cl(repo)
    assert _rows(j, "claimed_closed_but_open") == [], subject
    assert r.code == 0


def test_a_trailer_with_a_closing_verb_is_flagged_and_a_trailer_alone_is_not(repo):
    _task(repo, "ab")
    _clean(repo)
    sha = _commit(repo, "fix the parser\n\nCrossbox-Step: TASK-A-001.b")
    _commit(repo, "tidy\n\nCrossbox-Step: TASK-A-001.a", path="t.txt")
    r, j = _cl(repo)
    assert [(x["ref"], x["sha"]) for x in _rows(j, "claimed_closed_but_open")] == [
        ("TASK-A-001.b", sha)]


def test_a_closing_commit_for_a_done_step_is_not_flagged(repo):
    _task(repo, "a", status="done")
    _clean(repo)
    _commit(repo, "close TASK-A-001.a")
    r, j = _cl(repo)
    assert _rows(j, "claimed_closed_but_open") == []


def test_days_window_and_other_branches(repo):
    _task(repo, "ab")
    _clean(repo)
    git(repo, "checkout", "-q", "-b", "side")
    _commit(repo, "close TASK-A-001.a", ago=40 * DAY, path="o.txt")
    _commit(repo, "close TASK-A-001.b", ago=2 * DAY, path="n.txt")
    git(repo, "checkout", "-q", "main")
    r, j = _cl(repo)
    assert [x["ref"] for x in _rows(j, "claimed_closed_but_open")] == ["TASK-A-001.b"]
    r, j = _cl(repo, "--days", "60")
    assert sorted(x["ref"] for x in _rows(j, "claimed_closed_but_open")) == [
        "TASK-A-001.a", "TASK-A-001.b"]


# ---------------------------------------------------------------- (d) artifact missing

def test_a_cited_path_missing_at_head_is_flagged_and_an_existing_one_is_not(repo):
    _task(repo, "a", status="done",
          notes="added `src/x.py` and `docs/y.md`, see `README.md:3` and `https://a.b/c.md`; run `ls -la`")
    (repo / "src").mkdir()
    (repo / "src" / "x.py").write_text("x = 1\n")
    (repo / "README.md").write_text("r\n")
    _clean(repo)
    r, j = _cl(repo)
    rows = _rows(j, "artifact_missing")
    assert [(x["ref"], x["path"]) for x in rows] == [("TASK-A-001.a", "docs/y.md")]
    assert r.code == 1
    assert j["classes"]["artifact_missing"]["of"] == 3


def test_open_steps_notes_are_not_checked_for_artifacts(repo):
    _task(repo, "a", status="pending", notes="will add `docs/later.md`")
    _clean(repo)
    r, j = _cl(repo)
    assert _rows(j, "artifact_missing") == [] and r.code == 0


# ---------------------------------------------------------------- (e) unmerged branches

def _with_origin(repo, tmp_path):
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "main")
    return origin


def test_unmerged_remote_branches_list_only_those_naming_a_step_plus_a_total(repo, tmp_path):
    _task(repo, "ab")
    _clean(repo)
    _with_origin(repo, tmp_path)
    git(repo, "checkout", "-q", "-b", "feat/TASK-A-001.a-thing")
    _commit(repo, "one", path="1.txt", ago=3 * DAY)
    _commit(repo, "two", path="2.txt", ago=3 * DAY)
    git(repo, "push", "-q", "origin", "feat/TASK-A-001.a-thing")
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "bysubject")
    _commit(repo, "TASK-A-001.b: work", path="3.txt", ago=2 * DAY)
    git(repo, "push", "-q", "origin", "bysubject")
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "unrelated")
    _commit(repo, "nothing", path="4.txt")
    git(repo, "push", "-q", "origin", "unrelated")
    git(repo, "checkout", "-q", "main")
    git(repo, "fetch", "-q", "origin")
    r, j = _cl(repo)
    e = j["classes"]["unmerged_branches"]
    rows = {x["branch"]: x for x in e["rows"]}
    assert set(rows) == {"origin/feat/TASK-A-001.a-thing", "origin/bysubject"}
    assert rows["origin/feat/TASK-A-001.a-thing"]["commits"] == 2
    assert rows["origin/feat/TASK-A-001.a-thing"]["age_seconds"] >= 3 * DAY - 60
    assert "TASK-A-001.a" in rows["origin/feat/TASK-A-001.a-thing"]["refs"]
    assert rows["origin/bysubject"]["refs"] == ["TASK-A-001.b"]
    assert e["of"] == 3 and e["count"] == 2
    assert r.code == 1


def test_a_merged_remote_branch_and_head_are_not_unmerged(repo, tmp_path):
    _task(repo, "a")
    _clean(repo)
    _with_origin(repo, tmp_path)
    git(repo, "push", "-q", "origin", "main:feat/TASK-A-001.a-done")
    git(repo, "fetch", "-q", "origin")
    r, j = _cl(repo)
    e = j["classes"]["unmerged_branches"]
    assert e["rows"] == [] and e["of"] == 0 and r.code == 0


# ---------------------------------------------------------------- not evaluable

def test_no_baseline_names_the_classes_that_could_not_run_and_reports_the_rest(tmp_path):
    from conftest import init_repo
    r0 = tmp_path / "nb"
    r0.mkdir()
    git(r0, "init", "-q", "-b", "trunk")
    assert run(r0, "init").code == 0
    _task(r0, "a", status="done")
    git(r0, "add", "-A")
    git(r0, "commit", "-q", "-m", "queue")
    r, j = _cl(r0)
    assert r.code == 2
    assert j["classes"]["unreachable"]["evaluable"] is False
    assert j["classes"]["unmerged_branches"]["evaluable"] is False
    assert "no upstream and no main or master" in j["classes"]["unreachable"]["cause"]
    assert j["classes"]["unevidenced"]["evaluable"] is True
    assert j["classes"]["unevidenced"]["count"] == 1
    assert j["classes"]["claimed_closed_but_open"]["evaluable"] is True
    text = run(r0, "closures").out
    assert "NOT EVALUABLE" in text and "unreachable" in text


def test_outside_git_only_the_queue_class_runs(plain):
    _task(plain, "a", status="done")
    r, j = _cl(plain)
    assert r.code == 2
    assert j["classes"]["unevidenced"]["evaluable"] is True
    for c in ("unreachable", "claimed_closed_but_open", "artifact_missing", "unmerged_branches"):
        assert j["classes"][c]["evaluable"] is False and "git" in j["classes"][c]["cause"]


def test_a_missing_queue_is_not_evaluable(tmp_path):
    p = tmp_path / "noq"
    p.mkdir()
    git(p, "init", "-q", "-b", "main")
    r = run(p, "closures")
    assert r.code == 2 and "NOT EVALUABLE" in r.err


# ---------------------------------------------------------------- read only, text, days

def _snapshot(repo):
    files = {str(p.relative_to(repo)): p.read_bytes()
             for p in repo.rglob("*") if p.is_file() and ".git/" not in str(p) + "/"
             and ".git" not in p.relative_to(repo).parts}
    refs = git(repo, "for-each-ref").stdout
    return files, refs


def test_closures_is_read_only(repo):
    _task(repo, "ab", status="done", notes="`docs/gone.md`")
    _clean(repo)
    _commit(repo, "close TASK-A-001.b")
    before = _snapshot(repo)
    run(repo, "closures")
    run(repo, "closures", "--json", "--days", "5")
    assert _snapshot(repo) == before


def test_bad_days_is_a_usage_error(repo):
    _task(repo, "a")
    assert run(repo, "closures", "--days", "0").code == 64


def test_text_output_prints_counts_beside_denominators_and_the_commit(repo):
    _task(repo, "ab")
    _clean(repo)
    sha = _commit(repo, "close TASK-A-001.a", ago=7200)
    out = run(repo, "closures").out
    assert "claimed closed but open: 1 of 2 open step(s)" in out
    assert sha[:7] in out and "close TASK-A-001.a" in out and "2h ago" in out


def test_origin_head_pointing_at_an_unmerged_branch_is_not_a_branch(repo, tmp_path):
    _task(repo, "a")
    cfg = repo / ".crossbox" / "config.yaml"
    cfg.write_text(cfg.read_text() + "upstream: origin/main\n")
    _clean(repo)
    _with_origin(repo, tmp_path)
    git(repo, "checkout", "-q", "-b", "other")
    _commit(repo, "TASK-A-001.a: work", path="o.txt")
    git(repo, "push", "-q", "origin", "other")
    git(repo, "checkout", "-q", "main")
    git(repo, "fetch", "-q", "origin")
    git(repo, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/other")
    r, j = _cl(repo)
    assert [x["branch"] for x in _rows(j, "unmerged_branches")] == ["origin/other"]
    assert j["classes"]["unmerged_branches"]["remote_branches"] == 1


def test_a_trailer_without_a_closing_verb_never_closes():
    from crossbox.closures import subject_closes
    assert not subject_closes("tidy", "TASK-A-001.a", ["TASK-A-001.a"])
    assert subject_closes("fix tidy", "TASK-A-001.a", ["TASK-A-001.a"])


def test_the_baseline_itself_is_never_listed_even_when_ahead_of_local_main(repo, tmp_path):
    _task(repo, "a")
    cfg = repo / ".crossbox" / "config.yaml"
    cfg.write_text(cfg.read_text() + "upstream: origin/main\n")
    _clean(repo)
    _with_origin(repo, tmp_path)
    _commit(repo, "TASK-A-001.a: ahead", path="a.txt")
    git(repo, "push", "-q", "origin", "main")
    git(repo, "reset", "-q", "--hard", "HEAD~1")
    r, j = _cl(repo)
    e = j["classes"]["unmerged_branches"]
    assert j["baseline"] == "origin/main"
    assert e["rows"] == [] and e["of"] == 0 and e["remote_branches"] == 0


def test_prose_with_a_slash_is_not_a_cited_path():
    from crossbox.closures import cited_paths
    assert cited_paths("use `and/or` here; `read/write` access; see `src/crossbox/why.py` and `docs/`") == \
        ["src/crossbox/why.py", "docs/"]


def _commit_r7(repo, msg, ago=0):
    when = f"@{int(time.time() - ago)} +0000"
    env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
    cp = subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", msg], cwd=str(repo), env=env,
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr


def _task_r7(repo, steps, tid="TASK-A-001"):
    body = "".join(f"- id: {sid}\n  title: step {sid}\n  status: {st}\n{extra}" for sid, st, extra in steps)
    (repo / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: t\npriority: normal\nsteps:\n{body}")


def test_a_commit_with_an_older_date_does_not_hide_recent_closers(repo):
    """R7: see INVARIANTS.md, seventh-review findings."""
    _task_r7(repo, [("e", "pending", "")])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")
    _commit_r7(repo, "close TASK-A-001.e")
    _commit_r7(repo, "skewed-date commit", ago=40 * 86400)          # a wrong clock, or a rebase
    j = json.loads(run(repo, "closures", "--json").out)
    rows = j["classes"]["claimed_closed_but_open"]["rows"]
    assert [r["ref"] for r in rows] == ["TASK-A-001.e"]


def test_a_shallow_clone_cannot_judge_a_recorded_commit_it_does_not_have(tmp_path, world):
    """R7: see INVARIANTS.md, seventh-review findings."""
    w = world
    _task_r7(w.laptop, [("a", "pending", "")])
    w.commit(w.laptop, "queue")
    _commit_r7(w.laptop, "TASK-A-001.a: the work")
    sha = git(w.laptop, "rev-parse", "HEAD").stdout.strip()
    assert run(w.laptop, "done", "TASK-A-001.a", "--commit", sha).code == 0
    w.commit(w.laptop, "close a")
    for i in range(3):
        _commit_r7(w.laptop, f"later {i}")
    git(w.laptop, "push", "-q", "origin", "HEAD")
    sh = tmp_path / "shallow"
    git(tmp_path, "clone", "-q", "--depth", "1", f"file://{w.origin}", str(sh))
    r = run(sh, "closures", "--json")
    j = json.loads(r.out)
    un = j["classes"]["unreachable"]
    assert un["evaluable"] is False and "shallow" in un["cause"]
    assert r.code == 2


@pytest.mark.parametrize("verb", ["lint", "closures"])
def test_an_unreadable_task_file_is_named_and_the_run_is_not_clean(repo, verb):
    """R7: see INVARIANTS.md, seventh-review findings."""
    _task_r7(repo, [("a", "pending", "")])
    (repo / ".crossbox" / "queue" / "TASK-Z-009.yaml").write_text("steps: [ {{{ broken\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "q")
    r = run(repo, verb)
    assert r.code == 2
    assert "TASK-Z-009" in r.out and "unreadable" in r.out.lower()


def test_a_bare_file_name_is_present_when_any_tracked_file_has_that_name(repo):
    """R7: see INVARIANTS.md, seventh-review findings."""
    (repo / "engine").mkdir()
    (repo / "engine" / "backtest.py").write_text("x\n")
    _task_r7(repo, [("a", "done", '  notes: "fixed `backtest.py` and `gone.py`"\n')])
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "q")
    rows = json.loads(run(repo, "closures", "--json").out)["classes"]["artifact_missing"]["rows"]
    assert [r["path"] for r in rows] == ["gone.py"]
