"""`crossbox closures`: are the closures the queue records true, read from git.

A step marked done is a statement. This checks five things the statement can get
wrong, each a class with its count printed beside its denominator:

  unevidenced              done steps with no `commits:` recorded (informational)
  unreachable              a recorded commit is not in the baseline, or is gone
  claimed_closed_but_open  an open step that a recent commit says it closes
  artifact_missing         a done step's notes cite a path that is not at HEAD
  unmerged_branches        remote branches with commits the baseline lacks, listed
                           when they name a step, with a total of all of them

See docs/closures.md. Read only. Exit 0 when the last four are empty, 1 when any
has rows, 2 NOT EVALUABLE when a class could not run (the others still report).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from typing import List, Optional

from . import evidence, gitutil, model
from .activity import MAX_COMMITS, TRAILER, Scan, _ago, _branch_re, _token_re
from .errors import NOT_EVALUABLE, OK

_c = model.clean
DAY = 86400
BRANCH_COMMITS = 2000

CLASSES = ("unevidenced", "unreachable", "claimed_closed_but_open",
           "artifact_missing", "unmerged_branches")
LABELS = {"unevidenced": "unevidenced", "unreachable": "unreachable",
          "claimed_closed_but_open": "claimed closed but open",
          "artifact_missing": "artifact missing", "unmerged_branches": "unmerged branches"}
UNITS = {"unevidenced": "done step(s)", "unreachable": "recorded commit(s)",
         "claimed_closed_but_open": "open step(s)", "artifact_missing": "cited path(s)",
         "unmerged_branches": "unmerged remote branch(es)"}
FINDINGS = CLASSES[1:]

# The documented phrase set. A subject closes a step only when one of these verbs
# starts a sentence (the subject's start, the start after a conventional-commit
# `type(scope): ` prefix, or after `. ` / `; `) and the step ref follows it as a
# whole token. `start X`, `X: wip`, `close the X gap`, `X done` do not match.
VERBS = (r"clos(?:e|es|ed)|done|fix(?:es|ed)?|finish(?:es|ed)?|complet(?:e|es|ed)")
_ANCHOR = re.compile(
    r"(?:^|[.;]\s+)(?:[A-Za-z][\w-]*(?:\([^)]*\))?!?:\s+)?(?:" + VERBS + r")\b[:\s]*",
    re.IGNORECASE)

_TICKS = re.compile(r"`([^`\n]+)`")
_EXTS = ("py", "md", "yaml", "yml", "json", "toml", "txt", "sh", "js", "ts", "rs", "go",
         "cfg", "ini", "sql", "html", "css", "c", "h", "rb", "java")
_PATH_OK = re.compile(r"[A-Za-z0-9_./+@-]+")


def closing_tails(subject: str) -> List[str]:
    """The text after each sentence-anchored closing verb in a subject."""
    return [subject[m.end():] for m in _ANCHOR.finditer(subject)]


def subject_closes(subject: str, ref: str, trailers=()) -> bool:
    tails = closing_tails(subject)
    if ref in trailers:
        return bool(tails)
    rx = _token_re(ref)
    return any(rx.match(t) for t in tails)


def cited_paths(notes) -> List[str]:
    """Repository-relative paths cited in backticks in a step's notes."""
    out = []
    for raw in _TICKS.findall(notes if isinstance(notes, str) else ""):
        p = re.sub(r"[:#]\d[\d\-,]*$", "", raw.strip().rstrip(".,;:)"))
        if p.startswith("./"):
            p = p[2:]
        if (not p or "://" in p or p[0] in "/~-" or ".." in p.split("/")
                or not _PATH_OK.fullmatch(p)):
            continue
        last = p.rstrip("/").rsplit("/", 1)[-1]
        ext = last.rsplit(".", 1)[-1].lower() if "." in last.strip(".") else ""
        # a path names a file (a known extension) or a directory (a trailing slash); a bare
        # word pair with a slash ("and/or", "read/write") is prose, not a path
        if ext in _EXTS or (p.endswith("/") and len(p) > 1):
            if p not in out:
                out.append(p)
    return out


