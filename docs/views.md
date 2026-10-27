# Views: `progress`, `near-done`, `graph`

Three readers that answer "how far along is the queue?": they write nothing,
take no lock and are not metered by the rate gate. They share [`plan`](plan.md)'s
graph and [`ready`](readiness.md)'s classification and re-derive neither. The
same files always give byte-identical output. The sample outputs below are what
these commands print for [`examples/sample-queue/`](../examples/sample-queue) as
the laptop sees it; a test regenerates them and compares.

Rules that hold for all three (see [DESIGN.md](../DESIGN.md)):

- **Absence is never a pass.** A task file that does not parse or fails the
  writers' shape check is listed with its cause and every count is then a floor
  (exit 1). A queue that is missing, a claims file that is damaged or a gate
  probe that gave no answer is NOT EVALUABLE (exit 2, and `--json` prints
  `{"not_evaluable": "<why>"}` when there is nothing else to print).
- **Counts sit beside their denominators**, and a percentage is rounded down so
  only a finished task reads 100%. A task with no steps is `0/0` and `—`, never 100%.
- **Titles are untrusted.** They come from files anyone can edit. In every view a
  title or task id is printed on one line with control and bidirectional
  characters removed (line breaks and tabs become a space). The graph escapes
  further, below.

## `crossbox progress`

    crossbox progress [--by task|domain|priority] [--archive] [--json]

    $ crossbox progress
    progress (queue only; --archive adds archived tasks): closed = done + skipped; percent = closed / total steps, rounded down
    TASK-ML-001    0/2 closed    0 done    0 skipped    0%  Train the spam classifier
    TASK-WEB-001   1/4 closed    1 done    0 skipped   25%  Rate-limit the login endpoint
    TASK-WEB-002   1/4 closed    0 done    1 skipped   25%  Password reset by email
    domain ml      0/2 closed    0 done    0 skipped    0%  (1 task(s))
    domain web     2/8 closed    1 done    1 skipped   25%  (2 task(s))
    all           2/10 closed    1 done    1 skipped   20%  (3 task(s))

- `closed` is `done + skipped`, printed over the task's step count; `done` and
  `skipped` are also shown separately. The percentage is `closed / total`,
  rounded down.
- Without `--by`: one row per task, then one per domain, then `all`. With
  `--by`: that grouping only, then `all`. Priorities sort `critical`, `high`,
  `normal`, `low`, then anything else; a task with no `domain:` is grouped as `(none)`.
- The default scope is the queue and the header says so. `--archive` adds
  archived tasks (marked `(archived)`); if the archive directory does not exist
  that is NOT EVALUABLE, not "no archived tasks".
- A task file that does not parse, fails the shape check, or whose id another
  file in scope also holds (counting either would double-count) is listed as
  `UNREADABLE <id> (<file>): <cause>`, left out of every total, and the output
  ends `counts are a floor: N task file(s) unreadable`. A step whose status is
  neither done nor skipped counts as not closed.
- Exit: 0; 1 (unreadable files); 2 (queue missing, or `--archive` with no
  archive directory); 64 usage.

`--json`: `scope`, `by`, `groups` (each `kind`, `key`, `title`, `archived` (both `null` unless the group is a task),
`tasks`, `steps`, `done`, `skipped`, `closed`, `pct`; `pct` is null for no
steps), `total`, `unreadable`, `counts_are_floor`.

## `crossbox near-done`

    crossbox near-done [--max-open 2] [--min-pct 60] [--json]

Tasks that are close to closing: at least one and at most `--max-open` open
steps AND at least `--min-pct` percent closed (inclusive; `--min-pct` is 0 to
100, `--max-open` at least 1). A task with nothing left open is complete and a
task with no steps has nothing to measure: neither is listed. For each remaining
step: its state on this machine and **which configured machines could work it**
(from `machines:` capabilities; a target of `any` is every machine).

    $ crossbox near-done --max-open 3 --min-pct 25
    near-done on laptop (main): at most 3 open step(s) and at least 25% closed
    TASK-WEB-001  1/4  25%  3 open  Rate-limit the login endpoint
      .b  CLAIMED (CLM-laptop-001 (laptop/main))   workable on: any machine
      .c  elsewhere (target staging)   workable on: workstation
      .d  WAITING on TASK-WEB-001.c   workable on: any machine
    TASK-WEB-002  1/4  25%  3 open  Password reset by email
      .a  WAITING on TASK-WEB-001.b   workable on: any machine
      .c  GATED here (vendor-sdk-2 closed)   workable on: any machine
      .d  READY here   workable on: any machine
    near-done: 2 of 3 task(s) with open steps listed; 0 listed step(s) workable on no configured machine; 0 malformed or in a cycle

- Sorted by open steps ascending, then percentage descending (compared
  exactly, not rounded), then id.
- States, from `ready`'s own buckets: `READY here`, `GATED here (<gate> <state>)`,
  `CLAIMED (<claim>)`, `WAITING on <refs>`, `elsewhere (target <t>)` (this machine
  cannot work it), plus `MALFORMED (<cause>)` and `CYCLE (<refs>)` from `plan`'s
  graph, and `NOT EVALUABLE (<why>)`.
- A remaining step whose target no configured machine provides says
  `workable on: none` and is counted in the summary line; it is why the task
  cannot close.
- A malformed step puts `contains malformed steps` on its task's line. The task
  is still listed (it is near done but needs repair).
