# Prior art and credits

## Prior art and positioning

**[Beads]** (`bd`, MIT) is the closest tool and the more mature one: a graph issue
tracker for AI agents with dependency-aware `bd ready`, hash-based ids that avoid
merge collisions, claims, and git hooks, at a scale crossbox does not attempt.
**On dependency-aware ready work and on claims, crossbox is not ahead of Beads.**
Its claim is also documented as racy under load (issue #4657); crossbox's claims are
racy too, and say so.

What crossbox adds is narrower: a **rate gate** that refuses a looping agent; an
**origin guard** that refuses to mint an id another branch or worktree already
used; **per-machine capability gating** of readiness; **advisory claims stated as
advisory**, unioned across branches by a merge driver; and a **doctor** that checks,
repairs and self-tests the files, with pre-commit and merge-driver installers. If
you already run Beads, you probably want Beads.

Other neighbours: Taskmaster AI (a PRD into a task list, one repository),
Backlog.md (a Markdown board, no claims), git-bug (issues stored as git objects),
and issue-tracker MCP servers (server-authoritative, not repository-native).

## Credits

**Original to this project:** the origin guard and sibling-aware id minting over
worktree branches, the rate gate with its claimed-close exemption, the claims
namespace and "mine" rule, the identity-keyed, order-independent claims merge driver, capability-routed
readiness with not-evaluable probes, the canonical writer and its round-trip gate,
the doctor's repair classes and gates, and the test suite.

Developed in a private AI-experimentation environment and extracted as a standalone
tool. It builds on published research and open-source work, credited below. If you
believe your work influenced this project and isn't credited, please open an issue.
Attribution corrections are always welcome.

- **Blackboard architecture** (B. Hayes-Roth, "A blackboard architecture for
  control", *Artificial Intelligence* 26, 1985): a shared surface that independent
  agents read and write to coordinate, which is what the queue and the claim board
  are (*background*).
- **Leases and fencing tokens** (M. Burrows, "The Chubby lock service for
  loosely-coupled distributed systems", OSDI 2006): why a claim in a file can only
  be advisory, and what a hard lock would need (*context*).
- **Shared state over message passing**: influenced by C. Liu, "The Organizational
  Behavior of Agentic AI: Collective Intelligence in Human-Agent Workflows",
  [arXiv:2606.30986](https://arxiv.org/abs/2606.30986) (*influenced by*). The paper
  reports that shared-state forms of agent organisation perform better than
  human-imitation, handoff-heavy ones when they make context durable and inspectable;
  crossbox took only that general direction, keeping the claim index as a file every
  session reads instead of passing messages. The paper does not describe claims.
- **The rate gate on mutating calls**: influenced by Y. Louck, A. Stulman and A. Dvir, "Security Analysis
  of Agentic AI Communication Protocols: A Comparative Evaluation",
  [arXiv:2511.03841](https://arxiv.org/abs/2511.03841), whose availability
  findings motivate refusing a runaway writer instead of queueing it (*influenced by*).
- **[Beads]**: cited as prior art and positioned against; no code or text taken.

[Beads]: https://github.com/gastownhall/beads

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](limits.md) · [credits](credits.md)
