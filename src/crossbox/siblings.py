"""The set of git refs that stand for "what other sessions have done".

One function, used by the origin guard, task-id minting and the claims union,
so no reader can quietly look at a narrower set than the minter:

  for every worktree of this repository: refs/heads/<branch> and
  refs/remotes/<remote>/<branch>; plus the upstream (config ``upstream``,
  default <remote>/<default branch>); plus any config ``extra_refs`` patterns.

Worktrees share one object store, so reading a file at another worktree's
branch needs no network. Refs that do not resolve are skipped by the callers.
"""
from __future__ import annotations

from . import gitutil


def _norm(ref: str, remote: str) -> str:
    if ref.startswith("refs/"):
        return ref
    if ref.startswith(remote + "/"):
        return "refs/remotes/" + ref
    return ref


def ref_set(cfg) -> list:
    """Raises gitutil.GitError when worktrees cannot be enumerated."""
    refs = []
    for w in gitutil.worktrees(cfg.repo):
        b = w.get("branch")
        if b:
            refs.append(f"refs/heads/{b}")
            refs.append(f"refs/remotes/{cfg.remote}/{b}")
    if cfg.upstream:
        refs.append(_norm(cfg.upstream, cfg.remote))
    refs.extend(gitutil.for_each_ref(cfg.repo, cfg.extra_refs))
    seen, out = set(), []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def sibling_worktrees(cfg) -> list:
    """[(path, branch_ref or None)] for every OTHER worktree of this repository
    whose directory exists. Their working copies hold what a sibling session
    has minted but not committed yet, which no ref can show.

    Each path is the sibling's copy of the crossbox root: when --root names a
    subdirectory, the same subdirectory inside the sibling worktree.
    Raises gitutil.GitError when worktrees cannot be enumerated."""
    top = gitutil.toplevel(cfg.repo)
    here = (top or cfg.repo).resolve()
    sub = gitutil.prefix(cfg.repo)
    out = []
    for w in gitutil.worktrees(cfg.repo):
        p = w.get("path")
        if not p:
            continue
        from pathlib import Path
        pp = Path(p).resolve()
        if pp == here or not pp.is_dir():
            continue
        b = w.get("branch")
        out.append((pp / sub, f"refs/heads/{b}" if b else None))
    return out


def own_branch_ref(cfg):
    b = gitutil.current_branch(cfg.repo)
    return f"refs/heads/{b}" if b else None


def short(ref: str) -> str:
    for p in ("refs/heads/", "refs/remotes/"):
        if ref.startswith(p):
            return ref[len(p):]
    return ref
