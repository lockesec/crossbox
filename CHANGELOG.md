# Changelog

All notable changes to crossbox. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.1] - 2026-11-09

- `activity` opens a claim's window at LOCAL midnight of its date, the clock the claim was stamped
  with. It used UTC midnight, so east of UTC work done right after claiming did not count until
  UTC's day caught up (and the window opened early west of UTC).
- Python 3.11 or newer is required (3.9 and 3.10 are end-of-life). CI tests 3.11 to 3.14 on `ubuntu-latest`.
### Changed
- The package metadata, NOTICE and the plugin manifests name the author, locke.

### Fixed
- The queue guard no longer refuses reads or the crossbox CLI because a quoted
  argument or a neighbouring command holds a writer word: a writer counts only in
  the simple command that names the directory, outside quotes. A directory name
  split by quotes or a backslash (`.cross"box"`) is now seen.
- `status` and `show` list a step whose `status:` is not a string as UNKNOWN STATUS
  instead of crashing; `notes` names a notes file that is not UTF-8 (exit 2) and
  shows the rest; `note` refuses to append to one.
- `closures --days`, `activity --hot-hours` and `CROSSBOX_LOCK_TIMEOUT` reject
  values they cannot use (exit 64, or exit 2 naming the variable).
- Claiming a step this slot already holds adds nothing and says which claim holds it.
- `closures` prints the day a step was closed ("closed today") instead of an hour
  count the date-only record cannot support.
- The local-path test runs outside a git checkout (an unpacked sdist).

### Changed
- The SessionStart hook labels its output as repository data, not instructions;
  SECURITY.md names queue text as an instruction channel.
- README, DESIGN, the guard and rate-gate docs match the code; `docs/plugin.md`
  describes the plugin instead of how it was assembled; `examples/claude-code/`
  (a pointer) is gone. The `test` extra adds `ijson`, and CONTRIBUTING installs it.

## [0.2.0] - unreleased

First public release. How it was hardened: [DESIGN.md](DESIGN.md).

- Task files: one YAML file per task, lettered steps with dependencies across steps and tasks.
  A canonical writer re-parses its output before replacing a file, and a per-tree lock allows one writer at a time.
- Verbs to create, edit, close (`done --commit SHA`), skip, reopen, rename, reorder, renumber, archive and check tasks and steps.
- One exit-code contract: 0 ok, 1 refused or findings, 2 NOT EVALUABLE, 3 partial, 64 usage.
- Advisory claims, unioned across worktrees and branches by identity, with a git merge driver. `activity` checks claims against commits.
- Origin guard and sibling-aware id minting, and a rate gate for looping agents.
- Readiness by machine capability (`ready`, `why`), with gates and a needs registry. A gate probe is a shell command, and `--no-probes` skips it.
- `plan` (ranking, critical path, waves), `progress`, `near-done` and `graph` (Mermaid, DOT, JSON), all read only.
- `closures` checks that each recorded closure names a reachable commit. `lint` finds decisions written in notes that no field carries.
- `doctor` checks, repairs and self-tests task files, with pre-commit hook and merge-driver installers.
- Claude Code plugin and marketplace: session hook, queue guard, skill, stdio MCP server (11 tools) and an opt-in status line.
