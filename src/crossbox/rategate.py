"""The rate gate: a looping agent is refused, not queued.

Every mutating verb is metered by its canonical identity (aliases resolve
first), before it runs; a batch verb counts once. Rolling window, default 30
calls per 600 s, per machine user: the state file lives outside the repository
(never synced), so all slots on a machine share one budget.

Exemption: `done`, `batch-done`, `skip`, `edit-step` and `release` are not counted when every step
ref (or claim id) they name is covered by an ACTIVE claim held by THIS slot.
Minting verbs and `reopen` are never exempt.

Failure policy: a corrupt or wrong-shape state file is treated as empty (and
rebuilt on the next write); an unwritable state file allows the call. Only a
trip blocks. A trip does not consume budget.

Environment:
  CROSSBOX_RATE_DISABLE=1     turn the gate off
  CROSSBOX_RATE_CAP=N         calls per window (N <= 0 disables)
  CROSSBOX_RATE_WINDOW=S      window in seconds
  CROSSBOX_RATE_STATE=PATH    state file
"""
from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

from . import claims as _claims
from . import lock, model
from .errors import CrossboxError, NotEvaluable

COUNTED = frozenset("""new add batch-add edit edit-step remove-step move-step swap
reorder done batch-done skip reopen archive unarchive renumber claim release
note clear-notes doctor-fix""".split())
EXEMPTABLE = frozenset({"done", "batch-done", "skip", "edit-step", "release"})


def state_path() -> Path:
    env = os.environ.get("CROSSBOX_RATE_STATE")
    if env:
        return Path(env)
    base = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(base) / "crossbox" / "rate-gate.json"


def settings(cfg) -> tuple:
    """(cap, window). An invalid environment value is NOT EVALUABLE naming the
    variable: a gate that silently ignores its setting is a gate that is off."""
    cap = cfg.rate_cap
    window = cfg.rate_window
    raw = os.environ.get("CROSSBOX_RATE_CAP")
    if raw:
        try:
            cap = int(raw)
        except ValueError:
            raise NotEvaluable(f"CROSSBOX_RATE_CAP={raw!r} is not an integer "
                               f"(N <= 0 disables the gate)") from None
    raw = os.environ.get("CROSSBOX_RATE_WINDOW")
    if raw:
        try:
            window = float(raw)
        except ValueError:
            window = float("nan")
        if not (math.isfinite(window) and window > 0):
            raise NotEvaluable(f"CROSSBOX_RATE_WINDOW={raw!r} is not a positive number "
                               f"of seconds")
    return cap, window


def _read(path: Path) -> list:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        stamps = data.get("stamps") if isinstance(data, dict) else None
        if not isinstance(stamps, list):
            return []
        return [float(s) for s in stamps if isinstance(s, (int, float))]
    except (OSError, ValueError, TypeError):
        return []


def my_active_claim_ids(ctx) -> list:
    """The ids `release --all-mine` would release (none when the claims file
    cannot be read: the call is then metered)."""
    try:
        rows = _claims.load_claims(ctx.cfg.claims)
    except _claims.ClaimsDamaged:
        return []
    return [str(c.get("id")) for c in rows if _claims.is_active(c)
            and _claims.is_mine(c, ctx.machine, ctx.slot)]


def exempt(ctx, verb: str, refs: list) -> bool:
    if verb not in EXEMPTABLE or not refs:
        return False
    try:
        rows = _claims.load_claims(ctx.cfg.claims)
    except _claims.ClaimsDamaged:
        return False
    mine = [c for c in rows if _claims.is_active(c)
            and _claims.is_mine(c, ctx.machine, ctx.slot)]
    covered = {str(c.get("step_ref")) for c in mine if c.get("step_ref")}
    covered |= {str(c.get("id")) for c in mine}
    return all(r in covered for r in refs)


def check(ctx, verb: str, refs: list = (), now: float = None) -> None:
    """Raise CrossboxError(1) on a trip; otherwise record the call."""
    if verb not in COUNTED:
        return
    if os.environ.get("CROSSBOX_RATE_DISABLE") == "1":
        return
    cap, window = settings(ctx.cfg)
    if cap <= 0:
        return
    if exempt(ctx, verb, list(refs)):
        return
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return  # unwritable state: allow (documented failure policy)
    # read-count-append-write under the machine-wide lock, so parallel callers
    # cannot each read the same short list and all pass
    with lock.hold_file(path.with_name(path.name + ".lock"), what="rate gate"):
        _check_locked(verb, path, cap, window, now)


def _check_locked(verb, path, cap, window, now):
    now = time.time() if now is None else now
    stamps = [s for s in _read(path) if now - s < window]
    if len(stamps) >= cap:
        retry = max(1, math.ceil(window - (now - min(stamps))))
        raise CrossboxError(
            f"rate gate: REFUSED `{verb}`: {len(stamps)} mutating calls in the last "
            f"{int(window)} s (cap {cap}) on this machine. If an agent is looping, stop "
            f"it; otherwise retry in ~{retry} s. (Closing or skipping a step you hold a claim on "
            f"is exempt.)")
    stamps.append(now)
    try:
        model.atomic_write_text(path, json.dumps({"stamps": stamps}))
    except OSError:
        pass  # unwritable state: allow
