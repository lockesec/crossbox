"""`crossbox plan`: which READY step matters most, and in what order the rest
has to happen (contract: docs/plan.md).

One graph over every open step on every machine. Section 1 is the READY set
exactly as `ready.classify` computes it (never re-derived here), ranked by how
much work each step unblocks. Read only; no model; the same files always give
byte-identical output.

Reading, cycle finding (iterative Tarjan), layering (Kahn) and the critical
path are linear in steps + edges, with no recursion. "Unblocks" (distinct
descendants per READY step) is counted once over the condensed graph with
big-integer bitsets: one OR per edge of ``N / 64`` machine words, so the time
is O(E * N / 64), not a search per READY step, and the live bitsets are freed
as soon as the last step that reads them has. The worst case (a wide graph in
which many steps stay needed) holds up to ``N * N / 8`` bytes of bitsets:
6,000 steps use a few megabytes, 50,000 a few hundred.
"""
from __future__ import annotations

import json
from collections import deque

from . import claims as _claims
from . import deps as _deps
from . import model, needs
from .config import PRIORITY_RANK
from .model import clean
from .errors import NOT_EVALUABLE, OK, REFUSED
from .ready import classify, needs_problems


def _rank(priority) -> int:
    return PRIORITY_RANK.get(priority, 4)


class _Node:
    __slots__ = ("ref", "title", "priority", "target", "dep_refs")

    def __init__(self, ref, title, priority, target, dep_refs):
        self.ref, self.title, self.priority = ref, title, priority
        self.target, self.dep_refs = target, dep_refs


# ---------------------------------------------------------------- reading

def _refs_of_task(ctx, tid, cache, closed=False) -> list:
    """The step refs of a task a task-ref dependency waits on: the non-closed
    ones, or every step with ``closed`` (the graph view draws finished work)."""
    key = (tid, closed)
    if key not in cache:
        tf = ctx.store.find(tid)
        refs = []
        if tf is not None and tf.ok:
            refs = [f"{tid}.{s.get('id')}" for s in tf.steps
                    if isinstance(s, dict)
                    and (closed or s.get("status") not in model.TERMINAL)]
        cache[key] = refs
    return cache[key]


def dep_targets(ctx, tf, token, cache, closed=False) -> tuple:
    """(refs, task) for one depends_on token of a step in task file ``tf``:
    the step refs an edge is drawn from, and, when it is a task ref naming no
    such step, that task id (else None). Shared by `plan` and `graph`, so both
    resolve a dependency identically."""
    tok = token.strip()
    if "-" not in tok:
        return [f"{tf.id}.{tok}"], None
    if ctx.g.is_ref(tok):
        tid, sid = ctx.g.parse_ref(tok)
        return [f"{tid}.{sid}"], None
    refs = _refs_of_task(ctx, tok, cache, closed)
    return (refs, None) if refs else ([], tok)


