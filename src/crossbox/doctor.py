"""`crossbox doctor`: check, fix, selftest, install-hook, install-merge-driver.

crossbox itself only ever writes canonical YAML, so the damage doctor repairs
comes from hand edits and bad merges. Fixable classes:

  mixed-indent    step markers at differing indents (the file does not parse):
                  outlier step blocks are re-indented to the majority indent
                  (ties -> the smallest); aborts if a dedent would eat text.
  dangling-notes  a closed quoted ``notes:`` followed by deeper orphan lines:
                  becomes one ``notes: |`` block holding both.
  absorbed-step   the file parses, but a step-shaped marker (``- id: x`` with
                  1-2 letters AND a ``status:`` line) sits inside a preceding
                  step's notes: it is relocated after the last real step.
  unknown-status  a step status outside pending/blocked/in_progress/done/
                  skipped: set to ``pending`` with a provenance line in notes.

Every fix passes three gates: the result parses; the step ids equal the
expected list, in order; every top-level key is schema-shaped
(``^[a-z][a-z0-9_]*$``). Other problems are diagnosed, never fixed.
"""
from __future__ import annotations

import copy
import difflib
import os
import re
import shlex
import shutil
import stat
import sys
import tempfile
from pathlib import Path

import yaml

from . import deps as _deps
from . import _yaml
from . import gitutil, model
from .errors import NOT_EVALUABLE, OK, PARTIAL, REFUSED, CrossboxError, NotEvaluable
from .store import TaskFile

KEY0 = re.compile(r"^([A-Za-z_][\w-]*)\s*:")
MARKER = re.compile(r"""^(\s*)-\s+id:\s*(['"]?)([a-z]+)\2\s*(?:#.*)?$""")
BLOCK_START = re.compile(r"^(\s*)(-\s+)?([\w-]+):\s*[|>][+-]?\d*[+-]?\s*(?:#.*)?$")
QUOTED_START = re.compile(r"""^(\s*)(-\s+)?([\w-]+):\s*(["'])(.*)$""")
TOP_KEY_SHAPE = re.compile(r"[a-z][a-z0-9_]*")
HOOK_MARK = "# crossbox-pre-commit"


# ---------------------------------------------------------------- text scanner

def _close_index(s: str, q: str) -> int:
    """Index just past the closing quote in s (s starts AFTER the opening
    quote), or -1. A single-quoted scalar closes only on a quote that is not
    half of ''; a double-quoted one on a quote not escaped by a backslash."""
    i = 0
    while i < len(s):
        ch = s[i]
        if q == '"' and ch == "\\":
            i += 2
            continue
        if ch == q:
            if q == "'" and i + 1 < len(s) and s[i + 1] == "'":
                i += 2
                continue
            return i + 1
        i += 1
    return -1


def scan(text: str) -> dict:
    """Line-level structure for files that may not parse. Skips block-scalar
    content and multi-line quoted scalars (a ``- id:`` there is content)."""
    lines = text.splitlines()
    markers, region = [], [None, None]
    in_steps = False
    block_indent = None
    quote = None
    for i, line in enumerate(lines):
        if block_indent is not None:
            if not line.strip():
                continue
            ind = len(line) - len(line.lstrip(" "))
            if ind > block_indent:
                continue
            block_indent = None
        if quote is not None:
            if _close_index(line, quote) >= 0:
                quote = None
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m0 = KEY0.match(line)
        if m0:
            if m0.group(1) == "steps":
                in_steps = True
                region[0] = i
            elif in_steps and markers:
                region[1] = i
                in_steps = False
        if in_steps:
            mm = MARKER.match(line)
            if mm:
                markers.append((i, len(mm.group(1)), mm.group(3)))
        mb = BLOCK_START.match(line)
        if mb:
            block_indent = len(mb.group(1)) + (len(mb.group(2)) if mb.group(2) else 0)
            continue
        mq = QUOTED_START.match(line)
        if mq:
            if _close_index(mq.group(5), mq.group(4)) < 0:
                quote = mq.group(4)
    if region[0] is not None and region[1] is None:
        region[1] = len(lines)
    return {"lines": lines, "markers": markers, "region": region}


# ---------------------------------------------------------------- fixes

