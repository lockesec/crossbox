"""`crossbox graph`: the dependency graph as Mermaid, DOT or JSON (contract:
docs/views.md).

Read only; the same files always give byte-identical output. The open-step
graph (nodes, edges, cycles, malformed steps, critical path) is `plan`'s, read
through plan.analyse / plan.dep_targets; `ready`'s classification gives each
open step its state. Finished steps are additional nodes here.

Titles and task ids come from files anyone can edit, so they never reach the
output raw: node and subgraph ids are generated (n0, n1, ... / t0, t1, ...),
every label is cleaned (views.clean: one line, no control or bidi characters,
cut to 60 characters) and then escaped for its format.
"""
from __future__ import annotations

import json

from . import model, plan
from .errors import NOT_EVALUABLE, OK, REFUSED, CrossboxError, UsageError
from .model import clean

LABEL_LIMIT = 60

STATES = ("done", "skipped", "ready", "gated", "claimed", "waiting", "elsewhere",
          "malformed", "cycle", "not_evaluable")
MARK = {"done": "✓", "skipped": "⊘", "ready": "▶", "gated": "⏸",
        "claimed": "⚑", "waiting": "◌", "elsewhere": "↗",
        "malformed": "✖", "cycle": "↻", "not_evaluable": "?"}
# fill, stroke, text, extra mermaid style
COLORS = {
    "done": ("#e6f4ea", "#2e7d32", "#1b5e20", ""),
    "skipped": ("#f1f3f4", "#9aa0a6", "#5f6368", ",stroke-dasharray:4 3"),
    "ready": ("#c8f2d2", "#1b8a3a", "#0b3d1c", ",stroke-width:3px"),
    "gated": ("#fff4ce", "#b58900", "#5c4500", ""),
    "claimed": ("#dbe9ff", "#2a62c9", "#10305f", ""),
    "waiting": ("#ffffff", "#6b7280", "#111827", ""),
    "elsewhere": ("#ede7f6", "#6a4bb5", "#2d1b69", ",stroke-dasharray:2 2"),
    "malformed": ("#ffd6d6", "#c62828", "#7f0000", ""),
    "cycle": ("#ffd6d6", "#c62828", "#7f0000", ",stroke-dasharray:5 3"),
    "not_evaluable": ("#ffe0b2", "#e65100", "#4e2600", ",stroke-dasharray:3 3"),
}


# ---------------------------------------------------------------- escaping

_MERMAID_NAMED = {'"': "#quot;", "<": "#lt;", ">": "#gt;"}
_MERMAID_NUMERIC = set("#|[]{}`&%\\@")


def mermaid_text(s: str) -> str:
    """Escape already-cleaned text for the inside of a Mermaid ``["..."]``
    label. ``"`` ``<`` ``>`` and ``# | [ ] { } ` & % \\ @`` become entity
    codes, so nothing in a label can close the string, open a directive
    (``%%``), start a click/callback or an HTML tag."""
    out = []
    for ch in s:
        if ch in _MERMAID_NAMED:
            out.append(_MERMAID_NAMED[ch])
        elif ch in _MERMAID_NUMERIC:
            out.append(f"#{ord(ch)};")
        else:
            out.append(ch)
    return "".join(out)


