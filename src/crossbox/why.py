"""`crossbox why [REF]`: why a step is not workable here, traced to its root blockers.

The answer comes from the same classification `ready` uses (ready.classify), so
the two can never disagree about a step. `why` adds the walk: a WAITING step is
followed through each unmet dependency until it reaches steps that are not
waiting on anything (the roots), and each node carries one stable code:

  READY      workable on this machine now
  CLAIMED    an active claim holds it (claims are advisory; the holder is named)
  WAITING    a dependency is not closed
  GATED      a gate is closed, or could not be evaluated
  MALFORMED  the step or its file cannot be read as written
  ELSEWHERE  its target is provided by other machines, not this one
  DONE       closed (done, or its task is archived)
  SKIPPED    retired with a reason
  CYCLE      the dependency walk came back to a step already on the path

Without a REF it prints the queue-wide line: how many open steps are workable
here out of how many, what holds the rest, and how many are open elsewhere.

Exit: 0 explained, 1 unknown ref, 2 NOT EVALUABLE (a gate on the path gave no
answer, or the claims view is incomplete).
"""
from __future__ import annotations

import json

from . import claims as _claims
from . import model, needs
from . import ready as _ready
from .errors import NOT_EVALUABLE, OK, CrossboxError

_c = model.clean
BUCKETS = ("ready", "gated", "claimed", "waiting", "malformed")


def _providers(ctx, target) -> list:
    return sorted(name for name, m in ctx.cfg.machines.items() if target in m.capabilities)


def _detail(ctx, code, row) -> str:
    if code == "READY":
        return f"workable now on {ctx.machine}"
    if code == "CLAIMED":
        return "held by " + ", ".join(row["claims"])
    if code == "GATED":
        return "; ".join(f"{g['gate']} {g['state']} ({g['cause']})" for g in row["gates"])
    if code == "WAITING":
        return "waiting on " + ", ".join(row["waiting_on"])
    if code == "MALFORMED":
        return row["cause"]
    if code == "ELSEWHERE":
        prov = [p for p in _providers(ctx, row["target"]) if p != ctx.machine]
        who = ", ".join(prov) if prov else "no configured machine"
        return f"target {row['target']}: provided by {who}, not {ctx.machine}"
    return ""