def fix_mixed_indent(text: str):
    sc = scan(text)
    marks = sc["markers"]
    indents = [m[1] for m in marks]
    if len(set(indents)) <= 1:
        return None
    counts = {}
    for ind in indents:
        counts[ind] = counts.get(ind, 0) + 1
    best = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
    lines = list(sc["lines"])
    end = sc["region"][1]
    for k, (li, ind, _sid) in enumerate(marks):
        if ind == best:
            continue
        stop = marks[k + 1][0] if k + 1 < len(marks) else end
        delta = best - ind
        for j in range(li, stop):
            ln = lines[j]
            if not ln.strip():
                continue
            if delta < 0:
                if ln[:-delta].strip():
                    return None  # a dedent would eat text
                lines[j] = ln[-delta:]
            else:
                lines[j] = " " * delta + ln
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def fix_dangling_notes(text: str):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(r"""^(\s*)(-\s+)?notes:\s*(["'].*)$""", line)
        if not m:
            continue
        q = m.group(3)[0]
        rest = m.group(3)[1:]
        end = _close_index(rest, q)
        if end < 0 or rest[end:].strip() and not rest[end:].strip().startswith("#"):
            continue
        key_col = len(m.group(1)) + (len(m.group(2)) if m.group(2) else 0)
        orphans = []
        j = i + 1
        while j < len(lines):
            ln = lines[j]
            if ln.strip() and len(ln) - len(ln.lstrip(" ")) <= key_col:
                break
            orphans.append(ln)
            j += 1
        while orphans and not orphans[-1].strip():
            orphans.pop()
        if not any(o.strip() for o in orphans):
            continue
        try:
            value = _yaml.load_strict(q + rest[:end])
        except yaml.YAMLError:
            continue
        pad = " " * (key_col + 2)
        base = min(len(o) - len(o.lstrip(" ")) for o in orphans if o.strip())
        body = [pad + v if v else "" for v in str(value).splitlines()]
        body += [pad + o[base:] if o.strip() else "" for o in orphans]
        head = line[:line.index("notes:")] + "notes: |"
        new = lines[:i] + [head] + body + lines[i + 1 + len(orphans):]
        return "\n".join(new) + ("\n" if text.endswith("\n") else "")
    return None


ABS_MARK = re.compile(r"^(\s*)-\s+id:\s*['\"]?([a-z]{1,2})['\"]?\s*$")
STATUS_LINE = re.compile(r"[ \t]*status:[ \t]*\S")


def find_absorbed(data) -> list:
    """[(step_index, key, line_index, absorbed_ids)] for step string fields that
    swallowed step-shaped blocks. Requires a step-shaped id AND a status line,
    and an id not already used by a real step."""
    found = []
    real = set(i for i in model.step_ids(data) if isinstance(i, str))
    for si, s in enumerate(model.steps_of(data)):
        if not isinstance(s, dict):
            continue
        for key, val in s.items():
            if not isinstance(val, str) or "\n" not in val:
                continue
            lines = val.split("\n")
            # status_after[i]: some line at or after i is a `status:` line. One
            # backward pass; a regex over each tail was quadratic.
            status_after = [False] * (len(lines) + 1)
            for i in range(len(lines) - 1, -1, -1):
                status_after[i] = status_after[i + 1] or bool(STATUS_LINE.match(lines[i]))
            skip_through = -1
            for li, ln in enumerate(lines):
                if li <= skip_through:
                    continue
                m = ABS_MARK.match(ln)
                if not m or m.group(2) in real or not status_after[li]:
                    continue
                chunk = "\n".join(lines[li:])
                try:
                    parsed = _yaml.load_strict(_dedent(chunk))
                except yaml.YAMLError as e:
                    # Every earlier tail also contains the line the parser
                    # stopped on, so skip candidates up to it: one parse per
                    # error, not one per step-shaped line.
                    mark = getattr(e, "problem_mark", None) or getattr(e, "context_mark", None)
                    if mark is not None:
                        skip_through = li + mark.line
                    continue
                if (isinstance(parsed, list) and parsed
                        and all(isinstance(p, dict) and isinstance(p.get("id"), str)
                                and "status" in p for p in parsed)):
                    found.append((si, key, li, [p["id"] for p in parsed], parsed))
                    break
    return found


