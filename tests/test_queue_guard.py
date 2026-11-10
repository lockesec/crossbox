"""The PreToolUse queue guard denies what the README says it denies, and never
blocks reads or writes outside the queue directory."""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / \
    "queue-guard.py"
Q = ".crossbox/queue/TASK-001.yaml"


@pytest.fixture(autouse=True)
def _project(tmp_path, monkeypatch):
    """The guard acts only in a project that holds the guarded directory."""
    for d in (".crossbox", "tasks/cb"):
        (tmp_path / "proj" / d).mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))


def _decide(event, *args, env=None):
    import os
    e = dict(os.environ)
    e.pop("CROSSBOX_GUARD_DIR", None)
    e.update(env or {})
    cp = subprocess.run([sys.executable, str(GUARD), *args], input=json.dumps(event),
                        capture_output=True, text=True, env=e)
    assert cp.returncode == 0, cp.stderr
    if not cp.stdout.strip():
        return "allow"
    return json.loads(cp.stdout)["hookSpecificOutput"]["permissionDecision"]


def _w(path, tool="Write"):
    key = "notebook_path" if tool == "NotebookEdit" else "file_path"
    return {"tool_name": tool, "tool_input": {key: path}}


def _b(cmd):
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


DENY = [
    _w(Q), _w(Q, "Edit"), _w(Q, "MultiEdit"), _w(Q, "NotebookEdit"),
    _w(".crossbox/needs-registry.yaml"), _w(".crossbox/config.yaml", "Edit"),
    _w(".crossbox/claims.yaml"), _w(".crossbox/notes/laptop-to-workstation.md"),
    _w("/srv/app/.crossbox/queue/TASK-001.yaml"),
    _w(".CROSSBOX/queue/TASK-001.yaml"),
    _w("docs/../.crossbox/queue/TASK-001.yaml"),
    _w(".crossbox\\queue\\TASK-001.yaml"),
    _b(f"echo x >> {Q}"), _b(f"echo x > {Q}"), _b(f"echo x | tee -a {Q}"),
    _b(f"sed -i s/pending/done/ {Q}"), _b(f"sed -i.bak -e s/a/b/ {Q}"),
    _b(f"perl -pi -e s/a/b/ {Q}"),
    _b(f"cp /tmp/t.yaml {Q}"), _b(f"mv /tmp/t.yaml {Q}"), _b(f"rm {Q}"),
    _b(f"python3 -c \"open('{Q}','w').write('x')\""),
    _b("git checkout -- .crossbox/queue/"), _b("truncate -s0 .crossbox/claims.yaml"),
    _b("cd /repo && echo x > '.crossbox/needs-registry.yaml'"),
    # a `cd` into the directory, a heredoc, find -delete, git -C, patch, vim: formerly missed
    _b("cd .crossbox && echo hi > claims.yaml"), _b("cd .crossbox; printf x>queue/TASK-001.yaml"), _b("cd .crossbox/queue && echo > ../claims.yaml"), _b("python3 - <<'EOF'\nopen('.crossbox/claims.yaml','w').write('')\nEOF"), _b("find .crossbox -name '*.yaml' -delete"), _b("find .crossbox -type f -exec rm {} +"), _b("git -C .crossbox checkout -- ."), _b("git rm -r .crossbox/queue"), _b("cd .crossbox && : > claims.yaml"), _b("cd .crossbox && patch < /tmp/p"), _b("cd .crossbox && unlink claims.yaml"), _b("vim -es -c ':w' .crossbox/claims.yaml"),
    # a quoted word or a read beside a write must not hide the write itself
    _b("crossbox note 'x' && rm .crossbox/claims.yaml"), _b(f"cat {Q} | tee .crossbox/claims.yaml"),
    _b("echo 'rm' && rm '.crossbox/claims.yaml'"), _b("cd '.crossbox' && rm claims.yaml"),
    _b("git commit -m 'msg' && git checkout -- .crossbox/queue/"),
    _b("cat <<'E'\nhello\nE\nrm .crossbox/claims.yaml"),
    _b('cat > .cross"box"/queue/a.yaml'), _b("rm .cross\\box/claims.yaml"), _b("rm '.crossbox'/claims.yaml"),
]
ALLOW = [
    {"tool_name": "Read", "tool_input": {"file_path": Q}},
    _w("src/app.py"), _w("docs/crossbox-notes.md"), _w("my.crossbox/file"),
    _b(f"cat {Q}"), _b(f"grep -n status {Q}"), _b(f"cat {Q} > /tmp/copy.yaml"),
    _b("crossbox add TASK-001 'Fix it'"), _b("git add .crossbox && git commit -m q"),
    _b("git diff .crossbox/"), _b("sed -i s/a/b/ src/app.py"), _b("ls .crossbox/queue"),
    # the same shapes aimed at somewhere harmless, or only reading
    _b("cd .crossbox && cat claims.yaml"), _b("cd .crossbox && ls && git diff"), _b("cd .crossbox && echo hi > /tmp/x"), _b("find .crossbox -name '*.yaml'"), _b("git -C .crossbox log"), _b("python3 - <<'EOF'\nprint(1)\nEOF"), _b("cd src && echo hi > out.txt"), _b("git -C . status"), _b("cd .crossbox && grep -n status queue/TASK-001.yaml > /dev/null"),
    # a writer word inside quoted text is text, and a writer in another simple command that
    # does not name the directory writes elsewhere
    _b("crossbox note --to ws 'do not touch .crossbox by hand'"),
    _b("crossbox edit-step T.a --append-notes 'see .crossbox/queue; rm stale file'"),
    _b('git add .crossbox/claims.yaml && git commit -m "claim: mv step"'),
    _b("cat .crossbox/queue/a.yaml | tee /dev/null"),
    _b("grep -c id .crossbox/queue/*.yaml; rm -f scratch.txt"),
]


