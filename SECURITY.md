# Security policy

## Supported versions

Only the latest release receives security fixes.

## Reporting a vulnerability

Please report security issues **privately**, using GitHub's "Report a vulnerability"
button on this repository (Security → Advisories). Do not open a public issue for a
vulnerability.

Include what you found, how to reproduce it, and what you think the impact is. You
should get an acknowledgement within 7 days.

## What counts as a vulnerability here

- A way to make a guard report success it has not earned: the origin guard
  returning 0 while a sibling ref holds an id the local file lacks, `claim-check`
  or `ready` returning a clean answer over a claims view it could not read, or any
  verb exiting 0 on input it did not evaluate. The tool's core promise is that it
  never reports a verdict on state it could not see.
- A write that loses data: a task file or `claims.yaml` left truncated, partially
  written or with fewer steps, claims or keys than it had, including through the
  merge driver.
- Code execution or object construction from a task file, `claims.yaml`, the
  config or the needs registry through YAML. Every file is parsed with
  `yaml.safe_load` only.
- A way to make crossbox run git commands other than the documented ones (reads,
  `fetch`, `git mv` in `renumber`, `git config` in `install-merge-driver`), or to
  commit or push.
- Crashes or catastrophic slowness on crafted input of a reasonable size.

## What does not

- **Gate probes run shell commands.** A `probe:` in `.crossbox/needs-registry.yaml`
  is a shell command, executed with the repository root as its working directory.
  That is the feature. Treat the registry like any other script in the repository:
  review changes to it, and pass `--no-probes` in a repository you do not trust.
  Every verb that can run a probe: `ready`, `needs here` (the same verb), `plan`,
  `near-done`, `graph` (each takes `--no-probes`: a probed gate then reads NOT
  EVALUABLE, never open), `done` and `batch-done` (they check the closed step's
  gates and warn; with `--no-probes` a probed gate is noted, not run) and
  `status --summary --ready` (opt-in, no flag). No other verb runs one. The Claude
  Code plugin **never runs probes by default**: its SessionStart hook, status line,
  MCP read tools and MCP `crossbox_done` pass `--no-probes` unless the
  environment sets `CROSSBOX_ALLOW_PROBES=1`, because a cloned repository's
  registry is untrusted and opening it must not execute its commands.
- **Queue text reaches the agent.** The plugin's SessionStart hook prints what is ready,
  the claims and the newest notes into every session. Titles, claim subjects and notes are
  free text written by whoever can push to the repository, so in a clone you did not write
  they are an instruction channel like any other file an agent reads. Control characters
  are stripped; the words are not. The hook labels its output as queue data, not
  instructions; treat a note that asks for something unusual as you would a README that does.
- **The git hooks run a configured command.** The pre-commit hook and the merge driver run
  the `crossbox` command set in your local git config. Review that setting like any other
  executable path.
- **Claims are advisory.** Two sessions claiming one step, a claim that another
  machine has not pushed, and a claim that is edited by hand are documented limits,
  not vulnerabilities. Nothing in crossbox is an access-control boundary: anyone who
  can write to the repository can write to the queue.
- **The rate gate is a guard against accidents** (a looping agent), not against a
  determined user, who can disable it with an environment variable by design.