def _dedent(chunk: str) -> str:
    lines = chunk.split("\n")
    base = min((len(ln) - len(ln.lstrip(" ")) for ln in lines if ln.strip()), default=0)
    return "\n".join(ln[base:] for ln in lines)


def fix_absorbed(data):
    hits = find_absorbed(data)
    if not hits:
        return None, []
    data = copy.deepcopy(data)
    moved = []
    steps = data["steps"]
    for si, key, li, ids, parsed in hits:
        lines = steps[si][key].split("\n")
        steps[si][key] = "\n".join(lines[:li]).rstrip("\n")
        moved.extend(parsed)
    steps.extend(moved)
    return data, [p["id"] for p in moved]


def unknown_statuses(data) -> list:
    out = []
    for s in model.steps_of(data):
        if isinstance(s, dict) and "status" in s and s.get("status") not in model.OPEN + model.TERMINAL:
            out.append((s.get("id"), s.get("status")))
    return out


def fix_statuses(data, today: str):
    data = copy.deepcopy(data)
    changed = []
    for s in model.steps_of(data):
        if isinstance(s, dict) and "status" in s and s.get("status") not in model.OPEN + model.TERMINAL:
            old = s["status"]
            s["status"] = "pending"
            note = (f"doctor {today}: status {old!r} is not a known status; set to "
                    f"pending (known: {', '.join(model.OPEN + model.TERMINAL)})")
            old_notes = s.get("notes")
            s["notes"] = (str(old_notes).rstrip("\n") + "\n\n" + note) if old_notes else note
            changed.append((s.get("id"), old))
    return data, changed


def _gates(text: str, expected_ids: list, original_keys=None):
    try:
        data = model.parse_text(text)
    except model.ParseError as e:
        return None, f"result does not parse ({e})"
    if not isinstance(data, dict):
        return None, "result is not a mapping"
    if model.step_ids(data) != expected_ids:
        return None, f"step ids would be {model.step_ids(data)}, expected {expected_ids}"
    bad = [k for k in data if not isinstance(k, str) or not TOP_KEY_SHAPE.fullmatch(k)]
    if bad:
        return None, f"top-level keys not schema-shaped: {bad}"
    if original_keys is not None and set(data) != set(original_keys):
        return None, "top-level key set would change"
    return data, None


def repair(text: str, today: str) -> dict:
    """Run every fix. Returns {text, classes, error, manual}."""
    classes = []
    cur = text
    try:
        data = model.parse_text(cur)
        parsed = True
    except model.ParseError:
        parsed = False
    if not parsed:
        expected = [m[2] for m in scan(text)["markers"]]
        for name, fn in (("mixed-indent", fix_mixed_indent),
                         ("dangling-notes", fix_dangling_notes)):
            for _ in range(20):
                nxt = fn(cur)
                if nxt is None or nxt == cur:
                    break
                cur = nxt
                if name not in classes:
                    classes.append(name)
                try:
                    model.parse_text(cur)
                    break
                except model.ParseError:
                    continue
        data, err = _gates(cur, expected)
        if err:
            return {"text": text, "classes": [], "error": err, "manual": True}
    if not isinstance(data, dict):
        return {"text": text, "classes": [], "error": "top level is not a mapping",
                "manual": True}
    orig_keys = list(data)
    new_data, moved = fix_absorbed(data)
    if new_data is not None:
        classes.append("absorbed-step")
        expected = model.step_ids(data) + moved
        data = new_data
    else:
        expected = model.step_ids(data)
    data2, changed = fix_statuses(data, today)
    if changed:
        classes.append("unknown-status")
        data = data2
    if not classes:
        return {"text": text, "classes": [], "error": None, "manual": False}
    if "absorbed-step" in classes or "unknown-status" in classes:
        cur = model.render(data)
    _, err = _gates(cur, expected, orig_keys)
    if err:
        return {"text": text, "classes": [], "error": err, "manual": True}
    return {"text": cur, "classes": classes, "error": None, "manual": False}


# ---------------------------------------------------------------- check

