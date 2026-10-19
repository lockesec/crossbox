"""Task and step verbs. Each verb is a function ``cmd_<verb>(ctx, ...) -> int``
returning an exit code, raising CrossboxError to refuse."""
from __future__ import annotations

import copy
import json
import os
import re
from pathlib import Path

from . import claims as _claims
from . import evidence as _evidence
from . import deps as _deps
from . import gitutil, guard, model, needs
from .config import PRIORITY_RANK
from .errors import NOT_EVALUABLE, OK, PARTIAL, REFUSED, CrossboxError, NotEvaluable, UsageError
from .ids import next_step_id, step_chr

PREMISE_VERDICTS = ("STILL-VALID", "PREMISE-SHIFTED", "ALREADY-IMPLEMENTED",
                    "SUPERSEDED", "ABSORBED", "BLOCKED", "NOT_EVALUABLE")


# ---------------------------------------------------------------- helpers

def _nonblank(value, what):
    if value is None:
        return None
    if not str(value).strip():
        raise CrossboxError(f"refusing an empty {what} (a failed shell expansion must "
                            f"not read as a write)")
    return value


def _csv(value):
    if value is None:
        return None
    return [p.strip() for p in str(value).split(",") if p.strip()]


def check_priority(ctx, p):
    if p is None:
        return None
    if p not in ctx.cfg.priorities:
        raise CrossboxError(f"unknown priority {p!r}: use one of "
                            f"{', '.join(ctx.cfg.priorities)}")
    return p


def _writable(ctx, tf):
    if not tf.ok:
        raise CrossboxError(f"{ctx.cfg.rel(tf.path)} does not parse ({tf.error}); "
                            f"run `crossbox doctor check`")
    model.require_writable(tf.data, ctx.cfg.rel(tf.path))
    return copy.deepcopy(tf.data)


def _save(ctx, tf, data, expected_ids=None):
    model.write_task(tf.path, data, expected_ids, original=tf.text, warn=ctx.warn)
    ctx.store.invalidate()


def resolve_step(ctx, ref, queue_only=False):
    tid, sid = ctx.g.parse_ref(ref)
    tf = ctx.store.resolve(tid, queue_only=queue_only)
    if not tf.ok:
        raise CrossboxError(f"{ctx.cfg.rel(tf.path)} does not parse ({tf.error})")
    if tf.step(sid) is None:
        have = ", ".join(str(i) for i in model.step_ids(tf.data)) or "none"
        raise CrossboxError(f"{tid} has no step {sid!r} (steps: {have})")
    return tf, sid


def _step(data, sid):
    for s in model.steps_of(data):
        if isinstance(s, dict) and s.get("id") == sid:
            return s
    raise CrossboxError(f"step {sid!r} vanished")


def append_note(step, text):
    old = step.get("notes")
    if isinstance(old, str) and old.strip():
        step["notes"] = old.rstrip("\n") + "\n\n" + text
    elif old not in (None, "") and not isinstance(old, str):
        step["notes"] = str(old) + "\n\n" + text
    else:
        step["notes"] = text


def _warn_needs(ctx, tokens):
    """Writers accept exactly what `ready` accepts. With a registry present an
    unknown or multi-word token is refused (ready would call the step
    MALFORMED); with no registry every non-built-in token draws a warning,
    because ready cannot evaluate it and will say so (exit 2)."""
    if any(not isinstance(t, str) or not t.strip() for t in tokens):
        raise CrossboxError("needs: every token must be a non-empty string")
    try:
        reg = needs.Registry.load(ctx.cfg.registry)
    except CrossboxError as e:
        ctx.warn(f"warning: {e.message}; `ready` is NOT EVALUABLE until it is fixed")
        return
    refused = []
    for t in tokens:
        r = reg.resolve(t)[0]
        if r["status"] == "split":
            refused.append(f"{r['token']!r} contains spaces; write one token per entry")
        elif r["status"] == "unknown" and reg.present:
            sug = f" (did you mean {r['suggestion']}?)" if r["suggestion"] else ""
            refused.append(f"{r['token']!r} is not in the registry{sug}")
        elif r["status"] == "unknown":
            ctx.warn(f"warning: need {r['token']!r}: no needs registry at "
                     f"{ctx.cfg.rel(ctx.cfg.registry)}, so `ready` cannot tell whether it "
                     f"is a gate and will report the step NOT EVALUABLE")
        elif r["status"] == "alias":
            ctx.warn(f"warning: need {r['token']!r} is an alias of {r['canonical']!r}")
    if refused:
        raise CrossboxError("needs: " + "; ".join(refused) + f"; add it to "
                            f"{ctx.cfg.rel(ctx.cfg.registry)} first")


# ---------------------------------------------------------------- init

def cmd_init(ctx, prefix=None, machine=None):
    cfgp = ctx.repo / ".crossbox" / "config.yaml"
    for d in (ctx.cfg.queue, ctx.cfg.archive, ctx.cfg.notes):
        d.mkdir(parents=True, exist_ok=True)
        keep = d / ".gitkeep"
        if not any(d.iterdir()):
            keep.write_text("")
    if not cfgp.exists():
        name = machine or ctx.machine
        text = (f"# crossbox configuration. Every key is described in\n"
                f"# https://github.com/sethpsonnier/crossbox/blob/main/docs/configuration.md\n"
                f"prefix: {prefix or 'TASK'}\n"
                f"machines:\n  {name}:\n    provides: []\n")
        model.atomic_write_text(cfgp, text)
        ctx.say(f"wrote {ctx.cfg.rel(cfgp)}")
    else:
        ctx.say(f"{ctx.cfg.rel(cfgp)} already exists; left as is")
    ga = ctx.repo / ".gitattributes"
    line = f"{ctx.cfg.rel(ctx.cfg.claims)} merge=crossbox-claims"
    existing = ga.read_text(encoding="utf-8") if ga.exists() else ""
    if line not in existing.splitlines():
        model.atomic_write_text(ga, existing + ("" if not existing or existing.endswith("\n") else "\n") + line + "\n")
        ctx.say(f"added `{line}` to .gitattributes")
    ctx.say("next: `crossbox doctor install-merge-driver` and `crossbox doctor install-hook` "
            "in every clone")
    return OK


# ---------------------------------------------------------------- new

def cmd_new(ctx, title, domain=None, priority=None):
    title = model.one_line(_nonblank(title, "title") or "")
    dom = None
    if domain is not None:
        if not re.fullmatch(r"[A-Za-z]+", domain):
            raise UsageError(f"domain {domain!r}: letters only")
        dom = domain.upper()
    check_priority(ctx, priority)
    if not ctx.cfg.queue.is_dir():
        raise CrossboxError(f"{ctx.cfg.rel(ctx.cfg.queue)} does not exist; run `crossbox init`")
    # advisory: does someone elsewhere already hold a claim that looks like this?
    saved = ctx.out
    ctx.out = ctx.err
    try:
        _claims.cmd_claim_check(ctx, title, quiet=True)
    finally:
        ctx.out = saved
    tid, warnings = guard.mint_task_id(ctx, dom)
    for w in warnings:
        ctx.warn(w)
    path = ctx.cfg.queue / f"{tid}.yaml"
    if path.exists():
        raise CrossboxError(f"{ctx.cfg.rel(path)} already exists (its id field disagrees "
                            f"with its name?); refusing to overwrite a live task")
    data = {"id": tid, "title": title, "priority": priority or "normal"}
    if domain is not None:
        data["domain"] = domain
    data.update({"created": ctx.today, "created_by": ctx.machine,
                 "source_task": "", "steps": []})
    model.write_task(path, data, expected_ids=[])
    ctx.store.invalidate()
    ctx.say(f"created {tid}  ({ctx.cfg.rel(path)})")
    ctx.say(f'next: crossbox add {tid} "first step title"')
    return OK


