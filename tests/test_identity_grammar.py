"""Machine and slot names are safe path components (release review, blocker 6).

A note is written to ``notes/<from>-to-<to>.md``, where each address is a
machine or ``machine+slot``. Before, a recipient slot on another machine, a
``CROSSBOX_MACHINE``/``CROSSBOX_SLOT`` value, or a configured machine name
reached that filename unchecked, so ``--to workstation/../../x`` wrote outside
the notes directory. Names now follow one grammar, and the final path is
checked to stay inside the notes directory."""
import pytest
from conftest import run

from crossbox import ids

BAD = ["", "a/b", "a\\b", "..", ".", "a\x00b", "a\nb", "a+b", "lap-to-top", "../x"]


@pytest.mark.parametrize("name", BAD)
def test_the_grammar_refuses_separators_traversal_and_control_characters(name):
    assert ids.bad_identity(name) is not None


@pytest.mark.parametrize("name", ["laptop", "wt1", "my.box", "WORK_2", "gpu-box"])
def test_the_grammar_accepts_ordinary_names(name):
    assert ids.bad_identity(name) is None


@pytest.mark.parametrize("to", ["workstation/../../escape", "workstation/a/b",
                                "workstation/x+y", "workstation/", "workstation/a\nb"])
def test_a_remote_recipient_slot_outside_the_grammar_is_refused(world, to):
    r = run(world.laptop, "note", "--to", to, "hello")
    assert r.code == 1, (to, r)
    assert "slot" in r.err
    assert list((world.laptop / ".crossbox" / "notes").glob("*.md")) == []
    assert not (world.laptop / "escape-to-x.md").exists()


def test_a_valid_remote_slot_still_works(world):
    r = run(world.laptop, "note", "--to", "workstation/wt3", "hello")
    assert r.code == 0, r
    assert (world.laptop / ".crossbox" / "notes" / "laptop-to-workstation+wt3.md").exists()


@pytest.mark.parametrize("var,value", [("CROSSBOX_MACHINE", "../evil"),
                                       ("CROSSBOX_MACHINE", "a/b"),
                                       ("CROSSBOX_SLOT", "x+y"),
                                       ("CROSSBOX_SLOT", "../../evil")])
def test_an_environment_identity_outside_the_grammar_is_not_evaluable(repo, monkeypatch,
                                                                      var, value):
    monkeypatch.setenv(var, value)
    r = run(repo, "status")
    assert r.code == 2 and var in r.err, r


def test_a_configured_machine_name_outside_the_grammar_is_not_evaluable(repo):
    (repo / ".crossbox" / "config.yaml").write_text(
        "machines:\n  laptop: {}\n  'lap/../top': {}\n")
    r = run(repo, "status")
    assert r.code == 2 and "machines" in r.err, r
