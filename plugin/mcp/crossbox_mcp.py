#!/usr/bin/env python3
"""A stdio MCP server that exposes a fixed set of crossbox verbs.

stdlib only, Python 3.9+. It shells out to the `crossbox` command (it does not
import the package, so it works when only the command is installed) with an
argv list and shell=False. See docs/plugin.md for the contract; the short form:

  * a fixed verb allowlist, no free-form command, flag or path;
  * every value is validated first; a value that starts with "-" is refused;
  * --root comes from CLAUDE_PROJECT_DIR only, and only if it holds .crossbox/;
  * only crossbox exit codes 0/1/2 are answers (2 = NOT EVALUABLE); any other
    exit code, a death by signal and a Python traceback are tool errors that
    carry the output; a timeout, an unset project directory or a missing
    command are NOT EVALUABLE;
  * the read tools never run a needs-registry probe command (`--no-probes`)
    unless the environment says CROSSBOX_ALLOW_PROBES=1: a cloned repository's
    registry is untrusted;
  * text that cannot be encoded as UTF-8 (a lone surrogate) is refused, and no
    exception can end the server: it answers -32603 and keeps serving;
  * a timeout kills crossbox's whole process group, probe commands included.

Ids are checked against a wide regex, not the package's grammar: the real prefix
lives in the project's config, which this server must not read, and the server
has to run with only the `crossbox` command installed. The prefix may be any
text without control characters that does not start with "-" or a blank (the
package accepts any non-empty prefix); crossbox validates the id again against
the real config.
"""
import json
import os
import re
import shutil
import signal
import subprocess
import sys

SUPPORTED = ("2025-11-25", "2025-06-18", "2025-03-26")
MAX_MESSAGE = 1 << 20          # one JSON-RPC message, bytes
MAX_OUTPUT = 64 * 1024         # per stream returned to the model
MAX_TEXT = 2000                # titles, subjects, messages, characters
MAX_DEPENDS = 32
TIMEOUT = float(os.environ.get("CROSSBOX_MCP_TIMEOUT", "60"))

TASK_RE = r"[^\x00-\x20\x7f-][^\x00-\x1f\x7f]{0,63}?-(?:[A-Z]{1,64}-)?[0-9]{1,18}"
TASK_ID = re.compile(rf"^{TASK_RE}$")
REF = re.compile(rf"^{TASK_RE}\.[a-z]{{1,6}}$")
STEP = re.compile(r"^[a-z]{1,6}$")
CLAIM_ID = re.compile(r"^CLM-[A-Za-z0-9][A-Za-z0-9_.-]{0,126}-[0-9]{1,9}$")
ADDRESS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}(?:\+[A-Za-z0-9][A-Za-z0-9_.-]{0,63})?$")


class Refused(Exception):
    """An argument the server will not pass on."""


def _str(args, name, required=True):
    v = args.get(name)
    if v is None:
        if required:
            raise Refused(f"missing argument: {name}")
        return None
    if not isinstance(v, str):
        raise Refused(f"{name} must be a string")
    try:
        v.encode("utf-8")
    except UnicodeEncodeError:
        raise Refused(f"{name} holds text that is not valid Unicode (a lone surrogate)")
    return v


def _ident(args, name, rx, what, required=True):
    v = _str(args, name, required)
    if v is None:
        return None
    if v.lstrip().startswith("-"):
        raise Refused(f"{name} starts with '-'")
    if not rx.fullmatch(v):
        raise Refused(f"{name} {v[:60]!r} is not a valid {what}")
    return v


def _text(args, name, required=True):
    v = _str(args, name, required)
    if v is None:
        return None
    if not v.strip():
        raise Refused(f"{name} is empty")
    if v.startswith("-") or v.lstrip().startswith("-"):
        raise Refused(f"{name} starts with '-'")
    if "\x00" in v or "\n" in v or "\r" in v:
        raise Refused(f"{name} contains a NUL or newline")
    if len(v) > MAX_TEXT:
        raise Refused(f"{name} is longer than {MAX_TEXT} characters")
    return v


