"""The config file is validated at the boundary (release review, blockers 2 and 5).

Every configured data path must stay inside the repository and outside git's
own metadata, and every field must have the shape the code reads. A config
that fails either check is NOT EVALUABLE (exit 2) with the file and the field
named, never a traceback and never a write somewhere else."""
import os

import pytest
from conftest import run

# the repo fixture's machines, without `prefix`, so a test can set any key once
BASE = "machines:\n  laptop:\n    provides: [docker]\n"


def _cfg(repo, extra):
    (repo / ".crossbox" / "config.yaml").write_text(BASE + extra)


@pytest.mark.parametrize("key", ["dir", "queue", "archive", "notes", "claims", "registry"])
@pytest.mark.parametrize("value", ["/var/elsewhere", "../outside", ".crossbox/../../outside",
                                   ".git/crossbox", ".git"])
def test_a_data_path_outside_the_repo_or_inside_git_is_refused(repo, key, value):
    _cfg(repo, f"{key}: {value}\n")
    r = run(repo, "status")
    assert r.code == 2, r
    assert f"`{key}`" in r.err and "config.yaml" in r.err


def test_a_symlinked_data_dir_that_leaves_the_repo_is_refused(repo, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(outside, repo / "linked")
    _cfg(repo, "queue: linked/queue\n")
    r = run(repo, "new", "x", "--domain", "A")
    assert r.code == 2 and "`queue`" in r.err
    assert list(outside.iterdir()) == []


def test_a_path_inside_the_repo_is_accepted(repo):
    _cfg(repo, "notes: docs/crossbox-notes\n")
    assert run(repo, "status").code == 0


def test_an_escaping_path_writes_nothing_outside(repo, tmp_path):
    target = tmp_path / "escape"
    _cfg(repo, f"queue: ../{target.name}\n")
    r = run(repo, "new", "x", "--domain", "A")
    assert r.code == 2
    assert not target.exists()


@pytest.mark.parametrize("extra,field", [
    ("stale_days: soon\n", "stale_days"),
    ("stale_days: -1\n", "stale_days"),
    ("stale_days: true\n", "stale_days"),
    ("extra_refs: refs/heads/x\n", "extra_refs"),
    ("target_aliases: [a, b]\n", "target_aliases"),
    ("rate_gate: 30\n", "rate_gate"),
    ("rate_gate: {cap: many}\n", "rate_gate.cap"),
    ("rate_gate: {cap: 0}\n", "rate_gate.cap"),
    ("rate_gate: {window: -5}\n", "rate_gate.window"),
    ("priorities: high\n", "priorities"),
    ("priorities: []\n", "priorities"),
    ("prefix: [x]\n", "prefix"),
    ("remote: {a: b}\n", "remote"),
    ("upstream: [a]\n", "upstream"),
])
def test_a_wrong_shaped_field_is_not_evaluable_and_named(repo, extra, field):
    _cfg(repo, extra)
    r = run(repo, "status")
    assert r.code == 2, r
    assert f"`{field}`" in r.err
    assert "Traceback" not in r.err


@pytest.mark.parametrize("machines,field", [
    ("machines: [laptop]\n", "machines"),
    ("machines:\n  laptop: docker\n", "machines.laptop"),
    ("machines:\n  laptop:\n    provides: docker\n", "machines.laptop.provides"),
    ("machines:\n  laptop:\n    hostnames: {a: b}\n", "machines.laptop.hostnames"),
])
def test_a_wrong_shaped_machine_is_not_evaluable_and_named(repo, machines, field):
    (repo / ".crossbox" / "config.yaml").write_text("prefix: TASK\n" + machines)
    r = run(repo, "status")
    assert r.code == 2, r
    assert f"`{field}`" in r.err


@pytest.mark.parametrize("extra,named", [
    ("stale_day: 9\n", "stale_day"),
    ("machines:\n  laptop:\n    provide: [gpu]\n", "machines.laptop.provide"),
])
def test_an_unknown_config_key_is_reported_not_silently_defaulted(repo, extra, named):
    """A typo must not read as the default: `provide:` for `provides:` would
    otherwise drop a capability and route every gpu step elsewhere, silently."""
    cfg = repo / ".crossbox" / "config.yaml"
    cfg.write_text(extra if extra.startswith("machines") else BASE + extra)
    r = run(repo, "status")
    assert r.code == 0, r
    assert "unknown key" in r.err and f"`{named}`" in r.err


def test_known_keys_raise_no_unknown_key_warning(repo):
    _cfg(repo, "stale_days: 2\nrate_gate: {cap: 5, window: 60}\nextra_refs: []\n")
    r = run(repo, "status")
    assert r.code == 0 and "unknown key" not in r.err