# ---------------------------------------------------------------- add

def _add_one(ctx, tf, title, target=None, needs_=None, depends=None, notes=None,
             priority=None):
    title = model.one_line(_nonblank(title, "title") or "")
    if not title:
        raise CrossboxError("refusing an empty title")
    tgt = _deps.check_target_write(target or "any", ctx.cfg)
    if notes is not None:
        _nonblank(notes, "notes")
    check_priority(ctx, priority)
    data = _writable(ctx, tf)
    old_ids = model.step_ids(data)
    sid = next_step_id(old_ids)
    _claims.refuse_claimed_id(ctx, f"{tf.id}.{sid}")
    dl = list(depends or [])
    _deps.validate_depends(dl, tf, sid, ctx.store, old_ids, ctx.warn)
    nl = list(needs_ or [])
    _warn_needs(ctx, nl)
    step = {"id": sid, "title": title, "target": tgt, "needs": nl,
            "status": "pending", "depends_on": dl}
    if notes is not None:
        step["notes"] = notes
    if data.get("steps") is None:
        data["steps"] = []
    data["steps"].append(step)
    if priority:
        data["priority"] = priority
    _save(ctx, tf, data, old_ids + [sid])
    return sid


def _run_guard(ctx, tf):
    _writable(ctx, tf)  # a malformed local file is a refusal, not "could not look"
    code, msg = guard.origin_guard(ctx, tf.path)
    if code != OK:
        raise CrossboxError(msg, code)
    return msg


def cmd_add(ctx, task, title, target=None, needs_=None, depends=None, notes=None,
            priority=None):
    tf = ctx.store.resolve(task, queue_only=True)
    _run_guard(ctx, tf)
    sid = _add_one(ctx, tf, title, target, needs_, depends, notes, priority)
    ctx.say(f"added {tf.id}.{sid}")
    return OK


def split_pipe_row(line):
    cols, cur, i = [], [], 0
    while i < len(line):
        ch = line[i]
        if ch == "\\" and i + 1 < len(line) and line[i + 1] == "|":
            cur.append("|")
            i += 2
            continue
        if ch == "|":
            cols.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    cols.append("".join(cur))
    return [c.strip() for c in cols]


BATCH_COLS = ("title", "target", "needs", "depends", "notes", "priority")


def parse_batch(text, fmt="auto"):
    rows = []
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip("\r")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if fmt == "jsonl" or (fmt == "auto" and line.lstrip().startswith("{")):
            try:
                obj = json.loads(line)
            except ValueError as e:
                raise CrossboxError(f"line {n}: not valid JSON ({e}); nothing added")
            if not isinstance(obj, dict):
                raise CrossboxError(f"line {n}: a JSON row must be an object; nothing added")
            unknown = set(obj) - set(BATCH_COLS) - {"depends_on"}
            if unknown:
                raise CrossboxError(f"line {n}: unknown field(s) {sorted(unknown)}; nothing added")

            def lst(v):
                if v is None:
                    return []
                if isinstance(v, list):
                    return [str(x) for x in v]
                return _csv(v)
            rows.append((n, {"title": obj.get("title"), "target": obj.get("target") or None,
                             "needs": lst(obj.get("needs")),
                             "depends": lst(obj.get("depends", obj.get("depends_on"))),
                             "notes": obj.get("notes"), "priority": obj.get("priority")}))
            continue
        cols = split_pipe_row(line)
        if len(cols) > len(BATCH_COLS):
            raise CrossboxError(f"line {n}: {len(cols)} columns (max {len(BATCH_COLS)}); "
                                f"escape a literal pipe as \\| ; nothing added")
        cols += [""] * (len(BATCH_COLS) - len(cols))
        rows.append((n, {"title": cols[0], "target": cols[1] or None,
                         "needs": _csv(cols[2]) or [], "depends": _csv(cols[3]) or [],
                         "notes": cols[4] or None, "priority": cols[5] or None}))
    return rows


def cmd_batch_add(ctx, task, text, fmt="auto"):
    tf = ctx.store.resolve(task, queue_only=True)
    rows = parse_batch(text, fmt)
    if not rows:
        ctx.say("Added 0 of 0 (no rows in input)")
        return REFUSED
    _run_guard(ctx, tf)  # once, up front
    before = len(tf.steps)
    added = 0
    for n, r in rows:
        try:
            cur = ctx.store.resolve(task, queue_only=True)
            sid = _add_one(ctx, cur, r["title"], r["target"], r["needs"], r["depends"],
                           r["notes"], r["priority"])
            added += 1
            ctx.say(f"  added {cur.id}.{sid}  (line {n})")
        except CrossboxError as e:
            ctx.warn(f"  line {n}: {e.message}")
    after = len(ctx.store.resolve(task, queue_only=True).steps)
    delta = after - before
    ctx.say(f"Added {added} of {len(rows)} (yaml delta: {delta})")
    if delta != added:
        ctx.warn("MISMATCH: the file's step count moved by a different amount than "
                 "the rows reported added")
        return PARTIAL if added else REFUSED
    if added == len(rows):
        return OK
    return PARTIAL if added else REFUSED


# ---------------------------------------------------------------- edit

def cmd_edit(ctx, task, priority=None, phase=None, source=None, title=None):
    tf = ctx.store.resolve(task, queue_only=True)
    data = _writable(ctx, tf)
    changes = []
    if priority is not None:
        check_priority(ctx, priority)
        if data.get("priority") != priority:
            data["priority"] = priority
            changes.append("priority")
    if phase is not None and data.get("phase") != phase:
        data["phase"] = phase
        changes.append("phase")
    if source is not None and data.get("source_task") != source:
        data["source_task"] = source
        changes.append("source_task")
    if title is not None:
        t = model.one_line(_nonblank(title, "title"))
        if data.get("title") != t:
            data["title"] = t
            changes.append("title")
    if not changes:
        ctx.say(f"{tf.id}: no change")
        return OK
    _save(ctx, tf, data, model.step_ids(data))
    ctx.say(f"{tf.id}: updated {', '.join(changes)}")
    return OK


def cmd_edit_step(ctx, ref, title=None, notes=None, append_notes=None, clear_notes=False,
                  replace_notes=False, target=None, needs_=None, depends=None,
                  premise_verdict=None):
    if all(v is None for v in (title, notes, append_notes, target, needs_, depends,
                               premise_verdict)) and not clear_notes:
        raise UsageError("edit-step: give at least one of --title, --notes, "
                         "--append-notes, --clear-notes, --target, --needs, --depends, "
                         "--premise-verdict")
    if notes is not None and append_notes is not None:
        raise UsageError("--notes and --append-notes are mutually exclusive")
    if clear_notes and (notes is not None or append_notes is not None):
        raise UsageError("--clear-notes cannot be combined with --notes/--append-notes")
    _nonblank(title, "title")
    _nonblank(notes, "notes")
    _nonblank(append_notes, "notes")
    tf, sid = resolve_step(ctx, ref)
    data = _writable(ctx, tf)
    s = _step(data, sid)
    changed = []
    if title is not None:
        s["title"] = model.one_line(title)
        changed.append("title")
    if notes is not None:
        old = s.get("notes")
        if old not in (None, "") and str(old).strip() and not replace_notes:
            raise CrossboxError(f"{ref} already has notes; use --append-notes, or "
                                f"--notes with --replace-notes to overwrite them")
        s["notes"] = notes
        changed.append("notes")
    if append_notes is not None:
        append_note(s, append_notes)
        changed.append("notes (appended)")
    if clear_notes and "notes" in s:
        del s["notes"]
        changed.append("notes (cleared)")
    if target is not None:
        s["target"] = _deps.check_target_write(target, ctx.cfg)
        changed.append("target")
    if needs_ is not None:
        _warn_needs(ctx, needs_)
        s["needs"] = list(needs_)
        changed.append("needs")
    if depends is not None:
        ids = model.step_ids(data)
        _deps.validate_depends(depends, tf, sid, ctx.store, ids, ctx.warn)
        s["depends_on"] = list(depends)
        graph = _deps.sibling_graph(model.steps_of(data), tf.id)
        for cyc in _deps.cycles(graph):
            if sid in cyc:
                raise CrossboxError(f"refusing: this would create a dependency cycle "
                                    f"{' -> '.join(cyc)}")
            ctx.warn(f"warning: pre-existing cycle {' -> '.join(cyc)} (not through {sid}; "
                     f"left alone)")
        changed.append("depends_on")
    if premise_verdict is not None:
        if premise_verdict not in PREMISE_VERDICTS:
            raise CrossboxError(f"--premise-verdict must be one of {', '.join(PREMISE_VERDICTS)}")
        s["premise_verdict"] = premise_verdict
        changed.append("premise_verdict")
    if not changed:
        ctx.say(f"{ref}: no change")
        return OK
    _save(ctx, tf, data, model.step_ids(data))
    ctx.say(f"{ref}: updated {', '.join(changed)}"
            + ("  [archived task]" if tf.tier == "archive" else ""))
    return OK


