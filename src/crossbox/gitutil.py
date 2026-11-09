"""Thin, read-mostly wrappers around the git CLI.

crossbox never commits and never pushes. The only writing git commands it runs
are `git mv` (renumber) and `git config` (installing the merge driver), plus
`git fetch` (which updates remote-tracking refs only).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import List, Optional


class GitError(Exception):
    pass


def git(repo: Path, *args: str, timeout: Optional[float] = 60,
        check: bool = False) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env["LC_ALL"] = "C"
    try:
        cp = subprocess.run(["git", *args], cwd=str(repo), capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=timeout, env=env)
    except FileNotFoundError as e:
        raise GitError("git is not installed") from e
    except subprocess.TimeoutExpired as e:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from e
    if check and cp.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {cp.stderr.strip()}")
    return cp


def is_repo(path: Path) -> bool:
    try:
        cp = git(path, "rev-parse", "--is-inside-work-tree")
    except GitError:
        return False
    return cp.returncode == 0 and cp.stdout.strip() == "true"


def toplevel(path: Path) -> Optional[Path]:
    try:
        cp = git(path, "rev-parse", "--show-toplevel")
    except GitError:
        return None
    if cp.returncode != 0:
        return None
    return Path(cp.stdout.strip())


def prefix(path: Path) -> Path:
    """Where `path` sits below the top of its work tree (`.` at the top)."""
    cp = git(path, "rev-parse", "--show-prefix")
    if cp.returncode != 0:
        raise GitError(f"cannot locate {path} in its work tree: {cp.stderr.strip()}")
    return Path(cp.stdout.strip() or ".")


def git_dirs(repo: Path):
    """(git_dir, common_dir) as absolute paths, or None outside a repository."""
    cp = git(repo, "rev-parse", "--absolute-git-dir", "--git-common-dir")
    if cp.returncode != 0:
        return None
    lines = cp.stdout.strip().splitlines()
    if len(lines) != 2:
        return None
    gd = Path(lines[0])
    cd = Path(lines[1])
    if not cd.is_absolute():
        cd = (repo / cd)
    return gd.resolve(), cd.resolve()


def current_branch(repo: Path) -> Optional[str]:
    cp = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD")
    if cp.returncode != 0:
        return None
    return cp.stdout.strip() or None


def worktrees(repo: Path) -> List[dict]:
    """Every worktree of the repository: {"path", "branch" (short or None),
    "slot"}. The primary checkout's slot is "main"; a linked worktree's slot is
    its administrative name (the directory under <common-dir>/worktrees/).
    Raises GitError when enumeration fails."""
    cp = git(repo, "worktree", "list", "--porcelain")
    if cp.returncode != 0:
        raise GitError(f"git worktree list failed: {cp.stderr.strip()}")
    dirs = git_dirs(repo)
    if dirs is None:
        raise GitError("not inside a git repository")
    common = dirs[1]
    admin = {}
    wt_admin = common / "worktrees"
    if wt_admin.is_dir():
        for d in wt_admin.iterdir():
            gitdir_file = d / "gitdir"
            try:
                target = Path(gitdir_file.read_text().strip()).parent.resolve()
            except OSError:
                continue
            admin[str(target)] = d.name
    out: List[dict] = []
    cur: dict = {}
    for line in cp.stdout.splitlines() + [""]:
        if not line:
            if cur:
                out.append(cur)
            cur = {}
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val, "branch": None}
        elif key == "branch":
            cur["branch"] = val[len("refs/heads/"):] if val.startswith("refs/heads/") else val
        elif key == "bare":
            cur["bare"] = True
    result = []
    for i, w in enumerate(out):
        if w.get("bare"):
            continue
        rp = str(Path(w["path"]).resolve())
        w["slot"] = "main" if i == 0 else admin.get(rp, Path(w["path"]).name)
        result.append(w)
    return result


def has_remote(repo: Path, name: str) -> bool:
    cp = git(repo, "remote")
    return cp.returncode == 0 and name in cp.stdout.split()


def fetch(repo: Path, remote: str, timeout: float = 30) -> bool:
    try:
        cp = git(repo, "fetch", "--quiet", remote, timeout=timeout)
    except GitError:
        return False
    return cp.returncode == 0


def resolves(repo: Path, ref: str) -> bool:
    cp = git(repo, "rev-parse", "--verify", "--quiet", ref + "^{commit}")
    return cp.returncode == 0


def show(repo: Path, ref: str, relpath: str) -> Optional[str]:
    """Content of relpath at ref; None ONLY when the path is absent there.

    Any other failure (a bad ref, a corrupt or missing object, a permission
    error) raises GitError. `git show` exits non-zero for all of them alike,
    and reading every failure as "absent" let a view that was never obtained
    report as clean and empty. So presence is asked of `ls-tree` first, whose
    exit status separates "no such path" (0, empty) from "cannot read" (!= 0).
    """
    cp = git(repo, "ls-tree", "--name-only", ref, "--", relpath)
    if cp.returncode != 0:
        raise GitError(f"cannot read {relpath} at {ref}: {cp.stderr.strip()}")
    if not cp.stdout.strip():
        return None
    # `<ref>:./<path>` is relative to the working directory, like the ls-tree
    # above; a bare `<ref>:<path>` is relative to the top of the repository,
    # which is a different file when --root names a subdirectory.
    cp = git(repo, "cat-file", "blob", f"{ref}:./{relpath}")
    if cp.returncode != 0:
        raise GitError(f"cannot read {relpath} at {ref}: {cp.stderr.strip()}")
    return cp.stdout


def ls_names(repo: Path, ref: str, relpaths: List[str]) -> List[str]:
    """Paths under relpaths at ref. An absent directory lists nothing; a ref
    or tree that cannot be read raises GitError, never an empty list."""
    cp = git(repo, "ls-tree", "-r", "--name-only", ref, "--", *relpaths)
    if cp.returncode != 0:
        raise GitError(f"cannot list {', '.join(relpaths)} at {ref}: {cp.stderr.strip()}")
    return [ln for ln in cp.stdout.splitlines() if ln]


def default_upstream(repo: Path, remote: str) -> Optional[str]:
    cp = git(repo, "symbolic-ref", "--quiet", f"refs/remotes/{remote}/HEAD")
    if cp.returncode == 0 and cp.stdout.strip():
        return cp.stdout.strip()[len("refs/remotes/"):]
    for b in ("main", "master"):
        if resolves(repo, f"{remote}/{b}"):
            return f"{remote}/{b}"
    return None


def is_tracked(repo: Path, relpath: str) -> bool:
    cp = git(repo, "ls-files", "--error-unmatch", "--", relpath)
    return cp.returncode == 0


def for_each_ref(repo: Path, patterns: List[str]) -> List[str]:
    if not patterns:
        return []
    cp = git(repo, "for-each-ref", "--format=%(refname)", *patterns)
    if cp.returncode != 0:
        return []
    return [ln for ln in cp.stdout.splitlines() if ln]
