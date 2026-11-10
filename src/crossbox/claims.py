"""Advisory claims: who is working on which step.

A claim is a row in ``claims.yaml``. It is advisory, and says so: crossbox warns
and names the other holder; it does not lock. A real lock across machines needs
a lease server and fencing tokens, which a file in git cannot give.

Identity. A claim stores ``machine`` and ``slot`` as separate fields, both in
canonical form (the slot of the primary checkout is ``main``). Its id namespace
is ``CLM-<machine>-NNN`` from the primary checkout and ``CLM-<machine>-<slot>-NNN``
from a linked worktree, so two slots, or one slot name on two machines, never
share a counter. "Mine" means same machine AND same slot, compared in that one
form everywhere (release --all-mine, claim-check, the rate-gate exemption).
"""
from __future__ import annotations

import datetime as _dt
import json
import re
from pathlib import Path
from typing import Optional

import yaml

from . import gitutil, model, siblings
from . import _yaml
from .errors import NOT_EVALUABLE, OK, REFUSED, CrossboxError, NotEvaluable

HEADER = ("# crossbox claims: advisory, append-only. Written by `crossbox claim` and\n"
          "# `crossbox release`; merged across branches by `crossbox merge-driver`.\n")
CLAIM_KEYS = ["id", "machine", "slot", "subject", "source", "intent", "created",
              "status", "released", "step_ref", "renumbered_from", "carried_from"]
IDENTITY = ("id", "machine", "slot", "step_ref", "created", "subject")


CLAIM_STATUSES = ("active", "released")


class ClaimsDamaged(Exception):
    pass


# ---------------------------------------------------------------- file IO

def parse_claims(text: str) -> list:
    try:
        data = _yaml.load_strict(text)
    except yaml.YAMLError as e:
        raise ClaimsDamaged(f"does not parse: {str(e).splitlines()[0]}")
    if data is None:
        return []
    if not isinstance(data, dict):
        raise ClaimsDamaged("top level is not a mapping")
    rows = data.get("claims")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ClaimsDamaged("`claims` is not a list")
    for i, r in enumerate(rows):
        if not isinstance(r, dict):
            raise ClaimsDamaged(f"claim #{i + 1} is not a mapping")
        if "status" in r:
            # an unread status must not read as unclaimed: two sessions would take one step
            r["status"] = str(r["status"]).strip().lower()
            if r["status"] not in CLAIM_STATUSES:
                raise ClaimsDamaged(f"claim #{i + 1} has unknown status {r['status']!r} "
                                    f"({' or '.join(CLAIM_STATUSES)})")
        ref = r.get("step_ref")
        if isinstance(ref, str) and "." in ref:
            task, _, step = ref.strip().rpartition(".")
            r["step_ref"] = f"{task.upper()}.{step.lower()}"
    return rows


def load_claims(path: Path) -> list:
    if not path.exists():
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise ClaimsDamaged(f"unreadable: {e}")
    return parse_claims(text)


def render_claims(rows: list) -> str:
    ordered = []
    for r in rows:
        o = {k: r[k] for k in CLAIM_KEYS if k in r}
        for k, v in r.items():
            if k not in o:
                o[k] = v
        ordered.append(o)
    return HEADER + model.dump_yaml({"claims": ordered})


def save_claims(path: Path, rows: list) -> None:
    if model.has_surrogate(rows):
        raise CrossboxError(f"claims.yaml: {model.NOT_UNICODE}; nothing written")
    text = render_claims(rows)
    if parse_claims(text) != [dict(r) for r in rows]:
        raise CrossboxError("claims.yaml: round-trip gate failed; nothing written")
    original = path.read_text(encoding="utf-8") if path.exists() else None
    model.atomic_write_text(path, text)
    try:
        now_text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        now_text = None
    try:
        ok = now_text is not None and parse_claims(now_text) == [dict(r) for r in rows]
    except ClaimsDamaged:
        ok = False
    if not ok:
        model.restore_if_ours(path, text, now_text, original, parses=parse_claims)


# ---------------------------------------------------------------- identity

def c_machine(c: dict) -> str:
    return str(c.get("machine") or c.get("box") or "")


def c_slot(c: dict) -> str:
    s = c.get("slot")
    return str(s) if s not in (None, "") else "main"


def is_mine(c: dict, machine: str, slot: str) -> bool:
    return c_machine(c) == machine and c_slot(c) == slot