def diagnose(tf: TaskFile, store, today: str) -> list:
    """[(problem, fixable: bool)]"""
    out = []
    if tf.error is not None:
        loc = f"L{tf.error.line}:C{tf.error.col} " if tf.error.line else ""
        r = repair(tf.text or "", today) if tf.text is not None else {"classes": []}
        if r.get("classes"):
            out.append((f"{loc}{tf.error.problem} ({'+'.join(r['classes'])})", True))
        else:
            out.append((f"{loc}{tf.error.problem}", False))
        return out
    data = tf.data
    for p in model.structure_problems(data):
        out.append((p, False))
    if isinstance(data, dict) and isinstance(data.get("id"), str) and tf.tier == "queue" \
            and not tf.path.name.startswith(data["id"] + ".") and tf.path.stem != data["id"]:
        out.append((f"id field {data['id']!r} disagrees with file name {tf.path.name!r}", False))
    for si, key, _li, ids, _p in find_absorbed(data):
        out.append((f"step #{si + 1} {key} swallowed step marker(s) {ids} (absorbed-step)", True))
    for sid, st in unknown_statuses(data):
        out.append((f"step {sid}: unknown status {st!r} (unknown-status)", True))
    for s in model.steps_of(data):
        if not isinstance(s, dict):
            continue
        dl = s.get("depends_on")
        if dl is None:
            continue
        if not isinstance(dl, list):
            out.append((f"step {s.get('id')}: depends_on is not a list ({dl!r})", False))
            continue
        for d in dl:
            if not isinstance(d, str):
                out.append((f"step {s.get('id')}: non-string dependency {d!r}", False))
                continue
            st, detail = _deps.dep_state(d, tf, store)
            if st == _deps.DANGLING:
                out.append((f"step {s.get('id')}: dependency {d!r} names nothing ({detail})",
                            False))
    return out


def _targets(ctx, task=None, archive=False, staged=False) -> list:
    cfg = ctx.cfg
    if staged:
        if not gitutil.is_repo(ctx.repo):
            return []
        # --relative: names from the crossbox root, the same frame as `dirs`
        # (without it git names files from the top of the repository)
        cp = gitutil.git(ctx.repo, "diff", "--cached", "--name-only", "--relative",
                         "--diff-filter=ACMR")
        if cp.returncode != 0:
            # an unreadable index is not "nothing staged"
            raise NotEvaluable(f"doctor check --staged: NOT EVALUABLE: git diff --cached "
                               f"failed: {cp.stderr.strip()}")
        out = []
        dirs = [cfg.rel(cfg.queue)] + ([cfg.rel(cfg.archive)] if archive else [])
        for name in cp.stdout.splitlines():
            parent, _, base = name.rpartition("/")
            if parent in dirs and ctx.g.file_re.fullmatch(base):
                cpb = gitutil.git(ctx.repo, "show", f":./{name}")
                text = cpb.stdout if cpb.returncode == 0 else None
                tier = "queue" if parent == dirs[0] else "archive"
                if text is not None:
                    out.append(TaskFile(ctx.repo / name, tier, text=text))
        return out
    files = ctx.store.files("queue") + (ctx.store.files("archive") if archive else [])
    if task:
        files = [f for f in ctx.store.files() if f.id == task]
    return files


def cmd_check(ctx, task=None, archive=False, staged=False, quiet=False) -> int:
    ctx.require_queue("doctor check")
    files = _targets(ctx, task, archive, staged)
    if task and not files:
        ctx.say(f"doctor check: {task} not found")
        return NOT_EVALUABLE
    bad = 0
    total_findings = 0
    for tf in files:
        probs = diagnose(tf, ctx.store, ctx.date_str)
        if probs:
            bad += 1
            total_findings += len(probs)
            for p, fixable in probs:
                ctx.say(f"{ctx.cfg.rel(tf.path)}: {p} [{'fixable' if fixable else 'manual'}]")
    if not quiet or bad:
        ctx.say(f"doctor check: {bad} of {len(files)} file(s) with problems "
                f"({total_findings} finding(s))")
    return REFUSED if bad else OK


