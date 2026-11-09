"""The needs registry: the vocabulary of tokens a step may list under ``needs``.

Registry file (``.crossbox/needs-registry.yaml``, optional)::

    schema: 1
    tokens:
      docker:         {kind: capability, desc: "documentation only; never routes"}
      staging-up:     {kind: gate, probe: "curl -fsS http://localhost:8080/health"}
      release-signed: {kind: gate, clearable: operator}
      big-refactor:   {kind: estimate}
    aliases:
      containers: docker
    prefix_kinds:
      "compute-": estimate
      "upstream-fix:": gate

Kinds: ``capability`` and ``annotation`` and ``estimate`` are documentation;
only ``gate`` tokens affect readiness. Routing to machines is done by a step's
``target`` (see deps.py), never by ``needs``: one routing mechanism, not two.

Gate state: ``date-reached:YYYY-MM-DD`` is open iff UTC today >= date (closed
on a malformed date). A token with ``probe`` runs it through the shell with cwd
at the repository root and a 10 s timeout (the whole process group is killed
at the limit): exit 0 open, exit 1 closed,
anything else NOT EVALUABLE (shown as such, never as closed, and not cached).
A gate with no probe is manual; ``clearable`` defaults to ``external``;
``--operator`` treats ``clearable: operator`` gates as open.
"""
from __future__ import annotations

import datetime as _dt
import difflib
import os
import signal
import subprocess

import yaml

from . import _yaml

from .errors import NotEvaluable

OPEN, CLOSED, NE = "open", "closed", "not-evaluable"
BUILTIN_PREFIXES = {"date-reached:": "gate"}
KINDS = ("capability", "annotation", "estimate", "gate")
CLEARABLE = ("external", "operator")
TOP_KEYS = ("schema", "tokens", "aliases", "prefix_kinds")
SPEC_KEYS = ("kind", "probe", "clearable", "desc")


PROBE_TIMEOUT = 10.0


