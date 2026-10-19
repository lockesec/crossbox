"""Readiness on this machine: `crossbox ready` (= `crossbox needs here`), plus
`needs registry` and `needs audit`.

Every open step is classified, in this order:
  file/step shape -> status -> machine (target) -> claim -> depends_on shape ->
  deps met -> gates
into READY, GATED, CLAIMED or WAITING, or MALFORMED (excluded from every other
bucket, readiness unknown). Steps whose target this machine does not provide
are counted as "elsewhere" and not classified further.

Claims are read through the sibling union (other worktrees' branches and the
upstream), not only the local file.
"""
from __future__ import annotations

import json

from . import claims as _claims
from . import deps as _deps
from . import model, needs
from .config import PRIORITY_RANK
from .errors import NOT_EVALUABLE, OK, REFUSED


def needs_problems(reg, nl) -> tuple:
    """(bad, unevaluable) for a `needs` list of strings: ``bad`` are causes that
    make the step MALFORMED, ``unevaluable`` are tokens that cannot be checked
    because there is no registry. Shared by `ready` and `plan`."""
    bad, unevaluable = [], []
    for n in nl:
        r = reg.resolve(n)[0]
        if r["status"] == "split":
            bad.append(f"need {n!r} contains spaces")
        elif r["status"] == "unknown" and reg.present:
            sug = f" (did you mean {r['suggestion']}?)" if r["suggestion"] else ""
            bad.append(f"unknown need {n!r}{sug}")
        elif r["status"] == "unknown":
            unevaluable.append(n)
    return bad, unevaluable


_c = model.clean


def classify(ctx, operator=False, probes=True, keep_elsewhere=False) -> dict:
    """Raises claims.ClaimsDamaged when the local claims file is damaged, and
    NotEvaluable when the queue is missing. ``keep_elsewhere`` also records the
    rows counted as "elsewhere" (with their target) under ``elsewhere_rows``,
    for `why`; the default output is unchanged."""
    ctx.require_queue("ready")
    reg = needs.Registry.load(ctx.cfg.registry)
    view = _claims.read_view(ctx)
    held = _claims.active_step_claims(view.union)
    res = {"machine": ctx.machine, "slot": ctx.slot, "ready": [], "gated": [],
           "claimed": [], "waiting": [], "malformed": [], "elsewhere": 0,
           "open_here": 0, "claims_view": view.incomplete or "ok"}
    qfiles = ctx.store.files("queue")
    holders = {}
    for tf in qfiles:
        if tf.ok:
            holders.setdefault(tf.id, []).append(tf.path.name)
    for tf in qfiles:
        if not tf.ok:
            res["malformed"].append({"ref": tf.id, "file": tf.path.name,
                                     "cause": f"task file does not parse ({tf.error})"})
            continue
        if len(holders.get(tf.id, ())) > 1:
            # one id, several files: every step ref under it is ambiguous, so
            # none is READY and each file is reported
            res["malformed"].append({
                "ref": tf.id, "file": tf.path.name,
                "cause": f"task id defined in {len(holders[tf.id])} queue files "
                         f"({', '.join(holders[tf.id])}); `crossbox doctor check`"})
            continue
        # the writers' own shape check: a file no writer will touch (no id,
        # steps not a list, a step that is not a mapping, a missing, invalid
        # or duplicated step id) has no addressable steps, so none is READY
        probs = model.structure_problems(tf.data)
        if probs:
            res["malformed"].append({"ref": tf.id, "file": tf.path.name,
                                     "cause": "; ".join(probs)})
            continue
        steps = tf.data.get("steps")
        prio = str(tf.data.get("priority", "normal"))
        for s in steps or []:
            ref = f"{tf.id}.{s.get('id')}"
            st = s.get("status")
            if st in model.TERMINAL:
                continue
            row = {"ref": ref, "title": s.get("title", ""), "priority": prio}
            if st not in model.OPEN:
                res["malformed"].append(dict(row, cause=f"unknown status {st!r}"))
                continue
            t, known = _deps.read_target(s.get("target"), ctx.cfg)
            if not known:
                res["malformed"].append(dict(row, cause=f"unknown target {t!r}"))
                continue
            if not (t == "any" or t in ctx.machine_caps):
                res["elsewhere"] += 1
                if keep_elsewhere:
                    res.setdefault("elsewhere_rows", []).append(dict(row, target=t))
                continue
            res["open_here"] += 1
            if ref in held:
                row["claims"] = [f"{c.get('id')} ({_claims.c_machine(c)}/{_claims.c_slot(c)})"
                                 for c in held[ref]]
                res["claimed"].append(row)
                continue
            dl = s.get("depends_on", [])
            if dl is None:
                dl = []
            if not isinstance(dl, list):
                res["malformed"].append(dict(row, cause="depends_on is not a list"))
                continue
            if any(not isinstance(d, str) for d in dl):
                res["malformed"].append(dict(row, cause="depends_on has a non-string element"))
                continue
            states = [(d, *_deps.dep_state(d, tf, ctx.store)) for d in dl]
            dangling = [f"{d}: {det}" for d, st2, det in states if st2 == _deps.DANGLING]
            if dangling:
                res["malformed"].append(dict(row, cause="dangling dependency: " + "; ".join(dangling)))
                continue
            unmet = [_deps.qualify(d, tf.id) for d, st2, _ in states if st2 == _deps.UNMET]
            if unmet:
                res["waiting"].append(dict(row, waiting_on=unmet))
                continue
            nl = s.get("needs")
            if nl is None:
                nl = []
            if not isinstance(nl, list):
                res["malformed"].append(dict(row, cause="needs is not a list"))
                continue
            if any(not isinstance(n, str) for n in nl):
                res["malformed"].append(dict(row, cause="needs has a non-string element"))
                continue
            bad, unevaluable = needs_problems(reg, nl)
            if bad:
                res["malformed"].append(dict(row, cause="; ".join(bad)))
                continue
            gates = [{"gate": n, "state": needs.NE,
                      "cause": f"no needs registry at {ctx.cfg.rel(ctx.cfg.registry)}: "
                               f"cannot tell whether it is a gate"} for n in unevaluable]
            for gtok in reg.gate_tokens(nl):
                if not probes and (reg.tokens.get(gtok) or {}).get("probe"):
                    gates.append({"gate": gtok, "state": needs.NE, "cause": "probes disabled"})
                    continue
                state, cause = reg.gate_state(gtok, ctx.repo, operator=operator)
                if state != needs.OPEN:
                    gates.append({"gate": gtok, "state": state, "cause": cause})
            if gates:
                res["gated"].append(dict(row, gates=gates))
                continue
            res["ready"].append(row)
    rank = lambda r: (PRIORITY_RANK.get(r["priority"], 4), r["ref"])  # noqa: E731
    for k in ("ready", "gated", "claimed", "waiting"):
        res[k].sort(key=rank)
    # a gate that could not be evaluated is not an answer: the step stays
    # GATED with its cause, and the run as a whole is NOT EVALUABLE
    res["gates_not_evaluable"] = sum(
        1 for r in res["gated"] if any(g["state"] == needs.NE for g in r["gates"]))
    # a claim whose step_ref names no step protects nothing: the work it was
    # meant for may be listed READY above under another id
    res["orphaned_claims"] = [{"id": c.get("id"), "step_ref": c.get("step_ref"),
                               "holder": f"{_claims.c_machine(c)}/{_claims.c_slot(c)}"}
                              for c in _claims.orphaned(ctx, view.union)]
    res["claim_id_collisions"] = view.id_collisions
    return res