def dot_text(s: str) -> str:
    """Escape already-cleaned text for a double-quoted DOT string."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


# ---------------------------------------------------------------- the graph

class _Node:
    __slots__ = ("ref", "task", "sid", "title", "state", "detail", "context")

    def __init__(self, ref, task, sid, title, state, detail):
        self.ref, self.task, self.sid = ref, task, sid
        self.title, self.state, self.detail = title, state, detail
        self.context = False


def _gather(ctx, an, states, open_only) -> tuple:
    """(nodes, edges, task_titles, file_problems, steps_in_queue). Nodes: every step of every
    readable queue task (open ones only with ``open_only``)."""
    by_id = {}
    for tf in ctx.store.files("queue"):
        if tf.ok and not model.structure_problems(tf.data):
            by_id.setdefault(tf.id, []).append(tf)
    problems = []
    nodes, titles, files = {}, {}, {}
    total = 0
    for tid in sorted(by_id):
        if len(by_id[tid]) > 1:
            problems.append({"ref": tid, "cause": "task id held by more than one "
                                                  "queue file; its steps are not drawn"})
            continue
        tf = by_id[tid][0]
        files[tid] = tf
        titles[tid] = clean(tf.data.get("title", ""), LABEL_LIMIT)
        for s in tf.data.get("steps") or []:
            sid, status = s.get("id"), s.get("status")
            ref = f"{tid}.{sid}"
            total += 1
            if status == "done" or status == "skipped":
                if open_only:
                    continue
                state, detail = status, ""
            else:
                state, detail = states.get(ref, (plan.NOT_EVAL, "step was not classified"))
            nodes[ref] = _Node(ref, tid, sid, clean(s.get("title", ""), LABEL_LIMIT),
                               state, clean(detail))
    cache, edges = {}, set()
    for ref, n in nodes.items():
        tf = files[n.task]
        step = tf.step(n.sid)
        dl = step.get("depends_on") if step else None
        if not isinstance(dl, list):
            continue
        for tok in dl:
            if not isinstance(tok, str) or not tok.strip():
                continue
            try:
                srcs, _ = plan.dep_targets(ctx, tf, tok, cache, closed=True)
            except CrossboxError:
                continue
            for src in srcs:
                if src in nodes:
                    edges.add((src, ref))
    return nodes, edges, titles, problems, total


def graph_data(ctx, task_ids=(), open_only=False, max_nodes=150, max_edges=None,
               probes=True) -> dict:
    an = plan.analyse(ctx, "graph", probes=probes)
    for tid in task_ids:
        tf = ctx.store.find(tid)
        if tf is None or tf.tier != "queue":
            where = ("is archived (graph reads the queue)" if tf is not None
                     else "is not in the queue")
            raise UsageError(f"--task {clean(tid)}: {where}")
    states = plan.open_states(ctx, an)
    nodes, edges, titles, file_problems, total = _gather(ctx, an, states, open_only)
    if task_ids:
        listed = set(task_ids)
        sel = {r for r, n in nodes.items() if n.task in listed}
        context = {a for a, b in edges if b in sel and a not in sel}
        for r in context:
            nodes[r].context = True
        sel |= context
    else:
        sel = set(nodes)
    order = sorted(sel)
    sel_edges = sorted((a, b) for a, b in edges if a in sel and b in sel)
    path = list(an.path)
    crit = {(path[i], path[i + 1]) for i in range(len(path) - 1)}

    problems = list(an.malformed) + file_problems
    problems.sort(key=lambda m: (m["ref"], m["cause"]))
    in_view = [m for m in problems if m["ref"] in sel or m["ref"] not in nodes]
    cycle_in_view = [c for c in an.cycles if any(r in sel for r in c)]
    ne = list(an.not_eval)
    if any(nodes[r].state == plan.NOT_EVAL for r in order) and not ne:
        ne.append("a drawn step was not classified by `ready`")
    return {"machine": ctx.machine, "slot": ctx.slot,
            "scope": {"tasks": sorted(task_ids) or None, "open_only": bool(open_only)},
            "order": order, "nodes": nodes, "titles": titles,
            "edges": sel_edges, "critical": crit, "critical_path": path,
            "queue_steps": total, "malformed": in_view,
            "cycles": cycle_in_view, "not_evaluable": "; ".join(ne) if ne else None,
            "too_big": len(order) > max_nodes, "max_nodes": max_nodes,
            "too_many_edges": max_edges is not None and len(sel_edges) > max_edges,
            "max_edges": max_edges}


# ---------------------------------------------------------------- renderers

def _tasks_in_order(g) -> list:
    """[(task id, [refs])] in sorted task order."""
    nodes, out, idx = g["nodes"], [], {}
    for ref in g["order"]:
        t = nodes[ref].task
        if t not in idx:
            idx[t] = []
            out.append((t, idx[t]))
        idx[t].append(ref)
    return sorted(out, key=lambda x: x[0])


def _task_label(g, tid, refs) -> str:
    text = clean(tid, LABEL_LIMIT)
    if g["titles"].get(tid):
        text += f": {g['titles'][tid]}"
    if all(g["nodes"][r].context for r in refs):
        text += " (context)"
    return text


def render_mermaid(g) -> str:
    ids = {ref: f"n{i}" for i, ref in enumerate(g["order"])}
    nodes = g["nodes"]
    lines = ["flowchart LR"]
    used = sorted({nodes[r].state for r in g["order"]}, key=STATES.index)
    for st in used:
        fill, stroke, text, extra = COLORS[st]
        lines.append(f"  classDef {st} fill:{fill},stroke:{stroke},color:{text}{extra}")
    for i, (tid, refs) in enumerate(_tasks_in_order(g)):
        lines.append(f'  subgraph t{i}["{mermaid_text(_task_label(g, tid, refs))}"]')
        for ref in refs:
            n = nodes[ref]
            lines.append(f'    {ids[ref]}["{MARK[n.state]} {mermaid_text(_label_body(n))}"]'
                         f":::{n.state}")
        lines.append("  end")
    for a, b in g["edges"]:
        arrow = "==>" if (a, b) in g["critical"] else "-->"
        lines.append(f"  {ids[a]} {arrow} {ids[b]}")
    return "\n".join(lines)


def _label_body(n: _Node) -> str:
    return n.sid + (f": {n.title}" if n.title else "")


def render_dot(g) -> str:
    ids = {ref: f"n{i}" for i, ref in enumerate(g["order"])}
    nodes = g["nodes"]
    lines = ["digraph crossbox {", "  rankdir=LR;",
             '  node [shape=box, style="rounded,filled", fontname="Helvetica"];']
    for i, (tid, refs) in enumerate(_tasks_in_order(g)):
        lines.append(f"  subgraph cluster_{i} {{")
        lines.append(f'    label="{dot_text(_task_label(g, tid, refs))}";')
        for ref in refs:
            n = nodes[ref]
            fill, stroke, text, _x = COLORS[n.state]
            dashed = ",dashed" if "dasharray" in COLORS[n.state][3] else ""
            lines.append(f'    {ids[ref]} [label="{MARK[n.state]} {dot_text(_label_body(n))}", '
                         f'fillcolor="{fill}", color="{stroke}", fontcolor="{text}", '
                         f'style="rounded,filled{dashed}"];')
        lines.append("  }")
    for a, b in g["edges"]:
        attr = " [penwidth=3]" if (a, b) in g["critical"] else ""
        lines.append(f"  {ids[a]} -> {ids[b]}{attr};")
    lines.append("}")
    return "\n".join(lines)


def render_json(g) -> str:
    ids = {ref: f"n{i}" for i, ref in enumerate(g["order"])}
    nodes = g["nodes"]
    out = {
        "machine": g["machine"], "slot": g["slot"], "scope": g["scope"],
        "nodes": [{"id": ids[r], "ref": r, "task": nodes[r].task,
                   "title": nodes[r].title, "state": nodes[r].state,
                   "detail": nodes[r].detail, "context": nodes[r].context}
                  for r in g["order"]],
        "edges": [[a, b] for a, b in g["edges"]],
        "critical_path": g["critical_path"],
        "counts": {"nodes": len(g["order"]), "queue_steps": g["queue_steps"],
                   "edges": len(g["edges"])},
        "malformed": g["malformed"], "cycles": g["cycles"],
        "not_evaluable": g["not_evaluable"],
    }
    return json.dumps(out, indent=2)


RENDER = {"mermaid": render_mermaid, "dot": render_dot, "json": render_json}


def exit_code(g) -> int:
    if g["not_evaluable"]:
        return NOT_EVALUABLE
    if g["malformed"] or g["cycles"] or g["too_big"] or g["too_many_edges"] or g.get("too_long"):
        return REFUSED
    return OK


# GitHub's Mermaid refuses more than 500 edges and 50,000 characters of text; the
# defaults for --format mermaid are those limits. Other formats have no default.
MERMAID_MAX_EDGES = 500
MERMAID_MAX_CHARS = 50000


def cmd_graph(ctx, fmt="mermaid", task_ids=(), open_only=False, max_nodes=150,
              max_edges=None, max_chars=None, probes=True) -> int:
    if fmt == "mermaid":
        max_edges = MERMAID_MAX_EDGES if max_edges is None else max_edges
        max_chars = MERMAID_MAX_CHARS if max_chars is None else max_chars
    g = graph_data(ctx, task_ids, open_only, max_nodes, max_edges, probes)
    n = len(g["order"])
    text, g["too_long"] = None, False
    if not g["too_big"] and not g["too_many_edges"]:
        text = RENDER[fmt](g)
        if max_chars is not None and len(text) > max_chars:
            g["too_long"] = True
    code = exit_code(g)
    narrow = "narrow it with --task ID or --open, or raise the limit"
    if g["too_big"]:
        ctx.warn(f"graph: REFUSED: {n} nodes exceed --max-nodes {g['max_nodes']} (of "
                 f"{g['queue_steps']} step(s) in the queue); {narrow}. "
                 f"Nothing was dropped silently.")
    elif g["too_many_edges"]:
        ctx.warn(f"graph: REFUSED: {len(g['edges'])} edges exceed --max-edges "
                 f"{g['max_edges']} ({n} nodes); {narrow}. Nothing was dropped silently.")
    elif g["too_long"]:
        ctx.warn(f"graph: REFUSED: the {fmt} text is {len(text)} characters, over "
                 f"--max-chars {max_chars} ({n} nodes, {len(g['edges'])} edges); {narrow}. "
                 f"Nothing was dropped silently.")
    else:
        ctx.say(text)
    for m in g["malformed"]:
        ctx.warn(f"graph: MALFORMED {clean(m['ref'])}: {clean(m['cause'])}")
    for c in g["cycles"]:
        ctx.warn("graph: CYCLE: " + " -> ".join(clean(r) for r in c + [c[0]]))
    if g["not_evaluable"]:
        ctx.warn(f"graph: NOT EVALUABLE: {g['not_evaluable']}")
    if n == 0 and not (g["too_big"] or g["too_many_edges"] or g["too_long"]):
        ctx.warn(f"graph: nothing to draw (0 of {g['queue_steps']} step(s) selected)")
    return code
