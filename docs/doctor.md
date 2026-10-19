# Doctor

crossbox only writes canonical YAML, so damage comes from hand edits and bad
merges. `doctor check` reports, per file, parse errors with line and column,
step markers swallowed by a block scalar, unknown statuses and bad dependencies,
each marked fixable or manual. `--staged` checks the staged copies (what
`install-hook` runs before every commit); `--archive` includes the archive.

`doctor fix [TASK] [--write] [--diff]` is a dry run unless `--write`. It repairs
four classes: step blocks at differing indents; a quoted `notes:` followed by
orphaned deeper lines; a step swallowed into the previous step's notes (only when
the swallowed marker has a one- or two-letter id and a `status:` line, so a YAML
example quoted in notes is left alone); and unknown statuses. Every fix must still
parse, keep the expected step ids in order and keep every top-level key
schema-shaped, or it is refused. A backup goes to `.doctor-bak/` first.
`doctor selftest` builds one damaged file per class, checks it really is damaged,
and repairs it.

`install-hook` finds the hooks directory with `git rev-parse --git-path hooks`, so
it works in linked worktrees and with `core.hooksPath`, and refuses to overwrite a
hook it did not write. Git runs hooks at the repository's top level, so under a
`--root` that is a subdirectory the hook carries `--root <path from the top level>`;
a top-level root keeps the bare command. `install-merge-driver` sets `merge.crossbox-claims.*` in the
local git config and confirms with `git check-attr` that `claims.yaml` maps to it.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