def cmd_ready(ctx, as_json=False, operator=False, probes=True, limit=15) -> int:
    try:
        res = classify(ctx, operator=operator, probes=probes)
    except _claims.ClaimsDamaged as e:
        msg = f"claims file damaged ({e}); refusing to read it as 'no claims'"
        if as_json:
            ctx.say(json.dumps({"not_evaluable": msg}))
        else:
            ctx.say(f"ready: NOT EVALUABLE: {msg}")
        return NOT_EVALUABLE
    code = OK
    if res["malformed"] or res["orphaned_claims"]:
        code = REFUSED
    if res["claims_view"] != "ok" or res["gates_not_evaluable"]:
        code = NOT_EVALUABLE
    if as_json:
        out = dict(res)
        out["counts"] = {k: len(res[k]) for k in ("ready", "gated", "claimed", "waiting",
                                                 "malformed")}
        out["counts_are_floor"] = bool(res["malformed"] or res["orphaned_claims"]
                                       or res["gates_not_evaluable"])
        ctx.say(json.dumps(out, indent=2, default=str))
        return code
    ctx.say(f"READY on {ctx.machine} ({ctx.slot}): top {min(limit, len(res['ready']))} "
            f"of {len(res['ready'])}")
    for r in res["ready"][:limit]:
        ctx.say(f"  {_c(r['ref']):<22} {_c(r['priority']):<8} {_c(r['title'])}")
    for r in res["gated"]:
        g = "; ".join(f"{_c(x['gate'])} {x['state']} ({_c(x['cause'])})" for x in r["gates"])
        ctx.say(f"  GATED    {_c(r['ref'])}: {g}")
    for r in res["claimed"]:
        ctx.say(f"  CLAIMED  {_c(r['ref'])}: {_c(', '.join(r['claims']))}")
    for r in res["malformed"]:
        ctx.say(f"  MALFORMED {_c(r['ref'])}: {_c(r['cause'])}")
    for c in res["orphaned_claims"]:
        ctx.say(f"  ORPHANED claim {_c(c['id'])} ({_c(c['holder'])}) -> {_c(c['step_ref'])}: names no "
                f"step; the work it was meant for may be listed under another id")
    ctx.say(f"ready {len(res['ready'])} · gated {len(res['gated'])} · claimed "
            f"{len(res['claimed'])} · waiting {len(res['waiting'])} of {res['open_here']} "
            f"open step(s) workable here; {len(res['malformed'])} malformed; "
            f"{res['elsewhere']} for other machines")
    if res["malformed"]:
        ctx.say("counts are a floor: malformed steps are excluded from every bucket "
                "(`crossbox doctor check`)")
    if res["orphaned_claims"]:
        ctx.say(f"counts are a floor: {len(res['orphaned_claims'])} active claim(s) name no "
                f"step (`crossbox claims`)")
    for cid, n in sorted(res["claim_id_collisions"].items()):
        ctx.say(f"claims view: {n} different claims share the id {_c(cid)}")
    if res["claims_view"] != "ok":
        ctx.say(f"claims view: NOT EVALUABLE: {res['claims_view']}")
    if res["gates_not_evaluable"]:
        ctx.say(f"gates: NOT EVALUABLE: {res['gates_not_evaluable']} step(s) have a gate "
                f"whose probe gave no answer; READY is a floor")
    return code


