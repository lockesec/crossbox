"""Duplicate mapping keys are refused everywhere (release review, blocker 1).

PyYAML's safe loader keeps the LAST value of a repeated key. A task holding
two ``steps:`` blocks therefore parsed as the second one, and the next write
rewrote the file without the first. Every file crossbox reads now goes through
one strict loader: a duplicate key is NOT EVALUABLE (exit 2) and nothing is
written."""
import pytest
import yaml
from conftest import run, task_path

from crossbox import _yaml

DUP_STEPS = ("id: TASK-A-001\ntitle: t\nsteps:\n- id: a\n  title: first\n  status: pending\n"
             "steps:\n- id: b\n  title: second\n  status: pending\n")


def test_loader_refuses_a_duplicate_key_at_any_depth_and_names_the_line():
    with pytest.raises(_yaml.DuplicateKeyError) as e:
        _yaml.load_strict("a: 1\nb:\n  c: 1\n  c: 2\n")
    assert "'c'" in str(e.value) and e.value.problem_mark.line + 1 == 4


def test_loader_is_safe_load_for_a_clean_document():
    text = "a: 1\nb: [x, y]\nc: {d: 2}\n"
    assert _yaml.load_strict(text) == yaml.safe_load(text)


def test_loader_refuses_python_object_tags():
    with pytest.raises(yaml.YAMLError):
        _yaml.load_strict("!!python/object/apply:os.system ['true']\n")


@pytest.mark.parametrize("verb", [
    ("add", "TASK-A-001", "third"),
    ("done", "TASK-A-001.a"),
    ("edit", "TASK-A-001", "--phase", "x"),
    ("edit-step", "TASK-A-001.a", "--title", "x"),
    ("skip", "TASK-A-001.a", "--reason", "r"),
    ("reopen", "TASK-A-001.a", "--reason", "r"),
    ("remove-step", "TASK-A-001.a"),
])
def test_no_mutating_verb_rewrites_a_task_with_a_duplicate_key(repo, verb):
    # a duplicate key makes the task unparseable, so invariant 6 applies:
    # every writer refuses (exit 1), names the line, and writes nothing
    p = task_path(repo, "TASK-A-001")
    p.write_text(DUP_STEPS)
    r = run(repo, *verb)
    assert r.code == 1, r
    assert "L7:C1 duplicate key 'steps'" in r.err
    assert p.read_text() == DUP_STEPS


def test_show_reports_the_duplicate_instead_of_the_last_block(repo):
    task_path(repo, "TASK-A-001").write_text(DUP_STEPS)
    r = run(repo, "show", "TASK-A-001")
    assert r.code == 1 and "duplicate key 'steps'" in r.out and "second" not in r.out


def test_duplicate_key_in_claims_is_not_evaluable(repo):
    p = repo / ".crossbox" / "claims.yaml"
    text = "claims: []\nclaims: []\n"
    p.write_text(text)
    r = run(repo, "claims")
    assert r.code == 2 and "duplicate key 'claims'" in r.err
    r = run(repo, "claim", "x", "--step", "TASK-A-001.a")
    assert r.code == 2 and p.read_text() == text


def test_duplicate_key_in_config_is_not_evaluable(repo):
    cfg = repo / ".crossbox" / "config.yaml"
    cfg.write_text(cfg.read_text() + "prefix: OTHER\n")
    r = run(repo, "status")
    assert r.code == 2 and "duplicate key 'prefix'" in r.err


def test_duplicate_key_in_needs_registry_is_not_evaluable(repo):
    reg = repo / ".crossbox" / "needs-registry.yaml"
    reg.write_text("tokens:\n  docker: {}\ntokens:\n  gpu: {}\n")
    r = run(repo, "ready")
    assert r.code == 2 and "duplicate key 'tokens'" in r.err


def test_an_alias_bomb_is_refused_before_it_expands():
    bomb = "a: &a [x, x]\n" + "".join(
        f"{c}: &{c} [*{p}, *{p}]\n" for p, c in zip("abcdefghijklmnopqrstuvwxy", "bcdefghijklmnopqrstuvwxyz"))
    with pytest.raises(_yaml.BudgetError):
        _yaml.load_strict(bomb)


def test_a_few_aliases_still_load():
    assert _yaml.load_strict("a: &x [1]\nb: *x\n") == {"a": [1], "b": [1]}


def test_an_oversized_document_is_refused_unread(monkeypatch):
    monkeypatch.setattr(_yaml, "MAX_BYTES", 64)
    with pytest.raises(_yaml.BudgetError):
        _yaml.load_strict("a: " + "x" * 100 + "\n")
    assert _yaml.load_strict("a: 1\n") == {"a": 1}
