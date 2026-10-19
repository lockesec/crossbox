# Contributing

Thanks for helping. Three rules keep this tool honest.

**1. Every behaviour change ships with a test that fails without it.**
Each rule the tool enforces has a test named after the rule, and
[tests/INVARIANTS.md](tests/INVARIANTS.md) maps the numbered invariants to those
tests. A new guard also needs its negative control: show the guard refusing the bad
case *and* staying quiet on the nearby good case (the merge-driver test has a twin
that proves the same merge conflicts without the driver). If a guard cannot be made
to fire in a test, it is not a guard yet.

**2. Absence is never success.**
When crossbox cannot look (a ref does not resolve, a fetch fails, a file does not
parse), the answer is exit 2 or a stated floor, never 0 and never a small plausible
count. Every count is printed with its denominator. Keep `--json` output clean from
byte 0; warnings go to stderr.

**3. crossbox is the only writer, and it never commits.**
All task-file writes go through `model.write_task` (canonical dump, round-trip gate,
atomic replace). Git stays read-only apart from `git fetch`, `git mv` in `renumber`
and `git config` in the merge-driver installer. Do not add a code path that edits
YAML text line by line; that is the defect class the tool exists to remove.

Fixtures are synthetic and built in `tmp_path`: real throwaway git repositories (a
bare "origin", clones standing in for machines, linked worktrees, which crossbox calls slots). Use
neutral machine names (`laptop`, `workstation`, `ci`) and no real addresses.
Each example directory carries a `SYNTHETIC-DATA` file stating this; keep it, and add one
to any new example directory.

Run the tests without installing the package:

```console
python -m pip install pytest pyyaml
python -m pytest
```

PyYAML is the only runtime dependency, used through `safe_load`/`safe_dump` only, and
it should stay that way. Python 3.9 is the floor.

If a contribution adapts someone else's code or design, name the source in the
module's header comment and in the README's Credits section, and make sure its
license allows it.
