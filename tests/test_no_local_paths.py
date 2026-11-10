"""No tracked file names a path on the machine that built it.

pip-compile records its input file as `# via -r <path>`; run from a scratch
directory, that wrote a local absolute path into requirements-ci.txt."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# a person's home directory, or an AI session's scratch directory; CI's own /tmp paths are fine
LOCAL = re.compile(r"(?<![\w.])/(?:home|Users)/[\w.-]+/|/tmp/claude-|[A-Za-z]:\\Users\\")


_NOT_SHIPPED = {"build", "dist", "__pycache__"}


def _files():
    """Tracked files in a checkout; outside one (an unpacked sdist or a zip download) every
    file on disk except environments, caches and build output, so the check still runs."""
    r = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if r.returncode == 0 and (ROOT / ".git").exists():
        return r.stdout.split()
    return [str(p.relative_to(ROOT)) for p in ROOT.rglob("*")
            if p.is_file() and not any(part.startswith(".") or part in _NOT_SHIPPED
                                       or part.endswith(".egg-info")
                                       for part in p.relative_to(ROOT).parts[:-1])]


def test_the_file_list_works_outside_a_git_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 128, "", ""))
    files = _files()
    assert "README.md" in files and not any(f.startswith(".git/") for f in files)


def test_no_tracked_file_holds_a_local_absolute_path():
    files = _files()
    hits = []
    for f in files:
        p = ROOT / f
        if f == "tests/test_no_local_paths.py" or not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if LOCAL.search(line):
                hits.append(f"{f}:{n}: {line.strip()[:100]}")
    assert not hits, "\n".join(hits)
