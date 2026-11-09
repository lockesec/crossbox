"""Repository discovery, `.crossbox/config.yaml`, machine and slot identity.

Config file (every key optional)::

    prefix: TASK              # task ids are PREFIX-NNN or PREFIX-DOMAIN-NNN
    dir: .crossbox            # everything below is relative to the repo root
    queue: .crossbox/queue
    archive: .crossbox/archive
    notes: .crossbox/notes
    claims: .crossbox/claims.yaml
    registry: .crossbox/needs-registry.yaml
    remote: origin
    upstream: origin/main     # default: origin/<default branch>
    extra_refs: []            # for-each-ref patterns also read for sibling state
    priorities: [critical, high, normal, low]
    stale_days: 3
    target_aliases: {}        # legacy target spellings, read-only
    rate_gate: {cap: 30, window: 600}
    machines:
      laptop:
        hostnames: [my-laptop]
        provides: [docker]
      workstation:
        provides: [gpu, docker]
"""
from __future__ import annotations

import os
import hashlib
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

from . import _yaml, gitutil
from . import ids as _ids
from .errors import NotEvaluable

CONFIG_REL = Path(".crossbox") / "config.yaml"

DEFAULT_PRIORITIES = ["critical", "high", "normal", "low"]
PRIORITY_RANK = {"critical": 0, "high": 1, "normal": 2, "medium": 2, "low": 3}


@dataclass
class Machine:
    name: str
    provides: set = field(default_factory=set)
    hostnames: list = field(default_factory=list)

    @property
    def capabilities(self) -> set:
        # A machine always provides its own name, so `target: <machine>` works.
        return set(self.provides) | {self.name}


@dataclass
class Config:
    repo: Path
    prefix: str = "TASK"
    queue: Path = None
    archive: Path = None
    notes: Path = None
    claims: Path = None
    registry: Path = None
    remote: str = "origin"
    remote_explicit: bool = False
    upstream_setting: Optional[str] = None
    extra_refs: list = field(default_factory=list)
    priorities: list = field(default_factory=lambda: list(DEFAULT_PRIORITIES))
    stale_days: int = 3
    target_aliases: dict = field(default_factory=dict)
    rate_cap: int = 30
    rate_window: float = 600.0
    machines: dict = field(default_factory=dict)
    exists: bool = False
    unknown_keys: list = field(default_factory=list)
    _upstream_cache: object = field(default=None, repr=False)

    def rel(self, p: Path) -> str:
        """Repository-relative POSIX path, as git wants it."""
        return Path(os.path.relpath(p, self.repo)).as_posix()

    @property
    def upstream(self) -> Optional[str]:
        if self.upstream_setting:
            return self.upstream_setting
        if self._upstream_cache is None:
            up = None
            if gitutil.is_repo(self.repo):
                up = gitutil.default_upstream(self.repo, self.remote)
            self._upstream_cache = up or ""
        return self._upstream_cache or None

    def target_vocabulary(self) -> set:
        vocab = {"any"}
        for m in self.machines.values():
            vocab |= m.capabilities
        return vocab


def find_repo(root: Optional[str] = None) -> Path:
    for given, src in ((root, "--root"), (os.environ.get("CROSSBOX_ROOT"), "CROSSBOX_ROOT")):
        if given:
            p = Path(given).resolve()
            if not p.is_dir():
                # a mistyped root must never read as an empty, clean queue
                raise NotEvaluable(f"{src} {given!r} is not a directory")
            return p
    cwd = Path.cwd()
    top = gitutil.toplevel(cwd)
    return (top or cwd).resolve()


class _Bad(Exception):
    """A config field with the wrong shape; load_config names the file."""


def _str(raw, key, default, allow_none=False):
    v = raw.get(key, default)
    if v is None and allow_none:
        return None
    if not isinstance(v, str) or not v.strip():
        raise _Bad(f"`{key}` must be a non-empty string")
    return v


def _int(v, key, minimum):
    if isinstance(v, bool) or not isinstance(v, int) or v < minimum:
        raise _Bad(f"`{key}` must be an integer >= {minimum}")
    return v


def _num(v, key):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
        raise _Bad(f"`{key}` must be a number > 0")
    return float(v)


def _str_list(v, key, allow_empty=True):
    if v is None:
        v = []
    if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
        raise _Bad(f"`{key}` must be a list of strings")
    if not v and not allow_empty:
        raise _Bad(f"`{key}` must not be empty")
    return v


def _contained(repo: Path, key: str, value) -> Path:
    """A data path inside the repository and outside git's metadata.

    Resolved (symlinks followed) before the check, so neither an absolute
    path, a `..` walk, nor a symlinked directory can point crossbox's reads
    and writes somewhere else. The repository's own `.git` (a directory, or a
    file in a linked worktree) is refused at any depth."""
    if not isinstance(value, str) or not value.strip():
        raise _Bad(f"`{key}` must be a non-empty relative path")
    if Path(value).is_absolute():
        raise _Bad(f"`{key}` must be relative to the repository, not {value!r}")
    root = repo.resolve()
    target = (root / value).resolve()
    if target != root and root not in target.parents:
        raise _Bad(f"`{key}` ({value!r}) resolves outside the repository")
    inside = target.relative_to(root).parts
    if inside and inside[0] == ".git":
        raise _Bad(f"`{key}` ({value!r}) points into git's metadata")
    return target


def load_config(repo: Path) -> Config:
    cfg = Config(repo=repo)
    path = repo / CONFIG_REL
    raw = {}
    if path.exists():
        cfg.exists = True
        try:
            raw = _yaml.load_strict(path.read_text(encoding="utf-8")) or {}
        except (yaml.YAMLError, OSError) as e:
            raise NotEvaluable(f"{path}: config does not parse: {e}")
        if not isinstance(raw, dict):
            raise NotEvaluable(f"{path}: config must be a mapping")
    try:
        _fill(cfg, repo, raw)
    except _Bad as e:
        raise NotEvaluable(f"{path}: {e}")
    return cfg