def is_active(c: dict) -> bool:
    return c.get("status", "active") == "active"


def namespace(machine: str, slot: str) -> str:
    return machine if slot == "main" else f"{machine}-{slot}"


def next_claim_id(rows: list, ns: str) -> str:
    pat = re.compile(rf"CLM-{re.escape(ns)}-(\d+)")
    n = 0
    for r in rows:
        m = pat.fullmatch(str(r.get("id", "")))
        if m:
            n = max(n, int(m.group(1)))
    return f"CLM-{ns}-{n + 1:03d}"


def as_date(v) -> Optional[_dt.date]:
    if isinstance(v, _dt.datetime):
        return v.date()
    if isinstance(v, _dt.date):
        return v
    try:
        return _dt.date.fromisoformat(str(v).strip())
    except ValueError:
        return None


def is_stale(c: dict, today: _dt.date, days: int) -> bool:
    if not is_active(c):
        return False
    d = as_date(c.get("created"))
    if d is None:
        return False
    return (today - d).days > days


# ---------------------------------------------------------------- sibling union

class View:
    """Local claims plus foreign claims read from sibling refs."""

    def __init__(self):
        self.local: list = []
        self.union: list = []          # merged, release-monotonic
        self.foreign: list = []        # rows of union not present locally
        self.refs_read: list = []
        self.refs_missing: list = []   # resolve but have no claims file
        self.unparsed: list = []       # (ref, problem)
        self.unreadable: list = []     # (ref, git error)
        self.not_evaluable: Optional[str] = None
        self.source: dict = {}         # claim identity -> ref it was first seen at
        self.id_collisions: dict = {}  # id -> number of distinct claims using it

    @property
    def incomplete(self) -> Optional[str]:
        """The ONE completeness state every consumer reads: None when the view
        holds every sibling it set out to read, else why it does not. A view
        with a hole in it must never be scored as if the hidden claims did
        not exist (release review, blocker 3)."""
        why = []
        if self.not_evaluable:
            why.append(self.not_evaluable)
        if self.unparsed:
            why.append("sibling claims unparseable at "
                       + ", ".join(siblings.short(r) for r, _p in self.unparsed))
        if self.unreadable:
            why.append("sibling claims unreadable at "
                       + ", ".join(siblings.short(r) for r, _p in self.unreadable))
        return "; ".join(why) or None


def read_view(ctx, local_only: bool = False) -> View:
    """Raises ClaimsDamaged when the LOCAL file is damaged."""
    v = View()
    v.local = load_claims(ctx.cfg.claims)
    # keyed by claim IDENTITY, never by id alone: two clones that share a
    # machine and slot name can mint one id for two different claims, and a
    # release of one must never release the other
    merged = {}
    order = []
    for c in v.local:
        k = _identity(c)
        if k not in merged:
            merged[k] = dict(c)
            order.append(k)
    if not local_only and gitutil.is_repo(ctx.repo):
        try:
            refs = siblings.ref_set(ctx.cfg)
        except gitutil.GitError as e:
            v.not_evaluable = f"cannot enumerate worktrees: {e}"
            refs = []
        rel = ctx.cfg.rel(ctx.cfg.claims)
        for ref in refs:
            if not gitutil.resolves(ctx.repo, ref):
                continue
            try:
                text = gitutil.show(ctx.repo, ref, rel)
            except gitutil.GitError as e:
                v.unreadable.append((ref, str(e)))
                continue
            if text is None:
                v.refs_missing.append(ref)
                continue
            try:
                rows = parse_claims(text)
            except ClaimsDamaged as e:
                v.unparsed.append((ref, str(e)))
                continue
            v.refs_read.append(ref)
            for c in rows:
                k = _identity(c)
                if k not in merged:
                    merged[k] = dict(c)
                    order.append(k)
                    v.source[k] = ref
                elif not is_active(c) and is_active(merged[k]):
                    # release is monotonic: released anywhere = released
                    merged[k]["status"] = "released"
                    if c.get("released"):
                        merged[k]["released"] = c.get("released")
    local_keys = {_identity(c) for c in v.local}
    v.union = [merged[k] for k in order]
    v.foreign = [c for c in v.union if _identity(c) not in local_keys]
    ids = {}
    for c in v.union:
        ids.setdefault(str(c.get("id")), set()).add(_identity(c))
    v.id_collisions = {i: len(ks) for i, ks in ids.items() if len(ks) > 1}
    return v


