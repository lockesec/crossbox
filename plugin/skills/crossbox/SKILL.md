---
name: crossbox
description: Use the crossbox task queue. Triggers on: task queue, what next, what should I work on, claim a step, mark done, handoff, release a claim, crossbox. Teaches the loop plan, claim, work, done, release.
---

# The crossbox loop

crossbox is a git-native task queue that several agents, machines and git
worktrees share. The queue lives in `.crossbox/`. Use it instead of keeping the
list in your head or in TODO comments. If the project has no `.crossbox/`
directory, this skill does not apply.

You can run the `crossbox` command in a shell, or call the MCP tools
(`crossbox_plan`, `crossbox_claim`, ...) when they are available. They run the
same verbs.

## The loop

1. **Pick.** `crossbox plan` (or `crossbox ready`). Take the top of the READY
   ranking: it is the step that unblocks the most. A step listed as gated or
   waiting is not ready; do not start it.
2. **Claim.** `crossbox claim "<what you are doing>" --step <REF>` before you
   start, so another session does not take the same step. If other machines or
   worktrees share the queue, commit and push the claims file so they can see it.
   A claim is advisory: it tells others, it does not lock.
3. **Work.** If you find more work on the way, add it with
   `crossbox add <TASK> "<title>"` (use `--depends` for ordering). Never leave a
   TODO comment as the record.
4. **Finish.** `crossbox done <REF>` when the step is really done and verified.
   Then release: `crossbox release <CLAIM-ID>` (or `--all-mine`).
5. **Hand off.** For another machine or slot, `crossbox note --to <machine> "..."`.
   Read incoming notes with `crossbox notes`.

## Rules

- **Never edit files under `.crossbox/` directly.** Every write goes through the
  CLI, which checks it. A guard hook refuses direct edits; do not work around it.
- **Exit codes are part of the answer.**
  - 0: ok.
  - 1: refused, or findings (a rate gate, an origin guard, a claim overlap). Read
    the message; it says why. Do not retry blindly.
  - 2: NOT EVALUABLE. crossbox could not look (a fetch failed, a claims file is
    damaged, a ref does not resolve). That is never "clean" and never "nothing
    to do". Say so, and fix or report the cause.
- Do not mark a step done on the strength of "it ran without an error"; check
  the result the way a consumer would read it.
- Do not release or close another session's claim. `crossbox claims` lists them.
- A step that needs a capability this machine lacks (a GPU, a credential) shows
  as gated in `ready`; leave it for a machine that has it.

## Reference

`crossbox --help` lists the verbs; the full reference is `docs/reference.md` in
the crossbox repository.
