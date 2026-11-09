<h1 align="center">crossbox</h1>

<p align="center"><b>A task queue that lives in your repo, for agents working in parallel</b></p>

<p align="center"><a href="https://github.com/lockesec/crossbox/actions/workflows/ci.yml"><img alt="ci" src="https://github.com/lockesec/crossbox/actions/workflows/ci.yml/badge.svg"></a> <img alt="python" src="https://img.shields.io/badge/python-3.9%20%E2%80%93%203.14-blue"> <img alt="license" src="https://img.shields.io/badge/license-Apache--2.0-green"></p>

crossbox keeps a project's tasks as YAML files in its git repository. It is for running several coding agents (or people) on one codebase at once, so they do not pick up the same work or overwrite each other's plans.

A task is a list of lettered *steps*. Before starting a step, a session takes a *claim*: a committed note that says "I am working on this". Sessions often run in separate *worktrees*: second checkouts of the same repository in their own directories (`git worktree add`). Some flags and output call a worktree a *slot*.

Designed for:

- **One session or many.** One agent or person on one checkout works. More can join through git worktrees or from other machines.
- **Git connects them.** A claim or a new step id reaches another worktree once it is committed, and another machine once it is pushed and fetched. Outside a git repository the queue itself still works, but sessions have nothing to compare.
- **Any agent that can run a command.** There is no service and no model, and the Claude Code plugin is optional.

    pip install git+https://github.com/lockesec/crossbox
    crossbox init

No service, no account, no model inside: one Python package, with PyYAML its only dependency.

## See it work

Two worktrees of one repository, `app` and `app-feature`. Step `a` is claimed in `app`, so nothing is left to start in `app-feature`. Abridged; each output line is as printed:

```console
$ crossbox claim TASK-API-001.a
claimed CLM-laptop-001 -> TASK-API-001.a
$ cd ../app-feature && crossbox ready
READY on laptop (app-feature): top 0 of 0
  CLAIMED  TASK-API-001.a: CLM-laptop-001 (laptop/main)
$ crossbox add TASK-API-001 "Document the grammar"
added TASK-API-001.d
$ cd ../app && crossbox add TASK-API-001 "Fix the lexer"
crossbox: origin guard: REFUSED: other branches hold step ids this file lacks:
  feature: d
  rebase or land first, then add (set CROSSBOX_SKIP_ORIGIN_GUARD=1 to override)
```

The second worktree sees the claim as soon as it is committed, before any push. The first worktree is refused a second "step d": the *origin guard*, which compares step ids with other branches before minting one, names the branch that holds it. `sh examples/demo.sh` plays the whole [walkthrough](docs/walkthrough.md) in a throwaway repository; CI runs it on every push. This repository tracks its own roadmap with crossbox: run `crossbox status` in a clone.

## Why

Two sessions on one repository lose track of each other fast. Both pick the same step, both
add a "step d", a looping agent rewrites the queue thirty times, and nobody can say what is in
flight. crossbox is one shared board made only of files in git, so tasks diff, review and merge
like code. A step can need a capability (`gpu`, `staging`) that only some machines have, so each
machine sees the work it can do.

## How it works

