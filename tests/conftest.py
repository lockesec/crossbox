"""Tests run against the source tree (src/ layout) without an install; CI also
runs them against the installed package.

Fixtures build real, throwaway git repositories under tmp_path: a bare
"origin", a primary clone on the machine "laptop", linked worktrees as slots,
and a second clone standing in for the machine "workstation"."""
import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from crossbox.cli import main  # noqa: E402

CONFIG = """\
prefix: TASK
machines:
  laptop:
    provides: [docker]
  workstation:
    provides: [gpu, docker]
"""


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    gcfg = tmp_path / "gitconfig"
    gcfg.write_text("[init]\n\tdefaultBranch = main\n[user]\n\tname = Test\n"
                    "\temail = test@users.noreply.github.com\n")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(gcfg))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("CROSSBOX_RATE_STATE", str(tmp_path / "rate" / "state.json"))
    monkeypatch.setenv("CROSSBOX_RATE_DISABLE", "1")
    monkeypatch.setenv("CROSSBOX_MACHINE", "laptop")
    for k in ("CROSSBOX_SLOT", "CROSSBOX_ROOT", "CROSSBOX_SKIP_ORIGIN_GUARD",
              "CROSSBOX_RATE_CAP", "CROSSBOX_RATE_WINDOW"):
        monkeypatch.delenv(k, raising=False)


def git(cwd, *args, check=True):
    cp = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True)
    if check and cp.returncode != 0:
        raise AssertionError(f"git {args} failed: {cp.stderr}")
    return cp


class Result:
    def __init__(self, code, out, err):
        self.code, self.out, self.err = code, out, err

    def __repr__(self):
        return f"<Result {self.code}\nOUT:\n{self.out}\nERR:\n{self.err}>"


def run(root, *args, machine=None, **kw):
    out, err = io.StringIO(), io.StringIO()
    old = os.environ.get("CROSSBOX_MACHINE")
    if machine:
        os.environ["CROSSBOX_MACHINE"] = machine
    try:
        code = main(["--root", str(root), *map(str, args)], out=out, err=err, **kw)
    finally:
        if machine:
            if old is None:
                os.environ.pop("CROSSBOX_MACHINE", None)
            else:
                os.environ["CROSSBOX_MACHINE"] = old
    return Result(code, out.getvalue(), err.getvalue())


def init_repo(path, config=CONFIG, commit=True):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    r = run(path, "init")
    assert r.code == 0, r
    (path / ".crossbox" / "config.yaml").write_text(config)
    if commit:
        git(path, "add", "-A")
        git(path, "commit", "-q", "-m", "init")
    return path


@pytest.fixture
def repo(tmp_path):
    """A lone git repository (no remote) with crossbox initialised."""
    return init_repo(tmp_path / "repo")


@pytest.fixture
def plain(tmp_path):
    """crossbox in a directory that is not a git repository."""
    p = tmp_path / "plain"
    p.mkdir()
    r = run(p, "init")
    assert r.code == 0, r
    (p / ".crossbox" / "config.yaml").write_text(CONFIG)
    return p


class World:
    """origin (bare) + laptop clone (primary) + worktree slot wt1 + workstation clone."""

    def __init__(self, tmp):
        self.origin = tmp / "origin.git"
        git(tmp, "init", "-q", "--bare", "-b", "main", str(self.origin))
        seed = init_repo(tmp / "seed")
        git(seed, "remote", "add", "origin", str(self.origin))
        git(seed, "push", "-q", "origin", "main")
        self.laptop = tmp / "laptop"
        git(tmp, "clone", "-q", str(self.origin), str(self.laptop))
        self.wt1 = tmp / "wt1"
        git(self.laptop, "worktree", "add", "-q", "-b", "wt1", str(self.wt1))
        self.workstation = tmp / "workstation"
        git(tmp, "clone", "-q", str(self.origin), str(self.workstation))

    def commit(self, where, msg="wip"):
        git(where, "add", "-A")
        git(where, "commit", "-q", "-m", msg)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def task_path(root, tid):
    return Path(root) / ".crossbox" / "queue" / f"{tid}.yaml"