def _no_probes():
    """`--no-probes` unless the environment opts in (CROSSBOX_ALLOW_PROBES=1)."""
    return [] if os.environ.get("CROSSBOX_ALLOW_PROBES") == "1" else ["--no-probes"]


def b_plan(a):
    argv = ["plan", "--json"]
    top = a.get("top")
    if top is not None:
        if isinstance(top, bool) or not isinstance(top, int) or not 1 <= top <= 1000:
            raise Refused("top must be an integer from 1 to 1000")
        argv += ["--top", str(top)]
    return argv + _no_probes()


def b_ready(a):
    return ["ready", "--json"] + _no_probes()


def b_why(a):
    ref = _str(a, "ref", required=False)
    if ref is None:
        return ["why", "--json"] + _no_probes()
    if ref.lstrip().startswith("-"):
        raise Refused("ref starts with '-'")
    if not (REF.fullmatch(ref) or TASK_ID.fullmatch(ref)):
        raise Refused(f"ref {ref[:60]!r} is not a valid step ref or task id")
    return ["why", ref, "--json"] + _no_probes()


def b_activity(a):
    return ["activity", "--json"]


def b_closures(a):
    return ["closures", "--json"]


def b_show(a):
    return ["show", _ident(a, "task", TASK_ID, "task id")]


def b_claim(a):
    ref = _ident(a, "ref", REF, "step ref")
    return ["claim", _text(a, "subject"), "--step", ref]


COMMIT = re.compile(r"^(?:HEAD|[0-9a-f]{7,40})$")


def b_done(a):
    argv = ["done", _ident(a, "ref", REF, "step ref")]
    commit = _ident(a, "commit", COMMIT, "commit (HEAD or 7-40 hex digits)", required=False)
    return argv + (["--commit", commit] if commit else [])


def b_release(a):
    return ["release", _ident(a, "claim_id", CLAIM_ID, "claim id")]


def b_add(a):
    task = _ident(a, "task", TASK_ID, "task id")
    title = _text(a, "title")
    argv = ["add", task, title]
    deps = a.get("depends")
    if deps is not None:
        if not isinstance(deps, list) or len(deps) > MAX_DEPENDS:
            raise Refused(f"depends must be a list of at most {MAX_DEPENDS} step ids")
        ok = re.compile(rf"^(?:[a-z]{{1,6}}|{TASK_RE}\.[a-z]{{1,6}})$")
        for d in deps:
            if not isinstance(d, str) or not ok.fullmatch(d):
                raise Refused("depends holds a value that is not a step id or ref")
        if deps:
            argv += ["--depends", ",".join(deps)]
    return argv


def b_note(a):
    argv = ["note"]
    to = _ident(a, "to", ADDRESS, "machine or machine+slot", required=False)
    if to is not None:
        argv += ["--to", to]
    return argv + [_text(a, "message")]


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required),
            "additionalProperties": False}


