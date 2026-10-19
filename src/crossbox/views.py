"""`crossbox progress` and `crossbox near-done` (contract: docs/views.md).

Two read-only views of how far the queue is from empty. Neither writes, takes
the writer lock or is metered by the rate gate; the same files always give
byte-identical output. `near-done` reuses `plan`'s graph and `ready`'s
classification (plan.analyse / plan.open_states); it re-derives neither.

Counts are always printed beside their denominators. A task file that cannot
be read is listed with its cause and the totals say they are a floor.
"""
from __future__ import annotations

import json
from fractions import Fraction

from . import deps as _deps
from . import model, plan
from .model import clean
from .config import PRIORITY_RANK
from .errors import NOT_EVALUABLE, OK, REFUSED, NotEvaluable

DASH = "—"


# clean(): the one helper for untrusted text is model.clean


def pct(closed: int, total: int):
    """Whole percent, rounded DOWN so only a finished task reads 100%; None
    (shown as an em dash) when there are no steps: 0/0 is not 100%."""
    return None if total == 0 else (100 * closed) // total


def pct_text(closed: int, total: int) -> str:
    p = pct(closed, total)
    return DASH if p is None else f"{p}%"


# ---------------------------------------------------------------- reading

class Task:
    __slots__ = ("id", "title", "domain", "priority", "tier", "steps", "done",
                 "skipped")

    def __init__(self, tf):
        d = tf.data
        self.id = tf.id
        self.title = clean(d.get("title", ""))
        dom = d.get("domain")
        self.domain = clean(dom) if dom not in (None, "") else "(none)"
        self.priority = clean(d.get("priority", "normal")) or "normal"
        self.tier = tf.tier
        self.steps = [s for s in (d.get("steps") or []) if isinstance(s, dict)]
        sts = [s.get("status") for s in self.steps]
        self.done = sts.count("done")
        self.skipped = sts.count("skipped")

    @property
    def total(self) -> int:
        return len(self.steps)

    @property
    def closed(self) -> int:
        return self.done + self.skipped

    @property
    def open(self) -> int:
        return self.total - self.closed


def scan(ctx, archive=False) -> tuple:
    """(tasks, unreadable). ``unreadable`` rows are {"id", "file", "cause"}: a
    file that does not parse, fails the writers' shape check, or whose task id
    another file in scope also holds (counting either would double-count)."""
    tiers = ["queue"] + (["archive"] if archive else [])
    if archive and not ctx.cfg.archive.is_dir():
        raise NotEvaluable(
            f"--archive: NOT EVALUABLE: {ctx.cfg.rel(ctx.cfg.archive)} does not exist "
            f"under {ctx.repo}; archived tasks were not counted")
    files = [f for t in tiers for f in ctx.store.files(t)]
    held = {}
    for f in files:
        if f.ok:
            held.setdefault(f.id, []).append(f)
    tasks, bad = [], []
    for f in files:
        if not f.ok:
            bad.append({"id": f.id, "file": f.path.name,
                        "cause": f"task file does not parse ({f.error})"})
            continue
        probs = model.structure_problems(f.data)
        if probs:
            bad.append({"id": f.id, "file": f.path.name, "cause": "; ".join(probs)})
            continue
        if len(held[f.id]) > 1:
            others = ", ".join(sorted(f"{x.tier}/{x.path.name}" for x in held[f.id]))
            bad.append({"id": f.id, "file": f.path.name,
                        "cause": f"task id held by more than one file ({others})"})
            continue
        tasks.append(Task(f))
    tasks.sort(key=lambda t: (t.id, t.tier))
    bad.sort(key=lambda b: (b["id"], b["file"], b["cause"]))
    return tasks, bad


def _bad_text(bad: list) -> list:
    return [f"UNREADABLE {clean(b['id'])} ({clean(b['file'])}): {clean(b['cause'])}"
            for b in bad]


# ---------------------------------------------------------------- progress