def _run_probe(probe: str, repo) -> int:
    """Run a registry probe through the shell; its exit code. On timeout the
    whole process group is killed, so a probe that forked (``sleep 60; true``,
    a pipeline) cannot hold the output pipes open past the limit.
    Raises subprocess.TimeoutExpired."""
    posix = os.name == "posix"
    proc = subprocess.Popen(probe, shell=True, cwd=str(repo), stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=posix)
    try:
        proc.communicate(timeout=PROBE_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            if posix:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except OSError:
            pass
        try:
            proc.communicate(timeout=2)   # a child that left the group may still hold a pipe
        except subprocess.TimeoutExpired:
            pass
        raise
    return proc.returncode


class _BadRegistry(Exception):
    pass


def _name(k, what) -> str:
    if not isinstance(k, str) or not k.strip() or k != k.strip() or any(c.isspace() for c in k):
        raise _BadRegistry(f"{what} {k!r} must be a non-empty string without spaces")
    return k.lower()


def _mapping(raw, key) -> dict:
    v = raw.get(key)
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise _BadRegistry(f"`{key}` must be a mapping, not {type(v).__name__}")
    return v


def _validate(raw) -> tuple:
    if not isinstance(raw, dict):
        raise _BadRegistry("the top level must be a mapping")
    extra = [k for k in raw if k not in TOP_KEYS]
    if extra:
        raise _BadRegistry(f"unknown top-level key(s) {extra} (allowed: {', '.join(TOP_KEYS)})")
    if "schema" in raw and raw["schema"] != 1:
        raise _BadRegistry(f"`schema` must be 1, not {raw['schema']!r}")
    toks = {}
    for k, v in _mapping(raw, "tokens").items():
        name = _name(k, "token")
        if name in toks:
            raise _BadRegistry(f"token {k!r} is defined twice (names are case-insensitive)")
        if not isinstance(v, dict):
            raise _BadRegistry(f"token {k!r} must be a mapping like {{kind: gate}}, "
                               f"not {v!r}")
        bad = [x for x in v if x not in SPEC_KEYS]
        if bad:
            raise _BadRegistry(f"token {k!r} has unknown field(s) {bad} "
                               f"(allowed: {', '.join(SPEC_KEYS)})")
        kind = v.get("kind")
        if kind not in KINDS:
            raise _BadRegistry(f"token {k!r} has kind {kind!r}; want one of "
                               f"{', '.join(KINDS)} (exact spelling)")
        if "probe" in v and (kind != "gate" or not isinstance(v["probe"], str)
                             or not v["probe"].strip()):
            raise _BadRegistry(f"token {k!r}: `probe` must be a non-empty string on a "
                               f"gate")
        if "clearable" in v and (kind != "gate" or v["clearable"] not in CLEARABLE):
            raise _BadRegistry(f"token {k!r}: `clearable` must be one of "
                               f"{', '.join(CLEARABLE)} on a gate")
        if "desc" in v and not isinstance(v["desc"], str):
            raise _BadRegistry(f"token {k!r}: `desc` must be a string")
        toks[name] = v
    aliases = {}
    for k, v in _mapping(raw, "aliases").items():
        name = _name(k, "alias")
        target = _name(v, f"alias {k!r} target")
        if name in toks:
            raise _BadRegistry(f"alias {k!r} shadows a token of the same name")
        if target not in toks:
            raise _BadRegistry(f"alias {k!r} points at {v!r}, which is not a token")
        aliases[name] = target
    pk = {}
    for k, v in _mapping(raw, "prefix_kinds").items():
        name = _name(k, "prefix")
        if v not in KINDS:
            raise _BadRegistry(f"prefix {k!r} has kind {v!r}; want one of {', '.join(KINDS)}")
        pk[name] = v
    return toks, aliases, pk


class Registry:
    def __init__(self, tokens=None, aliases=None, prefix_kinds=None, present=False):
        self.tokens = tokens or {}
        self.aliases = aliases or {}
        self.prefix_kinds = dict(BUILTIN_PREFIXES)
        self.prefix_kinds.update(prefix_kinds or {})
        self.present = present
        self._probe_cache = {}

    @classmethod
    def load(cls, path):
        """Read and validate the registry. Anything but the declared shape is
        NOT EVALUABLE, never a silently weaker registry: a misspelt kind, a
        scalar token, a list where a mapping belongs or an alias to nothing
        would otherwise erase a gate without a word."""
        if not path.exists():
            return cls()
        try:
            raw = _yaml.load_strict(path.read_text(encoding="utf-8")) or {}
        except (OSError, UnicodeDecodeError, yaml.YAMLError) as e:
            raise NotEvaluable(f"{path.name}: needs registry does not parse: {e}")
        try:
            toks, aliases, pk = _validate(raw)
        except _BadRegistry as e:
            raise NotEvaluable(f"{path.name}: needs registry is malformed: {e}")
        return cls(toks, aliases, pk, present=True)

    # ------------------------------------------------------------ resolution
    def resolve(self, token) -> list:
        """List of dicts {token, status, canonical, kind, suggestion}.
        status: ok | alias | prefix | unknown | split."""
        if not isinstance(token, str):
            return [{"token": repr(token), "status": "unknown", "canonical": None,
                     "kind": None, "suggestion": None}]
        t = token.strip()
        if not t:
            return [{"token": token, "status": "unknown", "canonical": None,
                     "kind": None, "suggestion": None}]
        if " " in t:
            parts = []
            for p in t.split():
                for r in self.resolve(p):
                    r = dict(r)
                    r["split_from"] = t
                    parts.append(r)
            return [{"token": t, "status": "split", "canonical": None, "kind": None,
                     "suggestion": None, "parts": parts}] + parts
        low = t.lower()
        if low in self.tokens:
            return [{"token": t, "status": "ok", "canonical": low,
                     "kind": self.tokens[low]["kind"], "suggestion": None}]
        if low in self.aliases:
            c = self.aliases[low]
            return [{"token": t, "status": "alias", "canonical": c,
                     "kind": self.tokens[c]["kind"], "suggestion": c}]
        for pfx, kind in sorted(self.prefix_kinds.items(), key=lambda kv: -len(kv[0])):
            if low.startswith(pfx):
                return [{"token": t, "status": "prefix", "canonical": low, "kind": kind,
                         "suggestion": None}]
        near = difflib.get_close_matches(low, list(self.tokens) + list(self.aliases),
                                         n=1, cutoff=0.75)
        return [{"token": t, "status": "unknown", "canonical": None, "kind": None,
                 "suggestion": near[0] if near else None}]

    def gate_tokens(self, needs) -> list:
        """Canonical gate tokens among a step's needs (malformed entries are
        skipped here; audit reports them)."""
        if not isinstance(needs, list):
            return []
        out = []
        for n in needs:
            try:
                for r in self.resolve(n):
                    if r["status"] in ("ok", "alias", "prefix") and r["kind"] == "gate":
                        out.append(r["canonical"])
            except Exception:  # one bad row never blanks the rest
                continue
        return out

    # ------------------------------------------------------------ gates
    def gate_state(self, token: str, repo, operator: bool = False,
                   today: _dt.date = None) -> tuple:
        """(state, cause)"""
        if token.startswith("date-reached:"):
            raw = token[len("date-reached:"):]
            try:
                d = _dt.date.fromisoformat(raw)
            except ValueError:
                return CLOSED, f"malformed date {raw!r}"
            today = today or _dt.datetime.now(_dt.timezone.utc).date()
            return (OPEN, f"{d} reached") if today >= d else (CLOSED, f"opens {d}")
        spec = self.tokens.get(token) or {}
        probe = spec.get("probe")
        if probe:
            if token in self._probe_cache:
                return self._probe_cache[token]
            try:
                rc = _run_probe(probe, repo)
            except subprocess.TimeoutExpired:
                return NE, f"probe timed out after {PROBE_TIMEOUT:g} s"
            except OSError as e:
                return NE, f"probe could not run: {e}"
            if rc == 0:
                res = (OPEN, "probe exit 0")
            elif rc == 1:
                res = (CLOSED, "probe exit 1")
            else:
                return NE, f"probe gave no answer (exit {rc})"
            self._probe_cache[token] = res
            return res
        clearable = spec.get("clearable", "external")
        if operator and clearable == "operator":
            return OPEN, "operator-clearable, --operator given"
        return CLOSED, f"manual gate (clearable: {clearable})"