# ---------------------------------------------------------------- step-structure verbs

def _dup_guard(tf):
    ids = model.step_ids(tf.data)
    if len(ids) != len(set(ids)):
        raise CrossboxError(f"{tf.id} has duplicate step ids; run `crossbox doctor check`")


def _rewrite_sibling_deps(data, tid, mapping, drop=()):
    for s in model.steps_of(data):
        deps = s.get("depends_on")
        if not isinstance(deps, list):
            continue
        new = []
        for d in deps:
            if isinstance(d, str) and "-" not in d:
                if d in drop:
                    continue
                new.append(mapping.get(d, d))
            elif isinstance(d, str) and d.startswith(tid + "."):
                sid = d[len(tid) + 1:]
                if sid in drop:
                    continue
                new.append(f"{tid}.{mapping.get(sid, sid)}")
            else:
                new.append(d)
        s["depends_on"] = new


def external_refs(ctx, tid, sids):
    """[(TaskFile, step_id, token)] of steps in OTHER tasks whose depends_on names
    one of ``sids`` of task ``tid``."""
    out = []
    want = {f"{tid}.{s}" for s in sids}
    unread = []
    for f in ctx.store.files():
        if f.id == tid:
            continue
        if not f.ok:
            # a file that does not parse may hold the reference: when its text
            # names one (or cannot be read at all) the rename must not guess
            if f.text is None or any(w in f.text for w in want):
                unread.append(f)
            continue
        for s in f.steps:
            if isinstance(s, dict) and isinstance(s.get("depends_on"), list):
                for d in s["depends_on"]:
                    if d in want:
                        out.append((f, s.get("id"), d))
    if unread:
        raise NotEvaluable(_unread_dependents(ctx, unread))
    return out


def _unread_dependents(ctx, files):
    return (f"{', '.join(ctx.cfg.rel(f.path) for f in files)} may reference this and "
            f"does not parse; references there cannot be checked or rewritten, so "
            f"nothing was changed (run `crossbox doctor check`)")


def _external_writes(ctx, tid, mapping=None, strip=()):
    """Stage (do not write) the edits other task files need: ``mapping``
    renames references to ``tid``'s steps, ``strip`` deletes the given tokens.
    Every staged file has passed the writability check and the round-trip gate,
    so a bad dependent refuses the verb BEFORE anything is written."""
    files = {}
    sids = list(mapping) if mapping else [t.split(".", 1)[1] for t in strip]
    for f, _sid, _tok in external_refs(ctx, tid, sids):
        files[str(f.path)] = f
    out = []
    for f in files.values():
        data = _writable(ctx, f)
        for s in model.steps_of(data):
            deps = s.get("depends_on")
            if not isinstance(deps, list):
                continue
            if mapping:
                s["depends_on"] = [
                    f"{tid}.{mapping[d.split('.', 1)[1]]}"
                    if isinstance(d, str) and d.startswith(tid + ".")
                    and d.split(".", 1)[1] in mapping else d for d in deps]
            else:
                s["depends_on"] = [d for d in deps if d not in strip]
        ids = model.step_ids(data)
        model.check_task(f.path.name, data, ids)
        out.append((f.path, data, ids, f.text))
    return out


def _commit(ctx, writes, then=None):
    """Write every staged (path, data, ids, original_text), then run ``then``
    (the claim edit). All validation happened while staging; what remains is
    I/O. If any write fails, every file already written is put back to its
    original text and the error says so; if a file cannot be put back the error
    names it (that is the one case that needs `crossbox doctor check`)."""
    done = []
    try:
        for path, data, ids, original in writes:
            model.write_task(path, data, ids, original=original, warn=ctx.warn)
            done.append((path, original))
        if then is not None:
            then()
    except (CrossboxError, OSError) as e:
        stuck = []
        for path, original in reversed(done):
            try:
                model.atomic_write_text(path, original)
            except OSError:
                stuck.append(path.name)
        ctx.store.invalidate()
        if stuck:
            raise CrossboxError(f"{e}; PARTIALLY COMMITTED: could not restore "
                                f"{', '.join(stuck)}; run `crossbox doctor check`")
        raise CrossboxError(f"{e}; restored {len(done)} file(s) already written, "
                            f"nothing changed")
    ctx.store.invalidate()
    n = len(writes) - 1
    if n:
        ctx.say(f"rewrote cross-task references in {n} other task file(s)")


def _stage_check(writes):
    """Gate the primary task too, so nothing is written unless every staged
    file would pass."""
    for path, data, ids, _orig in writes:
        model.check_task(path.name, data, ids)


def _ref_map(tid, mapping):
    return {f"{tid}.{o}": f"{tid}.{n}" for o, n in mapping.items() if o != n}


def cmd_remove_step(ctx, ref, force=False):
    tf, sid = resolve_step(ctx, ref, queue_only=True)
    _dup_guard(tf)
    data = _writable(ctx, tf)
    s = _step(data, sid)
    if s.get("status") in model.TERMINAL and not force:
        raise CrossboxError(f"{ref} is {s.get('status')}; closed history is not removed "
                            f"(use --force if you mean it)")
    dependents = [x.get("id") for x in model.steps_of(data)
                  if x is not s and isinstance(x.get("depends_on"), list)
                  and (sid in x["depends_on"] or f"{tf.id}.{sid}" in x["depends_on"])]
    ext = external_refs(ctx, tf.id, [sid])
    if (dependents or ext) and not force:
        names = [f"{tf.id}.{d}" for d in dependents] + [f"{f.id}.{i}" for f, i, _ in ext]
        raise CrossboxError(f"{ref} is depended on by {', '.join(names)} "
                            f"(use --force to remove it and strip those references)")
    release = _claims.plan_removal(ctx, f"{tf.id}.{sid}", force)
    data["steps"] = [x for x in data["steps"] if x is not s]
    _rewrite_sibling_deps(data, tf.id, {}, drop={sid})
    expected = [i for i in model.step_ids(tf.data) if i != sid]
    writes = ([(tf.path, data, expected, tf.text)]
              + _external_writes(ctx, tf.id, strip={f"{tf.id}.{sid}"}))
    _stage_check(writes)
    _commit(ctx, writes, lambda: _claims.release_claims(ctx, release))
    ctx.say(f"removed {ref}" + (f"; stripped it from {len(dependents) + len(ext)} "
                                f"dependent(s)" if dependents or ext else ""))
    return OK