_S = {"type": "string"}
TOOLS = {
    "crossbox_plan": (b_plan, "Ranked READY steps, the critical path and waves over the whole queue "
                              "(crossbox plan --json). Read only; runs no probe command, so a "
                              "probed gate reads NOT EVALUABLE.",
                      _schema({"top": {"type": "integer", "minimum": 1, "maximum": 1000}})),
    "crossbox_ready": (b_ready, "What this machine can work on now (crossbox ready --json). Read only; "
                                 "runs no probe command, so a probed gate reads NOT EVALUABLE.",
                       _schema({})),
    "crossbox_why": (b_why, "Why a step is not workable here, traced to its root blockers "
                            "(crossbox why --json); without ref, the queue-wide count of what is "
                            "workable and what holds the rest. Read only; runs no probe command.",
                     _schema({"ref": _S})),
    "crossbox_activity": (b_activity, "Is each claim's holder working? HOT/QUIET/COLD/UNKNOWN per "
                                      "active claim from commits and branches naming the step, plus "
                                      "open steps with commits but no claim (crossbox activity --json). "
                                      "Read only; advisory.",
                          _schema({})),
    "crossbox_closures": (b_closures, "Are the queue's closures true? Unreachable or missing "
                                      "commits, closing commits for open steps, cited artifacts "
                                      "missing at HEAD, unmerged remote branches naming a step "
                                      "(crossbox closures --json). Read only.",
                          _schema({})),
    "crossbox_show": (b_show, "Show one task and its steps (crossbox show). Read only.",
                      _schema({"task": _S}, ["task"])),
    "crossbox_claim": (b_claim, "Claim a step before starting it (advisory).",
                       _schema({"ref": _S, "subject": _S}, ["ref", "subject"])),
    "crossbox_done": (b_done, "Mark a step done; pass commit (HEAD or a sha) to record the commit "
                              "the closure rests on, graded against the upstream.",
                      _schema({"ref": _S, "commit": _S}, ["ref"])),
    "crossbox_release": (b_release, "Release one claim by id.",
                         _schema({"claim_id": _S}, ["claim_id"])),
    "crossbox_add": (b_add, "Add a step to a task; new work found on the way goes here.",
                     _schema({"task": _S, "title": _S,
                              "depends": {"type": "array", "items": _S, "maxItems": MAX_DEPENDS}},
                             ["task", "title"])),
    "crossbox_note": (b_note, "Leave a note for another machine or slot.",
                      _schema({"message": _S, "to": _S}, ["message"])),
}


def _clip(b):
    s = b.decode("utf-8", "replace")
    if len(s) > MAX_OUTPUT:
        s = s[:MAX_OUTPUT] + f"\n[output cut at {MAX_OUTPUT} characters]"
    return s


def _answer(text, error=False):
    return {"content": [{"type": "text", "text": text}], "isError": error}


def run_crossbox(argv):
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if not proj:
        return _answer("NOT EVALUABLE: CLAUDE_PROJECT_DIR is not set, so there is no project to look at.")
    if not os.path.isdir(proj):
        return _answer(f"NOT EVALUABLE: CLAUDE_PROJECT_DIR is not a directory: {proj[:200]}")
    if not os.path.isdir(os.path.join(proj, ".crossbox")):
        return _answer("INACTIVE: no .crossbox/ in the project directory; crossbox is not set up here.")
    exe = shutil.which("crossbox")
    if not exe:
        return _answer("NOT EVALUABLE: the `crossbox` command is not on PATH "
                       "(install it: pip install git+https://github.com/lockesec/crossbox).")
    full = [exe, "--root", proj] + argv
    try:
        cp = _run_group(full, proj)
    except subprocess.TimeoutExpired:
        return _answer(f"NOT EVALUABLE: crossbox did not answer within {TIMEOUT:g} s.")
    except (OSError, ValueError) as e:
        why = getattr(e, "strerror", None) or type(e).__name__
        return _answer(f"NOT EVALUABLE: could not run crossbox: {why}.")
    out, err = _clip(cp.stdout), _clip(cp.stderr)
    rc = cp.returncode
    crashed = "Traceback (most recent call last)" in err
    if rc in (0, 1, 2) and not crashed:
        label = {0: "ok", 1: "refused or findings (an answer, not a tool failure)",
                 2: "NOT EVALUABLE: crossbox could not look; this is not a pass"}[rc]
        error = False
    elif crashed:
        label, error = "crossbox crashed (a traceback on stderr); this is not an answer", True
    elif rc < 0:
        label, error = f"crossbox was killed by signal {-rc}; this is not an answer", True
    else:
        label, error = "unexpected exit code; this is not an answer", True
    parts = [f"exit {rc}: {label}"]
    if out.strip():
        parts.append(out.rstrip())
    if err.strip():
        parts.append("stderr:\n" + err.rstrip())
    return _answer("\n".join(parts), error)