KNOWN_KEYS = {"dir", "queue", "archive", "notes", "claims", "registry", "prefix",
              "remote", "upstream", "extra_refs", "priorities", "stale_days",
              "target_aliases", "rate_gate", "machines"}
MACHINE_KEYS = {"provides", "hostnames"}
RATE_GATE_KEYS = {"cap", "window"}


def _fill(cfg: Config, repo: Path, raw: dict) -> None:
    # Unknown keys are kept for a warning, not refused (a newer config must stay
    # readable), but never silently: a typo would otherwise read as the default.
    cfg.unknown_keys = [str(k) for k in raw if k not in KNOWN_KEYS]
    base_rel = raw.get("dir", ".crossbox")
    _contained(repo, "dir", base_rel)

    def p(key, default_name):
        v = raw.get(key)
        if v is None:
            return _contained(repo, key, str(Path(base_rel) / default_name))
        return _contained(repo, key, v)

    cfg.prefix = _str(raw, "prefix", "TASK")
    cfg.queue = p("queue", "queue")
    cfg.archive = p("archive", "archive")
    cfg.notes = p("notes", "notes")
    cfg.claims = p("claims", "claims.yaml")
    cfg.registry = p("registry", "needs-registry.yaml")
    cfg.remote = _str(raw, "remote", "origin")
    cfg.remote_explicit = "remote" in raw
    cfg.upstream_setting = _str(raw, "upstream", None, allow_none=True)
    cfg.extra_refs = _str_list(raw.get("extra_refs"), "extra_refs")
    if "priorities" in raw:
        cfg.priorities = _str_list(raw["priorities"], "priorities", allow_empty=False)
    cfg.stale_days = _int(raw.get("stale_days", 3), "stale_days", 0)
    ta = raw.get("target_aliases") or {}
    if not isinstance(ta, dict) or not all(isinstance(k, str) and isinstance(v, str)
                                           for k, v in ta.items()):
        raise _Bad("`target_aliases` must be a mapping of strings to strings")
    cfg.target_aliases = {k.lower(): v.lower() for k, v in ta.items()}
    rg = raw.get("rate_gate")
    if rg is None:
        rg = {}
    if not isinstance(rg, dict):
        raise _Bad("`rate_gate` must be a mapping with `cap` and `window`")
    cfg.unknown_keys += [f"rate_gate.{k}" for k in rg if k not in RATE_GATE_KEYS]
    cfg.rate_cap = _int(rg.get("cap", 30), "rate_gate.cap", 1)
    cfg.rate_window = _num(rg.get("window", 600), "rate_gate.window")
    machines = raw.get("machines") or {}
    if not isinstance(machines, dict):
        raise _Bad("`machines` must be a mapping")
    for name, spec in machines.items():
        key = f"machines.{name}"
        why = _ids.bad_identity(name if isinstance(name, str) else "")
        if why:
            raise _Bad(f"`machines` has an invalid machine name {name!r} ({why})")
        if spec is None:
            spec = {}
        if not isinstance(spec, dict):
            raise _Bad(f"`{key}` must be a mapping (provides, hostnames)")
        cfg.unknown_keys += [f"{key}.{k}" for k in spec if k not in MACHINE_KEYS]
        cfg.machines[str(name)] = Machine(
            name=str(name),
            provides={c.lower() for c in _str_list(spec.get("provides"), f"{key}.provides")},
            hostnames=[h.lower() for h in _str_list(spec.get("hostnames"), f"{key}.hostnames")],
        )


def detect_machine(cfg: Config) -> str:
    env = os.environ.get("CROSSBOX_MACHINE")
    if env:
        return env.strip()
    host = socket.gethostname().lower()
    short = host.split(".")[0]
    for m in cfg.machines.values():
        if host in m.hostnames or short in m.hostnames:
            return m.name
    return unnamed_machine(short)


UNNAMED_PREFIX = "host-"


def unnamed_machine(hostname: str) -> str:
    """The identity of a machine no config names: stable and distinct per host, but not
    the host name itself. Claims, notes and `completed_by` are committed and pushed, so a
    raw host name ends up in a public history (found dogfooding this repository: the
    maintainer's host name in `.crossbox/queue/`). Name the machine in the config's
    `machines:` (or set CROSSBOX_MACHINE) to choose the word that is written."""
    digest = hashlib.sha256((hostname or "localhost").encode("utf-8")).hexdigest()
    return UNNAMED_PREFIX + digest[:6]


def machine_is_unnamed(cfg: Config, name: str) -> bool:
    """True when `name` is the anonymous fallback, not a configured or chosen name."""
    return (name.startswith(UNNAMED_PREFIX) and name not in cfg.machines
            and not os.environ.get("CROSSBOX_MACHINE"))


def detect_slot(repo: Path) -> str:
    """The canonical slot name: "main" for the primary checkout (and for a
    directory that is not a git repository), otherwise the worktree's
    administrative name. This is the ONE form stored and compared everywhere."""
    env = os.environ.get("CROSSBOX_SLOT")
    if env:
        return env.strip()
    try:
        dirs = gitutil.git_dirs(repo)
    except gitutil.GitError:
        return "main"
    if dirs is None:
        return "main"
    git_dir, common = dirs
    if git_dir == common:
        return "main"
    if git_dir.parent.name == "worktrees":
        return git_dir.name
    return git_dir.name


def machine_record(cfg: Config, name: str) -> Machine:
    return cfg.machines.get(name) or Machine(name=name)