def _cls(of, cause=None, rows=None):
    if cause is not None:
        return {"evaluable": False, "cause": cause, "count": None, "of": of, "rows": []}
    return {"evaluable": True, "cause": None, "count": len(rows), "of": of, "rows": rows}


def _age(now, t: Optional[float]) -> Optional[int]:
    return None if t is None else max(int(now - t), 0)


def _completed_epoch(step) -> Optional[float]:
    d = step.get("completed")
    if isinstance(d, _dt.datetime):
        d = d.date()
    if isinstance(d, str):
        try:
            d = _dt.date.fromisoformat(d[:10])
        except ValueError:
            return None
    if isinstance(d, _dt.date):
        return _dt.datetime(d.year, d.month, d.day, tzinfo=_dt.timezone.utc).timestamp()
    return None


def unreadable(ctx) -> list:
    """[(task id, file name, error)] for every task file that does not parse: its steps are not
    read, so no count over them is complete."""
    return [(tf.id, tf.path.name, str(tf.error)) for tf in ctx.store.files() if not tf.ok]


def _steps(ctx):
    done, open_, every = [], [], []
    for tf in ctx.store.files():
        if not tf.ok:
            continue
        for s in tf.steps:
            if not isinstance(s, dict):
                continue
            ref = f"{tf.id}.{s.get('id')}"
            every.append(ref)
            if s.get("status") == "done":
                done.append((ref, s))
            elif s.get("status") in model.OPEN and tf.tier == "queue":
                open_.append((ref, s))
    return done, open_, every


def _commit_time(ctx, sha: str) -> Optional[int]:
    cp = gitutil.git(ctx.repo, "log", "-1", "--format=%ct", sha)
    try:
        return int(cp.stdout.strip()) if cp.returncode == 0 else None
    except ValueError:
        return None


class Shallow(Exception):
    pass


def _unreachable(ctx, done, base, now):
    ref, name = base
    rows, total = [], 0
    shallow = gitutil.git(ctx.repo, "rev-parse", "--is-shallow-repository").stdout.strip() == "true"
    for sref, s in done:
        shas = s.get("commits")
        if not isinstance(shas, list):
            continue
        for sha in (str(x) for x in shas):
            total += 1
            exists = gitutil.git(ctx.repo, "cat-file", "-e", f"{sha}^{{commit}}").returncode == 0
            if not exists and shallow:
                # a shallow clone holds only part of the history: absent is not "rebased away"
                raise Shallow(f"shallow clone: commit {sha[:7]} recorded on {sref} may be outside the "
                              f"history this clone holds (git fetch --unshallow, then re-run)")
            if not exists:
                cause, t = "MISSING", _completed_epoch(s)
            else:
                cp = gitutil.git(ctx.repo, "merge-base", "--is-ancestor", sha, ref)
                if cp.returncode == 0:
                    continue
                if cp.returncode != 1:
                    raise gitutil.GitError(f"git merge-base failed ({cp.stderr.strip()})")
                cause, t = "UNMERGED", _commit_time(ctx, sha)
            rows.append({"ref": sref, "sha": sha, "cause": cause,
                         "age_seconds": _age(now, t),
                         "evidence": f"commit {sha[:7]} " + (
                             f"not in {name}" if cause == "UNMERGED"
                             else "no longer exists (rebased away or collected)")})
    return rows, total


def _claimed_closed(ctx, open_, scan, days, now):
    since, rows = now - days * DAY, []
    for sha, t, _src, subj, trailers in scan.commits:
        if t < since or not closing_tails(subj):
            continue
        for sref, _s in open_:
            if subject_closes(subj, sref, trailers):
                rows.append({"ref": sref, "sha": sha, "subject": subj,
                             "age_seconds": _age(now, t)})
    return rows


