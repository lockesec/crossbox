# closures

`crossbox done --commit SHA` lets a closure name its commit. `crossbox closures
[--json] [--days N]` checks that the queue's closures are true. It reads git and the
queue, writes nothing and moves no ref.

Each class prints its count beside its denominator.

| class | rows | denominator |
|---|---|---|
| unevidenced | done steps with no `commits:` recorded. Informational, not a failure. A step with a recorded commit is "evidenced" whether or not that commit names the step: `done --commit` warns at the time when it does not, and `closures` does not repeat the warning | done steps |
| unreachable | a recorded commit not reachable from the baseline (`UNMERGED`), or that no longer exists (`MISSING`: rebased away or collected) | recorded commits |
| claimed closed but open | an open step that a commit says it closes | open steps |
| artifact missing | a done step whose notes cite a repository path in backticks that is absent at `HEAD` | cited paths |
| unmerged branches | remote-tracking branches with commits the baseline lacks, listed only when their name or commits name a step | all unmerged remote branches (the total of remote branches is printed too) |

The baseline is the one `done --commit` grades against: the configured upstream, else
a local `main` or `master`.

## What counts as "closes" (claimed closed but open)

A commit within `--days` (default 30) on any local or remote-tracking branch flags an
open step when its subject has one of these verbs at the start of a sentence,
immediately followed by the step ref as a whole token (so `.a` never matches `.ab`):

`close`, `closes`, `closed`, `done`, `fix`, `fixes`, `fixed`, `finish`, `finishes`,
`finished`, `complete`, `completes`, `completed` (any case).

Sentence start means the start of the subject, the start after a conventional-commit
`type(scope): ` prefix, or after `. ` or `; `. A `Crossbox-Step: REF` trailer also
flags the step when the subject has such a verb at a sentence start (whatever follows).

It matches `close TASK-A-001.a`, `fixes TASK-A-001.a: parser`, `feat(x): done
TASK-A-001.a`. It does not match a mention: `start TASK-A-001.a`, `TASK-A-001.a: wip`,
`close the TASK-A-001.a gap`, `TASK-A-001.a done`, or `close TASK-A-001.ab`. A step that
was closed by that commit and then deliberately reopened shows here until the commit
leaves the window; the row names the commit so you can tell.

Artifact paths are backticked strings that name a file with a known extension (py, md,
yaml, yml, json, toml, txt, sh, js, ts, rs, go, cfg, ini, sql, html, css, c, h, rb, java)
or a directory with a trailing `/`. A word pair with a slash (`and/or`, `read/write`) is
prose, not a path; a directory cited without its trailing slash is not checked.
URLs, absolute paths, globs and anything with spaces are skipped; a trailing `:12` is
dropped.

## Exit codes

0 when unreachable, claimed-closed-but-open, artifact missing and unmerged branches
are all empty. 1 when any has rows. 2 NOT EVALUABLE when git cannot be read, when
there is no baseline (unreachable and unmerged branches), or when the queue is
missing; the output names each class that could not run, and the others still report.
Exit 2 outranks 1. The commit scan stops at 50000 commits; the count is then labelled
a floor.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](limits.md) · [credits](credits.md)
