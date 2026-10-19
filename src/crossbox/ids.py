"""Task-id and step-id grammar.

Task id:  PREFIX-NNN (no domain) or PREFIX-DOMAIN-NNN, DOMAIN = [A-Z]+,
          NNN decimal, zero-padded to at least 3 digits when minted.
Step id:  bijective base-26 lower-case letters: a..z, aa..az, ba...
Step ref: TASKID.stepid  (one grammar for every verb)
"""
from __future__ import annotations

import re
from typing import Iterable, Optional

from .errors import UsageError


def bad_identity(name) -> Optional[str]:
    """Why `name` cannot be a machine or slot name, or None when it can.

    Machine and slot names become parts of note file names
    (``<from>-to-<to>.md``, an address being ``machine`` or ``machine+slot``),
    so a name must be one safe path component: not empty, not ``.``/``..``,
    no ``/`` or ``\\``, no control characters. ``+`` and ``-to-`` are refused
    too: they are the separators inside a note file name, and a name holding
    one makes the inbox it addresses ambiguous."""
    if not isinstance(name, str) or not name:
        return "empty"
    if name in (".", ".."):
        return f"{name!r} is a path step"
    for ch in name:
        if ch in "/\\":
            return f"contains a path separator ({ch!r})"
        if ord(ch) < 32 or ord(ch) == 127:
            return "contains a control character"
    if "+" in name:
        return "contains '+' (the machine+slot separator)"
    if "-to-" in name:
        return "contains '-to-' (the note file-name separator)"
    return None


def step_ord(sid) -> int:
    if not isinstance(sid, str) or not re.fullmatch(r"[a-z]+", sid):
        return 0
    n = 0
    for ch in sid:
        n = n * 26 + (ord(ch) - ord("a") + 1)
    return n


def step_chr(n: int) -> str:
    if n <= 0:
        raise ValueError(n)
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(ord("a") + r) + out
    return out


def next_step_id(existing: Iterable) -> str:
    return step_chr(max([step_ord(s) for s in existing] + [0]) + 1)


class Grammar:
    def __init__(self, prefix: str):
        self.prefix = prefix
        p = re.escape(prefix)
        # every id shape is matched with fullmatch: a `$` anchor also accepts a
        # trailing newline, so "TASK-001\n" would pass as a valid id
        self.task_re = re.compile(rf"{p}-(?:([A-Z]+)-)?(\d+)")
        self.file_re = re.compile(rf"{p}-.*\.yaml")
        self.ref_re = re.compile(rf"({p}-(?:[A-Z]+-)?\d+)\.([a-z]+)")

    def is_task_id(self, s) -> bool:
        return isinstance(s, str) and bool(self.task_re.fullmatch(s))

    def domain_of(self, tid: str) -> Optional[str]:
        m = self.task_re.fullmatch(tid)
        return m.group(1) if m else None

    def number_of(self, tid: str) -> Optional[int]:
        m = self.task_re.fullmatch(tid)
        return int(m.group(2)) if m else None

    def make(self, domain: Optional[str], n: int) -> str:
        return f"{self.prefix}-{domain}-{n:03d}" if domain else f"{self.prefix}-{n:03d}"

    def name_number(self, name: str, domain: Optional[str]) -> Optional[int]:
        """Number from a file name that starts with an id of this domain
        (suffixed editorial names count)."""
        stem = name.rsplit("/", 1)[-1]
        p = re.escape(self.prefix)
        pat = rf"^{p}-{re.escape(domain)}-(\d+)(?!\d)" if domain else rf"^{p}-(\d+)(?!\d)"
        m = re.match(pat, stem)
        return int(m.group(1)) if m else None

    def parse_ref(self, ref: str) -> tuple:
        m = self.ref_re.fullmatch(ref or "")
        if not m:
            raise UsageError(f"bad step ref {ref!r}: expected {self.prefix}-DOMAIN-NNN.x")
        return m.group(1), m.group(2)

    def is_ref(self, s) -> bool:
        return isinstance(s, str) and bool(self.ref_re.fullmatch(s))

    def check_task_id(self, tid: str) -> str:
        if not self.is_task_id(tid):
            raise UsageError(f"bad task id {tid!r}: expected {self.prefix}-DOMAIN-NNN "
                             f"or {self.prefix}-NNN")
        return tid