def cmd_move_step(ctx, ref, new, force=False):
    if not model.STEP_ID_RE.fullmatch(new or ""):
        raise UsageError(f"new step id {new!r}: lower-case letters only")
    tf, sid = resolve_step(ctx, ref, queue_only=True)
    _dup_guard(tf)
    if tf.step(new) is not None:
        raise CrossboxError(f"{tf.id} already has a step {new!r}")
    data = _writable(ctx, tf)
    refs = _ref_map(tf.id, {sid: new})
    carry = _claims.plan_carry(ctx, refs, force)
    _step(data, sid)["id"] = new
    _rewrite_sibling_deps(data, tf.id, {sid: new})
    expected = [new if i == sid else i for i in model.step_ids(tf.data)]
    writes = [(tf.path, data, expected, tf.text)] + _external_writes(ctx, tf.id, {sid: new})
    _stage_check(writes)
    _commit(ctx, writes, lambda: _claims.apply_carry(ctx, carry, refs))
    ctx.say(f"moved {ref} -> {tf.id}.{new}")
    return OK


def cmd_swap(ctx, ref, other, force=False):
    tf, a = resolve_step(ctx, ref, queue_only=True)
    _dup_guard(tf)
    b = other.split(".")[-1]
    if tf.step(b) is None:
        raise CrossboxError(f"{tf.id} has no step {b!r}")
    if a == b:
        ctx.say("swap: same step; no change")
        return OK
    data = _writable(ctx, tf)
    refs = _ref_map(tf.id, {a: b, b: a})
    carry = _claims.plan_carry(ctx, refs, force)
    sa, sb = _step(data, a), _step(data, b)
    sa["id"], sb["id"] = b, a
    _rewrite_sibling_deps(data, tf.id, {a: b, b: a})
    expected = [b if i == a else a if i == b else i for i in model.step_ids(tf.data)]
    writes = [(tf.path, data, expected, tf.text)] + _external_writes(ctx, tf.id, {a: b, b: a})
    _stage_check(writes)
    _commit(ctx, writes, lambda: _claims.apply_carry(ctx, carry, refs))
    ctx.say(f"swapped {tf.id}.{a} <-> {tf.id}.{b}")
    return OK


def cmd_reorder(ctx, ref, before=None, after=None, to=None, force=False):
    if sum(x is not None for x in (before, after, to)) != 1:
        raise UsageError("reorder: give exactly one of --before, --after, --to")
    tf, sid = resolve_step(ctx, ref, queue_only=True)
    _dup_guard(tf)
    data = _writable(ctx, tf)
    steps = list(data["steps"])
    moving = _step(data, sid)
    rest = [s for s in steps if s is not moving]
    if to is not None:
        if not 1 <= to <= len(steps):
            raise CrossboxError(f"--to must be between 1 and {len(steps)}")
        pos = to - 1
    else:
        anchor = (before or after).split(".")[-1]
        if anchor == sid:
            raise CrossboxError("reorder: a step cannot be placed relative to itself")
        idx = [i for i, s in enumerate(rest) if s.get("id") == anchor]
        if not idx:
            raise CrossboxError(f"{tf.id} has no step {anchor!r}")
        pos = idx[0] + (1 if after else 0)
    new_order = rest[:pos] + [moving] + rest[pos:]
    if [s.get("id") for s in new_order] == [s.get("id") for s in steps]:
        ctx.say(f"reorder: {ref} is already at that position; no change")
        return OK
    mapping = {s.get("id"): step_chr(i + 1) for i, s in enumerate(new_order)}
    renamed_terminal = [o for o, n in mapping.items() if o != n and
                        _step(data, o).get("status") in model.TERMINAL]
    if renamed_terminal and not force:
        raise CrossboxError(f"reorder would rename closed step(s) {', '.join(renamed_terminal)} "
                            f"(their ids may be cited elsewhere); use --force")
    for s in new_order:
        s["id"] = mapping[s["id"]]
    data["steps"] = new_order
    _rewrite_sibling_deps(data, tf.id, mapping)
    graph = _deps.sibling_graph(new_order, tf.id)
    if _deps.cycles(graph):
        raise CrossboxError("reorder would leave a dependency cycle; nothing written")
    refs = _ref_map(tf.id, mapping)
    carry = _claims.plan_carry(ctx, refs, force)
    writes = ([(tf.path, data, [step_chr(i + 1) for i in range(len(new_order))], tf.text)]
              + _external_writes(ctx, tf.id, {o: n for o, n in mapping.items() if o != n}))
    _stage_check(writes)
    _commit(ctx, writes, lambda: _claims.apply_carry(ctx, carry, refs))
    ctx.say(f"reordered {tf.id}: " + ", ".join(f"{o}->{n}" for o, n in mapping.items() if o != n))
    return OK


# ---------------------------------------------------------------- done / skip / reopen

def _claimed_by_anyone(ctx, ref):
    try:
        rows = _claims.load_claims(ctx.cfg.claims)
    except _claims.ClaimsDamaged:
        return None
    return any(_claims.is_active(c) and c.get("step_ref") == ref for c in rows)


def _my_claims_on(ctx, ref):
    try:
        rows = _claims.load_claims(ctx.cfg.claims)
    except _claims.ClaimsDamaged:
        return []
    return [str(c.get("id")) for c in rows if _claims.is_active(c) and c.get("step_ref") == ref
            and _claims.is_mine(c, ctx.machine, ctx.slot)]


def _others_claiming(ctx, ref):
    """Active claims on ``ref`` held by another (machine, slot), siblings'
    included; [] when the view cannot be read (the hint is advisory)."""
    try:
        view = _claims.read_view(ctx)
    except _claims.ClaimsDamaged:
        return []
    return [c for c in _claims.active_step_claims(view.union).get(ref, [])
            if not _claims.is_mine(c, ctx.machine, ctx.slot)]


def _record_commits(ctx, ref, tf, sid, shas) -> int:
    """Append commit evidence to a step; returns how many were new."""
    data = _writable(ctx, tf)
    st = _step(data, sid)
    have = st.get("commits")
    have = list(have) if isinstance(have, list) else []
    new = [x for x in shas if x not in have]
    if new:
        st["commits"] = have + new
        _save(ctx, tf, data, model.step_ids(data))
    return len(new)


def _report_commits(ctx, ref, shas) -> None:
    for sha in shas:
        ctx.say("  " + _evidence.grade(ctx, sha)[1])
        if not _evidence.names_step(ctx, sha, ref):
            ctx.warn(f"warning: commit {sha[:7]} does not name {ref} in its subject or a "
                     f"{_evidence.TRAILER}: trailer (recorded anyway)")