def active_step_claims(rows: list) -> dict:
    out = {}
    for c in rows:
        if is_active(c) and c.get("step_ref"):
            out.setdefault(str(c["step_ref"]), []).append(c)
    return out


# ---------------------------------------------------------------- verbs

def cmd_claim(ctx, subject: str, source: str = "", intent: str = "",
              steps: Optional[list] = None, slot: Optional[str] = None) -> int:
    subject = model.one_line(subject or "")
    if not subject:
        raise CrossboxError("empty claim subject")
    my_slot = slot or ctx.slot
    try:
        rows = load_claims(ctx.cfg.claims)
    except ClaimsDamaged as e:
        raise NotEvaluable(f"{ctx.cfg.rel(ctx.cfg.claims)} is damaged ({e}); "
                           f"refusing to append to it")
    bindings = []
    if steps:
        for s in steps:
            ctx.g.parse_ref(s)
            bindings.append(s)
    elif ctx.g.is_ref(subject):
        bindings.append(subject)
    else:
        bindings.append(None)
    # warn (never refuse) about other holders of the same step
    try:
        view = read_view(ctx)
        held = active_step_claims(view.union)
    except ClaimsDamaged:
        held = {}
        view = None
    if view is not None and view.incomplete:
        ctx.warn(f"warning: sibling claims view incomplete ({view.incomplete}); "
                 f"another holder of this step may be hidden")
    for b in bindings:
        if b is None:
            continue
        tid, sid = ctx.g.parse_ref(b)
        try:
            tf = ctx.store.find(tid)
        except CrossboxError:
            tf = None
        if tf is None or tf.step(sid) is None:
            ctx.warn(f"warning: {b} does not name an existing step (claimed anyway)")
        for other in held.get(b, []):
            who = f"{c_machine(other)}/{c_slot(other)}"
            ctx.warn(f"warning: {b} is already claimed by {other.get('id')} ({who}); "
                     f"claims are advisory: talk to them before working it")
    ns = namespace(ctx.machine, my_slot)
    mine = {r.get("step_ref"): r.get("id") for r in rows
            if isinstance(r, dict) and r.get("status") == "active" and r.get("step_ref")
            and r.get("machine") == ctx.machine and r.get("slot") == my_slot}
    added = 0
    for b in bindings:
        if b and b in mine:
            ctx.say(f"you already hold {mine[b]} on {b}; nothing added")
            continue
        added += 1
        row = {"id": next_claim_id(rows, ns), "machine": ctx.machine, "slot": my_slot,
               "subject": subject, "source": source or "", "intent": intent or "",
               "created": ctx.date_str, "status": "active"}
        if b:
            row["step_ref"] = b
        rows.append(row)
        if b:
            ctx.say(f"claimed {row['id']} -> {b}")
        else:
            ctx.say(f"claimed {row['id']} (UNBOUND: not tied to a step, so no "
                    f"collision check, readiness view or rate-gate exemption can see it; "
                    f"use a step ref as the subject or --step)")
    if not added:
        return OK
    save_claims(ctx.cfg.claims, rows)
    ctx.say("This claim exists only in this tree until you commit and push "
            f"{ctx.cfg.rel(ctx.cfg.claims)}.")
    return OK


def cmd_release(ctx, ids: Optional[list] = None, all_mine: bool = False,
                all_box: bool = False, include_other_slots: bool = False) -> int:
    try:
        rows = load_claims(ctx.cfg.claims)
    except ClaimsDamaged as e:
        raise NotEvaluable(f"{ctx.cfg.rel(ctx.cfg.claims)} is damaged ({e})")
    selected = []
    refused = []
    if ids:
        want = set(ids)
        selected = [r for r in rows if str(r.get("id")) in want]
        missing = want - {str(r.get("id")) for r in selected}
        for m in sorted(missing):
            ctx.warn(f"{m}: no such claim in {ctx.cfg.rel(ctx.cfg.claims)}")
        denom = len(want)
    elif all_mine:
        selected = [r for r in rows if is_active(r) and is_mine(r, ctx.machine, ctx.slot)]
        denom = len(selected)
    elif all_box:
        mine_box = [r for r in rows if is_active(r) and c_machine(r) == ctx.machine]
        for r in mine_box:
            if c_slot(r) == ctx.slot or include_other_slots:
                selected.append(r)
            else:
                refused.append(r)
        denom = len(mine_box)
    else:
        raise CrossboxError("name claim ids, or pass --all-mine or --all-machine", 64)
    released = 0
    already = 0
    for r in selected:
        if is_active(r):
            r["status"] = "released"
            r["released"] = ctx.date_str
            released += 1
        else:
            already += 1
    for r in refused:
        ctx.warn(f"skipped {r.get('id')}: held by slot {c_slot(r)} "
                 f"(pass --include-other-slots to release peers' claims)")
    if released:
        save_claims(ctx.cfg.claims, rows)
    extra = f" ({already} already released)" if already else ""
    ctx.say(f"released {released} of {denom}{extra}")
    if released == 0 and already == 0:
        return REFUSED
    return OK