class _Walker:
    def __init__(self, ctx, res):
        self.ctx, self.res = ctx, res
        self.index = {}
        for k in BUCKETS:
            for row in res[k]:
                self.index.setdefault(row["ref"], (k.upper(), row))
        for row in res.get("elsewhere_rows", []):
            self.index.setdefault(row["ref"], ("ELSEWHERE", row))
        # a malformed task FILE (duplicate step id, id in two files, unparseable)
        # is listed by ready under the task id: every step ref under it is
        # that file's problem, not "not classified"
        self.file_bad = {row["ref"]: row for row in res["malformed"] if "file" in row}
        self.expanded = set()
        self.not_evaluable = []

    def _closed_or_missing(self, ref) -> dict:
        """A ref the classifier did not list: closed, archived, or not there."""
        g = self.ctx.store.g
        if g.is_ref(ref):
            tid, sid = g.parse_ref(ref)
        elif g.is_task_id(ref):
            tid, sid = ref, None
        else:
            raise CrossboxError(f"{ref!r} is neither a step ref nor a task id")
        tf = self.ctx.store.find(tid)
        if tf is None:
            raise CrossboxError(f"{ref}: task {tid} not found in queue or archive")
        if sid is None:
            return None
        s = tf.step(sid) if tf.ok else None
        if s is None:
            raise CrossboxError(f"{ref}: task {tid} has no step {sid!r}")
        if tf.tier == "archive":
            return {"ref": ref, "code": "DONE", "detail": "task archived", "children": []}
        st = s.get("status")
        if st == "done":
            return {"ref": ref, "code": "DONE", "detail": "closed", "children": []}
        if st == "skipped":
            return {"ref": ref, "code": "SKIPPED",
                    "detail": str(s.get("skip_reason") or "skipped"), "children": []}
        raise CrossboxError(f"{ref}: not classified (status {st!r})")

    def _task_node(self, tid):
        tf = self.ctx.store.find(tid)
        if tf is None:
            raise CrossboxError(f"task {tid} not found in queue or archive")
        if tf.tier == "archive":
            return {"ref": tid, "code": "DONE", "detail": "task archived", "children": []}, []
        open_refs = [f"{tid}.{s.get('id')}" for s in tf.steps
                     if isinstance(s, dict) and s.get("status") not in model.TERMINAL]
        if not open_refs:
            return {"ref": tid, "code": "DONE", "detail": "every step closed",
                    "children": []}, []
        return {"ref": tid, "code": "WAITING",
                "detail": f"task has {len(open_refs)} open step(s)",
                "children": []}, open_refs

    def _make(self, ref, path):
        """One node, plus the refs of the children still to be walked."""
        if ref in path:
            loop = path[path.index(ref):] + [ref]
            return {"ref": ref, "code": "CYCLE", "detail": "cycle: " + " -> ".join(loop),
                    "children": []}, []
        if ref not in self.index and self.ctx.store.g.is_task_id(ref):
            return self._task_node(ref)
        if ref not in self.index:
            g = self.ctx.store.g
            tid = g.parse_ref(ref)[0] if g.is_ref(ref) else None
            if tid in self.file_bad:
                return {"ref": ref, "code": "MALFORMED",
                        "detail": self.file_bad[tid]["cause"], "children": []}, []
            return self._closed_or_missing(ref), []
        code, row = self.index[ref]
        n = {"ref": ref, "code": code, "detail": _detail(self.ctx, code, row), "children": []}
        if code == "GATED" and any(g["state"] == needs.NE for g in row["gates"]):
            self.not_evaluable.append(ref)
        if code == "WAITING":
            if ref in self.expanded:
                n["seen"] = True  # a diamond: expanded once above
                return n, []
            self.expanded.add(ref)
            return n, list(row["waiting_on"])
        return n, []

    def node(self, ref) -> dict:
        """Depth-safe: an explicit stack, not recursion, so a chain of thousands
        of tasks explains like a chain of three."""
        path = []
        root, kids = self._make(ref, path)
        stack = []
        if kids:
            path.append(root["ref"])
            stack.append([root, kids, 0])
        while stack:
            frame = stack[-1]
            if frame[2] == len(frame[1]):
                stack.pop()
                path.pop()
                continue
            r = frame[1][frame[2]]
            frame[2] += 1
            child, ckids = self._make(r, path)
            frame[0]["children"].append(child)
            if ckids:
                path.append(child["ref"])
                stack.append([child, ckids, 0])
        return root


def _roots(node, out, seen) -> None:
    stack = [node]
    while stack:
        n = stack.pop()
        if n["code"] == "WAITING" and n["children"]:
            stack.extend(reversed(n["children"]))
            continue
        if n.get("seen"):
            continue
        if n["ref"] not in seen:
            seen.add(n["ref"])
            out.append({"ref": n["ref"], "code": n["code"], "detail": n["detail"]})


INDENT_CAP = 40  # deeper nodes keep a flat indent and say their depth


def _print_tree(ctx, node) -> None:
    stack = [(node, 0)]
    while stack:
        n, depth = stack.pop()
        tail = "  (see above)" if n.get("seen") else ""
        deep = f"  [depth {depth}]" if depth > INDENT_CAP else ""
        ctx.say(f"{'  ' * min(depth, INDENT_CAP)}{_c(n['ref'])}  {n['code']}  "
                f"{_c(n['detail'])}{tail}{deep}")
        stack.extend((ch, depth + 1) for ch in reversed(n["children"]))


def _depth(node) -> int:
    best, stack = 0, [(node, 0)]
    while stack:
        n, d = stack.pop()
        best = max(best, d)
        stack.extend((ch, d + 1) for ch in n["children"])
    return best


def _compact(node) -> str:
    """The trace as compact JSON, built without recursion (the json module's
    encoder recurses once per nesting level)."""
    out, stack = [], [node]
    while stack:
        n = stack.pop()
        if isinstance(n, str):
            out.append(n)
            continue
        head = {k: v for k, v in n.items() if k != "children"}
        out.append(json.dumps(head)[:-1] + ', "children": [')
        stack.append("]}")
        for i, ch in reversed(list(enumerate(n["children"]))):
            stack.append(ch)
            if i:
                stack.append(", ")
    return "".join(out)


