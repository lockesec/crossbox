"""The one parser and the one writer for task files.

crossbox owns its files. A write parses the whole file with ``yaml.safe_load``,
changes the data structure, and re-emits it canonically with ``yaml.safe_dump``:

* task keys in TASK_KEYS order, then unknown keys in their original order;
* step keys in STEP_KEYS order, then unknown keys in their original order;
* multi-line strings as literal block scalars (``|``), so line structure survives;
* ``needs`` / ``depends_on`` as flow lists produced by the emitter, so text like
  ``a: b`` is quoted and can never turn into a mapping;
* a line width large enough that nothing is ever folded.

Every write goes through ``write_task``: dump, re-parse, compare with the intended
data and the expected step-id sequence, write a temp file in the same directory,
``os.replace`` it into place, read it back and compare again. If any check fails
the original bytes are restored (or nothing is written) and the verb refuses.

Hand-written YAML comments are NOT preserved by a write. That is a deliberate
trade: a structural writer cannot corrupt the file the way a line editor can.
"""
from __future__ import annotations

import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable, Optional

import yaml

from . import _yaml

from .errors import CrossboxError, NotEvaluable

TASK_KEYS = ["id", "title", "priority", "domain", "phase", "created",
             "created_by", "source_task", "steps"]
STEP_KEYS = ["id", "title", "target", "needs", "status", "depends_on", "notes",
             "completed_by", "completed", "commits", "premise_verdict"]
LIST_KEYS = ("needs", "depends_on")

OPEN = ("pending", "blocked", "in_progress")
TERMINAL = ("done", "skipped")
STEP_ID_RE = re.compile(r"[a-z]+")


class ParseError(Exception):
    def __init__(self, problem: str, line: Optional[int] = None,
                 col: Optional[int] = None):
        self.problem = problem
        self.line = line
        self.col = col
        where = f"L{line}:C{col} " if line is not None else ""
        super().__init__(f"{where}{problem}")


# ---------------------------------------------------------------- emitter

class FlowList(list):
    """A list the dumper writes in flow style: ``[a, 'b: c']``."""


class _Dumper(yaml.SafeDumper):
    def ignore_aliases(self, data):  # never emit &anchors / *aliases
        return True


def _repr_str(dumper, value: str):
    if "\n" in value:
        return dumper.represent_scalar("tag:yaml.org,2002:str", value, style="|")
    return dumper.represent_scalar("tag:yaml.org,2002:str", value)


def _repr_flow(dumper, value):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", list(value),
                                     flow_style=True)


_Dumper.add_representer(str, _repr_str)
_Dumper.add_representer(FlowList, _repr_flow)

WIDTH = 1_000_000


def dump_yaml(data: Any) -> str:
    return yaml.dump(data, Dumper=_Dumper, sort_keys=False, allow_unicode=True,
                     width=WIDTH, default_flow_style=False)


# ---------------------------------------------------------------- parse

def parse_text(text: str) -> Any:
    try:
        return _yaml.load_strict(text)
    except yaml.MarkedYAMLError as e:
        mark = e.problem_mark or e.context_mark
        line = mark.line + 1 if mark else None
        col = mark.column + 1 if mark else None
        raise ParseError(str(e.problem or e.context or "YAML error"), line, col)
    except yaml.YAMLError as e:
        raise ParseError(str(e))


def load_file(path: Path) -> tuple:
    """(text, data). Raises ParseError or OSError."""
    text = path.read_text(encoding="utf-8")
    return text, parse_text(text)


def steps_of(data: Any) -> list:
    if not isinstance(data, dict):
        return []
    s = data.get("steps")
    return s if isinstance(s, list) else []


def step_ids(data: Any) -> list:
    return [s.get("id") for s in steps_of(data) if isinstance(s, dict)]


def structure_problems(data: Any) -> list:
    """Shape problems that make a task unsafe for any writer to touch."""
    probs = []
    if not isinstance(data, dict):
        return ["top level is not a mapping"]
    if has_surrogate(data):
        probs.append(NOT_UNICODE)
    if not isinstance(data.get("id"), str) or not data.get("id"):
        probs.append("no string `id:`")
    steps = data.get("steps")
    if steps is not None and not isinstance(steps, list):
        probs.append("`steps` is not a list")
        return probs
    seen = set()
    for i, s in enumerate(steps or []):
        if not isinstance(s, dict):
            probs.append(f"step #{i + 1} is not a mapping")
            continue
        sid = s.get("id")
        if not isinstance(sid, str) or not STEP_ID_RE.fullmatch(sid):
            probs.append(f"step #{i + 1} has id {sid!r} (want lower-case letters)")
            continue
        if sid in seen:
            probs.append(f"duplicate step id {sid!r}")
        seen.add(sid)
    return probs


def require_writable(data: Any, where: str) -> None:
    probs = structure_problems(data)
    if probs:
        raise CrossboxError(f"{where}: refusing to write a malformed task "
                            f"({'; '.join(probs)}); run `crossbox doctor check`")


# ---------------------------------------------------------------- canonical form

def _order(d: dict, keys: Iterable[str]) -> dict:
    out = {}
    for k in keys:
        if k in d:
            out[k] = d[k]
    for k, v in d.items():
        if k not in out:
            out[k] = v
    return out


def _plain(v: Any) -> Any:
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def canonical(data: dict) -> dict:
    task = _order(data, TASK_KEYS)
    if "steps" in task:
        if task["steps"] is None:
            task["steps"] = []
        new_steps = []
        for s in task["steps"]:
            if isinstance(s, dict):
                s = _order(s, STEP_KEYS)
                for k in LIST_KEYS:
                    v = s.get(k)
                    if isinstance(v, list) and all(
                            isinstance(x, (str, int, float, bool)) or x is None
                            for x in v):
                        s[k] = FlowList(v)
            new_steps.append(s)
        task["steps"] = new_steps
    return task


