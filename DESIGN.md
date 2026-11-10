# How crossbox was built

crossbox started as an internal tool. For months it coordinated several coding
agents and a human across two machines and a handful of git worktrees in a
private lab. This repository is not a copy of that tool. It is a clean-room
rebuild, put through repeated adversarial review before release. This page
explains why it was built that way and what the reviews caught.

## Contract first, code second

The rebuild began with a written behavioural contract: every verb, every exit
code, every file shape, and about eighty invariants ("`done`, `ready` and
`doctor` never disagree about whether a dependency is met"). The builder worked
only from that contract and never read the original source. A stranger reading
the contract had to be able to reproduce the behaviour, so every rule had to be
derived again from first principles.

Writing the contract was itself an audit. It turned up nine live defects in the
original tool. In one, claims minted from a worktree stored the slot name in a
form that "release mine", the overlap check and the rate-gate exemption never
matched, so sixteen claims sat stranded and unreleasable. That defect and the
rest were reproduced in the original and fixed there too. The rebuild was
designed so that none of those defect classes could exist from its first
commit.

## Design choices

- **One parser, one canonical writer.** Every mutating verb parses the whole
  file, changes the data, writes it back in one canonical YAML form, parses its
  own output again, and only then replaces the file atomically. This removes a
  whole class of line-editing bugs (a step appended at the wrong indent, silently
  swallowed into the previous step's notes). The cost is stated plainly:
  hand-written YAML comments are not preserved.
- **Absence never reads as a pass.** A fetch that failed, a ref that cannot be
  read, a damaged claims file or a gate probe with no answer exits 2 (NOT
  EVALUABLE), never 0. Counts print beside their denominators, and a count that
  could be incomplete is labelled a floor. Most of the review findings below
  belong to this one class, which is why it is a design rule and not a
  per-feature check.
- **Claims are advisory, and say so.** A file in git cannot be a lock: two
  sessions can both read "free" before either pushes. crossbox does not pretend
  otherwise. It shows the collision, names both holders, and picks a
  deterministic winner once the claims reach the upstream (see
  [docs/claims.md](docs/claims.md)). Claims are merged by identity, not by id
  string, with a merge driver whose result is idempotent and does not depend on
  merge order.
- **Guards at the places where parallel sessions meet.** The origin guard
  refuses to mint a step id that another branch or sibling worktree already
  holds, rather than quietly picking the next free letter and hiding the
  conflict. The rate gate refuses a looping agent, but never blocks an agent
  closing (`done`, `batch-done`, `skip`) work it has claimed; `reopen` stays metered.
- **0.2 kept the same rules.** `plan`, `progress`, `near-done` and `graph` read the
  files and write nothing, share one classification with `ready` instead of
  re-deriving it, print counts beside denominators, and read a missing input as
  NOT EVALUABLE (a floor, never a guessed ready). The plugin adds a hook, a guard, a
  skill, a stdio MCP server and an opt-in status line, all calling the CLI; none
  holds queue logic, and none runs a needs-registry probe unless the user opts in.
- **Small surface.** One Python package, PyYAML the only dependency. The plugin's
  MCP server is a stdio process the plugin starts; the package itself runs no server.
  Every command reads plain files and git.

## Seven rounds of adversarial review

Review rounds were run by separate AI sessions that had not written the code. Each had to reproduce
every finding with a disposable fixture and include a negative control, and each finding was pinned
to a test before it was fixed. [tests/INVARIANTS.md](tests/INVARIANTS.md) maps each one to its tests.
P1 means release-blocking, P2 significant and P3 minor.

| Round | What it found | Representative finding |
|---|---|---|
| 1 | 6 blockers | Duplicate YAML keys kept the last value, so a write could drop a whole `steps:` block |
| 2 | 2 fail-open paths | A malformed gate definition (`release: gate` instead of a mapping) turned a GATED step READY |
| 3 | 13 new (6 P1) | Two verbs running in one tree lost each other's writes and both exited 0; `reorder` moved a claim onto different work |
| 4 | 2 new (1 P1) | Deleting a claimed step and adding new work reused the letter, so the new work inherited the old claim |
| 5 | 15 new (1 P1) | With `--root` at a subdirectory, git paths were read in the wrong frame: the outer repository's claims counted as a sibling's, and the origin guard compared the wrong file and let a duplicate step id through |
| 6 | 20 new (P2 and P3, no P1) | A dependency on an archived task whose file did not parse read as met, so the dependent step was listed READY, while the step-ref form of the same dependency was correctly malformed |
| 7 | false results in `closures` and `lint`, in both directions | A commit with a skewed date hid recent closing commits from `closures`; `lint` flagged a gate on a step that was already closed |

The suite grew from 146 tests at the first review to over 1,100. The
pattern across rounds matters more than the count. Early rounds found broken
features. Later rounds found failures at the seams between correct parts: a
correct rename feeding a correct claim view, a correct writer racing another
correct writer. That shift is what the reviews were for.

## What it does not try to do

crossbox is not a lock service, an issue tracker, or a planner. Hundreds of
tasks are fine; tens of thousands are not the target. On dependency-aware
ready-work and on claims it is not ahead of [Beads](https://github.com/gastownhall/beads),
and [docs/credits.md](docs/credits.md) says so. Its narrower contribution is the origin guard, the rate
gate, capability-routed readiness, and being honest about what a file in git
can and cannot guarantee.

The roadmap lives in the tool itself: run `crossbox status` in a clone.