def cmd_needs_registry(ctx, as_json=False) -> int:
    reg = needs.Registry.load(ctx.cfg.registry)
    if as_json:
        ctx.say(json.dumps({"present": reg.present, "tokens": reg.tokens,
                            "aliases": reg.aliases, "prefix_kinds": reg.prefix_kinds},
                           indent=2, default=str))
        return OK
    if not reg.present:
        ctx.say(f"no registry at {ctx.cfg.rel(ctx.cfg.registry)} (built-in prefix "
                f"`date-reached:` still works)")
    for name, spec in sorted(reg.tokens.items()):
        ctx.say(f"  {name:<24} {spec.get('kind', 'annotation'):<11} {spec.get('desc', '')}")
    for a, c in sorted(reg.aliases.items()):
        ctx.say(f"  {a:<24} alias of {c}")
    for p, k in sorted(reg.prefix_kinds.items()):
        ctx.say(f"  {p + '*':<24} {k}")
    ctx.say("capability tokens are documentation only; routing is by `target`")
    return OK


def cmd_needs_audit(ctx, as_json=False) -> int:
    ctx.require_queue("needs audit")
    reg = needs.Registry.load(ctx.cfg.registry)
    findings, open_steps, closed_steps = [], 0, 0
    unparseable = []
    unevaluable = []
    for tf in ctx.store.files("queue"):
        # a parsed file of the wrong shape is as unread as one that does not
        # parse: `steps` that is not a list would otherwise read as no steps
        if not tf.ok or model.structure_problems(tf.data):
            unparseable.append(tf)
            continue
        for s in tf.steps:
            if s.get("status") in model.TERMINAL:
                closed_steps += 1
                continue
            open_steps += 1
            nl = s.get("needs") or []
            if not isinstance(nl, list):
                findings.append({"ref": f"{tf.id}.{s.get('id')}", "token": repr(nl),
                                 "status": "unknown", "suggestion": None})
                continue
            for n in nl:
                for r in reg.resolve(n):
                    if r["status"] == "unknown" and not reg.present and isinstance(n, str) \
                            and n.strip() and not r.get("split_from"):
                        unevaluable.append(f"{tf.id}.{s.get('id')}")
                        continue
                    if r["status"] in ("alias", "unknown", "split") and not r.get("split_from"):
                        findings.append({"ref": f"{tf.id}.{s.get('id')}", "token": r["token"],
                                         "status": r["status"],
                                         "suggestion": r.get("suggestion")})
    bad = [f for f in findings if f["status"] in ("unknown", "split")]
    no_reg = sorted(set(unevaluable))
    if as_json:
        ctx.say(json.dumps({"open_steps": open_steps, "closed_steps_skipped": closed_steps,
                            "findings": findings,
                            "unparseable": [tf.path.name for tf in unparseable],
                            "registry_present": reg.present,
                            "not_evaluable_without_registry": no_reg,
                            "counts_are_floor": bool(unparseable)}, indent=2))
    else:
        for f in findings:
            sug = f" -> {f['suggestion']}" if f["suggestion"] else ""
            ctx.say(f"  {f['status']:<8} {f['ref']}: {f['token']!r}{sug}")
        ctx.say(f"needs audit: {len(bad)} unknown/split and "
                f"{len(findings) - len(bad)} alias token(s) over {open_steps} open step(s) "
                f"({closed_steps} closed steps skipped: closed history)")
        for tf in unparseable:
            ctx.say(f"  UNPARSEABLE {tf.id} ({ctx.cfg.rel(tf.path)}): its needs were not read")
        if unparseable:
            ctx.say(f"counts are a floor: {len(unparseable)} task file(s) could not be read")
        if no_reg:
            ctx.say(f"needs audit: NOT EVALUABLE: no registry at "
                    f"{ctx.cfg.rel(ctx.cfg.registry)}; {len(no_reg)} step(s) list needs "
                    f"that cannot be checked: {', '.join(no_reg)}")
    if unparseable or no_reg:
        return NOT_EVALUABLE
    return REFUSED if bad else OK