def render(data: dict) -> str:
    return dump_yaml(canonical(data))


def comment_lines(text: str) -> int:
    return sum(1 for ln in text.splitlines() if ln.lstrip().startswith("#"))


def fsync_dir(d: Path) -> None:
    """Flush a directory entry after a rename into it. os.replace is atomic,
    but the new entry is only durable once the directory itself is synced.
    Best effort: platforms that cannot open a directory (Windows) skip it."""
    try:
        fd = os.open(str(d), os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def atomic_write_text(path: Path, text: str) -> None:
    if _SURROGATE.search(text):
        raise CrossboxError(f"{path.name}: {NOT_UNICODE}; nothing written")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp",
                               dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        if path.exists():
            try:
                os.chmod(tmp, path.stat().st_mode & 0o777)
            except OSError:
                pass
        os.replace(tmp, path)
        fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def restore_if_ours(path: Path, ours: str, now_text: Optional[str],
                    original: Optional[str], parses=None) -> None:
    """A read-back mismatch: undo OUR write, never someone else's. When the
    file holds well-formed content other than the bytes we wrote, another
    writer replaced it after us; restoring would erase their success, so leave
    it and refuse (exit 2). Bytes that do not parse are damage from our own
    write and are restored."""
    parses = parses or parse_text
    foreign = False
    if now_text is not None and now_text != ours:
        try:
            parses(now_text)
            foreign = True
        except Exception:  # damaged bytes: ours to undo
            foreign = False
    if foreign:
        raise NotEvaluable(f"{path.name}: another writer replaced the file during "
                           f"this write; left as they wrote it (re-run)")
    if original is not None:
        atomic_write_text(path, original)
    else:
        try:
            path.unlink()
        except OSError:
            pass
    raise CrossboxError(f"{path.name}: post-write check failed; original restored")


def check_task(name: str, data: dict, expected_ids: Optional[list] = None):
    """The round-trip gate, without writing: returns (text, intended) or raises
    CrossboxError. Callers that must change several files run this on every
    one of them BEFORE the first write."""
    if has_surrogate(data):
        raise CrossboxError(f"{name}: {NOT_UNICODE}; nothing written")
    text = render(data)
    intended = _plain(canonical(data))
    try:
        back = parse_text(text)
    except ParseError as e:
        raise CrossboxError(f"{name}: round-trip gate: output does not parse "
                            f"({e}); nothing written")
    if back != intended:
        raise CrossboxError(f"{name}: round-trip gate: output does not re-read "
                            f"as the intended data; nothing written")
    if expected_ids is not None and step_ids(back) != list(expected_ids):
        raise CrossboxError(f"{name}: round-trip gate: step ids would be "
                            f"{step_ids(back)}, expected {list(expected_ids)}; "
                            f"nothing written")
    return text, intended


def write_task(path: Path, data: dict, expected_ids: Optional[list] = None,
               original: Optional[str] = None, warn=None) -> str:
    """Canonically write ``data`` to ``path`` behind the round-trip gate.

    ``expected_ids`` is the exact step-id sequence the result must have.
    Returns the written text. Raises CrossboxError, leaving the original bytes
    (or no file) in place, when any check fails."""
    text, intended = check_task(path.name, data, expected_ids)
    if original is None and path.exists():
        original = path.read_text(encoding="utf-8")
    atomic_write_text(path, text)
    try:
        now_text = path.read_text(encoding="utf-8")
    except OSError:
        now_text = None
    try:
        ok = now_text is not None and parse_text(now_text) == intended
    except ParseError:
        ok = False
    if not ok:
        restore_if_ours(path, text, now_text, original)
    if warn and original and comment_lines(original):
        warn(f"note: {path.name}: {comment_lines(original)} hand-written comment "
             f"line(s) were not preserved (crossbox rewrites files canonically)")
    return text


# ---------------------------------------------------------------- small helpers

_SURROGATE = re.compile("[\ud800-\udfff]")

NOT_UNICODE = "contains text that is not valid Unicode (a lone surrogate)"


def has_surrogate(v: Any) -> bool:
    """True when any string in ``v`` (a mapping's keys and values, a list's
    items) holds a lone surrogate: text no UTF-8 file can carry and no strict
    terminal can print. YAML writes it as an escape, so the file looks fine
    and the crash comes later, at every reader."""
    if isinstance(v, str):
        return bool(_SURROGATE.search(v))
    if isinstance(v, dict):
        return any(has_surrogate(k) or has_surrogate(x) for k, x in v.items())
    if isinstance(v, (list, tuple)):
        return any(has_surrogate(x) for x in v)
    return False


def clean(text, limit=None) -> str:
    """Printable, single-line text from a title or id anyone can edit: line
    breaks and tabs become one space, every other control, format or surrogate
    character (ESC, NUL, bidi overrides, zero-width marks) is dropped, runs of
    blanks collapse, and ``limit`` cuts to that many characters ending in an
    ellipsis."""
    s = text if isinstance(text, str) else str(text)
    s = "".join(ch for ch in s
                if ch.isspace() or not unicodedata.category(ch).startswith("C"))
    s = " ".join(s.split())
    if limit is not None and len(s) > limit:
        s = s[:limit - 1] + "…"
    return s


def one_line(s: str) -> str:
    return " ".join(str(s).split())


def status_class(status: Any) -> str:
    if status in OPEN:
        return "open"
    if status in TERMINAL:
        return "terminal"
    return "unknown"