def _artifacts(ctx, done, now):
    top = gitutil.toplevel(ctx.repo) or ctx.repo
    rows, total = [], 0
    names = None
    for sref, s in done:
        for p in cited_paths(s.get("notes")):
            total += 1
            if gitutil.git(top, "cat-file", "-e", f"HEAD:{p.rstrip('/')}").returncode == 0:
                continue
            if "/" not in p.rstrip("/"):
                # a bare name ("backtest.py") names a file, not where it is: present when any
                # tracked file at HEAD has that name
                if names is None:
                    ls = gitutil.git(top, "ls-tree", "-r", "--name-only", "HEAD")
                    names = {n.rsplit("/", 1)[-1] for n in ls.stdout.splitlines()}
                if p in names:
                    continue
            rows.append({"ref": sref, "path": p,
                         "age_seconds": _age(now, _completed_epoch(s)),
                         "evidence": f"`{p}` cited in the notes, absent at HEAD"})
    return rows, total


def _unmerged(ctx, base, every, now):
    ref, _name = base
    names = gitutil.for_each_ref(ctx.repo, ["refs/remotes"])
    sym = gitutil.git(ctx.repo, "rev-parse", "--symbolic-full-name", ref)
    own = sym.stdout.strip() if sym.returncode == 0 else ref
    rows, total, remote_total = [], 0, 0
    fmt = "%ct%x1f%s%x1f%(trailers:key=" + TRAILER + ",valueonly,separator=%x2c)%x1e"
    for full in names:
        if full.endswith("/HEAD") or full in (ref, own):
            continue
        remote_total += 1
        n = gitutil.git(ctx.repo, "rev-list", "--count", f"{ref}..{full}")
        if n.returncode != 0:
            raise gitutil.GitError(f"git rev-list failed ({n.stderr.strip()})")
        count = int(n.stdout.strip() or 0)
        if count == 0:
            continue
        total += 1
        short = full[len("refs/remotes/"):]
        lg = gitutil.git(ctx.repo, "log", f"{ref}..{full}", f"--max-count={BRANCH_COMMITS}",
                         f"--format={fmt}")
        newest, mention = None, []
        brx_hits = [r for r in every if _branch_re(r).search(short)]
        for rec in lg.stdout.split("\x1e"):
            parts = rec.strip("\n").split("\x1f")
            if len(parts) < 3:
                continue
            newest = max(newest or 0, int(parts[0]))
            tr = [v.strip() for v in parts[2].split(",") if v.strip()]
            for r in every:
                if r not in mention and (r in tr or _token_re(r).search(parts[1])):
                    mention.append(r)
        refs = sorted(set(mention) | set(brx_hits))
        if refs:
            rows.append({"branch": short, "commits": count, "refs": refs,
                         "age_seconds": _age(now, newest)})
    return rows, total, remote_total


def assess(ctx, days: int = 30) -> dict:
    ctx.require_queue("closures")
    now = time.time()
    done, open_, every = _steps(ctx)
    res = {"days": days, "baseline": None, "classes": {}}
    unev = [{"ref": r, "age_seconds": _age(now, _completed_epoch(s))}
            for r, s in done if not (isinstance(s.get("commits"), list) and s["commits"])]
    res["classes"]["unevidenced"] = _cls(len(done), rows=unev)

    def blocked(cause):
        for k in FINDINGS:
            res["classes"].setdefault(k, _cls(None, cause=cause))

    try:
        if not gitutil.is_repo(ctx.repo):
            blocked("git cannot be read: not a git repository")
            return res
        if gitutil.git(ctx.repo, "rev-parse", "--verify", "-q", "HEAD").returncode != 0:
            blocked("git cannot be read: repository has no commits yet")
            return res
        base = evidence.baseline(ctx)
        res["baseline"] = base[1] if base else None
        nb = "no baseline: no upstream and no main or master branch to check against"
        if base:
            try:
                rows, total = _unreachable(ctx, done, base, now)
                res["classes"]["unreachable"] = _cls(total, rows=rows)
            except Shallow as e:
                res["classes"]["unreachable"] = _cls(None, cause=str(e))
        else:
            res["classes"]["unreachable"] = _cls(None, cause=nb)
        since = (_dt.datetime.fromtimestamp(now, _dt.timezone.utc)
                 - _dt.timedelta(days=days)).date()
        scan = Scan(ctx, since)
        if scan.error:
            res["classes"]["claimed_closed_but_open"] = _cls(
                None, cause=f"git cannot be read: {scan.error}")
        else:
            rows = _claimed_closed(ctx, open_, scan, days, now)
            c = _cls(len(open_), rows=rows)
            c["scanned_commits"] = len(scan.commits)
            c["floor"] = scan.truncated
            res["classes"]["claimed_closed_but_open"] = c
        rows, total = _artifacts(ctx, done, now)
        res["classes"]["artifact_missing"] = _cls(total, rows=rows)
        if base:
            rows, total, remote_total = _unmerged(ctx, base, every, now)
            c = _cls(total, rows=rows)
            c["remote_branches"] = remote_total
            res["classes"]["unmerged_branches"] = c
        else:
            res["classes"]["unmerged_branches"] = _cls(None, cause=nb)
    except gitutil.GitError as e:
        blocked(f"git cannot be read: {e}")
    return res


