"""Reading the queue and archive, and resolving task ids to files.

Identity is the ``id:`` field, never the file name: archive files may carry an
editorial suffix (``TASK-API-004-superseded.yaml``). When a file does not parse,
its first column-0 ``id:`` line is used, and the file stem only when there is
none.

Resolution (one resolver for every verb):
  1. collect claimants in queue/ and archive/;
  2. two or more claimants in the SAME tier -> ambiguous: refuse, list them;
  3. otherwise the queue copy wins over the archive copy ("shadowed").
"""
from __future__ import annotations

import difflib
import re
from pathlib import Path
from typing import Optional

from . import model
from .errors import CrossboxError

_ID_LINE = re.compile(r"""^id:\s*['"]?([^'"\s#]+)""", re.M)


class TaskFile:
    def __init__(self, path: Path, tier: str, text: Optional[str] = None):
        self.path = path
        self.tier = tier
        self.text: Optional[str] = text
        self.data = None
        self.error: Optional[model.ParseError] = None
        try:
            if text is None:
                self.text = path.read_text(encoding="utf-8")
            self.data = model.parse_text(self.text)
        except model.ParseError as e:
            self.error = e
        except (OSError, UnicodeDecodeError) as e:
            self.error = model.ParseError(f"unreadable: {e}")
        tid = None
        if isinstance(self.data, dict) and isinstance(self.data.get("id"), str):
            tid = self.data["id"]
        elif self.text is not None and self.error is not None:
            m = _ID_LINE.search(self.text)
            tid = m.group(1) if m else None
        self.id_from_field = tid is not None
        self.id = tid or path.stem

    @property
    def ok(self) -> bool:
        return self.error is None and isinstance(self.data, dict)

    @property
    def steps(self) -> list:
        return model.steps_of(self.data)

    def step(self, sid: str) -> Optional[dict]:
        for s in self.steps:
            if isinstance(s, dict) and s.get("id") == sid:
                return s
        return None

    def __repr__(self):
        return f"TaskFile({self.tier}:{self.path.name})"


class Store:
    def __init__(self, cfg, grammar):
        self.cfg = cfg
        self.g = grammar
        self._files = None

    def invalidate(self):
        self._files = None

    def _scan(self, d: Path, tier: str) -> list:
        if not d.is_dir():
            return []
        out = []
        for p in sorted(d.iterdir()):
            if p.is_file() and self.g.file_re.fullmatch(p.name):
                out.append(TaskFile(p, tier))
        return out

    def files(self, tier: Optional[str] = None) -> list:
        if self._files is None:
            self._files = (self._scan(self.cfg.queue, "queue")
                           + self._scan(self.cfg.archive, "archive"))
        if tier is None:
            return list(self._files)
        return [f for f in self._files if f.tier == tier]

    def claimants(self, tid: str) -> tuple:
        q = [f for f in self.files("queue") if f.id == tid]
        a = [f for f in self.files("archive") if f.id == tid]
        return q, a

    def find(self, tid: str) -> Optional[TaskFile]:
        """Resolve without raising on a miss. Raises on ambiguity."""
        q, a = self.claimants(tid)
        for tier, group in (("queue", q), ("archive", a)):
            if len(group) > 1:
                names = ", ".join(str(self.cfg.rel(f.path)) for f in group)
                raise CrossboxError(f"{tid} is ambiguous: {len(group)} files in "
                                    f"{tier}/ claim it ({names}); refusing to pick one")
        if q:
            return q[0]
        if a:
            return a[0]
        return None

    def resolve(self, tid: str, queue_only: bool = False) -> TaskFile:
        tf = self.find(tid)
        if tf is None or (queue_only and tf.tier != "queue"):
            where = "the queue" if queue_only else "the queue or archive"
            hint = ""
            if tf is not None:
                hint = " (it is archived; `crossbox unarchive` it first)"
            else:
                close = difflib.get_close_matches(
                    tid, sorted({f.id for f in self.files()}), n=5, cutoff=0.6)
                if close:
                    hint = "; did you mean: " + ", ".join(close)
            raise CrossboxError(f"{tid} not found in {where}{hint}")
        return tf

    def is_shadowed(self, tf: TaskFile) -> bool:
        return tf.tier == "archive" and any(f.id == tf.id for f in self.files("queue"))