@pytest.mark.parametrize("event", DENY, ids=lambda e: json.dumps(e["tool_input"])[:60])
def test_guard_denies_writes_under_the_crossbox_dir(event):
    """R6/F10: see INVARIANTS.md, sixth-review findings (the `cd`/heredoc/find shapes)."""
    assert _decide(event) == "deny"


@pytest.mark.parametrize("event", ALLOW, ids=lambda e: json.dumps(e["tool_input"])[:60])
def test_guard_never_blocks_reads_or_unrelated_writes(event):
    assert _decide(event) == "allow"


def test_guard_honours_a_custom_directory():
    ev = _w("tasks/cb/queue/TASK-001.yaml")
    assert _decide(ev) == "allow"
    assert _decide(ev, "--dir", "tasks/cb") == "deny"
    assert _decide(ev, env={"CROSSBOX_GUARD_DIR": "tasks/cb"}) == "deny"


def test_guard_never_blocks_on_a_malformed_event():
    cp = subprocess.run([sys.executable, str(GUARD)], input="not json", capture_output=True,
                        text=True)
    assert cp.returncode == 0 and cp.stdout == ""


def test_writer_pattern_is_not_quadratic_on_repeated_words():
    """`sed\\s+(\\S+\\s+)*-i` retried the unbounded group at every `sed`: a
    128 KB command of repeated `sed ` took over 30 s, long enough for a hook
    timeout to let the command through unchecked."""
    import importlib.util
    import time
    path = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "queue-guard.py"
    spec = importlib.util.spec_from_file_location("queue_guard_perf", path)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    for s in ("sed " * 32000, "perl " * 32000):
        t = time.monotonic()
        g.WRITERS.search(s)
        assert time.monotonic() - t < 1, s[:10]
    assert g.WRITERS.search("x; sed -n 1p f -i.bak .crossbox/q")


def _guard(proj, tool="Bash", cwd=None, **inp):
    ev = {"tool_name": tool, "tool_input": inp}
    if cwd:
        ev["cwd"] = str(cwd)
    env = {**os.environ, "CLAUDE_PROJECT_DIR": str(proj)}
    env.pop("CROSSBOX_GUARD_DIR", None)
    cp = subprocess.run([sys.executable, str(GUARD)], input=json.dumps(ev), capture_output=True,
                        text=True, env=env)
    return "deny" if '"deny"' in cp.stdout else "allow"


def test_the_guard_denies_relative_writes_when_the_session_is_inside_the_directory(tmp_path):
    """R6/F10: see INVARIANTS.md, sixth-review findings."""
    proj = tmp_path / "p"
    (proj / ".crossbox" / "queue").mkdir(parents=True)
    assert _guard(proj, cwd=proj / ".crossbox", command="echo x > claims.yaml") == "deny"
    assert _guard(proj, cwd=proj / ".crossbox", command="cat claims.yaml") == "allow"
    assert _guard(proj, cwd=proj, command="echo x > claims.yaml") == "allow"


def test_the_guard_is_silent_in_a_project_without_the_directory(tmp_path):
    """R6/F11: see INVARIANTS.md, sixth-review findings."""
    (tmp_path / "bare").mkdir()
    assert _guard(tmp_path / "bare", tool="Write", file_path=".crossbox/queue/T.yaml") == "allow"
    assert _guard(tmp_path / "bare", command="rm -rf ./.crossbox") == "allow"


def test_the_added_patterns_are_not_quadratic():
    """R6/F10: see INVARIANTS.md, sixth-review findings."""
    spec = importlib.util.spec_from_file_location("queue_guard_wide", GUARD)
    g = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(g)
    for s in ("find " * 32000, "git -C x " * 20000, "python3 - " * 20000, "cd a; " * 20000):
        t = time.monotonic()
        g.WRITERS.search(s)
        g.redirects_into(s, ".crossbox")
        assert time.monotonic() - t < 2, s[:10]


def test_the_guard_runs_standalone_with_the_events_the_readme_shows():
    hook = Path(__file__).resolve().parents[1] / "plugin" / "hooks" / "queue-guard.py"

    def call(event):
        cp = subprocess.run([sys.executable, str(hook)], input=json.dumps(event),
                            capture_output=True, text=True)
        assert cp.returncode == 0
        return cp.stdout

    deny = call({"tool_name": "Edit", "tool_input": {"file_path": "/r/.crossbox/queue/TASK-001.yaml"}})
    assert json.loads(deny)["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert call({"tool_name": "Bash", "tool_input": {"command": "echo x >> .crossbox/claims.yaml"}})
    assert call({"tool_name": "Read", "tool_input": {"file_path": "/r/.crossbox/queue/TASK-001.yaml"}}) == ""
    assert call({"tool_name": "Bash", "tool_input": {"command": "cat .crossbox/queue/TASK-001.yaml"}}) == ""
    assert call({"tool_name": "Write", "tool_input": {"file_path": "/r/src/app.py"}}) == ""
