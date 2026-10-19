#!/usr/bin/env python3
"""One short status line for the current slot, from `crossbox ready --json`.

Silent (no output) when the project has no .crossbox/. Prints `cbx: ?` when
crossbox could not look (exit 2, unreadable output, timeout). The answer is
cached in the repository's own git dir, one file per project root and machine,
so a status line that refreshes every few hundred milliseconds starts at most
one crossbox run per TTL (default 30 s, env CROSSBOX_STATUS_TTL; 0 turns the
cache off). A cache file is used only when 0 <= age < TTL (a modification time
in the future is stale, not eternal).

It runs `ready --json --no-probes`: it needs only the counts, and a cloned
repository's needs-registry probe commands must not run just because the
project was opened. Set CROSSBOX_ALLOW_PROBES=1 to let them run. A timeout
kills crossbox's whole process group. stdlib only, Python 3.9+.
"""
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time


def project_dir():
    d = os.environ.get("CLAUDE_PROJECT_DIR")
    if d:
        return d
    try:
        if not sys.stdin.isatty():
            ev = json.loads(sys.stdin.read(65536) or "{}")
            if isinstance(ev, dict):
                ws = ev.get("workspace")
                for v in ((ws or {}).get("current_dir") if isinstance(ws, dict) else None,
                          ev.get("cwd")):
                    if isinstance(v, str) and v:
                        return v
    except (ValueError, OSError):
        pass
    return os.getcwd()


def git(root, *args):
    try:
        cp = subprocess.run(["git", "-C", root, *args], capture_output=True, text=True,
                            timeout=5, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    return cp.stdout.strip() if cp.returncode == 0 else None


def render(rc, out):
    try:
        data = json.loads(out)
    except ValueError:
        return "cbx: ?"
    if rc not in (0, 1) or not isinstance(data, dict) or not isinstance(data.get("ready"), list):
        return "cbx: ?"
    me = f"({data.get('machine')}/{data.get('slot')})"
    claimed = data.get("claimed") or []
    mine = sum(1 for c in claimed if isinstance(c, dict) and
               any(isinstance(x, str) and x.endswith(me) for x in c.get("claims") or []))
    line = f"cbx: {len(data['ready'])} ready · {mine} claimed by me"
    if rc == 1:
        # exit 1 is findings, not necessarily a skipped file: say which, from the JSON
        mal, orph = data.get("malformed"), data.get("orphaned_claims")
        why = []
        if isinstance(mal, list) and mal:
            why.append(f"{len(mal)} malformed")
        if isinstance(orph, list) and orph:
            why.append(f"{len(orph)} orphaned claim(s)")
        line += f" (partial: {', '.join(why) or 'see `crossbox ready`'})"   # a floor
    return line


def run_group(argv, cwd, timeout):
    """subprocess.run with a timeout that kills the whole process group."""
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True, cwd=cwd,
                         start_new_session=os.name == "posix")
    try:
        out, _err = p.communicate(timeout=timeout)
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
    return p.returncode, out


def cache_path(gitdir, root):
    """One cache file per (project root, machine): two roots in one repository,
    or another CROSSBOX_MACHINE, never show each other's numbers."""
    key = "\0".join((os.path.realpath(root), os.environ.get("CROSSBOX_MACHINE", ""),
                     os.environ.get("CROSSBOX_ALLOW_PROBES", "")))
    return os.path.join(gitdir, "crossbox-status-"
                        + hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:16]
                        + ".cache")


def main():
    root = project_dir()
    if not os.path.isdir(os.path.join(root, ".crossbox")):
        top = git(root, "rev-parse", "--show-toplevel")
        if not top or not os.path.isdir(os.path.join(top, ".crossbox")):
            return 0
        root = top
    if not shutil.which("crossbox"):
        print("cbx: crossbox not installed")
        return 0
    try:
        ttl = float(os.environ.get("CROSSBOX_STATUS_TTL", "30"))
    except ValueError:
        ttl = 30.0
    gitdir = git(root, "rev-parse", "--absolute-git-dir")
    cache = cache_path(gitdir, root) if gitdir and ttl > 0 else None
    if cache:
        try:
            if 0 <= time.time() - os.path.getmtime(cache) < ttl:
                with open(cache, encoding="utf-8") as f:
                    line = f.read(500).strip()
                if line:
                    print(line)
                    return 0
        except OSError:
            pass
    try:
        argv = ["crossbox", "--root", root, "ready", "--json"]
        if os.environ.get("CROSSBOX_ALLOW_PROBES") != "1":
            argv.append("--no-probes")
        rc, out = run_group(argv, root, 10)
        line = render(rc, out)
    except (OSError, ValueError, subprocess.SubprocessError):
        line = "cbx: ?"
    if cache:
        try:
            tmp = f"{cache}.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(line + "\n")
            os.replace(tmp, cache)
        except OSError:
            pass
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