def cmd_done(ctx, ref, force=False, commits=None):
    tf, sid = resolve_step(ctx, ref)
    shas = []
    for spec in commits or []:
        sha = _evidence.resolve_commit(ctx, spec)
        if sha not in shas:
            shas.append(sha)
    s = tf.step(sid)
    if not _deps.workable(s.get("target"), ctx.cfg, ctx.machine_caps):
        t, known = _deps.read_target(s.get("target"), ctx.cfg)
        why = "is unknown to this config" if not known else "is not provided here"
        raise CrossboxError(f"{ref}: target {t!r} {why} (machine {ctx.machine!r} provides "
                            f"{', '.join(sorted(ctx.machine_caps))})")
    status = s.get("status")
    if status == "done":
        if not shas:
            ctx.say(f"{ref} is already done; no change")
            return OK
        n = _record_commits(ctx, ref, tf, sid, shas)
        ctx.say(f"{ref} is already done; recorded commit evidence ({n} new)" if n
                else f"{ref} is already done; commit(s) already recorded")
        _report_commits(ctx, ref, shas)
        return OK
    if status == "skipped":
        raise CrossboxError(f"{ref} is skipped; `crossbox reopen` it first if it was "
                            f"actually done")
    unmet = []
    deps_ = s.get("depends_on") or []
    if not isinstance(deps_, list):
        raise CrossboxError(f"{ref}: depends_on is not a list; run `crossbox doctor check`")
    for d in deps_:
        st, detail = _deps.dep_state(d, tf, ctx.store)
        if st != _deps.MET:
            unmet.append(f"{_deps.qualify(str(d), tf.id)} ({st}{'' if st == 'unmet' else ': ' + detail})")
    if unmet and not force:
        raise CrossboxError(f"{ref}: unmet dependencies: " + "; ".join(unmet)
                            + " (use --force to close anyway)")
    data = _writable(ctx, tf)
    st = _step(data, sid)
    st["status"] = "done"
    st.setdefault("completed_by", ctx.machine)
    st.setdefault("completed", ctx.today)
    if shas:
        have = st.get("commits") if isinstance(st.get("commits"), list) else []
        st["commits"] = list(have) + [x for x in shas if x not in have]
    if unmet:
        append_note(st, f"CLOSED WITH --force {ctx.date_str} by {ctx.origin}; unmet: "
                        + "; ".join(unmet))
    _save(ctx, tf, data, model.step_ids(data))
    ctx.say(f"done {ref}" + ("  (forced past unmet dependencies)" if unmet else ""))
    _report_commits(ctx, ref, shas)
    others = _others_claiming(ctx, ref)
    for c in others:
        ctx.warn(f"warning: {ref} is claimed by {c.get('id')} "
                 f"({_claims.c_machine(c)}/{_claims.c_slot(c)}); closed anyway "
                 f"(claims are advisory): tell them it is done")
    mine = _my_claims_on(ctx, ref)
    if mine:
        ctx.warn(f"hint: release {', '.join(mine)} when you are finished with it")
    elif not others and _claimed_by_anyone(ctx, ref) is False:
        ctx.warn(f"advice: no active claim covered {ref}; claim steps before working them "
                 f"so parallel sessions can see you")
    return OK


def cmd_batch_done(ctx, refs, force=False):
    ok = 0
    for r in refs:
        try:
            cmd_done(ctx, r, force=force)
            ok += 1
        except CrossboxError as e:
            ctx.warn(f"  {r}: {e.message}")
    ctx.say(f"closed {ok} of {len(refs)}")
    if ok == len(refs):
        return OK
    return PARTIAL if ok else REFUSED


def cmd_skip(ctx, ref, reason, superseded_by=None):
    _nonblank(reason, "--reason")
    tf, sid = resolve_step(ctx, ref)
    s = tf.step(sid)
    if s.get("status") in model.TERMINAL:
        raise CrossboxError(f"{ref} is already {s.get('status')}; nothing written")
    sup = ""
    if superseded_by:
        if ctx.g.is_ref(superseded_by):
            if superseded_by == ref:
                raise CrossboxError("a step cannot supersede itself")
            stf, ssid = ctx.g.parse_ref(superseded_by)
            target = ctx.store.resolve(stf)
            if not target.ok or target.step(ssid) is None:
                raise CrossboxError(f"--superseded-by {superseded_by}: no such step")
        elif ctx.g.is_task_id(superseded_by):
            ctx.store.resolve(superseded_by)
        else:
            raise UsageError(f"--superseded-by {superseded_by!r}: expected a task id or "
                             f"step ref")
        sup = f" Superseded by: {superseded_by}."
    data = _writable(ctx, tf)
    st = _step(data, sid)
    st["status"] = "skipped"
    st.setdefault("completed_by", ctx.machine)
    st.setdefault("completed", ctx.today)
    append_note(st, f"SKIPPED {ctx.date_str} by {ctx.origin}. Reason: "
                    f"{model.one_line(reason)}.{sup}")
    _save(ctx, tf, data, model.step_ids(data))
    ctx.say(f"skipped {ref}" + ("  [archived task]" if tf.tier == "archive" else ""))
    return OK


def cmd_reopen(ctx, ref, reason):
    _nonblank(reason, "--reason")
    tf, sid = resolve_step(ctx, ref)
    s = tf.step(sid)
    if s.get("status") not in model.TERMINAL:
        raise CrossboxError(f"{ref} is {s.get('status')!r}, not done or skipped; "
                            f"nothing written")
    data = _writable(ctx, tf)
    st = _step(data, sid)
    old = st.get("status")
    retracted = [f"status={old}"]
    for k in ("completed_by", "completed", "commits"):
        if k in st:
            v = st.pop(k)
            retracted.append(f"{k}={','.join(map(str, v)) if isinstance(v, list) else v}")
    st["status"] = "pending"
    append_note(st, f"REOPENED {ctx.date_str} by {ctx.origin}. Reason: "
                    f"{model.one_line(reason)}. Retracted: {', '.join(retracted)}.")
    _save(ctx, tf, data, model.step_ids(data))
    ctx.say(f"reopened {ref}")
    for f in ctx.store.files():
        if not f.ok:
            continue
        for x in f.steps:
            if not isinstance(x, dict) or x.get("status") != "done":
                continue
            dl = x.get("depends_on") if isinstance(x.get("depends_on"), list) else []
            if ref in dl or (f.id == tf.id and sid in dl):
                ctx.warn(f"advice: {f.id}.{x.get('id')} depends on {ref} and is still done")
    return OK


# ---------------------------------------------------------------- show / status

MARK = {"done": "[x]", "skipped": "[-]", "pending": "[ ]", "blocked": "[!]",
        "in_progress": "[~]"}


def cmd_show(ctx, task, full=False):
    tf = ctx.store.resolve(task)
    tag = ""
    if tf.tier == "archive":
        tag = "  [ARCHIVED" + ("; shadowed by a queue copy" if ctx.store.is_shadowed(tf) else "") + "]"
    if not tf.ok:
        ctx.say(f"{tf.id}  ({ctx.cfg.rel(tf.path)}){tag}")
        ctx.say(f"  DOES NOT PARSE: {tf.error}")
        return REFUSED
    d = tf.data
    ctx.say(f"{tf.id}  {d.get('title', '')}{tag}")
    meta = [f"{k}: {d[k]}" for k in ("priority", "domain", "phase", "created", "created_by",
                                     "source_task") if d.get(k) not in (None, "")]
    ctx.say("  " + " · ".join(meta))
    ext = [k for k in d if k not in model.TASK_KEYS]
    if ext:
        ctx.say(f"  other keys: {', '.join(ext)}")
    steps = tf.steps
    closed = sum(1 for s in steps if isinstance(s, dict) and s.get("status") in model.TERMINAL)
    ctx.say(f"  steps: {closed} of {len(steps)} closed")
    for s in steps:
        if not isinstance(s, dict):
            ctx.say(f"  ??? malformed step: {s!r}")
            continue
        st = s.get("status")
        mark = MARK.get(st, "[?]")
        ctx.say(f"  {mark} {s.get('id')}  {s.get('title', '')}")
        t, known = _deps.read_target(s.get("target"), ctx.cfg)
        bits = [f"status: {st}" + ("" if st in MARK else "  (UNKNOWN STATUS)"),
                f"target: {t}" + ("" if known else " (unknown)")]
        if s.get("needs"):
            bits.append(f"needs: {', '.join(map(str, s['needs']))}")
        if s.get("depends_on"):
            dl = s["depends_on"]
            shown_deps = ", ".join(map(str, dl)) if isinstance(dl, list) else repr(dl)
            bits.append(f"depends_on: {shown_deps}")
        if s.get("completed"):
            bits.append(f"closed {s.get('completed')} by {s.get('completed_by')}")
        if isinstance(s.get("commits"), list) and s["commits"]:
            bits.append("commits: " + ", ".join(str(x)[:12] for x in s["commits"]))
        ctx.say("        " + " · ".join(bits))
        notes = s.get("notes")
        if notes not in (None, ""):
            lines = str(notes).splitlines()
            shown = lines if full else lines[:2]
            for ln in shown:
                ctx.say(f"        | {ln}")
            if len(lines) > len(shown):
                ctx.say(f"        | ... ({len(lines) - len(shown)} more lines; --full shows all)")
    return OK


