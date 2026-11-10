"""`crossbox activity`: claim liveness read from git, and work nobody claimed.

Every commit here is real, dated with GIT_COMMITTER_DATE relative to now."""
import json
import os
import time

from conftest import git, run

HOUR = 3600
DAY = 24 * HOUR


def _task(repo, tid, sids):
    body = "".join(f"- id: {s}\n  title: step {s}\n  status: pending\n" for s in sids)
    (repo / ".crossbox" / "queue" / f"{tid}.yaml").write_text(
        f"id: {tid}\ntitle: t\npriority: normal\nsteps:\n{body}")


def _same_day(ago):
    """A claim is dated by its local calendar day, and activity counts from that day's local
    midnight. A commit "an hour ago" made in the first hour after midnight would land on the
    day before the claim and not count, so a same-day age is kept inside the current local
    day. Older ages (a day or more) are left alone: those tests age the claim too."""
    if ago >= DAY:
        return ago
    import datetime as dt
    since_midnight = time.time() - dt.datetime.combine(dt.date.today(), dt.time()).timestamp()
    return min(ago, max(0, int(since_midnight) - 1))


def _commit(where, msg, ago=0, path="work.txt"):
    """Commit `msg` dated `ago` seconds back (kept on today's UTC day); returns the age used."""
    ago = _same_day(ago)
    f = where / path
    f.write_text((f.read_text() if f.exists() else "") + msg + "\n")
    git(where, "add", "-A")
    when = f"@{int(time.time() - ago)} +0000"
    env = dict(os.environ, GIT_COMMITTER_DATE=when, GIT_AUTHOR_DATE=when)
    import subprocess
    cp = subprocess.run(["git", "commit", "-q", "-m", msg], cwd=str(where), env=env,
                        capture_output=True, text=True)
    assert cp.returncode == 0, cp.stderr
    return ago


def _age_claim(repo, days):
    """Rewrite every claim's created date to `days` ago (claims are date-stamped)."""
    import datetime as dt
    p = repo / ".crossbox" / "claims.yaml"
    old = dt.date.today().isoformat()
    new = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    p.write_text(p.read_text().replace(old, new))


def _act(repo, *args):
    r = run(repo, "activity", "--json", *args)
    return r, json.loads(r.out)


def _state(j, ref):
    (c,) = [c for c in j["claims"] if c["step_ref"] == ref]
    return c


def test_a_commit_naming_the_step_makes_its_claim_hot(repo):
    _task(repo, "TASK-A-001", "ab")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _commit(repo, "TASK-A-001.a: wire the endpoint", ago=2 * HOUR)
    r, j = _act(repo)
    assert r.code == 0, r
    c = _state(j, "TASK-A-001.a")
    assert c["state"] == "HOT"
    assert c["commits"] == 1 and c["signals"] == ["subject"]
    text = run(repo, "activity").out
    assert text.splitlines()[0].startswith(
        "activity: hot 1 · quiet 0 · cold 0 · unknown 0 of 1 active claim(s)")


def test_a_trailer_counts_and_a_longer_ref_does_not(repo):
    _task(repo, "TASK-A-001", ["a", "ab"])
    assert run(repo, "claim", "x", "--step", "TASK-A-001.a", "--step", "TASK-A-001.ab").code == 0
    _commit(repo, "refactor the parser\n\nCrossbox-Step: TASK-A-001.a", ago=HOUR)
    _commit(repo, "finish TASK-A-001.ab today", ago=HOUR)
    _, j = _act(repo)
    a, ab = _state(j, "TASK-A-001.a"), _state(j, "TASK-A-001.ab")
    assert a["state"] == "HOT" and a["signals"] == ["trailer"] and a["commits"] == 1
    assert ab["state"] == "HOT" and ab["commits"] == 1


def test_punctuation_after_the_ref_still_matches(repo):
    _task(repo, "TASK-A-001", "a")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _commit(repo, "fix (TASK-A-001.a), then tests", ago=HOUR)
    _, j = _act(repo)
    assert _state(j, "TASK-A-001.a")["state"] == "HOT"


def test_no_commit_on_an_old_claim_is_cold_and_says_what_was_not_seen(repo):
    _task(repo, "TASK-A-001", "a")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _age_claim(repo, 10)
    r, j = _act(repo)
    c = _state(j, "TASK-A-001.a")
    assert c["state"] == "COLD" and c["commits"] == 0
    assert "no commit naming the step seen" in run(repo, "activity").out
    assert r.code == 0


def test_a_fresh_claim_without_commits_is_quiet_not_cold(repo):
    _task(repo, "TASK-A-001", "a")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _, j = _act(repo)
    assert _state(j, "TASK-A-001.a")["state"] == "QUIET"


def test_old_evidence_inside_the_stale_window_is_quiet(repo):
    _task(repo, "TASK-A-001", "a")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _age_claim(repo, 5)
    _commit(repo, "TASK-A-001.a: first cut", ago=2 * DAY)
    _, j = _act(repo)
    c = _state(j, "TASK-A-001.a")
    assert c["state"] == "QUIET" and c["commits"] == 1


def test_a_commit_before_the_claim_was_made_does_not_count(repo):
    _task(repo, "TASK-A-001", "a")
    _commit(repo, "TASK-A-001.a: someone else, long ago", ago=20 * DAY)
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _age_claim(repo, 10)
    _, j = _act(repo)
    c = _state(j, "TASK-A-001.a")
    assert c["state"] == "COLD" and c["commits"] == 0