def _run_group(argv, cwd):
    """Run argv in its own session; on timeout kill the whole process group, so
    a probe command crossbox started does not outlive the call."""
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, shell=False, cwd=cwd,
                         start_new_session=os.name == "posix")
    try:
        out, err = p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            if hasattr(os, "killpg"):
                os.killpg(p.pid, signal.SIGKILL)
            else:
                p.kill()
        except OSError:
            pass
        p.communicate()
        raise
    return subprocess.CompletedProcess(argv, p.returncode, out, err)


def call_tool(name, args):
    spec = TOOLS.get(name)
    if spec is None:
        raise KeyError(name)
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _answer("refused: arguments must be an object", True)
    unknown = sorted(set(args) - set(spec[2]["properties"]))
    if unknown:
        return _answer(f"refused: unknown argument(s): {', '.join(k[:40] for k in unknown)}", True)
    try:
        argv = spec[0](args)
    except Refused as e:
        return _answer(f"refused: {e}", True)
    return run_crossbox(argv)


def handle(msg):
    """Return a response dict, or None for a notification."""
    if not isinstance(msg, dict):
        return _err(None, -32600, "invalid request")
    mid = msg.get("id")
    method = msg.get("method")
    if "id" not in msg:
        return None   # a notification is never answered, whatever it is
    if not isinstance(method, str) or isinstance(mid, (dict, list, bool)):
        return _err(None, -32600, "invalid request")
    params = msg.get("params")
    if method == "initialize":
        want = params.get("protocolVersion") if isinstance(params, dict) else None
        ver = want if want in SUPPORTED else SUPPORTED[0]
        return _ok(mid, {"protocolVersion": ver, "capabilities": {"tools": {}},
                         "serverInfo": {"name": "crossbox", "version": _version()}})
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": [{"name": n, "description": s[1], "inputSchema": s[2]}
                                    for n, s in TOOLS.items()]})
    if method == "tools/call":
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _err(mid, -32602, "tools/call needs a tool name")
        try:
            return _ok(mid, call_tool(params["name"], params.get("arguments")))
        except KeyError:
            return _err(mid, -32602, f"unknown tool: {params['name'][:60]}")
    return _err(mid, -32601, f"method not found: {method[:60]}")


def _version():
    """The plugin's own version, from its manifest; "unknown" when unreadable."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            v = json.load(f).get("version")
        return v if isinstance(v, str) else "unknown"
    except (OSError, ValueError):
        return "unknown"


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def _send(out, obj):
    out.write((json.dumps(obj, separators=(",", ":")) + "\n").encode("utf-8"))
    out.flush()


def serve(inp, out):
    while True:
        line = inp.readline(MAX_MESSAGE + 1)
        if not line:
            return
        if len(line) > MAX_MESSAGE and not line.endswith(b"\n"):
            while True:   # drop the rest of the oversized message in bounded pieces
                rest = inp.readline(65536)
                if not rest or rest.endswith(b"\n"):
                    break
            _send(out, _err(None, -32600, "message larger than 1 MiB refused"))
            continue
        if not line.strip():
            continue
        try:
            msg = json.loads(line)
        except (ValueError, RecursionError):
            _send(out, _err(None, -32700, "parse error"))
            continue
        try:
            resp = handle(msg)
        except Exception:        # one bad call must never end the session
            mid = msg.get("id") if isinstance(msg, dict) else None
            if isinstance(mid, (dict, list, bool)):
                mid = None
            resp = _err(mid, -32603, "internal error") if (
                isinstance(msg, dict) and "id" in msg) else None
        if resp is not None:
            _send(out, resp)


if __name__ == "__main__":
    serve(sys.stdin.buffer, sys.stdout.buffer)
