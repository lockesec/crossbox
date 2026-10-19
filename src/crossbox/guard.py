"""The origin guard (step-id minting) and sibling-aware task-id minting.

Step ids are minted locally as max+1. Two sessions adding a step to the same
task on different branches would both mint the same letter. The origin guard
runs before `add` (once before `batch-add`): it reads the task file at every
sibling ref (see siblings.ref_set) and REFUSES when any of them holds a step
id the local file lacks: rebase or land first.

Results:  OK (0) · REFUSED (1) · NOT EVALUABLE (2)
  * fetch of the remote fails or times out      -> NOT EVALUABLE
  * worktrees cannot be enumerated              -> NOT EVALUABLE
  * local file does not parse                   -> NOT EVALUABLE
  * a remote exists but no ref could be read    -> NOT EVALUABLE
  * `remote:` set but no such remote exists     -> NOT EVALUABLE
  * no remote and nothing readable              -> OK (a lone repository)
Opt-out: CROSSBOX_SKIP_ORIGIN_GUARD=1 (printed in every refusal).

Sibling worktrees of the same clone are also read in their WORKING COPIES,
so a step or task another worktree minted and has not committed yet is seen.
What remains unseen: an uncommitted mint in another clone (another machine, or a
second clone on this one) until it is committed, pushed and fetched. That
degrades to a loud merge conflict, not a silent duplicate.
"""
from __future__ import annotations

import os

from . import gitutil, model, siblings
from .errors import NOT_EVALUABLE, OK, REFUSED, NotEvaluable

SKIP_ENV = "CROSSBOX_SKIP_ORIGIN_GUARD"


def fetch_state(cfg) -> str:
    """'no-remote' | 'missing' | 'ok' | 'failed'. 'missing' is a `remote:` the
    config names and the repository lacks (a typo): not a lone repository."""
    if not gitutil.has_remote(cfg.repo, cfg.remote):
        return "missing" if cfg.remote_explicit else "no-remote"
    return "ok" if gitutil.fetch(cfg.repo, cfg.remote, timeout=30) else "failed"


def _missing_remote(cfg) -> str:
    return (f"origin guard: NOT EVALUABLE: config sets `remote: {cfg.remote}` but this "
            f"repository has no such remote (a typo?); cannot see what others minted "
            f"(set {SKIP_ENV}=1 to skip)")


def _ids(text):
    return [i for i in model.step_ids(model.parse_text(text)) if isinstance(i, str)]