def _visibility(ctx) -> list:
    lines = []
    if not gitutil.is_repo(ctx.repo):
        return ["visibility: not a git repository; nobody else can see these claims"]
    rel = ctx.cfg.rel(ctx.cfg.claims)
    cp = gitutil.git(ctx.repo, "status", "--porcelain", "--", rel)
    if cp.returncode == 0 and cp.stdout.strip():
        lines.append(f"visibility: {rel} has UNCOMMITTED changes; no other tree can "
                     f"see them until you commit and push")
    up = ctx.cfg.upstream
    if not up or not gitutil.resolves(ctx.repo, up):
        lines.append("visibility: NOT EVALUABLE: no upstream ref to compare with "
                     "(set `upstream:` in config or fetch)")
    else:
        cp = gitutil.git(ctx.repo, "log", "--oneline", f"{up}..HEAD", "--", rel)
        if cp.returncode == 0 and cp.stdout.strip():
            lines.append(f"visibility: {rel} has commits not yet on {up}; other "
                         f"machines see them only after they land")
    return lines


def first_upstream_depth(ctx, claim_id: str) -> Optional[int]:
    """How early the claim reached the upstream: the ancestor count of the first
    upstream commit whose diff adds ``id: <claim_id>`` (end-anchored)."""
    up = ctx.cfg.upstream
    if not up or not gitutil.resolves(ctx.repo, up):
        return None
    esc = re.sub(r"([.\[\]*^$\\])", r"\\\1", claim_id)
    rel = ctx.cfg.rel(ctx.cfg.claims)
    cp = gitutil.git(ctx.repo, "log", up, "--format=%H", "--reverse",
                     f"-Gid: {esc}$", "--", rel)
    if cp.returncode != 0 or not cp.stdout.strip():
        return None
    first = cp.stdout.split()[0]
    cp2 = gitutil.git(ctx.repo, "rev-list", "--count", first)
    try:
        return int(cp2.stdout.strip())
    except ValueError:
        return None


def collisions(ctx, rows: list) -> list:
    """Steps held by active claims from >= 2 distinct (machine, slot) pairs."""
    out = []
    for ref, cs in sorted(active_step_claims(rows).items()):
        holders = {(c_machine(c), c_slot(c)) for c in cs}
        if len(holders) < 2:
            continue
        depths = {str(c.get("id")): first_upstream_depth(ctx, str(c.get("id"))) for c in cs}
        reached = {k: d for k, d in depths.items() if d is not None}
        if reached:
            winner = min(reached, key=lambda k: (reached[k], k))
            verdict = f"{winner} reached upstream first and keeps the step"
        else:
            verdict = "NOT EVALUABLE: none of these claims has reached upstream; push, then re-run"
        out.append({"step_ref": ref, "claims": sorted(depths), "verdict": verdict})
    return out