| You want to | Run | Details |
|---|---|---|
| Create a task and its steps | `crossbox new "Parser rewrite" -d api`, then `crossbox add` (`-D a`: depends on step a; `-t gpu`: needs a GPU) | [data model](docs/data-model.md) |
| See what you can start here | `crossbox ready`; `crossbox why <step>` for one that is not ready | [readiness](docs/readiness.md) |
| Pick what unblocks the most | `crossbox plan`: ranking, critical path, parallel waves | [plan](docs/plan.md) |
| Tell the others | `crossbox claim <step>`; `crossbox activity` checks claims against commits | [claims](docs/claims.md) |
| Add steps without colliding | `crossbox add` reads sibling branches and worktrees first | [guards](docs/guards.md) |
| Finish and hand off | `crossbox done <step> --commit HEAD`, `release --all-mine`, `note --to <machine>` | [closures](docs/closures.md) |
| See the whole queue | `progress`, `near-done`, `graph` (Mermaid or Graphviz) | [views](docs/views.md), [the sample queue drawn](docs/walkthrough.md#the-queue-as-a-graph) |
| Keep the files sound | `crossbox doctor`: checks, repairs, self-tests; installs the hook and merge driver | [doctor](docs/doctor.md) |

The CLI is the only writer. To set up a clone: `crossbox init`, `crossbox doctor install-merge-driver`,
`crossbox doctor install-hook` ([setup](docs/walkthrough.md#set-up-a-clone)). POSIX; CI runs Ubuntu on Python 3.9 to 3.14.

## In a Claude Code session

The repository is also a plugin marketplace. With `crossbox` on PATH, run
`claude plugin marketplace add lockesec/crossbox`, then `claude plugin install crossbox@crossbox`.

- **Context at start.** A SessionStart hook prints `ready`, claims and notes; an opt-in status line shows `cbx: 3 ready · 1 claimed by me`.
- **A guard and a skill.** A PreToolUse guard refuses Write, Edit and shell writes under `.crossbox/`, so the agent goes through the CLI. The `crossbox` skill teaches plan, claim, work, done, release, with exit 2 read as "could not look".
- **MCP tools.** A stdio MCP server exposes eleven verbs as tools, a fixed list, each run as a plain argument list with no shell. The plugin runs no needs-registry probe unless `CROSSBOX_ALLOW_PROBES=1`.

Details and limits: [docs/plugin.md](docs/plugin.md), [docs/agents.md](docs/agents.md).

## Why you can trust it

1. **Agents' claims are checked against git.** `closures`: every `done` names a reachable commit. `activity`: every claim has commits behind it. `lint`: decisions written in a step's notes that no field records, such as a gate named in prose.
2. **A write is whole or nothing.** A file is replaced only after the writer's own output parses back to the same data. A per-tree lock serialises writers: a second writer waits, then exits 2 rather than overwrite.
3. **Exit 2 means it could not look.** A failed fetch, a damaged claims file, an unreadable ref or a probe with no answer is NOT EVALUABLE (exit 2), not a clean 0. Counts print beside their denominators; a count that may be incomplete is labelled a floor.
4. **Claims merge the same way on every clone.** The merge driver matches claims by identity, not id string, and is tested idempotent and commutative, so criss-cross merges converge on one id per claim.
5. **Guards name the collision.** The origin guard names the ref and the ids. The rate gate refuses a machine past 30 writes in 10 minutes (the default), says when the window reopens, and does not count closing steps this worktree has claimed. A claim collision names both holders; the claim that reached the upstream first keeps the step.
6. **The commit is yours.** crossbox runs git reads and `fetch` (plus `git mv` in `renumber` and `git config` in the installers). It does not commit or push.
7. **Tested against a written contract.** A clean-room rebuild from a contract of 80 invariants, every finding a test before its fix: over 880 tests. [tests/INVARIANTS.md](tests/INVARIANTS.md) maps each promise to its tests.

## Exit codes

| code | meaning |
|---|---|
| 0 | ok, including a no-op |
| 1 | refused, or findings (origin guard, rate gate, claim overlap, doctor problems) |
| 2 | NOT EVALUABLE: it could not look, and the output says why |
| 3 | partial: a batch verb did some of its work and refused the rest |
| 64 | usage error, including an unknown flag |

Every verb and flag: [docs/reference.md](docs/reference.md) and [configuration](docs/configuration.md); what's in this release: [CHANGELOG.md](CHANGELOG.md).

## Where it falls short

- **Claims are advisory.** Two sessions can claim one step before either pushes; crossbox reports it, it cannot prevent it. Uncommitted work elsewhere is invisible.
- **It has one real user so far.** Its parent runs daily in the author's private project; nobody else's team has tried it.
- **Gate probes are shell commands from the repository.** In a clone you did not write, read `needs-registry.yaml` or pass `--no-probes`.

How it was checked, known holes and what is untested: [docs/limits.md](docs/limits.md).

## Prior art and credits

Developed in a private AI-experimentation environment and extracted as a standalone tool. The maintainer designed and directed it. Much of the code was written with AI coding agents, and separate AI sessions reviewed it adversarially; the tests and [DESIGN.md](DESIGN.md) show what was checked.

- **Closest tool:** [Beads] (`bd`), a graph issue tracker for agents with dependency-aware ready work, hash ids, claims and git hooks, at a larger scale. crossbox is the smaller, file-first one. Neighbours: Taskmaster AI, Backlog.md, git-bug ([more](docs/credits.md#prior-art-and-positioning)).
- **Original here:** the origin guard and sibling-aware id minting, the rate gate and its claimed-close exemption, the identity-keyed claims merge driver, capability-routed readiness, claims stated as advisory, the canonical writer and its round-trip gate, the doctor, and the test suite.
- **Influences** (what each gave: [docs/credits.md](docs/credits.md)): Hayes-Roth's blackboard architecture (1985); Burrows' Chubby paper (OSDI 2006: why a claim in a file can only be advisory); C. Liu, [arXiv:2606.30986](https://arxiv.org/abs/2606.30986) (shared, inspectable state between agents); Louck, Stulman and Dvir, [arXiv:2511.03841](https://arxiv.org/abs/2511.03841) (the rate gate); and Beads, as prior art with no code or text taken.

Attribution corrections are welcome.

## License

Apache-2.0 - see [LICENSE](LICENSE) and [NOTICE](NOTICE).

[Beads]: https://github.com/gastownhall/beads
