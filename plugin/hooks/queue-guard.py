#!/usr/bin/env python3
"""Claude Code PreToolUse hook: keep agents on the crossbox CLI.

Refuses direct writes to anything under the crossbox directory (task files,
claims.yaml, notes, config and the needs registry, whose probes are shell
commands), so every write goes through the tool's round-trip gate, rate gate
and origin guard. Reads are not targeted: a writing command counts only in the
same simple command (split on ; && || | and newlines) that names the directory,
and a word inside quotes is text, not a command, so `crossbox note 'do not rm
.crossbox'` and `cat .crossbox/x | tee /dev/null` pass.

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
directory): elsewhere it allows everything. The cwd is not walked up to a
repository root: with CLAUDE_PROJECT_DIR unset and a cwd in a subdirectory,
the guard is inactive, so set CLAUDE_PROJECT_DIR (Claude Code does).

It is a best-effort nudge, not a sandbox: a shell command that hides the path
(a script file, a variable, an alias, a glob such as `.cross*`, a `cd` to a
computed path, `xargs`, `env` or `bash -c` / `sh -c` around a command), or uses
a writer that is not in the list above (`curl -o`, `wget -O`, `tar -x`,
`unzip`, `awk -i inplace`, `ed`, `mkdir`, ...) or puts more than 16 words between a command and
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
# `>`, `>>`, `>|`, `&>`, `>&` (bash: `>&file` is `>file 2>&1`) and `N>`
REDIRECT = re.compile(r"(?:[0-9&]?>>?[|&]?)\s*(['\"]?)([^\s'\";&|]+)\1")


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


def unquote(s: str) -> str:
    """What the shell sees after quote removal: `.cross"box"` and `.cross\\box` are `.crossbox`."""
    return re.sub(r"[\"'\\\\]", "", s)


def segments(command: str) -> list:
    """The simple commands of ``command`` as (raw, bare) pairs. Splits on ; & | and newlines
    outside quotes; ``bare`` blanks every quoted span, so a quoted word is not a command. A
    heredoc keeps the rest of the command with the command that reads it, unblanked:
    a command after the heredoc ends is still seen."""
    out, raw, bare, q, i = [], [], [], "", 0
    while i < len(command):
        c = command[i]
        if q:
            raw.append(c)
            bare.append(" ")
            if c == "\\" and q == '"' and i + 1 < len(command):
                raw.append(command[i + 1])
                bare.append(" ")
                i += 1
            elif c == q:
                q = ""
        elif c in "'\"":
            q = c
            raw.append(c)
            bare.append(" ")
        elif c == "\\" and i + 1 < len(command):
            raw.append(command[i:i + 2])
            bare.append("  ")
            i += 1
        elif command.startswith("<<", i):
            raw.append(command[i:])
            bare.append(command[i:])      # the body and anything after it stay visible
            break
        elif c in ";&|\n":
            out.append(("".join(raw), "".join(bare)))
            raw, bare = [], []
        else:
            raw.append(c)
            bare.append(c)
        i += 1
    out.append(("".join(raw), "".join(bare)))
    return [s for s in out if s[0].strip()]


def writes_into(command: str, d: str, cwd: str = "") -> bool:
    """A simple command that names ``d`` (or runs after a `cd` into it, or in a cwd inside it)
    and has a writing command outside its quoted text."""
    inside = bool(cwd and under(cwd, d))
    for raw, bare in segments(command):
        if (inside or names_dir(unquote(raw), d)) and WRITERS.search(bare):
            return True
        if any(under(m.group(2), d) for m in CD.finditer(unquote(raw))):
            inside = True
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
        if redirects_into(cmd, d, cwd) or redirects_into(unquote(cmd), d, cwd):
            return f"do not redirect into {d}/; use the crossbox CLI"
        if writes_into(cmd, d, cwd):
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