def cmd_claims(ctx, show_all: bool = False, as_json: bool = False) -> int:
    ctx.require_queue("claims")
    try:
        view = read_view(ctx)
    except ClaimsDamaged as e:
        raise NotEvaluable(f"{ctx.cfg.rel(ctx.cfg.claims)} is damaged ({e})")
    local_keys = {_identity(c) for c in view.local}
    # local rows as the union sees them (a release anywhere is a release here)
    local_all = [c for c in view.union if _identity(c) in local_keys]
    local = local_all if show_all else [c for c in local_all if is_active(c)]
    foreign_active = [c for c in view.foreign if is_active(c)]
    stale = [c for c in view.union if is_stale(c, ctx.today, ctx.cfg.stale_days)]
    stale_ids = {str(c.get("id")) for c in stale}
    coll = collisions(ctx, view.union)
    vis = _visibility(ctx)
    if as_json:
        payload = {
            "machine": ctx.machine, "slot": ctx.slot,
            "local": local, "local_total": len(view.local),
            "foreign_active": [dict(c, seen_at=siblings.short(view.source.get(_identity(c), '')))
                               for c in foreign_active],
            "id_collisions": view.id_collisions,
            "orphaned": [c.get("id") for c in orphaned(ctx, view.union)],
            "sibling_refs_read": len(view.refs_read),
            "sibling_refs_unparsed": [{"ref": r, "problem": p} for r, p in view.unparsed],
            "sibling_refs_unreadable": [{"ref": r, "problem": p} for r, p in view.unreadable],
            "stale": [c.get("id") for c in stale],
            "collisions": coll, "visibility": vis,
            "not_evaluable": view.incomplete,
        }
        ctx.say(json.dumps(payload, indent=2, default=str))
        return NOT_EVALUABLE if view.incomplete else OK
    label = "all" if show_all else "active"
    ctx.say(f"{len(local)} {label} local claim(s) of {len(view.local)} in "
            f"{ctx.cfg.rel(ctx.cfg.claims)}")
    for c in local:
        _print_claim(ctx, c, stale_ids=stale_ids)
    ctx.say(f"siblings: {len(foreign_active)} foreign active claim(s) from "
            f"{len(view.refs_read)} readable ref(s)"
            + (f", {len(view.unparsed)} unparseable" if view.unparsed else ""))
    for c in foreign_active:
        src = siblings.short(view.source.get(_identity(c), "?"))
        _print_claim(ctx, c, tag=f"[from {src}]", stale_ids=stale_ids)
    for ref, prob in view.unparsed:
        ctx.say(f"  UNPARSEABLE claims file at {siblings.short(ref)}: {prob}")
    for ref, prob in view.unreadable:
        ctx.say(f"  UNREADABLE claims file at {siblings.short(ref)}: {prob}")
    if stale:
        ctx.say(f"stale (active, created more than {ctx.cfg.stale_days} days ago; "
                f"advisory only, never auto-released): {len(stale)} of "
                f"{sum(1 for c in view.union if is_active(c))} active")
    for col in coll:
        ctx.say(f"COLLISION on {col['step_ref']}: {', '.join(col['claims'])}: {col['verdict']}")
    for cid, n in sorted(view.id_collisions.items()):
        ctx.say(f"ID COLLISION: {n} different claims use the id {cid} (two clones share a "
                f"machine and slot name?); the merge driver renumbers one on the next merge; "
                f"release by id only after that")
    for c in orphaned(ctx, view.union):
        ctx.say(f"ORPHANED {c.get('id')} -> {c.get('step_ref')}: names no existing step")
    for line in vis:
        ctx.say(line)
    if view.incomplete:
        ctx.say(f"siblings: NOT EVALUABLE: {view.incomplete}")
        return NOT_EVALUABLE
    return OK


def _print_claim(ctx, c, tag="", stale_ids=()):
    bits = [str(c.get("id")), f"{c_machine(c)}/{c_slot(c)}",
            f"-> {c['step_ref']}" if c.get("step_ref") else "(unbound)",
            str(c.get("status", "active")), f"created {c.get('created')}"]
    if str(c.get("id")) in stale_ids:
        bits.append("STALE")
    if tag:
        bits.append(tag)
    ctx.say("  " + "  ".join(bits) + f"\n      {c.get('subject', '')}")


STOP = set("""the and for with from into onto this that these those then than when
what which while where will would should could have has had are was were been
being not but all any can its it's our out use used using via per add fix make
new update step task work more less also only just some each other about after
before over under between within without again""".split())
TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._-]+")


def tokens(text: str) -> set:
    return {t for t in TOKEN_RE.findall((text or "").lower())
            if len(t) > 2 and t not in STOP}