def _line(k, c):
    if not c["evaluable"]:
        return f"{LABELS[k]}: NOT EVALUABLE: {c['cause']}"
    extra = ""
    if k == "unmerged_branches":
        return (f"{LABELS[k]}: {c['count']} naming a step of {c['of']} "
                f"{UNITS[k]} ({c['remote_branches']} remote branch(es) in all)")
    if c.get("floor"):
        extra = f" (floor: scan stopped at {MAX_COMMITS} commits)"
    return f"{LABELS[k]}: {c['count']} of {c['of']} {UNITS[k]}{extra}"


def _ago_s(a):
    return "age unknown" if a is None else _ago(a)


def _row(k, r):
    if k == "unevidenced":
        return f"{_c(r['ref'])}  ({_ago_s(r['age_seconds'])} closed)"
    if k == "unreachable":
        return f"{_c(r['ref'])}  {r['cause']}  {_c(r['evidence'])}  ({_ago_s(r['age_seconds'])})"
    if k == "claimed_closed_but_open":
        return (f"{_c(r['ref'])}  {r['sha'][:7]} {_c(r['subject'])}  ({_ago_s(r['age_seconds'])})")
    if k == "artifact_missing":
        return f"{_c(r['ref'])}  {_c(r['evidence'])}  (closed {_ago_s(r['age_seconds'])})"
    return (f"{_c(r['branch'])}  {r['commits']} commit(s), newest {_ago_s(r['age_seconds'])}"
            f"  names {', '.join(_c(x) for x in r['refs'])}")


def cmd_closures(ctx, as_json=False, days=30) -> int:
    res = assess(ctx, days)
    cl = res["classes"]
    ne = [k for k in CLASSES if not cl[k]["evaluable"]]
    found = [k for k in FINDINGS if cl[k]["evaluable"] and cl[k]["rows"]]
    bad = unreadable(ctx)
    code = NOT_EVALUABLE if (ne or bad) else (1 if found else OK)
    if as_json:
        ctx.say(json.dumps(dict(res, not_evaluable=ne, findings=found, exit=code,
                                unreadable=[{"task": t, "file": f, "error": e} for t, f, e in bad]),
                           indent=2, default=str))
        return code
    base = res["baseline"] or "none"
    ctx.say(f"closures: baseline {base} · commits read for the last {days} day(s)")
    for k in CLASSES:
        ctx.say(_line(k, cl[k]))
        for r in cl[k]["rows"]:
            ctx.say("  " + _row(k, r))
    for t, f, e in bad:
        ctx.say(f"UNREADABLE {_c(t)} ({_c(f)}): {_c(e)[:120]}; its steps were not read, so every "
                f"count above is a floor")
    if ne:
        ctx.say("NOT EVALUABLE: " + ", ".join(LABELS[k] for k in ne)
                + " could not run (cause above); the other classes are reported.")
    elif not found:
        ctx.say("closures: no findings (unevidenced is informational).")
    return code