- Exit: 0; 1 (a listed step is malformed or in a cycle, or a task file is
  unreadable); 2 NOT EVALUABLE as `ready` (claims view incomplete or damaged: every
  step whose state depends on it reads `NOT EVALUABLE`; a gate probe with no
  answer; queue missing); 64 usage.

`--json`: `machine`, `slot`, `criteria`, `tasks_with_open_steps` (the denominator of
"N listed"), `tasks` (each `id`, `title`, `closed`, `total`, `open`, `pct`, `steps`
with `ref`, `id`, `title`, `state`, `detail`, `target`, `workable_on`),
`unworkable_steps`, `problem_steps`, `unreadable`, `not_evaluable`,
`counts_are_floor`. `workable_on` is the list of configured machine names, `["any"]`
for target `any`, and `[]` when none.

## `crossbox graph`

    crossbox graph [--format mermaid|dot|json] [--task ID]... [--open] [--max-nodes 150]
                     [--max-edges N] [--max-chars N] [--no-probes]

Exports the dependency graph for viewing. Mermaid output renders on GitHub when
pasted in a ```` ```mermaid ```` block; DOT renders with Graphviz. The sample queue
drawn this way is in the [walkthrough](walkthrough.md#the-queue-as-a-graph).

- **Nodes:** steps of the readable queue tasks (default all; `--open` only open
  steps; archived tasks are never drawn). `--task` (repeatable) limits to those
  tasks plus the steps they depend on directly, which are drawn as context (their
  subgraph says `(context)`, JSON `"context": true`). One subgraph per task,
  labelled with the task id and title. A task id held by two queue files is
  not drawn and is listed as malformed.
- **State** as a class: `done`, `skipped`, `ready`, `gated`, `claimed`, `waiting`,
  `elsewhere`, `malformed`, `cycle`, `not_evaluable`. Open steps take `ready`'s
  classification on this machine (`--machine` changes it); `malformed` and
  `cycle` are `plan`'s. Each class has a fixed colour and a text marker in the
  label (`✓ ⊘ ▶ ⏸ ⚑ ◌ ↗ ✖ ↻ ?`) so the state reads without colour. When the claims
  view is incomplete or `ready` could not run, steps whose state depends on it are
  `not_evaluable`, never a guessed `ready`.
- **Edges** dependency -> dependent, resolved by the same function `plan` uses (a
  task-ref dependency draws one edge from each of that task's steps). Unlike
  `plan`, an edge from a finished step is drawn too, so the default picture shows
  what the finished work unblocked; with `--open` the edges are exactly `plan`'s.
  Edges on `plan`'s critical path are drawn thick (`==>`, DOT `penwidth=3`).
- **Untrusted text.** Node and subgraph ids are generated (`n0`, `n1`, ... in
  sorted ref order; `t0`, `t1`, ...), never taken from file text. A label is
  cleaned (one line, no control or bidirectional characters), cut to 60
  characters with `…`, then escaped. Mermaid: `"` `<` `>` become `#quot;`
  `#lt;` `#gt;`, and `#` `|` `[` `]` `{` `}` backtick `&` `%` `\` `@` become `#NN;` codes, so
  no label can close its string, start a `%%` directive, a `click` or an HTML tag.
  DOT: quoted, with `\"` and `\\`. The same applies to task titles in subgraph labels. JSON titles
  are cleaned and cut but not entity-escaped.
- **Mermaid limits.** GitHub's Mermaid refuses more than 500 edges and 50,000
  characters of text. For `--format mermaid` the defaults are `--max-edges 500` and
  `--max-chars 50000`; over either the export refuses (exit 1), prints nothing and
  names the numbers. `dot` and `json` have no default edge or character limit (the
  flags still apply if given). A task-ref dependency on an N-step task is N edges.
- **Probes.** `plan`, `near-done` and `graph` take `--no-probes` (as `ready` does): a
  probed gate reads NOT EVALUABLE ("probes disabled"), exit 2.
- **Size.** Over `--max-nodes` the export refuses with exit 1 and prints nothing
  on stdout; stderr names the node count and suggests `--task` or `--open`. It
  never silently drops nodes.
- Messages (malformed steps, cycles, NOT EVALUABLE, an empty selection) go to
  stderr so stdout stays a graph you can pipe.
- `--format json`: `nodes` (`id`, `ref`, `task`, `title`, `state`, `detail`,
  `context`), `edges` (`[from_ref, to_ref]`), `critical_path` (the whole path,
  as refs), plus `machine`, `slot`, `scope`, `counts` (`nodes` beside
  `queue_steps`), `malformed`, `cycles`, `not_evaluable`.
- Exit: 0; 1 (a drawn step is malformed or in a cycle, a task file is unreadable
  or duplicated, or the size refusal); 2 NOT EVALUABLE as `ready` (wins over 1);
  64 usage, including a `--task` that is not in the queue.

## Tests

`tests/test_views.py` and `tests/test_graph.py`; invariants V1 to V23 in
[tests/INVARIANTS.md](../tests/INVARIANTS.md). The graph tests check every output
line against the exact shapes the renderer emits (so file text cannot start a
line, a directive or an id), a hostile-title list (quote and bracket
breakouts, `</script>`, `%%{init}`, backticks, `|`, newlines, NUL and other
control characters, a 10 KB title, U+202E), and that the walkthrough's graph is
what `crossbox graph` prints today.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](known-limits.md) · [credits](credits.md)