def cmd_claim_check(ctx, title: str, source: str = "", quiet: bool = False) -> int:
    """Exit 0 clean, 1 overlap found, 2 not evaluable (never "no overlap" when
    the sibling view could not be read)."""
    if not ctx.cfg.queue.is_dir():
        if not quiet:
            ctx.say(f"claim-check: NOT EVALUABLE: {ctx.cfg.rel(ctx.cfg.queue)} does not "
                    f"exist; nothing was checked")
        return NOT_EVALUABLE
    try:
        view = read_view(ctx)
    except ClaimsDamaged as e:
        if not quiet:
            ctx.say(f"claim-check: NOT EVALUABLE: local claims damaged ({e})")
        return NOT_EVALUABLE
    mine = tokens(title) | tokens(source)
    src_base = Path(source).name if source else ""
    hits = []
    for c in view.union:
        if not is_active(c) or is_mine(c, ctx.machine, ctx.slot):
            continue
        theirs = tokens(" ".join(str(c.get(k, "")) for k in ("subject", "intent", "source")))
        shared = mine & theirs
        same_src = bool(src_base) and Path(str(c.get("source") or "")).name == src_base
        if len(shared) >= 2 or same_src:
            hits.append((c, sorted(shared), same_src))
    ne = view.incomplete
    if not quiet or hits:
        for c, shared, same_src in hits:
            why = "same source" if same_src else f"shared: {', '.join(shared)}"
            ctx.say(f"overlap: {c.get('id')} ({c_machine(c)}/{c_slot(c)}) "
                    f"{c.get('subject', '')!r} [{why}]")
    active_other = sum(1 for c in view.union if is_active(c)
                       and not is_mine(c, ctx.machine, ctx.slot))
    if hits:
        if not quiet:
            ctx.say(f"claim-check: {len(hits)} overlapping of {active_other} active "
                    f"claim(s) held elsewhere" + (f" (view incomplete: {ne})" if ne else ""))
        return REFUSED
    if ne:
        if not quiet:
            ctx.say(f"claim-check: NOT EVALUABLE: {ne}; 0 overlaps among what was readable "
                    f"is not 'no overlap'")
        return NOT_EVALUABLE
    if not quiet:
        ctx.say(f"claim-check: no overlap with {active_other} active claim(s) held elsewhere")
    return OK


# ---------------------------------------------------------------- claims on renamed steps

def orphaned(ctx, rows: list) -> list:
    """Active claims whose step_ref names no step in the queue or archive."""
    out = []
    for c in rows:
        ref = c.get("step_ref")
        if not is_active(c) or not ref:
            continue
        try:
            tid, sid = ctx.g.parse_ref(str(ref))
            tf = ctx.store.find(tid)
        except CrossboxError:
            continue  # an ambiguous id is doctor's business, not an orphan
        if tf is None or (tf.ok and tf.step(sid) is None):
            out.append(c)
    return out


def plan_carry(ctx, mapping: dict, force: bool = False) -> list:
    """Before a verb renames steps (``mapping``: old ref -> new ref), decide
    what happens to the claims bound to them. This slot's own claims are
    carried to the new ref. A claim held by anyone else cannot be rewritten
    from here, so the rename is refused unless ``force``. Returns the local
    claim ids to carry."""
    mapping = {o: n for o, n in mapping.items() if o != n}
    if not mapping:
        return []
    try:
        view = read_view(ctx)
    except ClaimsDamaged as e:
        raise NotEvaluable(f"{ctx.cfg.rel(ctx.cfg.claims)} is damaged ({e}); cannot "
                           f"tell whether a claim binds a step this would rename")
    local_keys = {_identity(c) for c in view.local}
    carry, foreign = [], []
    for c in view.union:
        if not is_active(c) or str(c.get("step_ref")) not in mapping:
            continue
        if _identity(c) in local_keys and is_mine(c, ctx.machine, ctx.slot):
            carry.append(_identity(c))
        else:
            foreign.append(c)
    if foreign:
        names = ", ".join(f"{c.get('id')} ({c_machine(c)}/{c_slot(c)}) on {c.get('step_ref')}"
                          for c in foreign)
        if not force:
            raise CrossboxError(
                f"refusing to rename claimed step(s): {names} would then bind different "
                f"work, and another session's claim cannot be rewritten from here. Ask "
                f"the holder to release it, or pass --force")
        ctx.warn(f"warning: --force: {names} still bind(s) the OLD step id(s), which now "
                 f"name different work; tell the holder")
    if view.incomplete:
        ctx.warn(f"warning: sibling claims view incomplete ({view.incomplete}); a claim "
                 f"on a renamed step may be hidden")
    return carry


