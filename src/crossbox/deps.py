"""Targets and dependencies: one resolution used by `done`, readiness and doctor.

Targets. A step's ``target`` names a capability the machine must provide
(``any`` = none). Every configured machine provides its declared capabilities
plus its own name. On READ: absent/empty -> any; a configured legacy alias ->
its canonical value; a known capability -> itself; anything else stays as
written and is workable NOWHERE (it is never widened to ``any``). On WRITE only
canonical values are accepted.

Dependencies. Three token forms:
  bare step id ``c``           - sibling step in the same task
  step ref ``PFX-DOM-NNN.c``   - a step in another task
  task ref ``PFX-DOM-NNN``     - that whole task is complete
Result per token: "met", "unmet" or "dangling" (names nothing).
"""
from __future__ import annotations

import difflib

from . import model
from .errors import CrossboxError

MET, UNMET, DANGLING = "met", "unmet", "dangling"


# ---------------------------------------------------------------- targets

def read_target(value, cfg) -> tuple:
    """(canonical_or_raw, known: bool)"""
    if value is None or (isinstance(value, str) and not value.strip()):
        return "any", True
    if not isinstance(value, str):
        return repr(value), False
    v = value.strip().lower()
    v = cfg.target_aliases.get(v, v)
    if v in cfg.target_vocabulary():
        return v, True
    return value, False


def workable(value, cfg, caps: set) -> bool:
    t, known = read_target(value, cfg)
    if not known:
        return False
    return t == "any" or t in caps


def check_target_write(value: str, cfg) -> str:
    v = (value or "").strip().lower()
    vocab = cfg.target_vocabulary()
    if v in vocab and v == value.strip():
        return v
    if v in vocab:
        return v
    if v in cfg.target_aliases:
        raise CrossboxError(f"target {value!r} is a legacy spelling; write "
                            f"{cfg.target_aliases[v]!r}")
    near = difflib.get_close_matches(v, sorted(vocab), n=3, cutoff=0.6)
    hint = f"; did you mean {', '.join(near)}" if near else ""
    raise CrossboxError(f"unknown target {value!r}: known targets are "
                        f"{', '.join(sorted(vocab))} (declare capabilities under "
                        f"`machines:` in .crossbox/config.yaml){hint}")


# ---------------------------------------------------------------- deps

def dep_state(token, owner, store) -> tuple:
    """(state, detail) for one depends_on token of a step in task ``owner``
    (a TaskFile)."""
    if not isinstance(token, str) or not token.strip():
        return DANGLING, f"non-string or empty dependency {token!r}"
    g = store.g
    tok = token.strip()
    if "-" not in tok:
        if not model.STEP_ID_RE.fullmatch(tok):
            return DANGLING, f"{tok!r} is not a step id"
        s = owner.step(tok)
        if s is None:
            return DANGLING, f"{owner.id}.{tok} names no sibling step"
        return (MET if s.get("status") in model.TERMINAL else UNMET), f"{owner.id}.{tok}"
    if g.is_ref(tok):
        tid, sid = g.parse_ref(tok)
        try:
            tf = owner if tid == owner.id else store.find(tid)
        except CrossboxError as e:
            return DANGLING, str(e)
        if tf is None:
            return DANGLING, f"{tok}: task {tid} not found"
        if not tf.ok:
            return DANGLING, f"{tok}: task file {tf.path.name} does not parse"
        s = tf.step(sid)
        if s is None:
            return DANGLING, f"{tok}: task {tid} has no step {sid!r}"
        if tf.tier == "archive":
            return MET, tok
        return (MET if s.get("status") in model.TERMINAL else UNMET), tok
    if g.is_task_id(tok):
        try:
            tf = store.find(tok)
        except CrossboxError as e:
            return DANGLING, str(e)
        if tf is None:
            return DANGLING, f"task {tok} not found"
        # parse first: an archived file that does not parse is not "done", it
        # is unread (the step-ref form above tests tf.ok before the tier too)
        if not tf.ok:
            return DANGLING, f"{tok}: task file {tf.path.name} does not parse"
        if tf.tier == "archive":
            return MET, tok
        steps = [s for s in tf.steps if isinstance(s, dict)]
        if steps and all(s.get("status") in model.TERMINAL for s in steps):
            return MET, tok
        return UNMET, tok
    return DANGLING, f"{tok!r} is neither a step id, a step ref nor a task id"


def qualify(token: str, owner_id: str) -> str:
    return f"{owner_id}.{token}" if "-" not in token else token


def sibling_graph(steps: list, owner_id: str) -> dict:
    g = {}
    for s in steps:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str):
            continue
        edges = []
        deps = s.get("depends_on") or []
        if isinstance(deps, list):
            for d in deps:
                if not isinstance(d, str):
                    continue
                if "-" not in d:
                    edges.append(d)
                elif d.startswith(owner_id + "."):
                    edges.append(d[len(owner_id) + 1:])
        g[s["id"]] = edges
    return g


def cycles(graph: dict) -> list:
    """Every elementary cycle found by DFS, each as a list of ids."""
    found = []
    seen_keys = set()
    color = {}

    def dfs(n, stack):
        color[n] = 1
        stack.append(n)
        for m in graph.get(n, []):
            if m not in graph:
                continue
            if color.get(m) == 1:
                cyc = stack[stack.index(m):]
                key = frozenset(cyc)
                if key not in seen_keys:
                    seen_keys.add(key)
                    found.append(cyc + [m])
            elif color.get(m) is None:
                dfs(m, stack)
        stack.pop()
        color[n] = 2

    for n in graph:
        if color.get(n) is None:
            dfs(n, [])
    return found


def validate_depends(deps: list, owner, step_id: str, store, siblings: list,
                     warn) -> None:
    """Shared by add and edit-step. ``siblings`` = step ids that exist (or will
    exist) in the task. Refuses self-deps, unknown bare ids and unknown task
    refs; warns on step refs that do not resolve."""
    g = store.g
    for d in deps:
        if not isinstance(d, str) or not d.strip():
            raise CrossboxError(f"empty dependency token {d!r}")
        if d == step_id or d == f"{owner.id}.{step_id}":
            raise CrossboxError(f"a step may not depend on itself ({owner.id}.{step_id})")
        if "-" not in d:
            if d not in siblings:
                raise CrossboxError(f"dependency {d!r}: {owner.id} has no step {d!r}")
        elif g.is_ref(d):
            tid, sid = g.parse_ref(d)
            if tid == owner.id:
                if sid not in siblings:
                    raise CrossboxError(f"dependency {d!r}: {owner.id} has no step {sid!r}")
                continue
            st, detail = dep_state(d, owner, store)
            if st == DANGLING:
                warn(f"warning: dependency {d} does not resolve now ({detail})")
        elif g.is_task_id(d):
            if store.find(d) is None:
                raise CrossboxError(f"dependency {d!r}: no such task in queue or archive")
        else:
            raise CrossboxError(f"dependency {d!r} is neither a step id, a step ref "
                                f"nor a task id")