class _Tally:
    __slots__ = ("tasks", "steps", "done", "skipped")

    def __init__(self):
        self.tasks = self.steps = self.done = self.skipped = 0

    def add(self, t: Task) -> None:
        self.tasks += 1
        self.steps += t.total
        self.done += t.done
        self.skipped += t.skipped

    @property
    def closed(self) -> int:
        return self.done + self.skipped

    def row(self, **extra) -> dict:
        return dict(extra, tasks=self.tasks, steps=self.steps, done=self.done,
                    skipped=self.skipped, closed=self.closed,
                    pct=pct(self.closed, self.steps))


def progress_data(ctx, by=None, archive=False) -> dict:
    ctx.require_queue("progress")
    tasks, bad = scan(ctx, archive)
    kinds = [by] if by else ["task", "domain"]
    groups = []
    for kind in kinds:
        if kind == "task":
            for t in tasks:
                tl = _Tally()
                tl.add(t)
                groups.append(tl.row(kind="task", key=t.id, title=t.title,
                                     archived=t.tier == "archive"))
            continue
        acc = {}
        for t in tasks:
            acc.setdefault(t.domain if kind == "domain" else t.priority,
                           _Tally()).add(t)
        order = sorted(acc) if kind == "domain" else sorted(
            acc, key=lambda k: (PRIORITY_RANK.get(k, 4), k))
        for k in order:
            groups.append(acc[k].row(kind=kind, key=k, title=None, archived=None))
    total = _Tally()
    for t in tasks:
        total.add(t)
    return {"scope": "queue+archive" if archive else "queue",
            "by": by, "groups": groups, "total": total.row(),
            "unreadable": bad, "counts_are_floor": bool(bad)}


def cmd_progress(ctx, by=None, archive=False, as_json=False) -> int:
    data = progress_data(ctx, by, archive)
    code = REFUSED if data["unreadable"] else OK
    if as_json:
        ctx.say(json.dumps(data, indent=2))
        return code
    scope = ("queue and archive" if archive
             else "queue only; --archive adds archived tasks")
    ctx.say(f"progress ({scope}): closed = done + skipped; percent = closed / total "
            f"steps, rounded down")
    rows = []
    for g in data["groups"]:
        label = g["key"] if g["kind"] == "task" else f"{g['kind']} {g['key']}"
        if g.get("archived"):
            label += " (archived)"
        rows.append((clean(label), g, g.get("title", "")))
    rows.append(("all", dict(data["total"], kind="all"), ""))
    w = max(len(r[0]) for r in rows)
    cw = max(len(f"{g['closed']}/{g['steps']}") for _, g, _t in rows)
    for label, g, title in rows:
        cl = f"{g['closed']}/{g['steps']}"
        line = (f"{label:<{w}}  {cl:>{cw}} closed  {g['done']:>3} done  "
                f"{g['skipped']:>3} skipped  {pct_text(g['closed'], g['steps']):>4}")
        if g["kind"] != "task":
            line += f"  ({g['tasks']} task(s))"
        if title:
            line += f"  {title}"
        ctx.say(line)
    for line in _bad_text(data["unreadable"]):
        ctx.say(line)
    if data["unreadable"]:
        ctx.say(f"counts are a floor: {len(data['unreadable'])} task file(s) unreadable "
                f"and not counted (`crossbox doctor check`)")
    return code


# ---------------------------------------------------------------- near-done

def _workable_on(target, known, cfg) -> list:
    if not known:
        return []
    if target == "any":
        return ["any"]
    return sorted(n for n, m in cfg.machines.items() if target in m.capabilities)


_STATE_TEXT = {
    plan.READY: lambda d: "READY here",
    plan.GATED: lambda d: f"GATED here ({d})",
    plan.CLAIMED: lambda d: f"CLAIMED ({d})",
    plan.WAITING: lambda d: f"WAITING on {d}",
    plan.ELSEWHERE: lambda d: f"elsewhere ({d})",
    plan.MALFORMED: lambda d: f"MALFORMED ({d})",
    plan.CYCLE: lambda d: f"CYCLE ({d})",
    plan.NOT_EVAL: lambda d: f"NOT EVALUABLE ({d})",
}