def origin_guard(ctx, task_path) -> tuple:
    """(code, message)"""
    if os.environ.get(SKIP_ENV) == "1":
        return OK, f"origin guard skipped ({SKIP_ENV}=1)"
    cfg = ctx.cfg
    if not gitutil.is_repo(cfg.repo):
        return OK, "origin guard: not a git repository; no siblings to check"
    fs = fetch_state(cfg)
    if fs == "missing":
        return NOT_EVALUABLE, _missing_remote(cfg)
    if fs == "failed":
        return NOT_EVALUABLE, (f"origin guard: NOT EVALUABLE: `git fetch {cfg.remote}` "
                               f"failed or timed out; cannot see what others minted "
                               f"(set {SKIP_ENV}=1 to skip)")
    try:
        refs = siblings.ref_set(cfg)
    except gitutil.GitError as e:
        return NOT_EVALUABLE, f"origin guard: NOT EVALUABLE: {e}"
    try:
        local = set(_ids(task_path.read_text(encoding="utf-8")))
    except (OSError, model.ParseError) as e:
        return NOT_EVALUABLE, f"origin guard: NOT EVALUABLE: local file unreadable ({e})"
    rel = cfg.rel(task_path)
    own = siblings.own_branch_ref(cfg)
    # ids in our own last commit that we removed on purpose are not gaps
    known = set(local)
    try:
        # an unborn branch (no commit yet) has no last commit to compare with
        head = gitutil.show(cfg.repo, "HEAD", rel) if gitutil.resolves(cfg.repo, "HEAD") \
            else None
    except gitutil.GitError as e:
        return NOT_EVALUABLE, f"origin guard: NOT EVALUABLE: {e}"
    if head is not None:
        try:
            known |= set(_ids(head))
        except model.ParseError:
            pass
    readable = 0
    gaps = []
    for ref in refs:
        if ref == own or not gitutil.resolves(cfg.repo, ref):
            continue
        readable += 1
        anc = gitutil.git(cfg.repo, "merge-base", "--is-ancestor", ref, "HEAD")
        if anc.returncode == 0:
            continue  # everything at that ref is already in our history
        try:
            text = gitutil.show(cfg.repo, ref, rel)
        except gitutil.GitError as e:
            return NOT_EVALUABLE, f"origin guard: NOT EVALUABLE: {e}"
        if text is None:
            continue
        try:
            there = _ids(text)
        except model.ParseError:
            return NOT_EVALUABLE, (f"origin guard: NOT EVALUABLE: {rel} does not parse "
                                   f"at {siblings.short(ref)}")
        missing = [i for i in there if i not in known]
        if missing:
            gaps.append((ref, missing))
    # uncommitted mints in sibling worktrees of this clone: ids in their
    # working copy that their own branch does not have yet
    try:
        wts = siblings.sibling_worktrees(cfg)
    except gitutil.GitError as e:
        return NOT_EVALUABLE, f"origin guard: NOT EVALUABLE: {e}"
    for path, bref in wts:
        f = path / rel
        try:
            wt_text = f.read_text(encoding="utf-8")
        except FileNotFoundError:
            continue
        except OSError as e:
            return NOT_EVALUABLE, (f"origin guard: NOT EVALUABLE: cannot read {f} "
                                   f"in a sibling worktree ({e})")
        try:
            wt_ids = _ids(wt_text)
        except model.ParseError:
            return NOT_EVALUABLE, (f"origin guard: NOT EVALUABLE: {rel} does not parse "
                                   f"in the worktree at {path}")
        committed = set()
        if bref and gitutil.resolves(cfg.repo, bref):
            try:
                t = gitutil.show(cfg.repo, bref, rel)
                committed = set(_ids(t)) if t is not None else set()
            except (gitutil.GitError, model.ParseError):
                committed = set()
        missing = [i for i in wt_ids if i not in committed and i not in known]
        if missing:
            gaps.append((f"{path} (uncommitted)", missing))
    if gaps:
        lines = ["origin guard: REFUSED: other branches hold step ids this file lacks:"]
        for ref, missing in gaps:
            lines.append(f"  {siblings.short(ref)}: {', '.join(missing)}")
        lines.append(f"  rebase or land first, then add (set {SKIP_ENV}=1 to override)")
        return REFUSED, "\n".join(lines)
    if readable == 0 and fs != "no-remote":
        # a successful fetch of a remote that has no branches yet (a freshly
        # created, empty repository) is nothing to compare, not a failure
        cp = gitutil.git(cfg.repo, "for-each-ref", "--format=%(refname)",
                         f"refs/remotes/{cfg.remote}/")
        if fs == "ok" and cp.returncode == 0 and not cp.stdout.strip():
            return OK, (f"origin guard: ok (remote {cfg.remote!r} has no branches yet; "
                        f"nothing to compare)")
        return NOT_EVALUABLE, ("origin guard: NOT EVALUABLE: no sibling ref could be read "
                               f"(set {SKIP_ENV}=1 to skip)")
    return OK, f"origin guard: ok ({readable} ref(s) read)"


def mint_task_id(ctx, domain) -> tuple:
    """(new_id, warnings). Max over: local queue+archive id fields and file
    names, and file names under queue/ and archive/ at every sibling ref."""
    cfg, g = ctx.cfg, ctx.g
    warnings = []
    top = 0
    for tf in ctx.store.files():
        if g.is_task_id(tf.id) and g.domain_of(tf.id) == domain:
            top = max(top, g.number_of(tf.id))
        n = g.name_number(tf.path.name, domain)
        if n is not None:
            top = max(top, n)
    if gitutil.is_repo(cfg.repo):
        fs = fetch_state(cfg)
        if fs == "missing":
            raise NotEvaluable(_missing_remote(cfg))
        try:
            refs = siblings.ref_set(cfg)
        except gitutil.GitError as e:
            refs = []
            warnings.append(f"warning: cannot enumerate worktrees ({e})")
        readable = 0
        paths = [cfg.rel(cfg.queue), cfg.rel(cfg.archive)]
        for ref in refs:
            if not gitutil.resolves(cfg.repo, ref):
                continue
            try:
                names = gitutil.ls_names(cfg.repo, ref, paths)
            except gitutil.GitError as e:
                warnings.append(f"warning: {siblings.short(ref)} could not be read ({e}); "
                                f"ids minted there are not counted")
                continue
            readable += 1
            for name in names:
                n = g.name_number(name, domain)
                if n is not None:
                    top = max(top, n)
        # file names in sibling worktrees' working copies: a task minted there
        # and not committed yet is invisible to every ref
        try:
            wts = siblings.sibling_worktrees(cfg)
        except gitutil.GitError:
            wts = []
        for path, _bref in wts:
            for rel in paths:
                d = path / rel
                try:
                    names = [q.name for q in d.iterdir()] if d.is_dir() else []
                except OSError as e:
                    warnings.append(f"warning: cannot list {d} ({e}); ids minted there "
                                    f"are not counted")
                    continue
                for name in names:
                    n = g.name_number(name, domain)
                    if n is not None:
                        top = max(top, n)
        if readable == 0 and fs != "no-remote":
            warnings.append("warning: no sibling ref readable; minted from the local "
                            "view only")
        elif fs == "failed":
            warnings.append(f"warning: `git fetch {cfg.remote}` failed; the id may be "
                            f"stale if someone minted since the last fetch")
    return g.make(domain, top + 1), warnings
