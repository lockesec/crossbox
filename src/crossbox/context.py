"""The per-invocation context every verb receives."""
from __future__ import annotations

import datetime as _dt
import os
import re
import sys
from pathlib import Path
from typing import Optional, TextIO

from . import config as _config
from .errors import NotEvaluable
from .ids import Grammar, bad_identity
from .store import Store


_SURROGATE = re.compile("[\ud800-\udfff]")
# C0 and C1 controls except newline and tab: ESC, CSI, BEL, CR, backspace...
_TERMINAL_CONTROL = re.compile("[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _printable(msg):
    """Text safe to send to a terminal. A lone surrogate (from a hand-edited
    file) cannot be encoded by a strict stdout: show U+FFFD instead of dying
    with a traceback. A control character (an ESC sequence in a claim subject
    or a title from a branch you did not write) is dropped, so no text read
    from the queue can drive the terminal; newline and tab stay."""
    if not isinstance(msg, str):
        return msg
    return _TERMINAL_CONTROL.sub("", _SURROGATE.sub("\ufffd", msg))


class Ctx:
    def __init__(self, root: Optional[str] = None, out: Optional[TextIO] = None,
                 err: Optional[TextIO] = None, machine: Optional[str] = None,
                 slot: Optional[str] = None, today: Optional[_dt.date] = None,
                 now: Optional[_dt.datetime] = None):
        self.repo: Path = _config.find_repo(root)
        self.cfg = _config.load_config(self.repo)
        # an explicit value (even an empty one) is the caller's, never "unset"
        self.machine = machine if machine is not None else _config.detect_machine(self.cfg)
        self.slot = slot if slot is not None else _config.detect_slot(self.repo)
        for what, value, env, given in (("machine", self.machine, "CROSSBOX_MACHINE", machine),
                                        ("slot", self.slot, "CROSSBOX_SLOT", slot)):
            why = bad_identity(value)
            if why:
                if given is not None:
                    flag = "--machine" if what == "machine" else f"the {what} argument"
                    raise NotEvaluable(f"{flag} {value!r} is not a valid {what} name ({why})")
                src = f"${env}" if os.environ.get(env) else f"the detected {what}"
                raise NotEvaluable(f"{src} = {value!r} is not a valid {what} name ({why}); "
                                   f"set {env} to override")
        self.out = out if out is not None else sys.stdout
        self.err = err if err is not None else sys.stderr
        if self.cfg.unknown_keys:
            self.warn("crossbox: config: unknown key(s) ignored: "
                      + ", ".join(f"`{k}`" for k in self.cfg.unknown_keys)
                      + " (see docs/configuration.md)")
        self.now = now or _dt.datetime.now()
        self.today = today or self.now.date()
        self.g = Grammar(self.cfg.prefix)
        self.store = Store(self.cfg, self.g)

    def require_queue(self, verb: str) -> None:
        """Every reader calls this first: a missing queue is not an empty,
        clean one (exit 2, never 0)."""
        if not self.cfg.queue.is_dir():
            raise NotEvaluable(
                f"{verb}: NOT EVALUABLE: {self.cfg.rel(self.cfg.queue)} does not exist "
                f"under {self.repo} (run `crossbox init`, or check --root, "
                f"CROSSBOX_ROOT and `queue:` in the config); nothing was checked")

    # output helpers ------------------------------------------------------
    def say(self, msg: str = "") -> None:
        print(_printable(msg), file=self.out)

    def warn(self, msg: str) -> None:
        print(_printable(msg), file=self.err)

    @property
    def date_str(self) -> str:
        return self.today.isoformat()

    @property
    def machine_caps(self) -> set:
        return _config.machine_record(self.cfg, self.machine).capabilities

    @property
    def origin(self) -> str:
        """Human-readable origin: machine, or machine/slot."""
        return self.machine if self.slot == "main" else f"{self.machine}/{self.slot}"
