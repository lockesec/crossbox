# The Claude Code plugin

The repository is also a Claude Code plugin marketplace with one plugin,
`crossbox`. It makes an agent use the queue without being told to each session.

    claude plugin marketplace add lockesec/crossbox
    claude plugin install crossbox@crossbox

(Inside a session, `/plugin marketplace add lockesec/crossbox`, then
`/plugin` opens the plugin's panel to install it.) The marketplace `name` and
the plugin `name` are both `crossbox`; they must match the manifest.

The plugin needs the `crossbox` command on PATH (`pip install git+...`). When
it is missing, every part of the plugin says so in one line and does nothing
else; it never fails a session or a tool call because crossbox is absent. "Says so"
is deliberate: a missing command is NOT EVALUABLE, never a silent empty queue.

## Layout

    .claude-plugin/marketplace.json     the marketplace: one plugin, source ./plugin
    plugin/.claude-plugin/plugin.json   name, version (= the package version), description
    plugin/hooks/hooks.json             SessionStart + PreToolUse, both via ${CLAUDE_PLUGIN_ROOT}
    plugin/hooks/session-start.sh       prints what is ready, your claims and new notes at session start
    plugin/hooks/queue-guard.py         refuses direct edits under .crossbox/ (see agents.md)
    plugin/skills/crossbox/SKILL.md     the loop
    plugin/.mcp.json                    the MCP server
    plugin/mcp/crossbox_mcp.py          stdio MCP server, stdlib only
    plugin/statusline/crossbox-status.sh     sh entry for the status line
    plugin/statusline/crossbox_status.py     the logic (stdlib, Python 3.11+)

Nothing is active outside a repository that has a `.crossbox/` directory: the
hooks (the queue guard included: it allows everything in a project that does not
hold the guarded directory at its root), the status line and the MCP tools check
first and stay silent (hooks exit 0 with no output; MCP tools answer `INACTIVE:
no .crossbox/ ...`). The project is `CLAUDE_PROJECT_DIR` (hooks fall back to the
event's or the current directory).

**The plugin never runs probes by default.** A cloned repository's
`.crossbox/needs-registry.yaml` is untrusted: its `probe:` entries are shell
commands, and with the plugin installed, opening the repository must not execute
them. The session-start hook (`ready --no-probes`), the status line
(`ready --json --no-probes`) and the MCP `crossbox_ready` / `crossbox_plan` /
`crossbox_why` / `crossbox_done` tools
pass `--no-probes` unless the environment has `CROSSBOX_ALLOW_PROBES=1`; a probed
gate then reads NOT EVALUABLE ("probes disabled"). The session hook also passes
`--root` for the project directory, so a project in a subdirectory shows its own
queue. The status line's cache has one file per project root and machine and is
used only while `0 <= age < TTL`.

## a. Hooks

Two hooks, wired through hooks.json as `sh "${CLAUDE_PLUGIN_ROOT}/hooks/session-start.sh"`
and `python3 "${CLAUDE_PLUGIN_ROOT}/hooks/queue-guard.py"` (run through the
interpreter, so no executable bit has to survive the install). Both are
best-effort nudges, not a sandbox.

- The queue guard refuses Write/Edit on a path under `.crossbox/` and shell commands
  that write there (the list of writers and the known limits are in
  [agents.md](agents.md)). It acts only in a project that holds the directory at its root.
- The session hook goes quiet outside a `.crossbox/` project, passes `--root` for the
  project directory, passes `--no-probes` unless `CROSSBOX_ALLOW_PROBES=1`, and
  inside a project with no `crossbox` command prints a single `NOT EVALUABLE` line.

## b. Skill: the loop

`SKILL.md` teaches one loop and when to use it:

1. `crossbox plan` (or `ready`) to pick a step; prefer the top of `next`.
2. `crossbox claim "<subject>" --step <ref>` before starting; commit and push
   the claims file if other machines or worktrees share the queue.
3. Do the work. New work found on the way: `crossbox add`, never a TODO comment.
4. `crossbox done <ref>`; `crossbox release <claim>` (or `--all-mine`).
5. Exit codes are part of the answer: 2 means "could not look", never "clean".
   Never edit files under `.crossbox/` directly.

Its description triggers on: task queue, what next, claim, mark done, handoff,
crossbox. Under 150 lines.

## c. MCP tool

One stdio MCP server (JSON-RPC 2.0, protocol `initialize`, `tools/list`,
`tools/call`), Python 3.11+, no dependencies. Tools:

| tool | runs | arguments |
|---|---|---|
| `crossbox_plan` | `crossbox plan --json` | `top` (optional int) |
| `crossbox_ready` | `crossbox ready --json` | none |
| `crossbox_why` | `crossbox why [<ref>] --json` | `ref` (optional; a step ref or task id) |
| `crossbox_activity` | `crossbox activity --json` | none |
| `crossbox_closures` | `crossbox closures --json` | none |
| `crossbox_show` | `crossbox show <task>` | `task` (a task id; `show` takes no step ref) |
| `crossbox_claim` | `crossbox claim <subject> --step <ref>` | `ref`, `subject` |
| `crossbox_done` | `crossbox done <ref> [--commit <sha>]` | `ref`, `commit` (optional: `HEAD` or 7-40 hex digits) |
| `crossbox_release` | `crossbox release <claim-id>` | `claim_id` |
| `crossbox_add` | `crossbox add <task> <title> [--depends ...]` | `task`, `title`, `depends` (optional list) |
| `crossbox_note` | `crossbox note <message>` | `message`, `to` (optional) |

Rules:
- **Allowlist.** Only these verbs. No tool takes a free-form command, flag list
  or path.
- **argv only.** Every call is `subprocess.run([...], shell=False)` with a fixed
  argv; argument values are passed as separate argv elements, never joined into
  a string. A value that starts with `-` is refused (it would be read as a flag).
  Refs, task ids and claim ids are checked against the id grammar before the
  call; titles, subjects and messages are capped at 2000 characters and refused
  if they contain a NUL, CR or LF. Ids are checked with a wide regex in
  the server, not the package's `Grammar`: the real prefix lives in the project's
  config, which the server never reads, and it must run with only the `crossbox`
  command installed (no import). The regex accepts any prefix without control
  characters that does not start with `-` or a blank (the package accepts any
  non-empty prefix, including `_`, `-` and more than 16 characters; only a prefix
  with a leading blank or `-` is CLI-only). crossbox validates again against the
  real config.
- **Text that cannot be encoded** (a lone surrogate such as JSON `\ud800`) is
  refused like any bad value, and no exception can end the server: a failure
  while handling a request is answered `-32603 internal error` and the server
  keeps serving.
- **Only exit codes 0, 1 and 2 are answers.** Any other exit code (64, 3, ...), a
  death by signal and a Python traceback on stderr are `isError: true` with the
  output attached; they are never reported as an answer.
- **A timeout kills the whole process group** (MCP 60 s, status line 10 s), so a
  probe command crossbox started does not outlive the call. A refused value is a tool error (`isError: true`, text `refused: ...`)
  and nothing is run; unknown argument names are refused the same way.
- **Project directory** comes from `CLAUDE_PROJECT_DIR` (Claude Code exports
  it to stdio servers; the server's cwd is not documented, so it is not relied
  on). It is passed as `--root`; the model can never supply a root or path.
  `CLAUDE_PROJECT_DIR` unset or not a directory -> NOT EVALUABLE; a directory
  without `.crossbox/` -> `INACTIVE`. Neither runs anything.
- **Message size.** One JSON-RPC message is one line. A line over 1 MiB is
  refused with -32600 (id null) without being held whole, and the rest of it is
  discarded in bounded reads; the server keeps serving. Malformed JSON is -32700.
  Only object messages are accepted (no batches).
- **Protocol.** Answers `initialize` by echoing the client's `protocolVersion`
  when it is one the server supports (`2025-11-25`, `2025-06-18`,
  `2025-03-26`), else `2025-11-25`; accepts `notifications/initialized` and
  any other notification without replying; unknown methods get JSON-RPC
  error -32601; `tools/call` for an unknown tool is -32602; `ping` is answered.
- **Exit codes pass through.** The tool result carries the exit code and both
  streams; exit 1 and 2 are not errors of the tool, they are answers, and the
  text says which (`NOT EVALUABLE` for 2). A timeout (default 60 s) is reported
  as NOT EVALUABLE (env `CROSSBOX_MCP_TIMEOUT` overrides the 60 s, for tests). A
  missing `crossbox` command is one NOT EVALUABLE line, also an answer. Each
  stream is cut at 64 KiB.
- **Write verbs are still rate-gated** by crossbox itself; the server adds no
  gate of its own and does not retry.

## d. Status line

`crossbox-status.sh` prints one short line for the current slot, e.g.
`cbx: 3 ready · 1 claimed by me`, from `crossbox ready --json`. "Claimed by me"
counts the claims on this machine and slot. The answer is cached in
`<git dir>/crossbox-status-<hash>.cache` (one file per worktree, project root and machine) for 30 s, so a refresh never
spawns more than one run per TTL; env `CROSSBOX_STATUS_TTL` overrides it (`0`
turns the cache off; without a git dir there is no cache). Exit 2, unreadable
output or a timeout shows `cbx: ?` and nothing else; exit 1 (a task file was
skipped) shows the counts marked `(partial ...)` because they are a floor.
No `.crossbox/` -> no output; no `crossbox` command -> `cbx: crossbox not installed`.

A plugin cannot contribute `statusLine`, so it is an opt-in snippet for
`settings.json` using the script's installed path, not `${CLAUDE_PLUGIN_ROOT}`
(point it at a checkout of this repository, or at the plugin's directory in
Claude Code's plugin cache):

    {"statusLine": {"type": "command", "command": "sh /path/to/plugin/statusline/crossbox-status.sh"}}

## How it is tested

`tests/test_plugin.py` checks that both manifests parse and point at files that exist,
that the plugin version equals the package version, and that hooks.json resolves under
the plugin root. The MCP server is driven over a real subprocess pipe with a fake
`crossbox` on PATH: each tool must run exactly the expected argv, and injection attempts
(`; rm`, `$(...)`, a leading `-`, a newline, a 10 MB title) are refused or passed as one
inert argument. The status line is checked against a fixture queue, its cache and its
`cbx: ?` on exit 2. CI has no Claude Code, so `claude plugin validate --strict` on
`plugin/` and on the marketplace runs locally before a release.

plugin.json keeps `version` equal to the package version on purpose: users stay on a
released version until the next release.

---
[README](../README.md) · [walkthrough](walkthrough.md) · [data model](data-model.md) · [reference](reference.md) · [claims](claims.md) · [guards](guards.md) · [readiness](readiness.md) · [plan](plan.md) · [views](views.md) · [closures](closures.md) · [lint](lint.md) · [doctor](doctor.md) · [configuration](configuration.md) · [plugin](plugin.md) · [agents](agents.md) · [known limits](limits.md) · [credits](credits.md)
