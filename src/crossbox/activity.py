"""`crossbox activity`: is each claim's holder really working, read from git.

A claim is a statement someone chose to make. Commits are a witness nobody has
to remember to produce. For every active step claim this reads git and gives
one state:

  HOT      a commit or branch naming the step within the last --hot-hours
  QUIET    evidence older than that but inside the stale window (stale_days),
           or a claim younger than the stale window with no evidence yet
  COLD     no evidence for longer than the stale window
  UNKNOWN  git could not be read, the scan was truncated, or the claim names no
           step (a subject-only claim has nothing to match)

Evidence: a commit dated on or after the claim's date whose subject names the
step ref as a whole token, or that carries a `Crossbox-Step: <ref>` trailer; a
branch (local or remote-tracking) whose name holds the ref as a whole token
(`/`, `-`, `_` and the ends of the name are the boundaries, case-insensitive;
`feat/TASK-API-001.a-x` matches `.a`, `feat/TASK-API-001.ab-x` does not). A branch
is dated by its own reflog, the time it was created or last moved, so a branch
made today at an old commit counts as made today; with no reflog (a
remote-tracking ref, a clone, reflogs off) it is dated by its tip commit. Every local branch is read, so unpushed work in a sibling worktree counts.

The reverse is listed too: open steps with no active claim that commits or
branches named inside the stale window, i.e. work nobody claimed.

Advisory only: nothing is released, and "no commit seen" never means idle (an
agent can work for an hour before committing).

Exit 0, or 2 (NOT EVALUABLE) when git or the claims view could not be read.
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time

from . import claims as _claims
from . import gitutil, model, siblings
from .errors import NOT_EVALUABLE, OK

_c = model.clean
TRAILER = "Crossbox-Step"
MAX_COMMITS = 50000
STATES = ("HOT", "QUIET", "COLD", "UNKNOWN")
HOUR = 3600
DAY = 24 * HOUR


def _token_re(ref: str):
    # whole token: not preceded by an id character, not followed by a step letter
    # or another id character (so `.a` never matches `.ab`)
    return re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(ref)}(?![a-z0-9_-])")


def _branch_re(ref: str):
    # a branch name has no ids to protect beyond the ref itself: the ref must sit
    # between name boundaries (`/`, `-`, `_`, start, end), never inside a longer id
    return re.compile(rf"(?<![^/_-]){re.escape(ref)}(?![^/_-])", re.IGNORECASE)


def _midnight(d: _dt.date) -> float:
    return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).timestamp()


class Scan:
    """Commits and branch tips since a date, read once."""

    def __init__(self, ctx, since: _dt.date):
        self.commits = []   # (sha, epoch, ref, subject, trailer values)
        self.branches = []  # (short ref, tip epoch, full refname)
        self.repo = ctx.repo
        self.error = None
        self.truncated = False
        if not gitutil.is_repo(ctx.repo):
            self.error = "not a git repository"
            return
        fmt = "%H%x1f%ct%x1f%S%x1f%s%x1f%(trailers:key=" + TRAILER + \
              ",valueonly,separator=%x2c)%x1e"
        try:
            # decided directly, not by matching git's wording (which varies by version)
            if gitutil.git(ctx.repo, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
                self.error = "repository has no commits yet"
                return
            # no --since: git stops walking at the first commit dated before it, so one commit
            # with an older committer date (a wrong clock, a rebase that keeps author dates)
            # would hide every recent commit below it; the date is filtered after the walk
            cp = gitutil.git(ctx.repo, "log", "--branches", "--remotes", "HEAD",
                             f"--max-count={MAX_COMMITS}", f"--format={fmt}")
            self._since = _dt.datetime(since.year, since.month, since.day,
                                       tzinfo=_dt.timezone.utc).timestamp()
            br = gitutil.git(ctx.repo, "for-each-ref", "--format=%(refname)%1f"
                             "%(committerdate:unix)", "refs/heads", "refs/remotes")
        except gitutil.GitError as e:
            self.error = str(e)
            return
        if cp.returncode != 0:
            self.error = f"git log failed: {cp.stderr.strip()}"
            return
        if br.returncode != 0:
            self.error = f"git for-each-ref failed: {br.stderr.strip()}"
            return
        seen = set()
        for rec in cp.stdout.split("\x1e"):
            rec = rec.strip("\n")
            if not rec:
                continue
            parts = rec.split("\x1f")
            if len(parts) < 5:
                continue
            sha, ct, src, subj, tr = parts[:5]
            if sha in seen:
                continue
            seen.add(sha)
            try:
                t = int(ct)
            except ValueError:
                continue
            vals = [v.strip() for v in tr.split(",") if v.strip()]
            if t < self._since:
                continue
            self.commits.append((sha, t, siblings.short(src), subj, vals))
        self.truncated = len(seen) >= MAX_COMMITS
        for line in br.stdout.splitlines():
            name, _, ct = line.partition("\x1f")
            if name.endswith("/HEAD"):
                continue
            try:
                self.branches.append((siblings.short(name), int(ct), name))
            except ValueError:
                continue

    def _branch_time(self, full: str, tip: int) -> int:
        """When the branch was created or last moved (its newest reflog entry),
        else its tip commit date. Read only for branches whose name matched."""
        try:
            cp = gitutil.git(self.repo, "reflog", "show", "--date=unix", "-1",
                             "--format=%gd", full, "--")
        except gitutil.GitError:
            return tip
        m = re.search(r"@\{(\d+)\}", cp.stdout) if cp.returncode == 0 else None
        return int(m.group(1)) if m else tip

    def evidence(self, ref: str, since_epoch: float) -> dict:
        rx = _token_re(ref)
        hits, signals = [], []
        for sha, t, src, subj, trailers in self.commits:
            if t < since_epoch:
                continue
            how = None
            if ref in trailers:
                how = "trailer"
            elif rx.search(subj):
                how = "subject"
            if how:
                hits.append((t, sha, src))
                if how not in signals:
                    signals.append(how)
        brx = _branch_re(ref)
        for name, tip, full in self.branches:
            if not brx.search(name):
                continue
            t = self._branch_time(full, tip)
            if t >= since_epoch:
                hits.append((t, None, name))
                if "branch" not in signals:
                    signals.append("branch")
        commits = sum(1 for h in hits if h[1])
        last = max(hits, default=None)
        return {"commits": commits, "signals": signals,
                "last": last[0] if last else None,
                "last_sha": (last[1] or "")[:7] if last else None,
                "last_ref": last[2] if last else None}


def _ago(seconds: float) -> str:
    if seconds < HOUR:
        return f"{max(int(seconds // 60), 0)}m ago"
    if seconds < 2 * DAY:
        return f"{int(seconds // HOUR)}h ago"
    return f"{int(seconds // DAY)}d ago"


def assess(ctx, hot_hours: float = 24.0) -> dict:
    """Raises claims.ClaimsDamaged when the local claims file is damaged."""
    ctx.require_queue("activity")
    view = _claims.read_view(ctx)
    now = time.time()
    stale = ctx.cfg.stale_days
    window_start = ctx.today - _dt.timedelta(days=stale)
    active = [c for c in view.union if _claims.is_active(c)]
    dates = [d for d in (_claims.as_date(c.get("created")) for c in active) if d]
    scan = Scan(ctx, min(dates + [window_start]))
    rows = []
    for c in active:
        ref = c.get("step_ref")
        created = _claims.as_date(c.get("created"))
        row = {"id": c.get("id"), "step_ref": ref,
               "holder": f"{_claims.c_machine(c)}/{_claims.c_slot(c)}",
               "created": str(c.get("created")), "commits": 0, "signals": [],
               "last": None, "last_sha": None, "last_ref": None}
        if not ref:
            row.update(state="UNKNOWN", detail="subject-only claim: no step to match")
        elif created is None:
            row.update(state="UNKNOWN", detail="claim has no readable created date")
        elif scan.error:
            row.update(state="UNKNOWN", detail=f"git not read: {scan.error}")
        else:
            ev = scan.evidence(str(ref), _midnight(created))
            row.update(ev)
            age_days = (ctx.today - created).days
            if ev["last"] is not None and now - ev["last"] <= hot_hours * HOUR:
                row.update(state="HOT", detail=f"{ev['commits']} commit(s), last "
                           f"{_ago(now - ev['last'])}" + _where(ev))
            elif ev["last"] is not None and now - ev["last"] <= stale * DAY:
                row.update(state="QUIET", detail=f"{ev['commits']} commit(s), last "
                           f"{_ago(now - ev['last'])}" + _where(ev))
            elif age_days <= stale and ev["last"] is None:
                row.update(state="QUIET", detail=f"claimed {age_days}d ago, no commit "
                           f"naming the step seen yet")
            elif scan.truncated:
                row.update(state="UNKNOWN", detail=f"scan stopped at {MAX_COMMITS} "
                           f"commits; older evidence not read")
            elif ev["last"] is None:
                row.update(state="COLD", detail=f"claimed {age_days}d ago, no commit "
                           f"naming the step seen")
            else:
                row.update(state="COLD", detail=f"last evidence {_ago(now - ev['last'])}"
                           + _where(ev))
        rows.append(row)
    unclaimed = []
    held = _claims.active_step_claims(view.union)
    if not scan.error:
        since = _midnight(window_start)
        for tf in ctx.store.files("queue"):
            if not tf.ok:
                continue
            for s in tf.steps:
                if not isinstance(s, dict) or s.get("status") not in model.OPEN:
                    continue
                ref = f"{tf.id}.{s.get('id')}"
                if ref in held:
                    continue
                ev = scan.evidence(ref, since)
                if ev["last"] is not None:
                    unclaimed.append(dict(ev, ref=ref, title=s.get("title", ""),
                                          detail=f"{ev['commits']} commit(s), last "
                                                 f"{_ago(now - ev['last'])}" + _where(ev)))
    counts = {s: sum(1 for r in rows if r["state"] == s) for s in STATES}
    return {"claims": rows, "unclaimed": unclaimed, "counts": counts,
            "active": len(rows), "commits_scanned": len(scan.commits),
            "since": min(dates + [window_start]).isoformat(), "hot_hours": hot_hours,
            "stale_days": stale, "git_error": scan.error, "truncated": scan.truncated,
            "claims_view": view.incomplete or "ok"}


def _where(ev) -> str:
    if ev.get("last_sha"):
        return f" ({ev['last_sha']} on {ev['last_ref']})"
    if ev.get("last_ref"):
        return f" (branch {ev['last_ref']})"
    return ""


def cmd_activity(ctx, as_json=False, hot_hours=24.0) -> int:
    try:
        res = assess(ctx, hot_hours)
    except _claims.ClaimsDamaged as e:
        msg = f"claims file damaged ({e}); refusing to read it as 'no claims'"
        ctx.say(json.dumps({"not_evaluable": msg}) if as_json
                else f"activity: NOT EVALUABLE: {msg}")
        return NOT_EVALUABLE
    ne = bool(res["git_error"]) or res["claims_view"] != "ok"
    if as_json:
        ctx.say(json.dumps(res, indent=2, default=str))
        return NOT_EVALUABLE if ne else OK
    k = res["counts"]
    ctx.say(f"activity: hot {k['HOT']} · quiet {k['QUIET']} · cold {k['COLD']} · "
            f"unknown {k['UNKNOWN']} of {res['active']} active claim(s); "
            f"{res['commits_scanned']} commit(s) scanned since {res['since']}")
    order = {s: i for i, s in enumerate(("COLD", "UNKNOWN", "QUIET", "HOT"))}
    for r in sorted(res["claims"], key=lambda r: (order[r["state"]], str(r["id"]))):
        ctx.say(f"  {r['state']:<8} {_c(str(r['id'])):<22} {_c(str(r['step_ref'] or '-')):<18} "
                f"{_c(r['holder'])}  {_c(r['detail'])}")
    if res["unclaimed"]:
        ctx.say(f"unclaimed activity: {len(res['unclaimed'])} open step(s) with no active "
                f"claim named by commits or branches in the last {res['stale_days']} day(s):")
        for u in res["unclaimed"]:
            ctx.say(f"  {_c(u['ref']):<18} {_c(u['detail'])}  {_c(u['title'])}")
    if res["git_error"]:
        ctx.say(f"NOT EVALUABLE: git not read ({_c(res['git_error'])})")
    if res["claims_view"] != "ok":
        ctx.say(f"NOT EVALUABLE: claims view incomplete ({_c(str(res['claims_view']))})")
    ctx.say(f"signals: commit subjects and {TRAILER} trailers naming the step, branch names "
            f"holding it. Advisory: no claim is released; no commit seen is not idle.")
    return NOT_EVALUABLE if ne else OK
