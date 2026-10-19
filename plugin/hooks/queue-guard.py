#!/usr/bin/env python3
"""Claude Code PreToolUse hook: keep agents on the crossbox CLI.

Refuses direct writes to anything under the crossbox directory (task files,
claims.yaml, notes, config and the needs registry, whose probes are shell
commands), so every write goes through the tool's round-trip gate, rate gate
and origin guard. Reads are never blocked.

What it covers:
  * Write, Edit, MultiEdit and NotebookEdit on a path under the directory;
  * Bash commands that redirect into the directory, or that name it together
    with a writing command: tee, sed -i, perl -i, cp, mv, rm, ln, install,
    truncate, dd, touch, chmod, chown, rsync, unlink, shred, patch, sponge,
    an editor (vi, vim, nvim, nano), find ... -delete/-exec, git (also
    `git -C DIR`) checkout/restore/apply/stash/reset/clean/rm/mv, or an
    interpreter run with -c/-e or fed a heredoc (python, perl, ruby, node);
  * a `cd`/`pushd` into the directory: a relative redirect after it is a
    redirect into the directory, and so is any redirect in a command whose
    working directory (the event's `cwd`) is already inside it.
Paths are compared case-insensitively (macOS and Windows file systems are),
with backslashes and `..` normalised.

It is active only in a project that has the directory at its root (the
project is CLAUDE_PROJECT_DIR, else the event's cwd, else the current
directory): elsewhere it allows everything.

It is a best-effort nudge, not a sandbox: a shell command that hides the path
(a script file, a variable, an alias, a `cd` to a computed path, `xargs`, `env`
or `bash -c` around a command) or puts more than 16 words between a command and
its flag can still get past a pattern match. Pair it with an integrity check of
the hook surface.

The plugin registers it (plugin/hooks/hooks.json). To use it without the
plugin, register in .claude/settings.json:
  "hooks": {"PreToolUse": [{"matcher": "Write|Edit|MultiEdit|NotebookEdit|Bash",
            "hooks": [{"type": "command",
                       "command": "python3 /path/to/queue-guard.py"}]}]}

If the crossbox directory is not `.crossbox` (config `dir:`), pass it:
`... queue-guard.py --dir tasks/cb`, or set CROSSBOX_GUARD_DIR.
"""
import json
import os
import posixpath
import re
import sys

WRITERS = re.compile(
    r"(^|[\s;&|(`])("
    r"tee|cp|mv|rm|ln|install|truncate|dd|touch|chmod|chown|rsync"
    r"|unlink|shred|patch|sponge|vi|vim|nvim|nano"
    # the word groups are bounded: an unbounded `(\S+\s+)*` is retried at every
    # `sed`, quadratic on a long command, and a hook that times out checks nothing
    r"|sed\s+(\S+\s+){0,16}-[a-zA-Z]*i\S*"
    r"|perl\s+(\S+\s+){0,16}-[a-zA-Z]*i\S*"
    r"|find\s+(\S+\s+){0,16}-(delete|exec|execdir|ok|okdir)"
    r"|git\s+(-C\s+\S+\s+|-c\s+\S+\s+|--[a-z-]+(=\S+)?\s+){0,4}"
    r"(checkout|restore|apply|stash|reset|clean|rm|mv)"
    r"|(python[0-9.]*|perl|ruby|node)\s+(-[a-zA-Z]+\s+){0,16}-[a-zA-Z]*[ce]"
    r"|(python[0-9.]*|perl|ruby|node)\s+(-\S*\s+){0,16}<<\S*"
    r")(\s|$)")

CD = re.compile(r"(?:^|[\s;&|(`])(?:cd|pushd)\s+(?:--\s+)?(['\"]?)([^\s'\";&|)]+)\1")
REDIRECT = re.compile(r"(?:[0-9&]?>>?\|?)\s*(['\"]?)([^\s'\";&|]+)\1")


def norm(p) -> str:
    p = str(p).replace("\\", "/")
    if p:
        p = posixpath.normpath(p)
    return p.casefold()


def guarded_dir(argv) -> str:
    d = os.environ.get("CROSSBOX_GUARD_DIR") or ".crossbox"
    if "--dir" in argv:
        i = argv.index("--dir")
        if i + 1 < len(argv):
            d = argv[i + 1]
    return norm(d).strip("/")


def under(path, d: str) -> bool:
    p = norm(path)
    return p == d or p.startswith(d + "/") or f"/{d}/" in f"/{p}/"


def names_dir(command: str, d: str) -> bool:
    c = command.replace("\\", "/").casefold()
    return re.search(rf"(^|[\s'\"=/:(]){re.escape(d)}(/|[\s'\";&|)]|$)", c) is not None


def project_root(event: dict) -> str:
    return (os.environ.get("CLAUDE_PROJECT_DIR")
            or (event.get("cwd") if isinstance(event.get("cwd"), str) else "")
            or os.getcwd())


def active(event: dict, d: str) -> bool:
    """The guard acts only in a project that has the directory at its root."""
    try:
        return os.path.isdir(os.path.join(project_root(event), d))
    except (OSError, ValueError):
        return False


def redirects_into(command: str, d: str, cwd: str = "") -> bool:
    """A redirect whose target is under ``d``: written as such, or relative to a
    directory the command ``cd``s into, or relative to a ``cwd`` that is
    itself inside ``d`` (the event's working directory)."""
    bases = [m.group(2) for m in CD.finditer(command) if under(m.group(2), d)]
    if cwd and under(cwd, d):
        bases.append(d)
    for m in REDIRECT.finditer(command):
        target = m.group(2)
        if under(target, d):
            return True
        if target.startswith(("/", "~", "$", "&")):
            continue
        if any(under(posixpath.join(base, target), d) for base in bases):
            return True
    return False


def decide(event: dict, d: str):
    tool = event.get("tool_name", "")
    inp = event.get("tool_input") or {}
    if not isinstance(inp, dict):
        return None
    if tool in ("Write", "Edit", "MultiEdit", "NotebookEdit"):
        path = inp.get("file_path") or inp.get("notebook_path") or ""
        if path and under(path, d):
            return "crossbox files are written by the crossbox CLI only"
    elif tool == "Bash":
        cmd = str(inp.get("command", ""))
        cwd = event.get("cwd") if isinstance(event.get("cwd"), str) else ""
        if redirects_into(cmd, d, cwd):
            return f"do not redirect into {d}/; use the crossbox CLI"
        if (names_dir(cmd, d) or (cwd and under(cwd, d))) and WRITERS.search(cmd):
            return f"do not modify {d}/ from the shell; use the crossbox CLI"
    return None


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0  # not our business; never block on a malformed event
    if not isinstance(event, dict):
        return 0
    d = guarded_dir(argv)
    if not active(event, d):
        return 0
    why = decide(event, d)
    if why:
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse", "permissionDecision": "deny",
            "permissionDecisionReason": why + " (e.g. `crossbox edit-step REF --append-notes ...`)"}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