def _dump_with_trace(doc, trace) -> str:
    """json.dumps(indent=2), except a trace deeper than INDENT_CAP is written
    compact: an indented 5000-level tree would be hundreds of megabytes."""
    if _depth(trace) <= INDENT_CAP:
        return json.dumps(dict(doc, trace=trace), indent=2)
    mark = "\x00TRACE\x00"
    text = json.dumps(dict(doc, trace=mark), indent=2)
    return text.replace(json.dumps(mark), _compact(trace))


def _summary(ctx, res, as_json) -> int:
    counts = {k: len(res[k]) for k in BUCKETS}
    # ready's own denominator; an unreadable task FILE is a file, not a step,
    # so it is reported apart rather than counted as an open step
    total = res["open_here"]
    files = sum(1 for r in res["malformed"] if "file" in r)
    bad_steps = counts["malformed"] - files
    elsewhere = {}
    for row in res.get("elsewhere_rows", []):
        elsewhere[row["target"]] = elsewhere.get(row["target"], 0) + 1
    ne = res["claims_view"] != "ok" or res["gates_not_evaluable"]
    if as_json:
        ctx.say(json.dumps({"machine": ctx.machine, "counts": counts, "open_here": total,
                            "unreadable_files": files, "elsewhere": elsewhere, "claims_view": res["claims_view"],
                            "not_evaluable": bool(ne)}, indent=2))
        return NOT_EVALUABLE if ne else OK
    n = dict(counts, malformed=bad_steps)
    held = [f"{n[k]} {k}" for k in ("waiting", "claimed", "gated", "malformed") if n[k]]
    line = (f"{counts['ready']} of {total} open step(s) workable on {ctx.machine}: "
            + (", ".join(held) if held else "nothing held"))
    if files:
        line += f"; {files} task file(s) unreadable (`crossbox doctor check`)"
    if elsewhere:
        parts = ", ".join(f"{t}: {n}" for t, n in sorted(elsewhere.items()))
        line += f"; {sum(elsewhere.values())} more open elsewhere ({parts})"
    ctx.say(line)
    if res["claims_view"] != "ok":
        ctx.say(f"NOT EVALUABLE: claims view incomplete ({_c(str(res['claims_view']))})")
    if res["gates_not_evaluable"]:
        ctx.say(f"NOT EVALUABLE: {res['gates_not_evaluable']} gated step(s) have a gate "
                f"that gave no answer")
    ctx.say("crossbox why <ref> traces one step to what holds it")
    return NOT_EVALUABLE if ne else OK


def cmd_why(ctx, ref=None, as_json=False, operator=False, probes=True) -> int:
    try:
        res = _ready.classify(ctx, operator=operator, probes=probes, keep_elsewhere=True)
    except _claims.ClaimsDamaged as e:
        msg = f"claims file damaged ({e}); refusing to read it as 'no claims'"
        ctx.say(json.dumps({"not_evaluable": msg}) if as_json else f"why: NOT EVALUABLE: {msg}")
        return NOT_EVALUABLE
    if ref is None:
        return _summary(ctx, res, as_json)
    w = _Walker(ctx, res)
    top = w.node(ref.strip())
    roots = []
    _roots(top, roots, set())
    if top["code"] != "WAITING":
        roots = [{"ref": top["ref"], "code": top["code"], "detail": top["detail"]}]
    ne = bool(w.not_evaluable) or res["claims_view"] != "ok"
    if as_json:
        ctx.say(_dump_with_trace({"ref": top["ref"], "code": top["code"],
                                  "machine": ctx.machine, "roots": roots,
                                  "not_evaluable": w.not_evaluable,
                                  "claims_view": res["claims_view"]}, top))
        return NOT_EVALUABLE if ne else OK
    _print_tree(ctx, top)
    if top["code"] == "WAITING":
        for r in roots:
            ctx.say(f"root: {_c(r['ref'])}  {r['code']}  {_c(r['detail'])}")
    if res["claims_view"] != "ok":
        ctx.say(f"NOT EVALUABLE: claims view incomplete ({_c(str(res['claims_view']))}); "
                f"a claim held elsewhere may be missing above")
    if w.not_evaluable:
        ctx.say(f"NOT EVALUABLE: a gate gave no answer on {', '.join(w.not_evaluable)}")
    return NOT_EVALUABLE if ne else OK