def cmd_fix(ctx, task=None, archive=False, write=False, show_diff=False) -> int:
    files = _targets(ctx, task, archive)
    if not files:
        ctx.say("doctor fix: no target files" + (f" ({task} not found)" if task else ""))
        return NOT_EVALUABLE
    fixed, manual, would = 0, 0, 0
    for tf in files:
        probs = diagnose(tf, ctx.store, ctx.date_str)
        if not probs:
            continue
        has_manual = any(not fx for _, fx in probs)
        r = repair(tf.text or "", ctx.date_str)
        name = ctx.cfg.rel(tf.path)
        if r["classes"]:
            if show_diff or not write:
                diff = difflib.unified_diff((tf.text or "").splitlines(True),
                                            r["text"].splitlines(True), name, name + " (fixed)")
                sys_out = "".join(diff)
                if show_diff:
                    ctx.say(sys_out.rstrip("\n"))
            if write:
                bak = tf.path.parent / ".doctor-bak"
                bak.mkdir(exist_ok=True)
                shutil.copy2(tf.path, bak / (tf.path.name + ".bak"))
                model.atomic_write_text(tf.path, r["text"])
                fixed += 1
                ctx.say(f"fixed {name}: {', '.join(r['classes'])} (backup in "
                        f"{ctx.cfg.rel(bak)})")
            else:
                would += 1
                ctx.say(f"would fix {name}: {', '.join(r['classes'])} (dry run; --write applies)")
        if has_manual or r["error"]:
            manual += 1
            why = r["error"] or "; ".join(p for p, fx in probs if not fx)
            ctx.say(f"manual: {name}: {why}")
    ctx.store.invalidate()
    touched = fixed if write else would
    ctx.say(f"doctor fix: {touched} fixable, {manual} need a human, of {len(files)} file(s)"
            + ("" if write else " (dry run)"))
    if manual and touched:
        return PARTIAL
    if manual:
        return REFUSED
    return OK


# ---------------------------------------------------------------- selftest

SELFTEST = {
    "mixed-indent": ("id: T-001\ntitle: selftest\nsteps:\n- id: a\n  title: one\n"
                     "  status: pending\n  notes: keep-me-1\n  - id: b\n    title: two\n"
                     "    status: pending\n", ["a", "b"], "keep-me-1"),
    "dangling-notes": ('id: T-002\ntitle: selftest\nsteps:\n- id: a\n  title: one\n'
                       '  status: pending\n  notes: "first line"\n    keep-me-2\n'
                       '- id: b\n  title: two\n  status: pending\n', ["a", "b"], "keep-me-2"),
    "absorbed-step": ("id: T-003\ntitle: selftest\nsteps:\n- id: a\n  title: one\n"
                      "  status: pending\n  notes: |\n    keep-me-3\n    - id: b\n"
                      "      title: two\n      status: pending\n", ["a", "b"], "keep-me-3"),
    "unknown-status": ("id: T-004\ntitle: selftest\nsteps:\n- id: a\n  title: one\n"
                       "  status: wip\n  notes: keep-me-4\n- id: b\n  title: two\n"
                       "  status: pending\n", ["a", "b"], "keep-me-4"),
}


def _bad_status(data) -> bool:
    return any(isinstance(s, dict) and model.status_class(s.get("status")) == "unknown"
               for s in model.steps_of(data))


def cmd_selftest(ctx) -> int:
    failures = 0
    with tempfile.TemporaryDirectory() as td:
        for name, (text, ids, sentinel) in SELFTEST.items():
            p = Path(td) / f"{name}.yaml"
            p.write_text(text)
            before = p.read_text()
            try:
                model.parse_text(before)
                parses = True
            except model.ParseError:
                parses = False
            # the fixture must reproduce its failure class before the fix counts
            if name == "absorbed-step":
                reproduces = parses and bool(find_absorbed(model.parse_text(before)))
            elif name == "unknown-status":
                reproduces = parses and _bad_status(model.parse_text(before))
            else:
                reproduces = not parses
            r = repair(before, "2000-01-01")
            ok = False
            why = r.get("error") or "no fix produced"
            if not reproduces:
                r = {"classes": [], "error": "fixture does not reproduce its failure"}
                why = r["error"]
            if r["classes"] and name in r["classes"]:
                try:
                    data = model.parse_text(r["text"])
                    ok = (model.step_ids(data) == ids and sentinel in r["text"]
                          and not _bad_status(data))
                    why = "ids or content lost" if not ok else ""
                except model.ParseError as e:
                    why = str(e)
            ctx.say(f"  {'ok  ' if ok else 'FAIL'} {name}" + (f": {why}" if not ok else ""))
            failures += 0 if ok else 1
    ctx.say(f"doctor selftest: {len(SELFTEST) - failures} of {len(SELFTEST)} passed")
    return OK if failures == 0 else REFUSED


