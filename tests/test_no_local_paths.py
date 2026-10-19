"""No tracked file names a path on the machine that built it.

pip-compile records its input file as `# via -r <path>`; run from a scratch
directory, that wrote a local absolute path into requirements-ci.txt."""
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# a person's home directory, or an AI session's scratch directory; CI's own /tmp paths are fine
LOCAL = re.compile(r"(?<![\w.])/(?:home|Users)/[\w.-]+/|/tmp/claude-|[A-Za-z]:\\Users\\")


def test_no_tracked_file_holds_a_local_absolute_path():
    files = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True,
                           check=True).stdout.split()
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