def _active_on(ctx, ref: str):
    """(view, mine, foreign): active claims on ``ref``; ``mine`` are this
    slot's own (releasable from here), ``foreign`` are anyone else's."""
    try:
        view = read_view(ctx)
    except ClaimsDamaged as e:
        raise NotEvaluable(f"{ctx.cfg.rel(ctx.cfg.claims)} is damaged ({e}); cannot "
                           f"tell whether a claim binds {ref}")
    local_keys = {_identity(c) for c in view.local}
    mine, foreign = [], []
    for c in view.union:
        if not is_active(c) or str(c.get("step_ref")) != ref:
            continue
        if _identity(c) in local_keys and is_mine(c, ctx.machine, ctx.slot):
            mine.append(c)
        else:
            foreign.append(c)
    return view, mine, foreign


def plan_removal(ctx, ref: str, force: bool = False) -> list:
    """Before a verb deletes the step ``ref``: an active claim must not outlive
    the work it names (the id can be minted again for different work). Without
    ``force`` any active claim refuses the removal. With ``force`` this slot's
    own claims are released (returned, for ``release_claims``); another
    session's cannot be touched from here, so it is warned about, and ``add``
    will not mint the id while it stands."""
    view, mine, foreign = _active_on(ctx, ref)
    if (mine or foreign) and not force:
        names = ", ".join(f"{c.get('id')} ({c_machine(c)}/{c_slot(c)})" for c in mine + foreign)
        raise CrossboxError(
            f"refusing to remove claimed step {ref}: {names} would outlive the work it "
            f"names. Release it first (`crossbox release`), ask the holder, or pass "
            f"--force (which releases your own claim and cannot touch anyone else's)")
    if foreign:
        names = ", ".join(f"{c.get('id')} ({c_machine(c)}/{c_slot(c)})" for c in foreign)
        ctx.warn(f"warning: --force: {names} still bind(s) {ref}, which no longer exists; "
                 f"tell the holder (`add` will not reuse the id while the claim stands)")
    if view.incomplete:
        ctx.warn(f"warning: sibling claims view incomplete ({view.incomplete}); a claim "
                 f"on {ref} may be hidden")
    return [_identity(c) for c in mine]


def release_claims(ctx, identities: list) -> None:
    """Release this slot's claims (by identity) after their step was removed."""
    if not identities:
        return
    rows = load_claims(ctx.cfg.claims)
    want = set(identities)
    for r in rows:
        if _identity(r) in want and is_active(r):
            r["status"] = "released"
            r["released"] = ctx.date_str
            ctx.say(f"released claim {r.get('id')} (its step was removed)")
    save_claims(ctx.cfg.claims, rows)


def refuse_claimed_id(ctx, ref: str) -> None:
    """``add`` is about to mint ``ref``: refuse while any active claim already
    names it, so a new step can never inherit a claim made for other work."""
    try:
        _view, mine, foreign = _active_on(ctx, ref)
    except NotEvaluable as e:
        ctx.warn(f"warning: {e}; the new step's id was not checked against claims")
        return
    held = mine + foreign
    if held:
        names = ", ".join(f"{c.get('id')} ({c_machine(c)}/{c_slot(c)})" for c in held)
        raise CrossboxError(
            f"refusing to add {ref}: active claim {names} already names that id and "
            f"would bind the new work. Release it (or have the holder release it) first")


def apply_carry(ctx, carry: list, mapping: dict) -> None:
    """Carry this slot's claims to the renamed steps: release the old claim
    and mint a new one on the new ref (``carried_from`` names the old id).
    Releasing and re-minting, instead of editing ``step_ref`` in place, keeps
    every claim's identity fixed, so copies on other branches merge cleanly."""
    if not carry:
        return
    rows = load_claims(ctx.cfg.claims)
    ns = namespace(ctx.machine, ctx.slot)
    want = set(carry)
    for r in list(rows):
        if _identity(r) not in want or not is_active(r):
            continue
        old_ref = str(r.get("step_ref"))
        new_ref = mapping[old_ref]
        r["status"] = "released"
        r["released"] = ctx.date_str
        subject = r.get("subject")
        new = {"id": next_claim_id(rows, ns), "machine": ctx.machine, "slot": ctx.slot,
               "subject": new_ref if subject == old_ref else subject,
               "source": r.get("source", ""), "intent": r.get("intent", ""),
               "created": ctx.date_str, "status": "active", "step_ref": new_ref,
               "carried_from": r.get("id")}
        rows.append(new)
        ctx.say(f"carried claim {r.get('id')} -> {new['id']} (now on {new_ref})")
    save_claims(ctx.cfg.claims, rows)