def _task_rank(tf):
    return PRIORITY_RANK.get(str(tf.data.get("priority", "normal")), 4)


def cmd_status(ctx, domain=None):
    if not ctx.cfg.queue.is_dir():
        raise CrossboxError(f"{ctx.cfg.rel(ctx.cfg.queue)} does not exist; run "
                            f"`crossbox init`", NOT_EVALUABLE)
    files = ctx.store.files("queue")
    bad = [f for f in files if not f.ok]
    good = [f for f in files if f.ok]
    if domain:
        good = [f for f in good if ctx.g.domain_of(f.id) == domain.upper()]
    good.sort(key=lambda f: (_task_rank(f), f.id))
    for f in good:
        steps = [s for s in f.steps if isinstance(s, dict)]
        closed = sum(1 for s in steps if s.get("status") in model.TERMINAL)
        ctx.say(f"{model.clean(f.id)}  [{closed}/{len(steps)}]  "
                f"{model.clean(f.data.get('priority', 'normal')):<8}  "
                f"{model.clean(f.data.get('title', ''))}")
        for s in steps:
            st = s.get("status")
            if st in model.TERMINAL:
                continue
            flag = "" if st in model.OPEN else "  (UNKNOWN STATUS)"
            ctx.say(f"    {MARK.get(st, '[?]')} {model.clean(s.get('id'))}  "
                    f"{model.clean(s.get('title', ''))}{flag}")
    ctx.say(f"{len(good)} task(s) listed of {len(files)} queue file(s)"
            + (f"; {len(bad)} unparseable (not listed, so this listing is a floor)"
               if bad else ""))
    for f in bad:
        ctx.warn(f"warning: {model.clean(ctx.cfg.rel(f.path))} does not parse "
                 f"({model.clean(f.error)}); skipped")
    # same contract as `status --summary` and `ready`: a skipped file is a finding
    return REFUSED if bad else OK


def cmd_status_summary(ctx, as_json=False, with_ready=False, domain=None, operator=False):
    from . import ready as _ready
    cfg = ctx.cfg
    if not cfg.queue.is_dir():
        msg = f"{cfg.rel(cfg.queue)} does not exist"
        if as_json:
            ctx.say(json.dumps({"not_evaluable": msg}))
        else:
            ctx.say(f"status: NOT EVALUABLE: {msg}")
        return NOT_EVALUABLE
    files = ctx.store.files("queue")
    if domain:
        files = [f for f in files if ctx.g.domain_of(f.id) == domain.upper()]
    bad = [f for f in files if not f.ok]
    by_status, by_prio, by_dom, by_target = {}, {}, {}, {}
    n_steps = 0
    for f in files:
        if not f.ok:
            continue
        p = str(f.data.get("priority", "normal"))
        by_prio[p] = by_prio.get(p, 0) + 1
        dm = ctx.g.domain_of(f.id) or "(none)"
        by_dom[dm] = by_dom.get(dm, 0) + 1
        for s in f.steps:
            if not isinstance(s, dict):
                by_status["(malformed step)"] = by_status.get("(malformed step)", 0) + 1
                n_steps += 1
                continue
            n_steps += 1
            st = s.get("status")
            key = st if st in model.OPEN + model.TERMINAL else f"UNKNOWN:{st}"
            by_status[key] = by_status.get(key, 0) + 1
            if st in model.OPEN:
                t, known = _deps.read_target(s.get("target"), cfg)
                t = t if known else f"UNKNOWN:{t}"
                by_target[t] = by_target.get(t, 0) + 1
    claims_info = {}
    code = OK
    try:
        view = _claims.read_view(ctx)
        act = [c for c in view.union if _claims.is_active(c)]
        claims_info = {"active": len(act), "of_total": len(view.union),
                       "stale": sum(1 for c in act if _claims.is_stale(c, ctx.today, cfg.stale_days)),
                       "foreign_active": sum(1 for c in view.foreign if _claims.is_active(c)),
                       "sibling_view": view.incomplete or "ok"}
        if view.incomplete:
            # the counts above are a floor: a sibling's claims are hidden
            claims_info["not_evaluable"] = f"sibling view incomplete: {view.incomplete}"
            code = NOT_EVALUABLE
    except _claims.ClaimsDamaged as e:
        claims_info = {"not_evaluable": f"claims file damaged: {e}"}
        code = NOT_EVALUABLE
    ready_info = {"count": None, "cause": "not computed; pass --ready (it runs gate probes)"}
    if with_ready and code == OK:
        res = _ready.classify(ctx, operator=operator)
        ready_info = {"count": len(res["ready"]), "of_open_here": res["open_here"],
                      "gated": len(res["gated"]), "claimed": len(res["claimed"]),
                      "waiting": len(res["waiting"]), "malformed": len(res["malformed"]),
                      "gates_not_evaluable": res["gates_not_evaluable"],
                      "orphaned_claims": len(res["orphaned_claims"])}
        if res["malformed"] or res["gates_not_evaluable"] or res["orphaned_claims"]:
            ready_info["floor"] = True
        if res["gates_not_evaluable"]:
            code = NOT_EVALUABLE
        elif res["malformed"] or res["orphaned_claims"]:
            code = REFUSED   # the same verdict `ready` gives
    if bad and code == OK:
        code = REFUSED
    payload = {
        "machine": ctx.machine, "slot": ctx.slot,
        "tasks": {"parsed": len(files) - len(bad), "of_files": len(files),
                  "unparseable": [f.path.name for f in bad]},
        "counts_are_floor": bool(bad),
        "steps": {"by_status": by_status, "of_total": n_steps},
        "tasks_by_priority": by_prio, "tasks_by_domain": by_dom,
        "open_steps_by_target": by_target,
        "claims": claims_info, "ready": ready_info,
    }
    if as_json:
        ctx.say(json.dumps(payload, indent=2, sort_keys=False, default=str))
        return code
    t = payload["tasks"]
    ctx.say(f"tasks: {t['parsed']} parsed of {t['of_files']} queue file(s)"
            + (f"; {len(bad)} UNPARSEABLE, so every count below is a floor" if bad else ""))
    ctx.say(f"steps ({n_steps}): " + ", ".join(f"{k} {v}" for k, v in sorted(by_status.items())))
    ctx.say("priority: " + ", ".join(f"{k} {v}" for k, v in sorted(by_prio.items())))
    ctx.say("domain: " + ", ".join(f"{k} {v}" for k, v in sorted(by_dom.items())))
    ctx.say("open steps by target: " + (", ".join(f"{k} {v}" for k, v in sorted(by_target.items()))
                                        or "none"))
    if "not_evaluable" in claims_info:
        ctx.say(f"claims: NOT EVALUABLE: {claims_info['not_evaluable']}")
    else:
        ctx.say(f"claims: {claims_info['active']} active of {claims_info['of_total']} "
                f"({claims_info['stale']} stale, {claims_info['foreign_active']} foreign); "
                f"sibling view {claims_info['sibling_view']}")
    if ready_info["count"] is None:
        ctx.say(f"ready here: unknown ({ready_info['cause']})")
    else:
        ctx.say(f"ready here: {ready_info['count']} of {ready_info['of_open_here']} open "
                f"step(s) workable on {ctx.machine}"
                + (" (a floor: some steps are malformed)" if ready_info.get("malformed") else "")
                + (f" (NOT EVALUABLE: {ready_info['gates_not_evaluable']} gate probe(s) gave "
                   f"no answer)" if ready_info.get("gates_not_evaluable") else ""))
    for f in bad:
        ctx.warn(f"warning: {cfg.rel(f.path)} does not parse ({f.error})")
    return code