def _read_graph(ctx) -> tuple:
    """(open_steps, candidates, malformed). Candidates are steps that pass every
    check `ready` makes (whatever their machine, claim or dependency state);
    ``dep_refs`` are the refs of the still-open steps each one waits on."""
    reg = needs.Registry.load(ctx.cfg.registry)
    malformed, cands, seen = [], {}, {}
    open_steps = 0
    task_cache = {}
    for tf in ctx.store.files("queue"):
        if not tf.ok:
            malformed.append({"ref": tf.id,
                              "cause": f"task file does not parse ({tf.error})"})
            continue
        probs = model.structure_problems(tf.data)
        if probs:
            malformed.append({"ref": tf.id, "cause": "; ".join(probs)})
            continue
        prio = str(tf.data.get("priority", "normal"))
        for s in tf.data.get("steps") or []:
            ref = f"{tf.id}.{s.get('id')}"
            st = s.get("status")
            if st in model.TERMINAL:
                continue
            open_steps += 1
            seen[ref] = seen.get(ref, 0) + 1

            def bad(cause, ref=ref):
                malformed.append({"ref": ref, "cause": cause})

            if st not in model.OPEN:
                bad(f"unknown status {st!r}")
                continue
            t, known = _deps.read_target(s.get("target"), ctx.cfg)
            if not known:
                bad(f"unknown target {t!r}")
                continue
            dl = s.get("depends_on", [])
            if dl is None:
                dl = []
            if not isinstance(dl, list):
                bad("depends_on is not a list")
                continue
            if any(not isinstance(d, str) for d in dl):
                bad("depends_on has a non-string element")
                continue
            states = [(d, *_deps.dep_state(d, tf, ctx.store)) for d in dl]
            dangling = [f"{d}: {det}" for d, st2, det in states if st2 == _deps.DANGLING]
            if dangling:
                bad("dangling dependency: " + "; ".join(dangling))
                continue
            nl = s.get("needs")
            if nl is None:
                nl = []
            if not isinstance(nl, list):
                bad("needs is not a list")
                continue
            if any(not isinstance(n, str) for n in nl):
                bad("needs has a non-string element")
                continue
            nbad, _ = needs_problems(reg, nl)
            if nbad:
                bad("; ".join(nbad))
                continue
            dep_refs, empty = set(), []
            for d, st2, _det in states:
                if st2 != _deps.UNMET:
                    continue
                refs, none_for = dep_targets(ctx, tf, d, task_cache)
                dep_refs.update(refs)
                if none_for is not None:
                    empty.append(none_for)
            if empty:
                bad(f"depends on task {', '.join(empty)}, which has no open step "
                    f"and is not complete")
                continue
            cands[ref] = _Node(ref, str(s.get("title", "")), prio, t, sorted(dep_refs))
    # a task id used by two queue files makes its refs ambiguous
    for ref in sorted(r for r, n in seen.items() if n > 1):
        cands.pop(ref, None)
        malformed[:] = [m for m in malformed if m["ref"] != ref]
        malformed.append({"ref": ref, "cause": "step ref defined in more than one task file"})
    return open_steps, cands, malformed


def _prune_unplannable(cands, malformed) -> None:
    """A step that waits on a step plan cannot place (malformed, or in a file no
    writer will touch) cannot be placed either: it joins `malformed`."""
    rev = {}
    for n in cands.values():
        for d in n.dep_refs:
            rev.setdefault(d, []).append(n.ref)
    queue = deque()
    why = {}
    for ref in sorted(cands):
        for d in cands[ref].dep_refs:
            if d not in cands:
                why[ref] = d
                queue.append(ref)
                break
    while queue:
        ref = queue.popleft()
        if ref not in cands:
            continue
        del cands[ref]
        malformed.append({"ref": ref, "cause": f"depends on {why[ref]}, which plan "
                                               f"cannot place (malformed or unreadable)"})
        for dep in sorted(rev.get(ref, [])):
            if dep in cands and dep not in why:
                why[dep] = ref
                queue.append(dep)


# ---------------------------------------------------------------- graph