# ---------------------------------------------------------------- merge driver

def _key(c: dict) -> tuple:
    """What a claim IS, apart from its id: who made it, when, about what."""
    return (c_machine(c), c_slot(c), str(c.get("created") or ""),
            str(c.get("subject") or ""), str(c.get("step_ref") or ""))


def _origin(c: dict) -> str:
    """The id the claim was minted with (a renumbered copy remembers it)."""
    return str(c.get("renumbered_from") or c.get("id"))


def _identity(c: dict) -> tuple:
    return (_origin(c),) + _key(c)


def _split_id(cid: str) -> tuple:
    m = re.fullmatch(r"(.*)-(\d+)", cid)
    return (m.group(1), int(m.group(2))) if m else (cid, None)


def _assign_ids(rows: list, cands: dict) -> None:
    """Give every claim a unique id, as a pure function of the SET of claims,
    so merging in either order, or merging again, gives the same ids.

    Claims minted with one id form a group; the one whose key sorts lowest
    keeps that id, the others are renumbered (``renumbered_from`` records the
    minted id). A renumbered claim keeps the number it already had when that
    number is still free."""
    groups = {}
    for r in rows:
        groups.setdefault(_origin(r), []).append(r)
    taken = set(groups)
    rest = []
    for origin, rs in groups.items():
        rs.sort(key=_key)
        first = rs[0]
        first["id"] = origin
        first.pop("renumbered_from", None)
        rest.extend((origin, r) for r in rs[1:])
    rest.sort(key=lambda x: (x[0], _key(x[1])))
    for origin, r in rest:
        base, _n = _split_id(origin)
        keep = sorted((c for c in cands.get(id(r), ()) if c != origin and c not in taken
                       and _split_id(c)[0] == base), key=lambda c: _split_id(c)[1] or 0)
        if keep:
            new = keep[0]
        else:
            used = [_split_id(t)[1] for t in taken if _split_id(t)[0] == base]
            new = f"{base}-{max([u for u in used if u is not None] + [0]) + 1:03d}"
        r["id"] = new
        r["renumbered_from"] = origin
        taken.add(new)


def merge_rows(ours: list, theirs: list) -> list:
    """Union two claim lists. Records match on identity (the minted id plus
    machine, slot, created, subject and step), so a copy the other side
    renumbered is still the same claim. Statuses only move forward. The
    result is idempotent and commutative up to row order."""
    out, by, cands = [], {}, {}
    for c in list(ours) + list(theirs):
        k = _identity(c)
        if k not in by:
            o = dict(c)
            by[k] = o
            out.append(o)
            cands[id(o)] = {str(c.get("id"))}
            continue
        o = by[k]
        cands[id(o)].add(str(c.get("id")))
        for f, v in c.items():
            if f in ("id", "renumbered_from"):
                continue
            if f not in o or o[f] in (None, ""):
                o[f] = v
        if not is_active(c):
            if is_active(o):
                o["status"] = "released"
                if c.get("released"):
                    o["released"] = c["released"]
            elif c.get("released") and (not o.get("released")
                                        or str(c["released"]) < str(o["released"])):
                o["released"] = c["released"]
    _assign_ids(out, cands)
    return out


def merge_driver(base: str, ours: str, theirs: str) -> int:
    """git merge driver: %O %A %B. Writes the result to ``ours`` (%A). Base is
    ignored: the file is append-only, so the base is never evidence that a
    claim was deleted. Exit 1 on anything unexpected so git keeps a textual
    conflict."""
    try:
        o = parse_claims(Path(ours).read_text(encoding="utf-8"))
        t = parse_claims(Path(theirs).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ClaimsDamaged) as e:
        print(f"crossbox merge-driver: cannot merge claims ({e}); leaving a conflict",
              file=__import__("sys").stderr)
        return REFUSED
    merged = merge_rows(o, t)
    model.atomic_write_text(Path(ours), render_claims(merged))
    return OK
