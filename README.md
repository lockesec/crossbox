# crossbox

[![ci](https://github.com/sethpsonnier/crossbox/actions/workflows/ci.yml/badge.svg)](https://github.com/sethpsonnier/crossbox/actions/workflows/ci.yml) ![python](https://img.shields.io/badge/python-3.9%20%E2%80%93%203.14-blue) ![license](https://img.shields.io/badge/license-Apache--2.0-green)

**crossbox is a task queue that lives in your repo as YAML, for several coding agents (and people) working one codebase
across worktrees and machines.** `ready` says what you can start, `claim` tells the others, and guards refuse the
collisions parallel sessions make.

## See it work

`sh examples/demo.sh` builds a throwaway repository with two worktrees and plays the whole [walkthrough](https://github.com/sethpsonnier/crossbox/blob/main/docs/walkthrough.md); CI runs it on every push. Abridged; the output lines are as printed:

```console
$ crossbox ready
READY on laptop (main): top 1 of 1
  TASK-API-001.a         high     Write tests for the new grammar
ready 1 · gated 0 · claimed 0 · waiting 1 of 2 open step(s) workable here; 0 malformed; 1 for other machines
$ crossbox claim TASK-API-001.a
claimed CLM-laptop-001 -> TASK-API-001.a
$ cd ../app-feature && crossbox ready        # a second worktree: the claim is committed, not pushed
READY on laptop (app-feature): top 0 of 0
  CLAIMED  TASK-API-001.a: CLM-laptop-001 (laptop/main)
$ crossbox add TASK-API-001 "Document the grammar"
added TASK-API-001.d
$ cd ../app && crossbox add TASK-API-001 "Fix the lexer"     # would mint d a second time
crossbox: origin guard: REFUSED: other branches hold step ids this file lacks:
  feature: d
  rebase or land first, then add (set CROSSBOX_SKIP_ORIGIN_GUARD=1 to override)
$ CROSSBOX_MACHINE=laptop crossbox --root examples/sample-queue plan --top 2    # the bundled sample queue
NEXT on laptop (main): top 2 of 2 ready
  TASK-ML-001.a          unblocks   1  dependents   1  low                Label 2,000 sign-ups
  TASK-WEB-002.d         unblocks   0  dependents   0  normal             Write the reset email copy
```

`progress` and `near-done` count closed steps beside their totals; `graph` draws the queue in Mermaid or Graphviz
([views](https://github.com/sethpsonnier/crossbox/blob/main/docs/views.md), [the sample queue drawn](https://github.com/sethpsonnier/crossbox/blob/main/docs/walkthrough.md#the-queue-as-a-graph)). This repository tracks its own roadmap with it: `crossbox status` in a clone.

## Install

    pip install git+https://github.com/sethpsonnier/crossbox

Then, in each clone: `crossbox init`, `crossbox doctor install-merge-driver`, `crossbox doctor install-hook` ([setup](https://github.com/sethpsonnier/crossbox/blob/main/docs/walkthrough.md#set-up-a-clone)). POSIX; CI runs Ubuntu on Python 3.9 to 3.14. No service to run, no account, no model inside: one Python package, PyYAML its only dependency. (A *slot*, in some flags and output, is one git worktree of a clone.)

## Why

Two sessions on one repository lose track of each other fast: both pick the same step, both mint a "step d", a looping agent
rewrites the queue thirty times, and nobody can say what is in flight. crossbox is one shared board that is only files
in git, so tasks diff, review and merge like code. A task is one YAML file of lettered steps with dependencies.

- A committed claim shows in every worktree of the clone; other machines see it once it is pushed.
- An `add` that would mint a step id another branch already holds is refused, and the branch is named.
- A step can need a capability (`gpu`, `staging`) only some machines have, so each machine sees the work it can do; `plan` ranks it by what it unblocks.

## How it works

1. **One file per task** ([data model](https://github.com/sethpsonnier/crossbox/blob/main/docs/data-model.md)). `crossbox new "Parser rewrite" -d api -p high` creates `.crossbox/queue/TASK-API-001.yaml`;
   `crossbox add` appends lettered steps, `-D a` for a dependency on step `a`, `-t gpu` for a capability. The CLI is the only writer.
2. **Ask what you can do** ([readiness](https://github.com/sethpsonnier/crossbox/blob/main/docs/readiness.md), [plan](https://github.com/sethpsonnier/crossbox/blob/main/docs/plan.md)). `crossbox ready` sorts the open steps into READY, GATED, CLAIMED, WAITING and
   MALFORMED for *this* machine; `crossbox why <ref>` traces a step that is not READY to what holds it. `crossbox plan` ranks the READY ones by how much they unblock, then prints the critical path, the parallel waves and a full order.
3. **Claim it** ([claims](https://github.com/sethpsonnier/crossbox/blob/main/docs/claims.md)). `crossbox claim TASK-API-001.a` adds a row to `.crossbox/claims.yaml`. `crossbox activity` checks each claim against commits and branches that name its step. Readers union the claims at every worktree's
   branch, its `origin/` copy and the upstream, so a sibling worktree sees a claim once it is committed. Claims are advisory
   and say so: two sessions can claim one step before either pushes, and crossbox then names both holders.
4. **Mint without colliding** ([guards](https://github.com/sethpsonnier/crossbox/blob/main/docs/guards.md)). Before `add`, the origin guard fetches and reads the task file at every sibling ref and in the
   other worktrees' working copies. A step id they hold that your file lacks is a refusal naming the branch.
5. **Close, hand off, keep the files honest** ([doctor](https://github.com/sethpsonnier/crossbox/blob/main/docs/doctor.md)). `crossbox done` (`--commit HEAD` records the commit it rests on; `crossbox closures` checks closures are true), then `release --all-mine`; `note --to workstation` for the machine
   with the GPU. A rate gate refuses a session past 30 writes in 10 minutes, but not one closing work it has claimed.
   `crossbox doctor` checks, repairs and self-tests task files, and installs the pre-commit hook and the claims merge driver.

## In a Claude Code session

The repository is also a plugin marketplace: with `crossbox` on PATH, `claude plugin marketplace add sethpsonnier/crossbox`, then
`claude plugin install crossbox@crossbox`. A SessionStart hook then prints `ready`, claims and notes as a session opens; a PreToolUse
guard refuses Write, Edit and shell writes under `.crossbox/` so the agent goes through the CLI; the `crossbox` skill teaches plan,
claim, work, done, release, with exit 2 read as "could not look"; a stdio MCP server exposes eleven verbs as tools, a fixed allowlist
with argv-only calls; an opt-in status line shows `cbx: 3 ready · 1 claimed by me`. The plugin runs no needs-registry probe unless
`CROSSBOX_ALLOW_PROBES=1`. Details and limits: [docs/plugin.md](https://github.com/sethpsonnier/crossbox/blob/main/docs/plugin.md), [docs/agents.md](https://github.com/sethpsonnier/crossbox/blob/main/docs/agents.md).

## Why you can trust it

- **It checks the agents' claims against git.** `closures`: every `done` names a reachable commit. `activity`: every claim has commits behind it.
  `lint`: decisions written in notes that the structure does not carry.
- **A write is whole or nothing.** A file is replaced only after the writer's own output parses back to the same data,
  and a per-tree lock serialises writers: a second writer waits, then exits 2 rather than overwriting.
- **It tells you when it could not look.** A failed fetch, a damaged claims file, an unreadable ref or a probe with no answer
  exits 2 (NOT EVALUABLE), not 0; counts print beside their denominators, and a count that may be incomplete is labelled a floor.
- **Claims merge the same way on every clone.** The merge driver matches claims by identity, not id string, and is tested
  idempotent and commutative, so criss-cross merges converge on one id per claim.
- **Guards name the collision.** The origin guard names the ref and the ids; the rate gate says when the window reopens; a
  claim collision names both holders, and the claim that reached the upstream first keeps the step.
- **The commit is yours.** crossbox runs git reads and `fetch` (plus `git mv` in `renumber` and `git config` in the installers); it never commits and never pushes.
- **Reviewed.** A clean-room rebuild from a written contract of 80 invariants, then seven rounds of adversarial review, every finding a test before its fix:
  over 880 tests. [tests/INVARIANTS.md](https://github.com/sethpsonnier/crossbox/blob/main/tests/INVARIANTS.md) maps each promise to its tests; [DESIGN.md](https://github.com/sethpsonnier/crossbox/blob/main/DESIGN.md) tells how it was built and what each round found.

**Exit codes:** 0 ok, including a no-op; 1 refused, or findings (origin guard, rate gate, claim overlap, doctor problems);
2 NOT EVALUABLE, it could not look and the output says why; 3 partial, a batch verb did some of its work and refused the
rest; 64 usage error, including an unknown flag. Every verb and flag: [docs/reference.md](https://github.com/sethpsonnier/crossbox/blob/main/docs/reference.md) and [configuration](https://github.com/sethpsonnier/crossbox/blob/main/docs/configuration.md); what each version does: [CHANGELOG.md](https://github.com/sethpsonnier/crossbox/blob/main/CHANGELOG.md).

## Known limits

- **Claims are advisory.** Two sessions can claim one step before either pushes; crossbox reports it, it cannot prevent it. **Another clone's uncommitted work is invisible** until committed, pushed and fetched (sibling worktrees of this clone are read directly).
- **Comments are not preserved** in files crossbox writes; keep commentary in `notes`.
- **Gate probes are shell commands from the repository.** Read `needs-registry.yaml` in a clone you did not write, or pass `--no-probes` ([SECURITY.md](https://github.com/sethpsonnier/crossbox/blob/main/SECURITY.md)).
- **Scale:** hundreds of tasks, not tens of thousands. Full list: [docs/known-limits.md](https://github.com/sethpsonnier/crossbox/blob/main/docs/known-limits.md).

## Prior art and credits

Developed in a private AI-experimentation environment and extracted as a standalone tool. The maintainer designed and directed it; much of the code was written with AI coding agents, and adversarial review rounds were run by separate AI sessions.

[Beads] (`bd`) is the closest tool: a graph issue tracker for agents with dependency-aware ready work, hash ids, claims and git hooks, at a larger scale. crossbox is the smaller, file-first one. Neighbours: Taskmaster AI, Backlog.md, git-bug ([more](https://github.com/sethpsonnier/crossbox/blob/main/docs/credits.md#prior-art-and-positioning)). Original here: the origin guard and sibling-aware id minting, the rate gate and its claimed-close exemption, the identity-keyed claims merge driver, capability-routed readiness, claims stated as advisory, the canonical writer and its round-trip gate, the doctor, and the test suite.

Influences, with what each gave, in [docs/credits.md](https://github.com/sethpsonnier/crossbox/blob/main/docs/credits.md): Hayes-Roth's blackboard architecture (1985); Burrows' Chubby paper (OSDI 2006: why a claim in a file can only be advisory); C. Liu, [arXiv:2606.30986](https://arxiv.org/abs/2606.30986) (shared, inspectable state between agents); Louck, Stulman and Dvir, [arXiv:2511.03841](https://arxiv.org/abs/2511.03841) (the rate gate); and Beads, as prior art with no code or text taken. Attribution corrections are welcome.

## License

Apache-2.0. See [LICENSE](https://github.com/sethpsonnier/crossbox/blob/main/LICENSE) and [NOTICE](https://github.com/sethpsonnier/crossbox/blob/main/NOTICE).

[Beads]: https://github.com/gastownhall/beads