# ---------------------------------------------------------------- archive

def archivable(tf):
    if not tf.ok or model.structure_problems(tf.data):
        return False, "does not parse or is malformed"
    steps = tf.steps
    if not steps:
        return False, "has no steps"
    open_ = [s.get("id") for s in steps if s.get("status") not in model.TERMINAL]
    if open_:
        return False, f"{len(open_)} step(s) not closed"
    return True, ""


def cmd_archive(ctx):
    queue = ctx.store.files("queue")
    moved, refused, skipped = 0, 0, 0
    ctx.cfg.archive.mkdir(parents=True, exist_ok=True)
    for tf in queue:
        ok, why = archivable(tf)
        if not ok:
            if not tf.ok:
                ctx.warn(f"  {ctx.cfg.rel(tf.path)}: {why}; left in queue")
            skipped += 1
            continue
        dest = ctx.cfg.archive / f"{tf.id}.yaml"
        holders = [f for f in ctx.store.files("archive") if f.id == tf.id]
        if dest.exists() and all(f.path != dest for f in holders):
            holders.append(type(tf)(dest, "archive"))
        conflict = None
        for h in holders:
            if not h.ok:
                conflict = f"{h.path.name} is in the archive and does not parse"
            elif h.data.get("title") != tf.data.get("title"):
                conflict = (f"{h.path.name} holds {tf.id} with a different title "
                            f"({h.data.get('title')!r})")
        if conflict:
            ctx.warn(f"  REFUSED {tf.id}: {conflict}; both files left in place")
            refused += 1
            continue
        for h in holders:
            if h.path != dest:
                h.path.unlink()
            ctx.say(f"  {tf.id}: replacing the archived copy {h.path.name} (same id and title)")
        os.replace(tf.path, dest)
        model.fsync_dir(dest.parent)
        model.fsync_dir(tf.path.parent)
        moved += 1
        ctx.say(f"  archived {tf.id}")
    ctx.store.invalidate()
    ctx.say(f"archived {moved} of {moved + refused} archivable task(s) "
            f"({len(queue)} in queue; {skipped} not archivable)")
    if refused and moved:
        return PARTIAL
    if refused:
        return REFUSED
    return OK


def cmd_unarchive(ctx, task):
    ctx.g.check_task_id(task)
    q, a = ctx.store.claimants(task)
    if q and a:
        raise CrossboxError(f"{task} is in both queue and archive; resolve the "
                            f"divergent copies by hand first")
    if q:
        ctx.say(f"{task} is already in the queue; no change")
        return OK
    if not a:
        raise CrossboxError(f"{task} not found in the archive")
    if len(a) > 1:
        raise CrossboxError(f"{task} is ambiguous in the archive: "
                            f"{', '.join(f.path.name for f in a)}")
    tf = a[0]
    if not tf.ok:
        raise CrossboxError(f"{tf.path.name} does not parse ({tf.error}); refusing")
    dest = ctx.cfg.queue / f"{task}.yaml"
    if dest.exists():
        raise CrossboxError(f"{ctx.cfg.rel(dest)} exists; refusing to overwrite")
    os.replace(tf.path, dest)
    model.fsync_dir(dest.parent)
    model.fsync_dir(tf.path.parent)
    ctx.store.invalidate()
    open_ = sum(1 for s in tf.steps if isinstance(s, dict) and s.get("status") not in model.TERMINAL)
    ctx.say(f"unarchived {task}: {open_} of {len(tf.steps)} step(s) open")
    return OK


# ---------------------------------------------------------------- renumber

def _ref_pattern(old):
    return re.compile(r"(?<![\w-])" + re.escape(old) + r"(?!\d)")


def _sub_all(value, pat, new):
    if isinstance(value, str):
        return pat.sub(new, value)
    if isinstance(value, list):
        return [_sub_all(v, pat, new) for v in value]
    if isinstance(value, dict):
        return {k: _sub_all(v, pat, new) for k, v in value.items()}
    return value


def cmd_renumber(ctx, task, target=None, next_=False, file=None, dry_run=False,
                 force=False):
    g = ctx.g
    g.check_task_id(task)
    q, a = ctx.store.claimants(task)
    claim = q + a
    if file:
        fp = Path(file) if os.path.isabs(file) else ctx.repo / file
        pick = [f for f in claim if f.path.resolve() == fp.resolve()]
        if not pick:
            raise CrossboxError(f"--file {file} does not claim {task}")
        src = pick[0]
    else:
        if not claim:
            raise CrossboxError(f"{task} not found")
        if len(claim) > 1:
            raise CrossboxError(f"{task} has {len(claim)} claimants "
                                f"({', '.join(ctx.cfg.rel(f.path) for f in claim)}); "
                                f"name one with --file")
        src = claim[0]
    if not src.ok:
        raise CrossboxError(f"{src.path.name} does not parse ({src.error})")
    dom = g.domain_of(task)
    if next_:
        if target:
            raise UsageError("give a target or --next, not both")
        new, warns = guard.mint_task_id(ctx, dom)
        for w in warns:
            ctx.warn(w)
    elif target and target.isdigit():
        new = g.make(dom, int(target))
    elif target:
        new = g.check_task_id(target)
    else:
        raise UsageError("renumber: give N, NEW-ID or --next")
    if new == task:
        ctx.say("renumber: same id; no change")
        return OK
    ndom, nnum = g.domain_of(new), g.number_of(new)
    occupied = [f for f in ctx.store.files()
                if f.id == new or g.name_number(f.path.name, ndom) == nnum]
    if occupied:
        raise CrossboxError(f"{new} is occupied by {', '.join(ctx.cfg.rel(f.path) for f in occupied)}; "
                            f"refusing to overwrite")
    vacates = len(claim) == 1
    pat = _ref_pattern(task)
    data = _writable(ctx, src)
    data = _sub_all(data, pat, new)
    data["id"] = new
    if ndom != dom and ("domain" in data or ndom):
        if ndom:
            data["domain"] = ndom
        else:
            data.pop("domain", None)
    name = src.path.name
    new_name = new + name[len(task):] if name.startswith(task) else f"{new}.yaml"
    dest = src.path.with_name(new_name)
    others = []
    if vacates:
        unread = [f for f in ctx.store.files()
                  if f is not src and not f.ok and (f.text is None or pat.search(f.text))]
        if unread:
            raise NotEvaluable(_unread_dependents(ctx, unread))
        for f in ctx.store.files():
            if f is src or not f.ok:
                continue
            nd = _sub_all(f.data, pat, new)
            if nd != f.data:
                others.append((f, nd))
    ctx.say(f"renumber {task} -> {new}: {ctx.cfg.rel(src.path)} -> {ctx.cfg.rel(dest)}; "
            f"references rewritten in {len(others)} other file(s)"
            + ("" if vacates else " (another file keeps the id, so external refs stay)"))
    # claims follow the task only when the old id leaves with it
    refs = ({f"{task}.{i}": f"{new}.{i}" for i in model.step_ids(src.data)}
            if vacates else {})
    carry = _claims.plan_carry(ctx, refs, force) if refs else []
    if dry_run:
        for f, _ in others:
            ctx.say(f"  would rewrite {ctx.cfg.rel(f.path)}")
        return OK
    rel_src = ctx.cfg.rel(src.path)
    if gitutil.is_repo(ctx.repo) and gitutil.is_tracked(ctx.repo, rel_src):
        cp = gitutil.git(ctx.repo, "mv", rel_src, ctx.cfg.rel(dest))
        if cp.returncode != 0:
            raise CrossboxError(f"git mv failed: {cp.stderr.strip()}")
    else:
        os.replace(src.path, dest)
        model.fsync_dir(dest.parent)
    model.write_task(dest, data, model.step_ids(data), original=src.text, warn=ctx.warn)
    for f, nd in others:
        model.write_task(f.path, nd, model.step_ids(nd), original=f.text, warn=ctx.warn)
    ctx.store.invalidate()
    _claims.apply_carry(ctx, carry, refs)
    return OK


