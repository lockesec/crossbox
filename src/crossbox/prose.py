"""`crossbox lint`: decisions a step's notes state that its structure does not carry.

A queue drifts when a decision is written in prose and never reaches the fields readiness
reads: a note says "blocked by TASK-API-001.c" while depends_on omits it, so `ready` and
`plan` rank the step as workable; a note opens with "Deferred:" while the status still says
pending. Two narrow rules, both anchored, because a bare keyword scan flags far more than it
should (in the system this was extracted from, one flagged 8% of steps, most of them wrong):

  gate-not-in-depends_on  a gating phrase ("blocked by", "waits for", "depends on", "do not
                          start before"; or "needs", "after", "once", "requires" at the start
                          of a sentence) names another step or task that depends_on omits
  decision-but-open       a note line or sentence that STARTS with a decision marker
                          ("Deferred:", "On hold:", "Parked:", "Won't do:", "Superseded by X",
                          or the marker in capitals) on a pending or in-progress step

Advisory: it reads and reports, never rewrites. Exit 0 clean, 1 findings, 2 NOT EVALUABLE.
"""
from __future__ import annotations

import json
import re

from . import deps as _deps
from . import model
from .errors import NOT_EVALUABLE, OK, REFUSED

_c = model.clean
GATE_ANYWHERE = re.compile(r"\b(?:blocked (?:by|on)|waiting (?:on|for)|waits? for|depends on|"
                           r"do not start (?:before|until)|don'?t start (?:before|until))\b", re.IGNORECASE)
GATE_START = re.compile(r"^(?:needs|requires|after|once)\b", re.IGNORECASE)
DECISION = re.compile(
    r"^(?:(?:deferred|on hold|parked|won'?t do|wontfix|not doing|cancell?ed|abandoned)\s*[:\-–(]"
    r"|superseded by\s+\S)", re.IGNORECASE)
DECISION_CAPS = re.compile(r"^(?:DEFERRED|ON HOLD|PARKED|WONTFIX|SUPERSEDED)(?![\w-])")
# forms found in a real queue (ported from the system crossbox was extracted from): a dated or
# attributed deferral, an imperative DEFER, a BLOCKED with its reason or owner
DECISION_MORE = re.compile(
    r"^(?:[Dd]eferred\s+(?:by\b|until\b|\d{4}-\d\d-\d\d)"
    r"|DEFER(?:-FOR-EVIDENCE\b|\s+until\b|\s*[\u2014\u2013:-]\s)"
    r"|BLOCKED(?:\s+until\b|\s+ON\s+[A-Z]|\s*[\u2014\u2013:]\s))")
# "DEFERRED from d2" / "DEFERRED in step f": the work was moved HERE; this step is not deferred
MOVED_HERE = re.compile(r"^deferred\s+(?:\w+ly\s+)?(?:from|in)\b", re.IGNORECASE)
# a sentence that says its gate no longer holds is not a gate
STALE = re.compile(r"\b(?:(?:is|was|now) stale|no longer|obsolete|outdated)\b", re.IGNORECASE)
# the gate is the step named right after the phrase, inside its clause
CLAUSE_END = re.compile(r"[;:()\u2014]|\s-\s|,\s+(?:and|but|so)\b|\.\s")
WINDOW = 60


def _sentences(text: str) -> list:
    out = []
    for line in (text or "").splitlines():
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+", line) if s.strip()]
    return out


def _refs(ctx, sentence: str) -> list:
    g = ctx.store.g
    found = [m.group(0) for m in g.ref_re.finditer(sentence)]
    for m in g.task_re.finditer(sentence):
        tid = m.group(0)
        if not any(r.startswith(tid + ".") for r in found) and tid not in found:
            end = m.end()
            if not (end < len(sentence) and sentence[end] == "." and end + 1 < len(sentence)
                    and sentence[end + 1].isalpha()):
                found.append(tid)
    return found


def lint(ctx) -> dict:
    ctx.require_queue("lint")
    rows, scanned, unreadable = [], 0, []
    for tf in ctx.store.files("queue"):
        if not tf.ok:
            unreadable.append({"task": tf.id, "file": tf.path.name, "error": str(tf.error)})
            continue
        for s in tf.steps:
            if not isinstance(s, dict) or s.get("status") not in model.OPEN:
                continue
            scanned += 1
            ref = f"{tf.id}.{s.get('id')}"
            deps = s.get("depends_on") if isinstance(s.get("depends_on"), list) else []
            have = {d if "-" in str(d) else f"{tf.id}.{d}" for d in deps if isinstance(d, str)}
            text = "\n".join(str(x) for x in (s.get("title") or "", s.get("notes") or "") if x)
            for sent in _sentences(text):
                gated = []
                if STALE.search(sent):
                    continue
                starts = [GATE_START.match(sent)] if GATE_START.match(sent) else []
                for m in list(GATE_ANYWHERE.finditer(sent)) + starts:
                    window = sent[m.end():m.end() + WINDOW]
                    cut = CLAUSE_END.search(window)
                    gated += _refs(ctx, window[:cut.start()] if cut else window)
                if gated:
                    for r in dict.fromkeys(gated):
                        if r == ref or r in have or any(r.startswith(h + ".") for h in have if "." not in h):
                            continue
                        if _deps.dep_state(r, tf, ctx.store)[0] == _deps.MET:
                            continue        # the gate is already closed: nothing for readiness to miss
                        rows.append({"ref": ref, "rule": "gate-not-in-depends_on", "names": r,
                                     "status": s.get("status"), "sentence": sent})
            # a decision marker counts only where a line starts, not mid-line: "... trades table.
            # Superseded by lifecycle_log." is about the table, not the step
            for line in text.splitlines():
                line = re.sub(r"^\s*(?:[-*+]\s+)?", "", line)
                sent = _sentences(line)[0] if _sentences(line) else ""
                if (sent and s.get("status") in ("pending", "in_progress") and not MOVED_HERE.match(sent)
                        and (DECISION.match(sent) or DECISION_CAPS.match(sent) or DECISION_MORE.match(sent))):
                    rows.append({"ref": ref, "rule": "decision-but-open", "names": None,
                                 "status": s.get("status"), "sentence": sent})
    return {"rows": rows, "scanned": scanned, "unreadable": unreadable}


def cmd_lint(ctx, as_json=False) -> int:
    res = lint(ctx)
    if as_json:
        ctx.say(json.dumps(res, indent=2))
        return NOT_EVALUABLE if res["unreadable"] else (REFUSED if res["rows"] else OK)
    else:
        ctx.say(f"lint: {len(res['rows'])} finding(s) over {res['scanned']} open step(s)")
        for r in res["rows"]:
            extra = f"names {_c(r['names'])}" if r["names"] else f"(status {_c(r['status'])})"
            ctx.say(f"  {_c(r['ref']):<18} {r['rule']} {extra}: {_c(r['sentence'][:120])}")
        if res["rows"]:
            ctx.say("advisory: add the dependency (crossbox edit-step --depends), or close the step "
                    "(skip --reason), or reword the note")
        for u in res["unreadable"]:
            ctx.say(f"UNREADABLE {_c(u['task'])} ({_c(u['file'])}): {_c(u['error'])[:120]}; its steps were "
                    f"not read, so the counts above are a floor")
    return NOT_EVALUABLE if res["unreadable"] else (REFUSED if res["rows"] else OK)