def test_a_branch_named_for_the_step_counts(repo):
    _task(repo, "TASK-A-001", "a")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")
    git(repo, "branch", "work/TASK-A-001.a")
    _, j = _act(repo)
    c = _state(j, "TASK-A-001.a")
    assert c["state"] == "HOT" and "branch" in c["signals"]


def test_unpushed_commits_in_a_sibling_worktree_are_seen(world):
    _task(world.laptop, "TASK-A-001", "a")
    world.commit(world.laptop, "queue")
    git(world.wt1, "merge", "-q", "main")
    os.environ["CROSSBOX_SLOT"] = "wt1"
    try:
        assert run(world.wt1, "claim", "TASK-A-001.a").code == 0
    finally:
        os.environ.pop("CROSSBOX_SLOT")
    _commit(world.wt1, "TASK-A-001.a: halfway", ago=HOUR)
    _, j = _act(world.laptop)
    c = _state(j, "TASK-A-001.a")
    assert c["state"] == "HOT" and c["last_ref"] == "wt1"


def test_a_subject_only_claim_is_unknown_not_cold(repo):
    _task(repo, "TASK-A-001", "a")
    assert run(repo, "claim", "looking into the flaky build").code == 0
    _age_claim(repo, 10)
    _, j = _act(repo)
    (c,) = j["claims"]
    assert c["state"] == "UNKNOWN" and "no step" in c["detail"]


def test_outside_git_every_claim_is_unknown_and_the_run_is_not_evaluable(plain):
    _task(plain, "TASK-A-001", "a")
    assert run(plain, "claim", "TASK-A-001.a").code == 0
    r, j = _act(plain)
    assert r.code == 2
    assert [c["state"] for c in j["claims"]] == ["UNKNOWN"]


def test_unclaimed_work_is_listed_and_closed_steps_are_not(repo):
    _task(repo, "TASK-A-001", "abc")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    _commit(repo, "TASK-A-001.b: started without claiming", ago=HOUR)
    _commit(repo, "TASK-A-001.c: done here", ago=HOUR)
    assert run(repo, "done", "TASK-A-001.c").code == 0
    _, j = _act(repo)
    assert [u["ref"] for u in j["unclaimed"]] == ["TASK-A-001.b"]
    assert j["unclaimed"][0]["commits"] == 1
    assert "TASK-A-001.b" in run(repo, "activity").out


def test_activity_is_read_only(repo):
    from crossbox import cli
    assert "activity" in cli.READ_ONLY
    _task(repo, "TASK-A-001", "a")
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")
    assert run(repo, "activity").code == 0
    assert git(repo, "status", "--porcelain").stdout == ""


def _claimed(repo, ref="TASK-A-001.a", sids="ab"):
    _task(repo, "TASK-A-001", sids)
    assert run(repo, "claim", ref).code == 0
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "queue")


def test_a_branch_for_a_longer_step_id_is_not_evidence_for_the_shorter(repo):
    _claimed(repo)
    git(repo, "branch", "feat/TASK-A-001.ab-thing")
    _, j = _act(repo)
    c = _state(j, "TASK-A-001.a")
    assert "branch" not in c["signals"] and c["commits"] == 0
    assert c["state"] == "QUIET" and c["last"] is None


def test_branch_names_match_as_whole_tokens_case_insensitively(repo):
    _claimed(repo)
    for name in ("feat/TASK-A-001.a-thing", "TASK-A-001.a", "work/task-a-001.A_wip"):
        git(repo, "branch", name)
        _, j = _act(repo)
        assert "branch" in _state(j, "TASK-A-001.a")["signals"], name
        git(repo, "branch", "-q", "-D", name)


def test_a_branch_is_dated_by_its_own_creation_not_its_tip_commit(repo):
    _task(repo, "TASK-A-001", "a")
    git(repo, "add", "-A")
    _commit(repo, "queue", ago=10 * DAY)           # the tip is ten days old
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    git(repo, "branch", "work/TASK-A-001.a")        # the branch is made just now
    _, j = _act(repo)
    c = _state(j, "TASK-A-001.a")
    assert c["state"] == "HOT" and c["signals"] == ["branch"]
    assert c["last"] > time.time() - HOUR


def test_a_branch_without_a_reflog_falls_back_to_its_tip_date(repo):
    _task(repo, "TASK-A-001", "a")
    git(repo, "add", "-A")
    ago = _commit(repo, "queue", ago=2 * HOUR)
    assert run(repo, "claim", "TASK-A-001.a").code == 0
    git(repo, "config", "core.logAllRefUpdates", "false")
    git(repo, "branch", "work/TASK-A-001.a")
    assert git(repo, "reflog", "show", "work/TASK-A-001.a").stdout.strip() == ""
    _, j = _act(repo)
    c = _state(j, "TASK-A-001.a")
    assert "branch" in c["signals"]
    assert ago - 120 <= time.time() - c["last"] <= ago + 600


def test_a_repo_with_no_commits_says_so_and_is_not_evaluable(tmp_path):
    from conftest import init_repo
    p = init_repo(tmp_path / "fresh", commit=False)
    _task(p, "TASK-A-001", "a")
    assert run(p, "claim", "TASK-A-001.a").code == 0
    r, j = _act(p)
    assert r.code == 2
    assert j["git_error"] == "repository has no commits yet"
    assert "repository has no commits yet" in _state(j, "TASK-A-001.a")["detail"]
    assert "ambiguous" not in r.out