def _sccs(nodes, succ) -> list:
    """Tarjan, iterative. Returns the strongly connected components."""
    index, low, on, stack, out = {}, {}, set(), [], []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(succ[root]))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            advanced = False
            for w in it:
                if w not in index:
                    index[w] = low[w] = counter
                    counter += 1
                    stack.append(w)
                    on.add(w)
                    work.append((w, iter(succ[w])))
                    advanced = True
                    break
                if w in on:
                    low[v] = min(low[v], index[w])
            if advanced:
                continue
            work.pop()
            if work:
                u = work[-1][0]
                low[u] = min(low[u], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(comp)
    return out


def _cycle_through(start, members, succ) -> list:
    """The shortest cycle through ``start`` inside ``members`` (ties: smallest
    refs first), as refs in edge order starting at ``start``."""
    prev = {}
    queue = deque()
    for w in succ[start]:
        if w in members and w not in prev:
            prev[w] = start
            queue.append(w)
    while queue and start not in prev:
        v = queue.popleft()
        for w in succ[v]:
            if w in members and w not in prev:
                prev[w] = v
                queue.append(w)
    path, cur = [], prev[start]
    while cur != start:
        path.append(cur)
        cur = prev[cur]
    path.append(start)
    path.reverse()
    return path


def _find_cycles(comps, succ) -> list:
    """Each cycle once, starting at its smallest ref. A component that is more
    than one simple cycle yields one cycle per member not yet named."""
    cycles = []
    for comp in comps:
        if len(comp) == 1 and comp[0] not in succ[comp[0]]:
            continue
        members = set(comp)
        covered = set()
        for m in sorted(comp):
            if m in covered:
                continue
            cyc = _cycle_through(m, members, succ)
            covered.update(cyc)
            # m is the smallest uncovered member, but an earlier cycle in the
            # component may hold smaller refs: rotate to this cycle's smallest
            k = cyc.index(min(cyc))
            cycles.append(cyc[k:] + cyc[:k])
    cycles.sort()
    return cycles


def _reach(starts, succ) -> set:
    seen, queue = set(starts), deque(starts)
    while queue:
        v = queue.popleft()
        for w in succ[v]:
            if w not in seen:
                seen.add(w)
                queue.append(w)
    return seen


def _popcount(x: int) -> int:
    try:
        return x.bit_count()
    except AttributeError:               # Python < 3.10
        return bin(x).count("1")


def _descendant_counts(nodes, succ, comps, wanted) -> dict:
    """ref -> number of distinct steps reachable from it (itself excluded), for
    the refs in ``wanted``. ``comps`` are the strongly connected components in
    Tarjan's emission order (every component after all it reaches), so one pass
    over the condensed graph gives each component the union of its successors'
    reach; the members of a cycle reach each other."""
    bit = {r: 1 << i for i, r in enumerate(nodes)}
    comp_of = {}
    for ci, comp in enumerate(comps):
        for r in comp:
            comp_of[r] = ci
    preds = [0] * len(comps)             # distinct predecessor components
    succ_comps = []
    for ci, comp in enumerate(comps):
        seen = {comp_of[w] for r in comp for w in succ[r]}
        seen.discard(ci)
        succ_comps.append(sorted(seen))
        for s in seen:
            preds[s] += 1
    reach, out = {}, {}
    for ci, comp in enumerate(comps):    # successors come first
        mask = 0
        for r in comp:
            mask |= bit[r]
        for s in succ_comps[ci]:
            mask |= reach[s]
            preds[s] -= 1
            if preds[s] == 0:            # its last reader has read it
                del reach[s]
        if preds[ci]:
            reach[ci] = mask
        need = [r for r in comp if r in wanted]
        if need:
            n = _popcount(mask) - 1
            for r in need:
                out[r] = n
    return out


def _layers(planned, succ) -> tuple:
    """(wave per ref, longest chain length starting at each ref)."""
    indeg = {r: 0 for r in planned}
    for r in planned:
        for w in succ[r]:
            indeg[w] += 1
    wave = {r: 0 for r in planned}
    queue = deque(sorted(r for r in planned if indeg[r] == 0))
    topo = []
    while queue:
        v = queue.popleft()
        topo.append(v)
        for w in succ[v]:
            wave[w] = max(wave[w], wave[v] + 1)
            indeg[w] -= 1
            if indeg[w] == 0:
                queue.append(w)
    down = {}
    for v in reversed(topo):
        down[v] = 1 + max((down[w] for w in succ[v]), default=0)
    return wave, down


def _critical_path(planned, succ, down) -> list:
    if not planned:
        return []
    top = max(down.values())
    cur = min(r for r in planned if down[r] == top)
    path = [cur]
    while down[cur] > 1:
        cur = min(w for w in succ[cur] if down[w] == down[cur] - 1)
        path.append(cur)
    return path


# ---------------------------------------------------------------- the verb

class Analysis:
    """Everything `plan`, `near-done` and `graph` share about the open steps:
    the placeable nodes, cycles, the critical path and what `ready` says."""

    def __init__(self, **kw):
        self.__dict__.update(kw)


def analyse(ctx, verb="plan", probes=True) -> Analysis:
    """One read of the open-step graph (``ctx.require_queue`` first: a missing
    queue is NOT EVALUABLE, never an empty plan). ``probes=False`` never runs a
    needs-registry probe: a gate that has one reads NOT EVALUABLE."""
    ctx.require_queue(verb)
    open_steps, cands, malformed = _read_graph(ctx)
    _prune_unplannable(cands, malformed)

    res, not_eval = None, []
    try:
        res = classify(ctx, probes=probes)
    except _claims.ClaimsDamaged as e:
        not_eval.append(f"claims file damaged ({e}); refusing to read it as 'no claims'")
    if res is not None:
        if res["claims_view"] != "ok":
            not_eval.append(f"claims view: {res['claims_view']}")
        if res["gates_not_evaluable"]:
            why = ("whose probe was not run (--no-probes)" if not probes
                   else "whose probe gave no answer")
            not_eval.append(f"{res['gates_not_evaluable']} step(s) have a gate {why}; "
                            f"READY is a floor")

    nodes = sorted(cands)
    succ = {r: [] for r in nodes}
    for r in nodes:
        for d in cands[r].dep_refs:
            succ[d].append(r)
    for r in nodes:
        succ[r].sort()

    comps = _sccs(nodes, succ)
    cycles = _find_cycles(comps, succ)
    cyc_nodes = {r for c in cycles for r in c}
    blocked = _reach(sorted(cyc_nodes), succ) - cyc_nodes if cyc_nodes else set()
    planned = [r for r in nodes if r not in cyc_nodes and r not in blocked]
    pset = set(planned)
    psucc = {r: [w for w in succ[r] if w in pset] for r in planned}
    wave, down = _layers(planned, psucc)
    path = _critical_path(planned, psucc, down)
    ready_refs = ({row["ref"] for row in res["ready"]}
                  if res is not None and res["claims_view"] == "ok" else set())
    unblocks = _descendant_counts(nodes, succ, comps, ready_refs & set(cands))
    return Analysis(unblocks=unblocks, open_steps=open_steps, cands=cands, malformed=malformed,
                    res=res, not_eval=not_eval, nodes=nodes, succ=succ,
                    cycles=cycles, cyc_nodes=cyc_nodes, blocked=blocked,
                    planned=planned, psucc=psucc, wave=wave, down=down, path=path)


# the step states of the views: lower-case, one vocabulary for near-done and graph
READY, GATED, CLAIMED, WAITING = "ready", "gated", "claimed", "waiting"
ELSEWHERE, MALFORMED, CYCLE, NOT_EVAL = "elsewhere", "malformed", "cycle", "not_evaluable"


def open_states(ctx, an) -> dict:
    """ref -> (state, detail) for every open step the analysis knows: malformed
    and in-a-cycle steps by structure, the rest from ``ready``'s own buckets.
    When the claims view is incomplete (or `ready` could not run) nothing that
    depends on it is reported: those steps read ``not_evaluable``."""
    res = an.res
    view_ok = res is not None and res["claims_view"] == "ok"
    rows = {}
    if view_ok:
        for key, state in (("ready", READY), ("gated", GATED), ("claimed", CLAIMED),
                           ("waiting", WAITING)):
            for row in res[key]:
                if state == GATED:
                    detail = "; ".join(f"{g['gate']} {g['state']}" for g in row["gates"])
                elif state == CLAIMED:
                    detail = ", ".join(row["claims"])
                elif state == WAITING:
                    detail = ", ".join(row["waiting_on"])
                else:
                    detail = ""
                rows[row["ref"]] = (state, detail)
    cyc_of = {}
    for c in an.cycles:
        text = " -> ".join(c + [c[0]])
        for r in c:
            cyc_of.setdefault(r, text)
    out = {}
    for m in an.malformed:
        out[m["ref"]] = (MALFORMED, m["cause"])
    for ref, n in an.cands.items():
        if ref in cyc_of:
            out[ref] = (CYCLE, cyc_of[ref])
        elif ref in rows:
            out[ref] = rows[ref]
        elif not _deps.workable(n.target, ctx.cfg, ctx.machine_caps):
            out[ref] = (ELSEWHERE, f"target {n.target}")
        elif not view_ok:
            why = "ready could not run" if res is None else "claims view incomplete"
            out[ref] = (NOT_EVAL, why)
        else:
            out[ref] = (NOT_EVAL, "ready did not classify this step")
    return out


def build(ctx, probes=True) -> dict:
    """The whole plan as a JSON-able dict (``--json`` prints it as is)."""
    an = analyse(ctx, probes=probes)
    open_steps, cands, malformed = an.open_steps, an.cands, an.malformed
    res, not_eval = an.res, an.not_eval
    # an incomplete claims view is as unanswerable as a damaged local file: no
    # step is ranked READY and no state is guessed (near-done and graph agree)
    view_ok = res is not None and res["claims_view"] == "ok"
    nodes, succ, cycles, cyc_nodes = an.nodes, an.succ, an.cycles, an.cyc_nodes
    blocked, planned, wave, path = an.blocked, an.planned, an.wave, an.path
    on_path = set(path)

    state = {}
    if view_ok:
        for key, label in (("ready", "READY"), ("gated", "GATED"),
                           ("claimed", "CLAIMED"), ("waiting", "WAITING")):
            for row in res[key]:
                state[row["ref"]] = label

    def state_of(ref):
        if not view_ok:
            return "NOT EVALUABLE"
        if ref in state:
            return state[ref]
        if not _deps.workable(cands[ref].target, ctx.cfg, ctx.machine_caps):
            return "elsewhere"
        return "UNKNOWN"

    nxt, listed = [], set()
    for row in (res["ready"] if view_ok else []):
        ref = row["ref"]
        # a ref plan could not place (malformed, or defined twice) is never
        # ranked, and a ref is listed once
        if ref not in cands or ref in listed:
            continue
        listed.add(ref)
        nxt.append({"ref": ref, "title": row["title"], "priority": row["priority"],
                    "target": cands[ref].target,
                    "unblocks": an.unblocks[ref],
                    "dependents": len(succ[ref]),
                    "on_critical_path": ref in on_path})
    nxt.sort(key=lambda r: (-r["unblocks"], _rank(r["priority"]), r["ref"]))

    def prow(ref):
        n = cands[ref]
        return {"ref": ref, "target": n.target, "priority": n.priority}

    waves = []
    if planned:
        by_wave = {}
        for r in planned:
            by_wave.setdefault(wave[r], []).append(r)
        for w in sorted(by_wave):
            members = sorted(by_wave[w], key=lambda r: (cands[r].target,
                                                        _rank(cands[r].priority), r))
            waves.append({"wave": w, "steps": [prow(r) for r in members]})
    order = sorted(planned, key=lambda r: (wave[r], _rank(cands[r].priority), r))
    malformed.sort(key=lambda m: (m["ref"], m["cause"]))
    orphaned = list(res["orphaned_claims"]) if res is not None else []
    floor = bool(malformed or orphaned or cycles or not_eval)
    return {
        "machine": ctx.machine, "slot": ctx.slot,
        "open_steps": open_steps, "nodes": len(nodes),
        "next": nxt,
        "critical_path": [{"ref": r, "target": cands[r].target, "state": state_of(r)}
                          for r in path],
        "waves": waves, "order": order,
        "cycles": cycles, "blocked_by_cycle": sorted(blocked),
        "malformed": malformed, "orphaned_claims": orphaned,
        "not_evaluable": "; ".join(not_eval) if not_eval else None,
        "counts_are_floor": floor,
    }


def exit_code(plan: dict) -> int:
    if plan["not_evaluable"]:
        return NOT_EVALUABLE
    if plan["malformed"] or plan["cycles"] or plan["orphaned_claims"]:
        return REFUSED
    return OK


def _more(ctx, indent, left, flag="--top") -> None:
    if left > 0:
        ctx.say(f"{indent}... {left} more not shown ({flag})")


def cmd_plan(ctx, as_json=False, top=10, probes=True) -> int:
    c = clean                       # file text reaches a terminal only cleaned
    plan = build(ctx, probes)
    code = exit_code(plan)
    if as_json:
        ctx.say(json.dumps(plan, indent=2, default=str))
        return code
    nxt, path = plan["next"], plan["critical_path"]
    ctx.say(f"NEXT on {plan['machine']} ({plan['slot']}): top {min(top, len(nxt))} "
            f"of {len(nxt)} ready")
    for r in nxt[:top]:
        crit = "critical" if r["on_critical_path"] else "        "
        ctx.say(f"  {c(r['ref']):<22} unblocks {r['unblocks']:>3}  dependents "
                f"{r['dependents']:>3}  {c(r['priority']):<8} {crit}  {c(r['title'])}")
    _more(ctx, "  ", len(nxt) - top)
    ctx.say(f"CRITICAL PATH: {len(path)} step(s)")
    for i, r in enumerate(path, 1):
        ctx.say(f"  {i:>3}. {c(r['ref']):<22} {c(r['target']):<10} {r['state']}")
    ctx.say(f"WAVES: {len(plan['waves'])}")
    for w in plan["waves"]:
        steps = w["steps"]
        ctx.say(f"  wave {w['wave']}: {len(steps)} step(s)")
        shown, last = 0, None
        for s in steps[:top]:
            if s["target"] != last:
                ctx.say(f"    [{c(s['target'])}]")
                last = s["target"]
            ctx.say(f"      {c(s['ref']):<22} {c(s['priority'])}")
            shown += 1
        _more(ctx, "    ", len(steps) - shown)
    order = plan["order"]
    ctx.say(f"ORDER: {len(order)} step(s)")
    for i, r in enumerate(order[:top], 1):
        ctx.say(f"  {i:>3}. {c(r)}")
    _more(ctx, "  ", len(order) - top)
    for cyc in plan["cycles"]:
        ctx.say("CYCLE: " + " -> ".join(clean(r) for r in cyc + [cyc[0]]))
    if plan["blocked_by_cycle"]:
        ctx.say(f"BLOCKED BY CYCLE ({len(plan['blocked_by_cycle'])}): "
                + ", ".join(c(r) for r in plan["blocked_by_cycle"]))
    for m in plan["malformed"]:
        ctx.say(f"MALFORMED {c(m['ref'])}: {c(m['cause'])}")
    for oc in plan["orphaned_claims"]:
        ctx.say(f"ORPHANED claim {c(oc['id'])} ({c(oc['holder'])}) -> {c(oc['step_ref'])}: "
                f"names no step")
    if plan["not_evaluable"]:
        ctx.say(f"NOT EVALUABLE: {c(plan['not_evaluable'])}")
    in_cycle = len({r for c in plan["cycles"] for r in c})
    ctx.say(f"plan: {plan['open_steps']} open step(s) · {plan['nodes']} node(s) · "
            f"{len(nxt)} ready here · {in_cycle} in a cycle · "
            f"{len(plan['malformed'])} malformed · {len(plan['waves'])} wave(s) · "
            f"critical path {len(path)}"
            + (" · counts are a floor" if plan["counts_are_floor"] else ""))
    return code