def near_done_data(ctx, max_open=2, min_pct=60, probes=True) -> dict:
    an = plan.analyse(ctx, "near-done", probes=probes)
    states = plan.open_states(ctx, an)
    tasks, bad = scan(ctx)
    listed, with_open = [], 0
    for t in tasks:
        if t.total == 0 or t.open == 0:
            continue
        with_open += 1
        if t.open > max_open or 100 * t.closed < min_pct * t.total:
            continue
        steps = []
        for s in t.steps:
            if s.get("status") in model.TERMINAL:
                continue
            sid = s.get("id")
            ref = f"{t.id}.{sid}"
            state, detail = states.get(ref, (plan.NOT_EVAL, "step was not classified"))
            target, known = _deps.read_target(s.get("target"), ctx.cfg)
            steps.append({"ref": ref, "id": sid, "title": clean(s.get("title", "")),
                          "state": state, "detail": clean(detail),
                          "target": clean(target),
                          "workable_on": _workable_on(target, known, ctx.cfg)})
        listed.append({"id": t.id, "title": t.title, "closed": t.closed,
                       "total": t.total, "open": t.open,
                       "pct": pct(t.closed, t.total), "steps": steps})
    listed.sort(key=lambda r: (r["open"], -Fraction(r["closed"], r["total"]), r["id"]))
    all_steps = [s for r in listed for s in r["steps"]]
    problems = [s for s in all_steps if s["state"] in (plan.MALFORMED, plan.CYCLE)]
    ne = list(an.not_eval)
    if any(s["state"] == plan.NOT_EVAL for s in all_steps) and not ne:
        ne.append("a listed step was not classified by `ready`")
    return {"machine": ctx.machine, "slot": ctx.slot,
            "criteria": {"max_open": max_open, "min_pct": min_pct},
            "tasks_with_open_steps": with_open, "tasks": listed,
            "unworkable_steps": sum(1 for s in all_steps if not s["workable_on"]),
            "problem_steps": len(problems), "unreadable": bad,
            "not_evaluable": "; ".join(ne) if ne else None,
            "counts_are_floor": bool(bad or ne)}


def near_done_exit(data: dict) -> int:
    if data["not_evaluable"]:
        return NOT_EVALUABLE
    if data["unreadable"] or data["problem_steps"]:
        return REFUSED
    return OK


def cmd_near_done(ctx, max_open=2, min_pct=60, as_json=False, probes=True) -> int:
    data = near_done_data(ctx, max_open, min_pct, probes)
    code = near_done_exit(data)
    if as_json:
        ctx.say(json.dumps(data, indent=2))
        return code
    crit = data["criteria"]
    ctx.say(f"near-done on {clean(data['machine'])} ({clean(data['slot'])}): at most {crit['max_open']} "
            f"open step(s) and at least {crit['min_pct']}% closed")
    for r in data["tasks"]:
        note = ""
        if any(s["state"] == plan.MALFORMED for s in r["steps"]):
            note = "  contains malformed steps"
        ctx.say(f"{clean(r['id'])}  {r['closed']}/{r['total']}  "
                f"{pct_text(r['closed'], r['total'])}  {r['open']} open{note}  {r['title']}")
        for s in r["steps"]:
            on = ("any machine" if s["workable_on"] == ["any"]
                  else ", ".join(clean(m) for m in s["workable_on"]) or "none")
            ctx.say(f"  .{clean(str(s['id']))}  {_STATE_TEXT[s['state']](s['detail'])}   "
                    f"workable on: {on}")
    for line in _bad_text(data["unreadable"]):
        ctx.say(line)
    if data["not_evaluable"]:
        ctx.say(f"NOT EVALUABLE: {data['not_evaluable']}")
    ctx.say(f"near-done: {len(data['tasks'])} of {data['tasks_with_open_steps']} task(s) "
            f"with open steps listed; {data['unworkable_steps']} listed step(s) workable "
            f"on no configured machine; {data['problem_steps']} malformed or in a cycle"
            + (" · counts are a floor" if data["counts_are_floor"] else ""))
    return code