# ---------------------------------------------------------------- installers

def crossbox_command() -> str:
    """The command the installed hook and merge driver run: an ABSOLUTE,
    shell-quoted path (a commit from a shell without the venv on PATH still
    finds it; a path with a space survives), forward slashes on Windows."""
    exe = shutil.which("crossbox")
    parts = [exe] if exe else [sys.executable, "-m", "crossbox"]
    if os.name == "nt":
        parts = [p.replace("\\", "/") for p in parts]
    return " ".join(shlex.quote(p) for p in parts)


def _hook_invocation(ctx, command=None) -> str:
    """The hook's crossbox command. Git runs hooks at the toplevel, so a root
    below it is baked in, relative to the toplevel and shell-quoted; a
    top-level root keeps the bare command."""
    cmd = command or crossbox_command()
    top = gitutil.toplevel(ctx.repo)
    rel = Path(os.path.relpath(ctx.repo.resolve(), top.resolve())).as_posix() if top else "."
    if rel == ".":
        return cmd
    return f"{cmd} --root {shlex.quote(rel)}"


def cmd_install_hook(ctx, command=None) -> int:
    if not gitutil.is_repo(ctx.repo):
        raise CrossboxError("not a git repository")
    cp = gitutil.git(ctx.repo, "rev-parse", "--git-path", "hooks")
    if cp.returncode != 0:
        raise CrossboxError(f"cannot locate the hooks directory: {cp.stderr.strip()}")
    hooks = Path(cp.stdout.strip())
    if not hooks.is_absolute():
        hooks = ctx.repo / hooks
    hooks.mkdir(parents=True, exist_ok=True)
    hook = hooks / "pre-commit"
    if hook.exists() and HOOK_MARK not in hook.read_text(encoding="utf-8", errors="replace"):
        raise CrossboxError(f"{hook} exists and was not written by crossbox; refusing to "
                            f"overwrite it. Add this line to it instead:\n"
                            f"  {_hook_invocation(ctx, command)} doctor check --staged --quiet")
    body = (f"#!/bin/sh\n{HOOK_MARK} (installed by `crossbox doctor install-hook`)\n"
            f"exec {_hook_invocation(ctx, command)} doctor check --staged --quiet\n")
    model.atomic_write_text(hook, body)
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    ctx.say(f"installed {hook}")
    return OK


DRIVER = "crossbox-claims"


def cmd_install_merge_driver(ctx, command=None) -> int:
    if not gitutil.is_repo(ctx.repo):
        raise CrossboxError("not a git repository")
    cmd = f"{command or crossbox_command()} merge-driver %O %A %B"
    for k, v in ((f"merge.{DRIVER}.name", "crossbox claims union"),
                 (f"merge.{DRIVER}.driver", cmd)):
        cp = gitutil.git(ctx.repo, "config", k, v)
        if cp.returncode != 0:
            raise CrossboxError(f"git config {k} failed: {cp.stderr.strip()}")
    rel = ctx.cfg.rel(ctx.cfg.claims)
    ga = ctx.repo / ".gitattributes"
    line = f"{rel} merge={DRIVER}"
    existing = ga.read_text(encoding="utf-8") if ga.exists() else ""
    if line not in existing.splitlines():
        sep = "" if not existing or existing.endswith("\n") else "\n"
        model.atomic_write_text(ga, existing + sep + line + "\n")
        ctx.say(f"added `{line}` to .gitattributes (commit it)")
    cp = gitutil.git(ctx.repo, "check-attr", "merge", "--", rel)
    if cp.returncode != 0 or not cp.stdout.strip().endswith(f": merge: {DRIVER}"):
        ctx.say(f"install-merge-driver: git check-attr does not map {rel} to {DRIVER}: "
                f"{cp.stdout.strip() or cp.stderr.strip()}")
        return REFUSED
    ctx.say(f"merge driver {DRIVER} installed and verified for {rel}")
    return OK
