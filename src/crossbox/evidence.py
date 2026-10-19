"""Commit evidence for a closure: `done REF --commit SHA`.

A closure that names its commit can be checked later; one that does not is a
statement. The commit is resolved to its full sha before anything is written,
graded against the upstream (or a local main/master when there is none), and
checked for naming the step (subject token or `Crossbox-Step:` trailer).
"""
from __future__ import annotations

from . import gitutil
from .activity import TRAILER, _token_re
from .errors import CrossboxError, NotEvaluable

REACHABLE, UNMERGED, UNKNOWN = "REACHABLE", "UNMERGED", "UNKNOWN"


def resolve_commit(ctx, spec: str) -> str:
    if not gitutil.is_repo(ctx.repo):
        raise NotEvaluable(f"--commit {spec}: not a git repository, so the commit "
                           f"cannot be checked; nothing written")
    spec = (spec or "").strip()
    if not spec or spec.startswith("-"):
        raise CrossboxError(f"--commit {spec!r} is not a commit")
    cp = gitutil.git(ctx.repo, "rev-parse", "--verify", "--quiet", f"{spec}^{{commit}}")
    if cp.returncode != 0 or not cp.stdout.strip():
        raise CrossboxError(f"--commit {spec}: not a commit in this repository; "
                            f"nothing written")
    return cp.stdout.strip()


def baseline(ctx):
    """The ref a closure should be reachable from, or None."""
    cands = []
    if ctx.cfg.upstream:
        cands.append(ctx.cfg.upstream)
    cands += ["refs/heads/main", "refs/heads/master"]
    for ref in cands:
        if gitutil.resolves(ctx.repo, ref):
            for p in ("refs/heads/", "refs/remotes/"):
                if ref.startswith(p):
                    return ref, ref[len(p):]
            return ref, ref
    return None


def grade(ctx, sha: str) -> tuple:
    """(grade, line) for one recorded commit."""
    base = baseline(ctx)
    if base is None:
        return UNKNOWN, f"commit {sha[:7]} UNKNOWN: no upstream and no main or master branch to check against"
    ref, name = base
    cp = gitutil.git(ctx.repo, "merge-base", "--is-ancestor", sha, ref)
    if cp.returncode == 0:
        return REACHABLE, f"commit {sha[:7]} REACHABLE from {name}"
    if cp.returncode == 1:
        return UNMERGED, f"commit {sha[:7]} UNMERGED: not in {name}"
    return UNKNOWN, f"commit {sha[:7]} UNKNOWN: git merge-base failed ({cp.stderr.strip()})"


def names_step(ctx, sha: str, ref: str) -> bool:
    cp = gitutil.git(ctx.repo, "log", "-1", "--format=%s%x1f%(trailers:key="
                     + TRAILER + ",valueonly,separator=%x2c)", sha)
    subj, _, tr = cp.stdout.strip("\n").partition("\x1f")
    return ref in [v.strip() for v in tr.split(",")] or bool(_token_re(ref).search(subj))