# ---------------------------------------------------------------- checks

def cmd_check_collisions(ctx, against=None, as_json=False):
    ctx.require_queue("check-collisions")
    ref = against or ctx.cfg.upstream
    if not gitutil.is_repo(ctx.repo) or not ref or not gitutil.resolves(ctx.repo, ref):
        msg = f"reference {ref!r} does not resolve" if ref else "no upstream ref configured"
        if as_json:
            ctx.say(json.dumps({"not_evaluable": msg}))
        else:
            ctx.say(f"check-collisions: NOT EVALUABLE: {msg}")
        return NOT_EVALUABLE
    files = ctx.store.files("queue")
    modified, colliding, compared = [], [], 0
    for f in files:
        try:
            there = gitutil.show(ctx.repo, ref, ctx.cfg.rel(f.path))
        except gitutil.GitError as e:
            msg = f"cannot read {ref}: {e}"
            if as_json:
                ctx.say(json.dumps({"not_evaluable": msg}))
            else:
                ctx.say(f"check-collisions: NOT EVALUABLE: {msg}")
            return NOT_EVALUABLE
        if there is None:
            continue
        compared += 1
        if f.text == there:
            continue
        try:
            theirs = model.parse_text(there)
            t2 = theirs.get("title") if isinstance(theirs, dict) else None
        except model.ParseError:
            t2 = None
        t1 = f.data.get("title") if f.ok else None
        (modified if t1 is not None and t1 == t2 else colliding).append(
            {"file": f.path.name, "local_title": t1, "their_title": t2})
    if as_json:
        ctx.say(json.dumps({"against": ref, "compared": compared, "of_queue_files": len(files),
                            "modified": modified, "collisions": colliding}, indent=2))
    else:
        for m in modified:
            ctx.say(f"  modified locally: {m['file']} (same title)")
        for c in colliding:
            ctx.say(f"  COLLISION: {c['file']}: local {c['local_title']!r} vs "
                    f"{ref} {c['their_title']!r}")
        ctx.say(f"check-collisions: {len(colliding)} collision(s), {len(modified)} modified, "
                f"among {compared} of {len(files)} queue file(s) present on {ref}")
    return REFUSED if colliding else OK


def _ids_at_ref(ctx, ref):
    out = []
    for tier, d in (("queue", ctx.cfg.queue), ("archive", ctx.cfg.archive)):
        for name in gitutil.ls_names(ctx.repo, ref, [ctx.cfg.rel(d)]):
            base = name.rsplit("/", 1)[-1]
            if not ctx.g.file_re.fullmatch(base):
                continue
            text = gitutil.show(ctx.repo, ref, name) or ""
            try:
                data = model.parse_text(text)
                tid = data.get("id") if isinstance(data, dict) else None
            except model.ParseError:
                tid = None
            out.append((tier, tid or base[:-5]))
    return out


def _blocking(pairs):
    counts = {}
    for tier, tid in pairs:
        counts[(tier, tid)] = counts.get((tier, tid), 0) + 1
    return {tid for (tier, tid), n in counts.items() if n > 1}


def cmd_check_duplicate_ids(ctx, as_json=False, baseline=None):
    ctx.require_queue("check-duplicate-ids")
    files = ctx.store.files()
    # a file that does not parse may carry the duplicate id: the count is a
    # floor, and the verdict is NOT EVALUABLE, never clean
    unreadable = [f for f in files if not f.ok]
    pairs = [(f.tier, f.id) for f in files]
    blocking = _blocking(pairs)
    tiers = {}
    for tier, tid in pairs:
        tiers.setdefault(tid, set()).add(tier)
    shadowed = sorted(t for t, s in tiers.items() if len(s) == 2)
    demoted = set()
    if baseline:
        if not gitutil.is_repo(ctx.repo) or not gitutil.resolves(ctx.repo, baseline):
            msg = f"baseline {baseline!r} does not resolve; tolerating nothing"
            if as_json:
                ctx.say(json.dumps({"not_evaluable": msg, "blocking": sorted(blocking)}))
            else:
                ctx.say(f"check-duplicate-ids: NOT EVALUABLE: {msg}")
            return NOT_EVALUABLE
        try:
            demoted = blocking & _blocking(_ids_at_ref(ctx, baseline))
        except gitutil.GitError as e:
            msg = f"baseline {baseline!r} cannot be read ({e}); tolerating nothing"
            if as_json:
                ctx.say(json.dumps({"not_evaluable": msg, "blocking": sorted(blocking)}))
            else:
                ctx.say(f"check-duplicate-ids: NOT EVALUABLE: {msg}")
            return NOT_EVALUABLE
    new_blocking = sorted(blocking - demoted)
    detail = {tid: [ctx.cfg.rel(f.path) for f in files if f.id == tid] for tid in blocking}
    unread = [str(ctx.cfg.rel(f.path)) for f in unreadable]
    if as_json:
        ctx.say(json.dumps({"files": len(files), "blocking": new_blocking,
                            "already_blocking_at_baseline": sorted(demoted),
                            "shadowed": shadowed, "detail": detail,
                            "unparseable": unread, "counts_are_floor": bool(unread)},
                           indent=2))
    else:
        for tid in new_blocking:
            ctx.say(f"  DUPLICATE {tid}: {', '.join(detail[tid])}")
        for tid in sorted(demoted):
            ctx.say(f"  (pre-existing at {baseline}) {tid}: {', '.join(detail[tid])}")
        for tid in shadowed:
            ctx.say(f"  shadowed (info): {tid} is in queue and archive; the queue copy wins")
        for name in unread:
            ctx.say(f"  UNPARSEABLE {name}: its id could not be read reliably")
        ctx.say(f"check-duplicate-ids: {len(new_blocking)} blocking id(s) among {len(files)} file(s)"
                + (f"; NOT EVALUABLE: {len(unread)} file(s) do not parse" if unread else ""))
    if new_blocking:
        return REFUSED
    return NOT_EVALUABLE if unread else OK


def cmd_check_empty_tasks(ctx, as_json=False, strict=False):
    ctx.require_queue("check-empty-tasks")
    files = ctx.store.files("queue")
    empty = []
    unread = [str(ctx.cfg.rel(f.path)) for f in files if not f.ok]
    for f in files:
        if not f.ok or f.steps:
            continue
        payload = [k for k in f.data if k not in model.TASK_KEYS]
        if payload:
            continue
        title = f.data.get("title")
        sibs = [ctx.cfg.rel(o.path) for o in ctx.store.files()
                if o is not f and o.ok and o.data.get("title") == title]
        empty.append({"id": f.id, "title": title, "same_title": sibs})
    if as_json:
        ctx.say(json.dumps({"empty": empty, "of_queue_files": len(files),
                            "unparseable": unread, "counts_are_floor": bool(unread)},
                           indent=2))
    else:
        for e in empty:
            extra = f"  (same title: {', '.join(e['same_title'])})" if e["same_title"] else ""
            ctx.say(f"  empty: {e['id']}  {e['title']}{extra}")
        for name in unread:
            ctx.say(f"  UNPARSEABLE {name}: not checked")
        ctx.say(f"check-empty-tasks: {len(empty)} empty of {len(files)} queue file(s)"
                + (f"; NOT EVALUABLE: {len(unread)} file(s) do not parse" if unread else ""))
    if strict and empty:
        return REFUSED
    return NOT_EVALUABLE if unread else OK
