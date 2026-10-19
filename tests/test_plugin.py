"""The Claude Code plugin: manifests, hooks wiring, the MCP server and the
status line. A fake `crossbox` executable on PATH records its argv, so the
tests assert exactly what the server and the status line would run."""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugin"
MCP = PLUGIN / "mcp" / "crossbox_mcp.py"
STATUS = PLUGIN / "statusline" / "crossbox_status.py"


# ---------------------------------------------------------------- manifests

def _load(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def _package_version():
    m = re.search(r'^version\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M)
    return m.group(1)


def test_marketplace_schema_and_source():
    m = _load(ROOT / ".claude-plugin" / "marketplace.json")
    assert m["name"] == "crossbox"
    assert isinstance(m["owner"], dict) and m["owner"]["name"]
    assert len(m["plugins"]) == 1
    p = m["plugins"][0]
    assert p["name"] == "crossbox"
    src = p["source"]
    assert src.startswith("./") and ".." not in src.split("/")
    assert (ROOT / src / ".claude-plugin" / "plugin.json").is_file()


def test_module_version_matches_package():
    import crossbox
    assert crossbox.__version__ == _package_version()


def test_plugin_json_matches_marketplace_and_package():
    j = _load(PLUGIN / ".claude-plugin" / "plugin.json")
    assert j["name"] == _load(ROOT / ".claude-plugin" / "marketplace.json")["plugins"][0]["name"]
    assert j["version"] == _package_version()
    assert j["license"] == "Apache-2.0"
    assert j["author"] == {"name": "crossbox"}  # the project, never a person
    assert j["repository"] == "https://github.com/sethpsonnier/crossbox"


def _command_files(cmd):
    return re.findall(r'\$\{CLAUDE_PLUGIN_ROOT\}/([^"\s]+)', cmd)


def test_hooks_json_commands_resolve_under_plugin_root():
    h = _load(PLUGIN / "hooks" / "hooks.json")
    assert h["description"]
    assert set(h["hooks"]) == {"SessionStart", "PreToolUse"}
    seen = []
    for entries in h["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                assert hook["type"] == "command"
                cmd = hook["command"]
                files = _command_files(cmd)
                assert len(files) == 1, cmd
                assert (PLUGIN / files[0]).is_file(), cmd
                # the variable is quoted, so a root with a space still works
                assert '"${CLAUDE_PLUGIN_ROOT}/' in cmd
                seen.append(files[0])
    assert sorted(seen) == ["hooks/queue-guard.py", "hooks/session-start.sh"]
    assert h["hooks"]["PreToolUse"][0]["matcher"] == "Write|Edit|MultiEdit|NotebookEdit|Bash"


def test_mcp_json_points_at_the_server():
    m = _load(PLUGIN / ".mcp.json")
    s = m["mcpServers"]["crossbox"]
    assert s["command"] == "python3"
    assert [Path(a.replace("${CLAUDE_PLUGIN_ROOT}", str(PLUGIN))).is_file() for a in s["args"]] == [True]


def test_skill_frontmatter_and_size():
    text = (PLUGIN / "skills" / "crossbox" / "SKILL.md").read_text()
    assert text.startswith("---\n")
    fm = text.split("---\n")[1]
    assert re.search(r"^name: crossbox$", fm, re.M)
    desc = re.search(r"^description: (.+)$", fm, re.M).group(1)
    for word in ("task queue", "what next", "claim", "mark done", "handoff", "crossbox"):
        assert word in desc
    assert len(text.splitlines()) < 150
    assert "Never edit files under `.crossbox/`" in text
    assert "NOT EVALUABLE" in text


def test_manifest_in_ships_the_plugin():
    m = (ROOT / "MANIFEST.in").read_text()
    assert "graft plugin" in m and "graft .claude-plugin" in m


def test_no_stale_reference_to_the_old_example_paths():
    for rel in ("README.md", "docs/agents.md", "examples/demo.sh",
                ".github/workflows/ci.yml", "MANIFEST.in"):
        assert "pretooluse-queue-guard" not in (ROOT / rel).read_text(), rel
    assert not (ROOT / "examples" / "claude-code" / "session-start.sh").exists()
    assert (ROOT / "examples" / "claude-code" / "README.md").is_file()


# ---------------------------------------------------------------- fixtures

FAKE = """#!{py}
import json, os, sys
with open(os.environ["FAKE_LOG"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stdout.write(os.environ.get("FAKE_OUT", ""))
sys.stderr.write(os.environ.get("FAKE_ERR", ""))
sys.exit(int(os.environ.get("FAKE_RC", "0")))
"""


class Rig:
    pass


@pytest.fixture
def rig(tmp_path):
    """A project with .crossbox/, a fake crossbox on PATH, and its argv log."""
    proj = tmp_path / "proj"
    (proj / ".crossbox").mkdir(parents=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "crossbox"
    fake.write_text(FAKE.format(py=sys.executable))
    fake.chmod(0o755)
    log = tmp_path / "argv.log"
    env = dict(os.environ)
    for k in ("CLAUDE_PROJECT_DIR", "CROSSBOX_MCP_TIMEOUT", "CROSSBOX_STATUS_TTL", "FAKE_OUT",
              "FAKE_ERR", "FAKE_RC"):
        env.pop(k, None)
    env.update(PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}", FAKE_LOG=str(log),
               CLAUDE_PROJECT_DIR=str(proj))
    r = Rig()
    r.proj, r.bindir, r.log, r.env, r.tmp = proj, bindir, log, env, tmp_path
    r.calls = lambda: [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
    return r


class Server:
    def __init__(self, env):
        self.p = subprocess.Popen([sys.executable, str(MCP)], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        self.n = 0

    def raw(self, data: bytes):
        self.p.stdin.write(data)
        self.p.stdin.flush()

    def read(self):
        line = self.p.stdout.readline()
        return json.loads(line) if line else None

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self.n += 1
            msg["id"] = self.n
        self.raw((json.dumps(msg) + "\n").encode())
        return None if notify else self.read()

    def call(self, tool, args=None):
        r = self.send("tools/call", {"name": tool, "arguments": args or {}})
        return r["result"]

    def close(self):
        try:
            self.p.stdin.close()
        except OSError:
            pass
        self.p.wait(timeout=10)
        self.p.stdout.close()
        self.p.stderr.close()


@pytest.fixture
def server(rig):
    s = Server(rig.env)
    s.send("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                          "clientInfo": {"name": "t", "version": "0"}})
    s.send("notifications/initialized", notify=True)
    yield s
    s.close()


def _one(env, tool, args=None):
    s = Server(env)
    try:
        return s.call(tool, args)
    finally:
        s.close()


def _text(result):
    return result["content"][0]["text"]


# ---------------------------------------------------------------- protocol

def test_initialize_echoes_supported_version_else_default(rig):
    s = Server(rig.env)
    try:
        for want, got in (("2025-06-18", "2025-06-18"), ("2025-03-26", "2025-03-26"),
                          ("1999-01-01", "2025-11-25"), (None, "2025-11-25")):
            params = {"protocolVersion": want} if want else {}
            r = s.send("initialize", params)
            assert r["result"]["protocolVersion"] == got
            assert r["result"]["capabilities"] == {"tools": {}}
            assert r["result"]["serverInfo"]["name"] == "crossbox"
            assert r["result"]["serverInfo"]["version"] == _package_version()
    finally:
        s.close()


def test_notifications_get_no_reply_and_unknown_method_errors(server):
    server.send("notifications/initialized", notify=True)
    server.send("notifications/whatever", {"x": 1}, notify=True)
    server.send("some/unknown", notify=True)          # an unknown notification: silence
    r = server.send("no/such/method")                 # the next reply is for THIS request
    assert r["id"] == server.n and r["error"]["code"] == -32601


def test_malformed_json_is_32700_and_the_server_survives(server):
    server.raw(b"{not json\n")
    r = server.read()
    assert r["error"]["code"] == -32700 and r["id"] is None
    server.raw(b"\xff\xfe\n")
    assert server.read()["error"]["code"] == -32700
    assert server.send("ping")["result"] == {}


def test_deeply_nested_json_does_not_kill_the_server(server):
    server.raw(b"[" * 200000 + b"\n")
    assert server.read()["error"]["code"] in (-32700, -32600)
    assert server.send("ping")["result"] == {}


def test_a_non_object_message_is_invalid_request(server):
    server.raw(b"[1,2]\n")
    assert server.read()["error"]["code"] == -32600
    server.raw(b"42\n")
    assert server.read()["error"]["code"] == -32600


def test_oversized_message_is_refused_without_buffering_and_server_continues(server):
    big = b'{"jsonrpc":"2.0","id":1,"method":"ping","params":{"x":"' + b"a" * (3 << 20) + b'"}}\n'
    server.raw(big)
    r = server.read()
    assert r["error"]["code"] == -32600 and "1 MiB" in r["error"]["message"]
    assert server.send("ping")["result"] == {}


def test_a_10_mb_title_never_reaches_crossbox(rig, server):
    title = "t" * (10 << 20)
    server.raw(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "tools/call", "params": {
        "name": "crossbox_add", "arguments": {"task": "TASK-001", "title": title}}}).encode() + b"\n")
    assert server.read()["error"]["code"] == -32600
    assert rig.calls() == [] and server.send("ping")["result"] == {}


def test_eof_ends_the_server_cleanly(rig):
    s = Server(rig.env)
    s.p.stdin.close()
    assert s.p.wait(timeout=10) == 0
    s.p.stdout.close()
    s.p.stderr.close()


def test_tools_list_is_the_fixed_allowlist(server):
    tools = server.send("tools/list")["result"]["tools"]
    assert sorted(t["name"] for t in tools) == sorted([
        "crossbox_plan", "crossbox_ready", "crossbox_show", "crossbox_claim",
        "crossbox_done", "crossbox_release", "crossbox_add", "crossbox_note", "crossbox_why", "crossbox_activity",
        "crossbox_closures"])
    for t in tools:
        assert t["description"]
        assert t["inputSchema"]["type"] == "object"
        assert t["inputSchema"]["additionalProperties"] is False


def test_why_refuses_a_ref_that_is_not_a_step_or_task(server):
    for bad in ("--root=/etc", "TASK-API-001.c; rm", "x"):
        res = server.call("crossbox_why", {"ref": bad})
        assert res["isError"] is True, bad


def test_done_refuses_a_commit_that_is_not_a_sha_or_head(server):
    for bad in ("--force", "HEAD~1", "main", "5cc1", "a" * 41, "5cc141a; rm"):
        res = server.call("crossbox_done", {"ref": "TASK-API-001.b", "commit": bad})
        assert res["isError"] is True, bad


def test_unknown_tool_is_32602(server):
    r = server.send("tools/call", {"name": "crossbox_rm", "arguments": {}})
    assert r["error"]["code"] == -32602


# ---------------------------------------------------------------- tools run the expected argv

CASES = [
    ("crossbox_plan", {}, ["plan", "--json", "--no-probes"]),
    ("crossbox_plan", {"top": 3}, ["plan", "--json", "--top", "3", "--no-probes"]),
    ("crossbox_ready", {}, ["ready", "--json", "--no-probes"]),
    ("crossbox_show", {"task": "TASK-API-001"}, ["show", "TASK-API-001"]),
    ("crossbox_why", {}, ["why", "--json", "--no-probes"]),
    ("crossbox_activity", {}, ["activity", "--json"]),
    ("crossbox_closures", {}, ["closures", "--json"]),
    ("crossbox_why", {"ref": "TASK-API-001.c"}, ["why", "TASK-API-001.c", "--json", "--no-probes"]),
    ("crossbox_why", {"ref": "TASK-API-001"}, ["why", "TASK-API-001", "--json", "--no-probes"]),
    ("crossbox_claim", {"ref": "TASK-API-001.a", "subject": "wire the endpoint"},
     ["claim", "wire the endpoint", "--step", "TASK-API-001.a"]),
    ("crossbox_done", {"ref": "TASK-API-001.b"}, ["done", "TASK-API-001.b"]),
    ("crossbox_done", {"ref": "TASK-API-001.b", "commit": "HEAD"},
     ["done", "TASK-API-001.b", "--commit", "HEAD"]),
    ("crossbox_done", {"ref": "TASK-API-001.b", "commit": "5cc141a"},
     ["done", "TASK-API-001.b", "--commit", "5cc141a"]),
    ("crossbox_release", {"claim_id": "CLM-laptop-wt1-001"}, ["release", "CLM-laptop-wt1-001"]),
    ("crossbox_add", {"task": "TASK-001", "title": "a new step"}, ["add", "TASK-001", "a new step"]),
    ("crossbox_add", {"task": "TASK-API-002", "title": "later", "depends": ["a", "TASK-API-001.c"]},
     ["add", "TASK-API-002", "later", "--depends", "a,TASK-API-001.c"]),
    ("crossbox_note", {"message": "hello there"}, ["note", "hello there"]),
    ("crossbox_note", {"message": "hi", "to": "workstation+wt2"},
     ["note", "--to", "workstation+wt2", "hi"]),
]


@pytest.mark.parametrize("tool,args,expected", CASES)
def test_each_tool_runs_exactly_the_expected_argv(rig, server, tool, args, expected):
    res = server.call(tool, args)
    assert res["isError"] is False, res
    assert rig.calls() == [["--root", str(rig.proj)] + expected]
    assert _text(res).startswith("exit 0")


def test_output_and_both_streams_are_returned(rig):
    res = _one({**rig.env, "FAKE_OUT": "ready: x", "FAKE_ERR": "warn: y"}, "crossbox_ready")
    assert "ready: x" in _text(res) and "stderr:\nwarn: y" in _text(res)


# ---------------------------------------------------------------- exit codes are answers

@pytest.mark.parametrize("rc,needle", [(1, "refused or findings"), (2, "NOT EVALUABLE")])
def test_exit_1_and_2_are_answers_not_tool_errors(rig, rc, needle):
    res = _one({**rig.env, "FAKE_RC": str(rc), "FAKE_ERR": "because"}, "crossbox_ready")
    assert res["isError"] is False
    assert f"exit {rc}" in _text(res) and needle in _text(res) and "because" in _text(res)


def test_exit_0_does_not_say_not_evaluable(server):
    assert "NOT EVALUABLE" not in _text(server.call("crossbox_ready"))


def test_timeout_is_not_evaluable(rig):
    (rig.bindir / "crossbox").write_text(f"#!{sys.executable}\nimport time\ntime.sleep(30)\n")
    t0 = time.time()
    res = _one({**rig.env, "CROSSBOX_MCP_TIMEOUT": "0.5"}, "crossbox_ready")
    assert time.time() - t0 < 10
    assert res["isError"] is False and "NOT EVALUABLE" in _text(res)


def test_missing_crossbox_binary_one_clear_message(rig):
    (rig.tmp / "empty").mkdir()
    res = _one({**rig.env, "PATH": str(rig.tmp / "empty")}, "crossbox_plan")
    t = _text(res)
    assert "NOT EVALUABLE" in t and "not on PATH" in t and "\n" not in t
    assert res["isError"] is False


# ---------------------------------------------------------------- project directory

def test_unset_project_dir_is_not_evaluable_and_runs_nothing(rig):
    env = dict(rig.env)
    del env["CLAUDE_PROJECT_DIR"]
    t = _text(_one(env, "crossbox_ready"))
    assert "NOT EVALUABLE" in t and rig.calls() == []


def test_project_dir_without_crossbox_dir_is_inactive(rig):
    shutil.rmtree(rig.proj / ".crossbox")
    t = _text(_one(rig.env, "crossbox_plan"))
    assert t.startswith("INACTIVE") and rig.calls() == []


def test_project_dir_that_is_not_a_directory(rig):
    f = rig.tmp / "afile"
    f.write_text("x")
    t = _text(_one({**rig.env, "CLAUDE_PROJECT_DIR": str(f)}, "crossbox_plan"))
    assert "NOT EVALUABLE" in t and rig.calls() == []


def test_the_model_cannot_supply_a_root(rig, server):
    for extra in ({"root": "/etc"}, {"path": "/etc"}, {"--root": "/etc"}):
        res = server.call("crossbox_ready", extra)
        assert res["isError"] is True and "unknown argument" in _text(res)
    assert rig.calls() == []


# ---------------------------------------------------------------- injection

BAD = [
    ("crossbox_show", {"task": "TASK-001; rm -rf /"}),
    ("crossbox_show", {"task": "$(touch /tmp/x)"}),
    ("crossbox_show", {"task": "`id`"}),
    ("crossbox_show", {"task": "--help"}),
    ("crossbox_show", {"task": "TASK-001\n"}),
    ("crossbox_show", {"task": "../../etc/passwd"}),
    ("crossbox_done", {"ref": "TASK-001"}),                       # a task id is not a step ref
    ("crossbox_done", {"ref": "TASK-001.a --force"}),
    ("crossbox_done", {"ref": "-TASK-001.a"}),
    ("crossbox_done", {"ref": 5}),
    ("crossbox_done", {}),
    ("crossbox_release", {"claim_id": "--all-box"}),
    ("crossbox_release", {"claim_id": "--all-machine"}),
    ("crossbox_release", {"claim_id": "CLM-x-001; id"}),
    ("crossbox_claim", {"ref": "TASK-001.a", "subject": "--step"}),
    ("crossbox_claim", {"ref": "TASK-001.a", "subject": " -x"}),
    ("crossbox_claim", {"ref": "TASK-001.a", "subject": "line\nbreak"}),
    ("crossbox_claim", {"ref": "TASK-001.a", "subject": "nul\x00byte"}),
    ("crossbox_claim", {"ref": "TASK-001.a", "subject": "cr\rhere"}),
    ("crossbox_claim", {"ref": "TASK-001.a", "subject": ""}),
    ("crossbox_claim", {"ref": "TASK-001.a", "subject": "x" * 900_000}),
    ("crossbox_add", {"task": "TASK-001", "title": "t" * 900_000}),
    ("crossbox_add", {"task": "TASK-001", "title": "-D a"}),
    ("crossbox_add", {"task": "TASK-001", "title": "ok", "depends": ["a,b;rm"]}),
    ("crossbox_add", {"task": "TASK-001", "title": "ok", "depends": ["-a"]}),
    ("crossbox_add", {"task": "TASK-001", "title": "ok", "depends": "a"}),
    ("crossbox_add", {"task": "TASK-001", "title": "ok", "depends": ["a"] * 100}),
    ("crossbox_note", {"message": "--to evil"}),
    ("crossbox_note", {"message": "hi", "to": "-x"}),
    ("crossbox_note", {"message": "hi", "to": "a;b"}),
    ("crossbox_note", {"message": "hi\nthere"}),
    ("crossbox_plan", {"top": "3; id"}),
    ("crossbox_plan", {"top": -1}),
    ("crossbox_plan", {"top": True}),
]


@pytest.mark.parametrize("tool,args", BAD)
def test_injection_and_malformed_values_are_refused_before_exec(rig, server, tool, args):
    res = server.call(tool, args)
    assert res["isError"] is True and _text(res).startswith("refused:")
    assert rig.calls() == []


def test_shell_metacharacters_in_a_title_are_one_inert_argv_element(rig, server):
    marker = rig.tmp / "pwned"
    title = f"fix `touch {marker}`; echo $(touch {marker}) && cat > {marker} | true #"
    res = server.call("crossbox_add", {"task": "TASK-001", "title": title})
    assert res["isError"] is False
    assert rig.calls() == [["--root", str(rig.proj), "add", "TASK-001", title]]
    assert not marker.exists()


def test_a_long_but_allowed_title_passes_whole(rig, server):
    title = "t" * 2000
    assert server.call("crossbox_add", {"task": "TASK-001", "title": title})["isError"] is False
    assert rig.calls()[0][-1] == title


def test_server_source_never_uses_a_shell():
    src = MCP.read_text()
    assert "shell=True" not in src and "os.system" not in src and "import shlex" not in src
    assert "shell=False" in src


# ---------------------------------------------------------------- hooks (behaviour, not wiring)

def _run_start(rig, **extra):
    env = {**rig.env, **extra}
    return subprocess.run(["sh", str(PLUGIN / "hooks" / "session-start.sh")], capture_output=True,
                          text=True, env=env, cwd=str(rig.tmp), input="")


def test_session_start_silent_outside_a_crossbox_project(rig):
    shutil.rmtree(rig.proj / ".crossbox")
    cp = _run_start(rig)
    assert cp.returncode == 0 and cp.stdout == "" and rig.calls() == []


def test_session_start_prints_ready_claims_notes_inside(rig):
    cp = _run_start(rig, FAKE_OUT="a line\n")
    assert cp.returncode == 0 and cp.stdout.startswith("## crossbox\n")
    assert [c[2] for c in rig.calls()] == ["ready", "claims", "notes"]
    assert all(c[:2] == ["--root", str(rig.proj)] for c in rig.calls())


def test_session_start_says_so_in_one_line_when_crossbox_is_missing(rig):
    (rig.tmp / "empty").mkdir()
    sh = shutil.which("sh")
    env = {**rig.env, "PATH": str(rig.tmp / "empty")}
    cp = subprocess.run([sh, str(PLUGIN / "hooks" / "session-start.sh")], capture_output=True,
                        text=True, env=env, cwd=str(rig.tmp))
    assert cp.returncode == 0
    assert cp.stdout.count("\n") == 2 and "NOT EVALUABLE" in cp.stdout and "## crossbox" in cp.stdout


# ---------------------------------------------------------------- status line

READY = {"machine": "m1", "slot": "main",
         "ready": [{"ref": "T-001.a"}, {"ref": "T-001.b"}, {"ref": "T-001.c"}],
         "claimed": [{"ref": "T-002.a", "claims": ["CLM-m1-001 (m1/main)"]},
                     {"ref": "T-002.b", "claims": ["CLM-m2-001 (m2/main)"]},
                     {"ref": "T-002.c", "claims": ["CLM-m1-002 (m1/main)", "CLM-m2-002 (m2/main)"]}]}


def _git_proj(rig):
    subprocess.run(["git", "init", "-q", str(rig.proj)], check=True)


def _status(rig, stdin="{}", **extra):
    env = {**rig.env, **extra}
    cp = subprocess.run([sys.executable, str(STATUS)], capture_output=True, text=True, env=env,
                        input=stdin, cwd=str(rig.tmp))
    assert cp.returncode == 0, cp.stderr
    return cp.stdout.strip()


def test_status_counts_ready_and_my_claims_from_a_fixture(rig):
    _git_proj(rig)
    out = _status(rig, FAKE_OUT=json.dumps(READY))
    assert out == "cbx: 3 ready · 2 claimed by me"
    assert rig.calls() == [["--root", str(rig.proj), "ready", "--json", "--no-probes"]]


def test_status_exit_2_is_a_question_mark_and_nothing_else(rig):
    _git_proj(rig)
    assert _status(rig, FAKE_RC="2", FAKE_OUT=json.dumps(READY)) == "cbx: ?"


def test_status_unparseable_output_is_a_question_mark(rig):
    _git_proj(rig)
    assert _status(rig, FAKE_OUT="not json") == "cbx: ?"
    assert _status(rig, FAKE_OUT="[1]", CROSSBOX_STATUS_TTL="0") == "cbx: ?"


def test_status_exit_1_marks_the_counts_as_a_floor(rig):
    _git_proj(rig)
    out = _status(rig, FAKE_RC="1", FAKE_OUT=json.dumps(READY))
    assert out.startswith("cbx: 3 ready") and "partial" in out


def test_status_silent_without_a_crossbox_dir(rig):
    shutil.rmtree(rig.proj / ".crossbox")
    assert _status(rig) == "" and rig.calls() == []


def test_status_reads_the_project_from_stdin_when_the_env_is_unset(rig):
    _git_proj(rig)
    env = dict(rig.env)
    del env["CLAUDE_PROJECT_DIR"]
    cp = subprocess.run([sys.executable, str(STATUS)], capture_output=True, text=True,
                        env={**env, "FAKE_OUT": json.dumps(READY)}, cwd=str(rig.tmp),
                        input=json.dumps({"workspace": {"current_dir": str(rig.proj)}}))
    assert cp.stdout.strip() == "cbx: 3 ready · 2 claimed by me"


def test_status_cache_is_honoured_within_ttl_and_expires(rig):
    _git_proj(rig)
    first = _status(rig, FAKE_OUT=json.dumps(READY), CROSSBOX_STATUS_TTL="30")
    # a changed answer inside the TTL is not seen: the cache served it, no second run
    second = _status(rig, FAKE_OUT=json.dumps({**READY, "ready": []}), CROSSBOX_STATUS_TTL="30")
    assert second == first and len(rig.calls()) == 1
    [cache] = (rig.proj / ".git").glob("crossbox-status-*.cache")
    old = time.time() - 120
    os.utime(cache, (old, old))
    third = _status(rig, FAKE_OUT=json.dumps({**READY, "ready": []}), CROSSBOX_STATUS_TTL="30")
    assert third.startswith("cbx: 0 ready") and len(rig.calls()) == 2


def test_status_ttl_zero_never_caches(rig):
    _git_proj(rig)
    _status(rig, FAKE_OUT=json.dumps(READY), CROSSBOX_STATUS_TTL="0")
    _status(rig, FAKE_OUT=json.dumps(READY), CROSSBOX_STATUS_TTL="0")
    assert len(rig.calls()) == 2 and not list((rig.proj / ".git").glob("crossbox-status*"))


def test_status_without_git_still_answers_uncached(rig):
    assert _status(rig, FAKE_OUT=json.dumps(READY)).startswith("cbx: 3 ready")


def test_status_missing_crossbox_says_so(rig):
    (rig.tmp / "empty").mkdir()
    assert _status(rig, PATH=str(rig.tmp / "empty")) == "cbx: crossbox not installed"


@pytest.mark.skipif(shutil.which("python3") is None, reason="no python3 on PATH")
def test_status_shell_wrapper_runs_the_python(rig):
    _git_proj(rig)
    cp = subprocess.run(["sh", str(PLUGIN / "statusline" / "crossbox-status.sh")], input="{}",
                        capture_output=True, text=True, cwd=str(rig.tmp),
                        env={**rig.env, "FAKE_OUT": json.dumps(READY)})
    assert cp.stdout.strip().startswith("cbx: 3 ready")


def _start(rig, **extra):
    return subprocess.run(["sh", str(PLUGIN / "hooks" / "session-start.sh")], capture_output=True,
                          text=True, env={**rig.env, **extra}, cwd=str(rig.tmp), input="")


def test_session_start_passes_no_probes_and_the_root_unless_probes_are_allowed(rig):
    """R6/F1: see INVARIANTS.md, sixth-review findings."""
    _start(rig)
    assert all(c[:2] == ["--root", str(rig.proj)] for c in rig.calls())
    assert rig.calls()[0] == ["--root", str(rig.proj), "ready", "--no-probes"]
    rig.log.unlink()
    _start(rig, CROSSBOX_ALLOW_PROBES="1")
    assert rig.calls()[0] == ["--root", str(rig.proj), "ready"]


def test_status_line_passes_no_probes_unless_allowed(rig):
    """R6/F1: see INVARIANTS.md, sixth-review findings."""
    _status(rig, FAKE_OUT="{}")
    assert rig.calls()[-1][-1] == "--no-probes"
    _status(rig, FAKE_OUT="{}", CROSSBOX_ALLOW_PROBES="1")
    assert rig.calls()[-1] == ["--root", str(rig.proj), "ready", "--json"]


def test_mcp_read_tools_pass_no_probes_unless_allowed(rig):
    """R6/F1: see INVARIANTS.md, sixth-review findings."""
    _one(rig.env, "crossbox_plan")
    _one({**rig.env, "CROSSBOX_ALLOW_PROBES": "1"}, "crossbox_plan")
    c = rig.calls()
    assert c[0][-1] == "--no-probes" and "--no-probes" not in c[1]


def test_session_start_reads_the_inner_queue_of_a_subdirectory_project(rig):
    """R6/F5: see INVARIANTS.md, sixth-review findings."""
    sub = rig.proj / "app"
    (sub / ".crossbox").mkdir(parents=True)
    _start(rig, CLAUDE_PROJECT_DIR=str(sub))
    assert rig.calls()[0][:2] == ["--root", str(sub)]


def test_a_lone_surrogate_is_refused_and_the_server_keeps_serving(rig, server):
    """R6/F2: see INVARIANTS.md, sixth-review findings."""
    server.raw(b'{"jsonrpc":"2.0","id":9,"method":"tools/call","params":{"name":"crossbox_add",'
               b'"arguments":{"task":"TASK-001","title":"x\\ud800y"}}}\n')
    r = server.read()
    assert r["result"]["isError"] is True and "not valid Unicode" in _text(r["result"])
    assert rig.calls() == []
    assert server.send("ping")["result"] == {}


def test_an_exception_in_handling_is_an_internal_error_not_a_dead_server(server, monkeypatch):
    """R6/F2: see INVARIANTS.md, sixth-review findings."""
    sys.path.insert(0, str(MCP.parent))
    import io

    import crossbox_mcp as m
    monkeypatch.setattr(m, "handle", lambda msg: 1 / 0)
    out = io.BytesIO()
    m.serve(io.BytesIO(b'{"jsonrpc":"2.0","id":3,"method":"ping"}\n{"jsonrpc":"2.0","id":4,"method":"ping"}\n'), out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert [x["error"]["code"] for x in lines] == [-32603, -32603] and [x["id"] for x in lines] == [3, 4]


@pytest.mark.parametrize("rc", [64, 3, 7])
def test_other_exit_codes_are_tool_errors_with_the_output(rig, rc):
    """R6/F13: see INVARIANTS.md, sixth-review findings."""
    res = _one({**rig.env, "FAKE_RC": str(rc), "FAKE_ERR": "usage text"}, "crossbox_ready")
    assert res["isError"] is True and "usage text" in _text(res) and f"exit {rc}" in _text(res)


def test_a_signal_death_and_a_traceback_are_tool_errors(rig):
    """R6/F13: see INVARIANTS.md, sixth-review findings."""
    (rig.bindir / "crossbox").write_text(f"#!{sys.executable}\nimport os,signal\nos.kill(os.getpid(), signal.SIGKILL)\n")
    res = _one(rig.env, "crossbox_ready")
    assert res["isError"] is True and "signal" in _text(res)
    (rig.bindir / "crossbox").write_text(f"#!{sys.executable}\nraise SystemExit(1)\n")
    ok = _one({**rig.env, "FAKE_RC": "1"}, "crossbox_ready")
    assert ok["isError"] is False
    (rig.bindir / "crossbox").write_text(
        f"#!{sys.executable}\nimport sys\nsys.stderr.write('Traceback (most recent call last):\\nboom\\n')\nsys.exit(1)\n")
    res = _one(rig.env, "crossbox_ready")
    assert res["isError"] is True and "crashed" in _text(res)


@pytest.mark.parametrize("tid", ["MY_PROJ-API-001", "MY-PROJ-007", "ab-1", "PREFIXLONGERTHANSIXTEEN-001", "proj.x-API-123456789012"])
def test_ids_the_cli_accepts_are_accepted(rig, server, tid):
    """R6/F15: see INVARIANTS.md, sixth-review findings."""
    res = server.call("crossbox_show", {"task": tid})
    assert res["isError"] is False, res
    assert rig.calls()[-1][-2:] == ["show", tid]


@pytest.mark.parametrize("tid", ["-X-001", "TASK-001\n", "TASK-001 ", " TASK-001", "TASK", "TASK-A-"])
def test_ids_that_are_not_ids_are_still_refused(rig, server, tid):
    """R6/F15: see INVARIANTS.md, sixth-review findings."""
    assert server.call("crossbox_show", {"task": tid})["isError"] is True
    assert rig.calls() == []


def _alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _grandchild_script(rig, pidfile):
    return (f"#!{sys.executable}\nimport subprocess,sys,time\n"
            f"p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
            f"open({str(pidfile)!r},'w').write(str(p.pid))\ntime.sleep(60)\n")


def test_mcp_timeout_kills_the_grandchildren_too(rig):
    """R6/F14: see INVARIANTS.md, sixth-review findings."""
    pidfile = rig.tmp / "pid"
    (rig.bindir / "crossbox").write_text(_grandchild_script(rig, pidfile))
    res = _one({**rig.env, "CROSSBOX_MCP_TIMEOUT": "1"}, "crossbox_ready")
    assert "NOT EVALUABLE" in _text(res)
    pid = int(pidfile.read_text())
    time.sleep(0.5)
    assert not _alive(pid)


def test_status_timeout_kills_the_grandchildren_too(rig):
    """R6/F14: see INVARIANTS.md, sixth-review findings."""
    pidfile = rig.tmp / "pid"
    (rig.bindir / "crossbox").write_text(_grandchild_script(rig, pidfile))
    src = STATUS.read_text().replace("run_group(argv, root, 10)", "run_group(argv, root, 1)")
    alt = rig.tmp / "status_fast.py"
    alt.write_text(src)
    cp = subprocess.run([sys.executable, str(alt)], capture_output=True, text=True, env=rig.env, input="{}")
    assert cp.stdout.strip() == "cbx: ?"
    time.sleep(0.5)
    assert not _alive(int(pidfile.read_text()))


def _git(rig):
    subprocess.run(["git", "init", "-q", str(rig.proj)], check=True)


def test_a_cache_dated_in_the_future_is_not_used(rig):
    """R6/F12: see INVARIANTS.md, sixth-review findings."""
    _git(rig)
    out = {"machine": "m", "slot": "main", "ready": [{}], "claimed": []}
    _status(rig, FAKE_OUT=json.dumps(out))
    [cache] = (rig.proj / ".git").glob("crossbox-status-*.cache")
    future = time.time() + 3600
    os.utime(cache, (future, future))
    out["ready"] = []
    assert _status(rig, FAKE_OUT=json.dumps(out)).startswith("cbx: 0 ready")


def test_the_cache_is_keyed_by_root_and_machine(rig):
    """R6/F12: see INVARIANTS.md, sixth-review findings."""
    _git(rig)
    out = lambda n: json.dumps({"machine": "m", "slot": "main", "ready": [{}] * n, "claimed": []})  # noqa: E731
    assert _status(rig, FAKE_OUT=out(1)).startswith("cbx: 1 ready")
    assert _status(rig, FAKE_OUT=out(2), CROSSBOX_MACHINE="other").startswith("cbx: 2 ready")
    inner = rig.proj / "inner"
    (inner / ".crossbox").mkdir(parents=True)
    assert _status(rig, FAKE_OUT=out(3), CLAUDE_PROJECT_DIR=str(inner)).startswith("cbx: 3 ready")
    assert _status(rig, FAKE_OUT=out(9)).startswith("cbx: 1 ready")        # same key: cached


def test_exit_1_wording_comes_from_the_json(rig):
    """R6/F19: see INVARIANTS.md, sixth-review findings."""
    _git(rig)
    base = {"machine": "m", "slot": "main", "ready": [], "claimed": []}
    a = _status(rig, FAKE_RC="1", FAKE_OUT=json.dumps({**base, "orphaned_claims": [{}]}), CROSSBOX_STATUS_TTL="0")
    b = _status(rig, FAKE_RC="1", FAKE_OUT=json.dumps({**base, "malformed": [{}, {}]}), CROSSBOX_STATUS_TTL="0")
    assert "1 orphaned claim" in a and "task file was skipped" not in a
    assert "2 malformed" in b


def test_the_readme_counts_the_mcp_tools_it_has():
    """R7: see INVARIANTS.md, seventh-review findings."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("m", ROOT / "plugin" / "mcp" / "crossbox_mcp.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    words = {8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve"}
    readme = (ROOT / "README.md").read_text()
    said = re.search(r"exposes (\w+) verbs as tools", readme).group(1)
    assert said == words[len(m.TOOLS)]
