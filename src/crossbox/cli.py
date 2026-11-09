"""Command line: `crossbox <verb> ...` (also `python -m crossbox`).

Exit codes (every verb):

    0   ok
    1   refused, or findings (includes a rate-gate trip and an origin-guard
        refusal)
    2   NOT EVALUABLE: the tool could not look (a fetch failed, a claims file is
        damaged, a ref does not resolve). Never a pass.
    3   partial: a batch or multi-target verb did some of its work
    64  usage error

Output meant for machines (`--json`) goes to stdout and is JSON from byte 0;
warnings and banners go to stderr.
"""
from __future__ import annotations

import argparse
import json
import sys

from . import __version__, doctor, graphview, lock, notes, plan, rategate, ready, verbs, views, why, activity, prose, closures
from . import claims as _claims
from . import config as _config
from .context import Ctx
from .errors import NOT_EVALUABLE, OK, USAGE, CrossboxError, UsageError
from .ids import bad_identity

ALIASES = {"batch-add": ["ba"], "batch-done": ["bd"], "edit-step": ["es"],
           "move-step": ["mv"], "reorder": ["ro"], "remove-step": ["rm-step"],
           "renumber": ["mv-task"], "ready": []}


def _csv(v):
    return [p.strip() for p in v.split(",") if p.strip()]


_NO_PROBES = ("never run a needs-registry probe command: a gate that has one reads "
               "NOT EVALUABLE ('probes disabled')")


def _positive(v):
    try:
        n = int(v)
    except ValueError:
        n = 0
    if n < 1:
        raise argparse.ArgumentTypeError(f"{v!r} is not a positive integer")
    return n


def _percent(v):
    try:
        n = int(v)
    except ValueError:
        n = -1
    if not 0 <= n <= 100:
        raise argparse.ArgumentTypeError(f"{v!r} is not a whole percent from 0 to 100")
    return n


class _Parser(argparse.ArgumentParser):
    """No abbreviations: `--for` must not run `--force`. Sub-parsers are built
    with the parent's class, so this reaches every verb."""

    def __init__(self, **kw):
        kw.setdefault("allow_abbrev", False)
        super().__init__(**kw)


def _parser() -> argparse.ArgumentParser:
    ap = _Parser(
        prog="crossbox",
        description="A task queue that lives in your repository as YAML, for several "
                    "coding agents and people working on one codebase. A \"slot\" is one "
                    "git worktree of a clone; the primary checkout is slot \"main\".")
    ap.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    ap.add_argument("--root", help="repository root (default: git toplevel of the cwd; "
                                   "env CROSSBOX_ROOT)")
    ap.add_argument("--machine", help="act as this machine (default: env CROSSBOX_MACHINE, "
                                      "then the config's hostnames, then the host name)")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="VERB")

    def add(name, help_, **kw):
        p = sub.add_parser(name, help=help_, aliases=ALIASES.get(name, []), **kw)
        p.set_defaults(verb=name)
        return p

    p = add("init", "create .crossbox/ with a starter config")
    p.add_argument("--prefix", help="task id prefix, e.g. TASK (ids read TASK-API-001)")
    p.add_argument("--machine", help="name of this machine in the config")

    p = add("new", "create a task (one YAML file); steps come from `add`")
    p.add_argument("title", help="what the task is, in a few words")
    p.add_argument("-d", "--domain", help="short area code in the id, e.g. API")
    p.add_argument("-p", "--priority", help="task priority, e.g. high")

    p = add("add", "add a step to a task; it gets the next free letter")
    p.add_argument("task", help="task id, e.g. TASK-API-001")
    p.add_argument("title", help="what the step is")
    p.add_argument("-t", "--target", help="machine or capability that must do it (default: any)")
    p.add_argument("-n", "--needs", type=_csv, help="comma-separated gates from the needs registry")
    p.add_argument("-D", "--depends", type=_csv,
                   help="comma-separated steps or tasks that must be done first, e.g. a,TASK-B-001")
    p.add_argument("--notes", help="free text kept with the step")
    p.add_argument("-p", "--priority", help="TASK priority, e.g. high (steps have none; this replaces the task's)")

    p = add("batch-add", "add many steps from a file (pipe rows or JSONL); - = stdin")
    p.add_argument("task")
    p.add_argument("file")
    p.add_argument("--format", choices=["auto", "pipe", "jsonl"], default="auto")

    p = add("edit", "edit task-level fields")
    p.add_argument("task")
    p.add_argument("-p", "--priority")
    p.add_argument("--phase")
    p.add_argument("--source")
    p.add_argument("--title")

    p = add("edit-step", "edit one step")
    p.add_argument("ref")
    p.add_argument("--title")
    p.add_argument("--notes")
    p.add_argument("--append-notes")
    p.add_argument("--clear-notes", action="store_true")
    p.add_argument("--replace-notes", action="store_true")
    p.add_argument("--target")
    p.add_argument("--needs", type=_csv)
    p.add_argument("--depends", type=_csv)
    p.add_argument("--premise-verdict")

    p = add("remove-step", "remove a step (ids are not renumbered)")
    p.add_argument("ref")
    p.add_argument("--force", action="store_true")

    p = add("move-step", "rename a step id")
    p.add_argument("ref")
    p.add_argument("new")
    p.add_argument("--force", action="store_true",
                   help="rename even when another session's claim binds the step")

    p = add("swap", "exchange two step ids")
    p.add_argument("ref")
    p.add_argument("other")
    p.add_argument("--force", action="store_true",
                   help="swap even when another session's claim binds a step")

    p = add("reorder", "move a step and renumber all ids from a")
    p.add_argument("ref")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--before")
    g.add_argument("--after")
    g.add_argument("--to", type=int)
    p.add_argument("--force", action="store_true")

    p = add("done", "close a step (--commit records the commit it rests on)")
    p.add_argument("ref", help="step ref, e.g. TASK-API-001.a")
    p.add_argument("--force", action="store_true",
                   help="close even though a dependency is not done (noted on the step)")
    p.add_argument("--commit", action="append", dest="commits", metavar="SHA",
                   help="record the commit the closure rests on (repeatable); "
                        "graded against the upstream")
    p.add_argument("--no-probes", action="store_true", help=_NO_PROBES)

    p = add("batch-done", "close several steps; continue and report")
    p.add_argument("refs", nargs="+")
    p.add_argument("--force", action="store_true")
    p.add_argument("--no-probes", action="store_true", help=_NO_PROBES)

    p = add("skip", "retire a step with a reason")
    p.add_argument("ref")
    p.add_argument("--reason", required=True)
    p.add_argument("--superseded-by")

    p = add("reopen", "reopen a done or skipped step")
    p.add_argument("ref")
    p.add_argument("--reason", required=True)

    p = add("show", "print one task")
    p.add_argument("task")
    p.add_argument("--full", action="store_true", help="print every line of notes")

    p = add("status", "list the queue, or --summary counts")
    p.add_argument("--domain", help="only tasks in this domain")
    p.add_argument("--summary", action="store_true", help="print counts instead of the list")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--ready", action="store_true", help="compute readiness (runs probes)")
    p.add_argument("--operator", action="store_true", help="treat gates marked `clearable: operator` as open")

    add("archive", "move fully closed tasks to the archive")
    p = add("unarchive", "move a task back to the queue")
    p.add_argument("task")

    p = add("renumber", "give a task a new id and rewrite references")
    p.add_argument("task")
    p.add_argument("target", nargs="?")
    p.add_argument("--next", action="store_true", dest="next_")
    p.add_argument("--file")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true",
                   help="renumber even when another session's claim binds a step")

    p = add("check-collisions", "compare queue files with a ref")
    p.add_argument("--against")
    p.add_argument("--json", action="store_true")
    p = add("check-duplicate-ids", "find ids claimed by several files")
    p.add_argument("--json", action="store_true")
    p.add_argument("--baseline")
    p = add("check-empty-tasks", "find tasks with no steps")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true")

    p = add("note", "leave a note for another machine or slot")
    p.add_argument("--to", help="MACHINE or MACHINE/SLOT (default: this machine)")
    p.add_argument("message", nargs="+", help="the note text")
    p = add("notes", "print incoming notes (or all)")
    p.add_argument("which", nargs="?", choices=["all"])
    add("clear-notes", "archive incoming notes")

    p = add("claim", "tell the other sessions you are working on a step (advisory)")
    p.add_argument("subject", help="a step ref (TASK-API-001.a) or free text describing the work")
    p.add_argument("--source", default="", help="where the work came from, e.g. a task id")
    p.add_argument("--intent", default="", help="one line on what you will do")
    p.add_argument("--step", action="append", dest="steps",
                   help="claim this step as well (repeatable); one claim per step")
    p.add_argument("--slot", help="claim as this slot (default: detected from the worktree)")
    p = add("release", "give up claims, by id or all of yours")
    p.add_argument("ids", nargs="*", help="claim ids, e.g. CLM-laptop-001")
    p.add_argument("--all-mine", action="store_true", help="release every active claim of this slot")
    p.add_argument("--all-machine", action="store_true", dest="all_box",
                   help="release every active claim of this machine; refuses other slots' "
                        "unless --include-other-slots")
    p.add_argument("--all-box", action="store_true", dest="all_box",
                   help=argparse.SUPPRESS)          # the old name of --all-machine
    p.add_argument("--include-other-slots", action="store_true",
                   help="with --all-machine, release other slots' claims too")
    p = add("claims", "list claims, including siblings'")
    p.add_argument("--all", action="store_true", dest="show_all", help="include released claims")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p = add("claim-check", "does a planned piece of work overlap an active claim?")
    p.add_argument("title", help="the work you plan to start")
    p.add_argument("--source", default="", help="where the work came from, e.g. a task id")
    p.add_argument("--quiet", action="store_true", help="print only on overlap")

    p = add("ready", "what can this machine start now: READY, GATED, CLAIMED, WAITING (= needs here)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p.add_argument("--operator", action="store_true", help="treat gates marked `clearable: operator` as open")
    p.add_argument("--no-probes", action="store_true", help=_NO_PROBES)
    p = add("lint", "decisions the notes state that the structure does not carry (advisory)")
    p.add_argument("--json", action="store_true", help="machine-readable output")
    p = add("activity", "is each claim's holder working? claim liveness and unclaimed work, from git")
    p.add_argument("--json", action="store_true")
    p.add_argument("--hot-hours", type=float, default=24.0,
                   help="evidence this recent reads HOT (default 24)")
    p = add("closures", "are the queue's closures true? unreachable commits, missing artifacts, closed-but-open steps, unmerged branches")
    p.add_argument("--json", action="store_true")
    p.add_argument("--days", type=_positive, default=30,
                   help="how far back commits are read for closing phrases (default 30)")
    p = add("why", "why a step is not workable here, traced to its root blockers")
    p.add_argument("ref", nargs="?", help="step ref or task id (omit for the queue-wide line)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--operator", action="store_true")
    p.add_argument("--no-probes", action="store_true", help=_NO_PROBES)
    p = add("plan", "what to work on next: ranked READY, critical path, waves, order")
    p.add_argument("--top", type=_positive, default=10,
                   help="rows per section in text output (default 10)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-probes", action="store_true", help=_NO_PROBES)
    p = add("progress", "completion counts per task, domain or priority, beside their totals")
    p.add_argument("--by", choices=["task", "domain", "priority"])
    p.add_argument("--archive", action="store_true", help="also count archived tasks")
    p.add_argument("--json", action="store_true")
    p = add("near-done", "tasks close to closing, and which machines can finish them")
    p.add_argument("--max-open", type=_positive, default=2,
                   help="at most this many open steps (default 2)")
    p.add_argument("--min-pct", type=_percent, default=60,
                   help="at least this percent of steps closed (default 60)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--no-probes", action="store_true", help=_NO_PROBES)
    p = add("graph", "export the dependency graph (mermaid, dot or json)")
    p.add_argument("--format", choices=["mermaid", "dot", "json"], default="mermaid")
    p.add_argument("--task", action="append", dest="tasks", default=[],
                   help="only this task and the steps it depends on (repeatable)")
    p.add_argument("--open", action="store_true", dest="open_only",
                   help="draw open steps only")
    p.add_argument("--max-nodes", type=_positive, default=150,
                   help="refuse a bigger graph instead of dropping nodes (default 150)")
    p.add_argument("--max-edges", type=_positive, default=None,
                   help="refuse more edges (default 500 for mermaid, its limit; none for "
                        "dot and json)")
    p.add_argument("--max-chars", type=_positive, default=None,
                   help="refuse more characters of output (default 50000 for mermaid, its "
                        "limit; none for dot and json)")
    p.add_argument("--no-probes", action="store_true", help=_NO_PROBES)
    p = add("needs", "needs registry: registry | audit | here")
    ns = p.add_subparsers(dest="needs_cmd", required=True, metavar="SUB")
    q = ns.add_parser("registry")
    q.add_argument("--json", action="store_true")
    q = ns.add_parser("audit")
    q.add_argument("--json", action="store_true")
    q = ns.add_parser("here")
    q.add_argument("--json", action="store_true")
    q.add_argument("--operator", action="store_true")
    q.add_argument("--no-probes", action="store_true")

    p = add("doctor", "check and repair task files; install git integration")
    ds = p.add_subparsers(dest="doctor_cmd", required=True, metavar="SUB")
    q = ds.add_parser("check")
    q.add_argument("--staged", action="store_true")
    q.add_argument("--task")
    q.add_argument("--archive", action="store_true")
    q.add_argument("--quiet", action="store_true")
    q = ds.add_parser("fix")
    q.add_argument("task", nargs="?")
    q.add_argument("--archive", action="store_true")
    q.add_argument("--write", action="store_true")
    q.add_argument("--diff", action="store_true")
    ds.add_parser("selftest")
    q = ds.add_parser("install-hook")
    q.add_argument("--command", help="how the hook should invoke crossbox")
    q = ds.add_parser("install-merge-driver")
    q.add_argument("--command", help="how git should invoke crossbox")

    p = add("merge-driver", "git merge driver for claims.yaml (%%O %%A %%B)")
    p.add_argument("base")
    p.add_argument("ours")
    p.add_argument("theirs")
    return ap


def _gate_verb(a) -> str:
    if a.verb == "doctor" and getattr(a, "doctor_cmd", None) == "fix" and a.write:
        return "doctor-fix"
    return a.verb


def _gate_refs(a, ctx) -> list:
    if a.verb in ("done", "edit-step", "skip"):
        return [a.ref]
    if a.verb == "batch-done":
        return list(a.refs)
    if a.verb == "release":
        if a.all_mine and not a.ids:
            # exactly the claims it will release: this slot's active ones
            return rategate.my_active_claim_ids(ctx)
        return list(a.ids or [])
    return []


READ_ONLY = frozenset({"show", "status", "check-collisions", "check-duplicate-ids",
                       "check-empty-tasks", "notes", "claims", "claim-check", "ready",
                       "needs", "plan", "progress", "near-done", "graph", "why", "activity", "lint",
                       "closures"})


def _mutates(a) -> bool:
    if a.verb == "doctor":
        return a.doctor_cmd in ("fix", "install-hook", "install-merge-driver")
    return a.verb not in READ_ONLY


def dispatch(ctx: Ctx, a) -> int:
    v = a.verb
    if v == "init":
        return verbs.cmd_init(ctx, a.prefix, a.machine)
    if v == "new":
        return verbs.cmd_new(ctx, a.title, a.domain, a.priority)
    if v == "add":
        return verbs.cmd_add(ctx, a.task, a.title, a.target, a.needs, a.depends, a.notes,
                             a.priority)
    if v == "batch-add":
        if a.file == "-":
            text = sys.stdin.read()
        else:
            try:
                with open(a.file, encoding="utf-8") as fh:
                    text = fh.read()
            except OSError as e:
                raise CrossboxError(f"cannot read {a.file}: {e}", 2)
        return verbs.cmd_batch_add(ctx, a.task, text, a.format)
    if v == "edit":
        return verbs.cmd_edit(ctx, a.task, a.priority, a.phase, a.source, a.title)
    if v == "edit-step":
        return verbs.cmd_edit_step(ctx, a.ref, a.title, a.notes, a.append_notes,
                                   a.clear_notes, a.replace_notes, a.target, a.needs,
                                   a.depends, a.premise_verdict)
    if v == "remove-step":
        return verbs.cmd_remove_step(ctx, a.ref, a.force)
    if v == "move-step":
        return verbs.cmd_move_step(ctx, a.ref, a.new, a.force)
    if v == "swap":
        return verbs.cmd_swap(ctx, a.ref, a.other, a.force)
    if v == "reorder":
        return verbs.cmd_reorder(ctx, a.ref, a.before, a.after, a.to, a.force)
    if v == "done":
        return verbs.cmd_done(ctx, a.ref, a.force, a.commits, not a.no_probes)
    if v == "batch-done":
        return verbs.cmd_batch_done(ctx, a.refs, a.force, not a.no_probes)
    if v == "skip":
        return verbs.cmd_skip(ctx, a.ref, a.reason, a.superseded_by)
    if v == "reopen":
        return verbs.cmd_reopen(ctx, a.ref, a.reason)
    if v == "show":
        return verbs.cmd_show(ctx, a.task, a.full)
    if v == "status":
        if a.summary or a.json or a.ready:
            return verbs.cmd_status_summary(ctx, a.json, a.ready, a.domain, a.operator)
        return verbs.cmd_status(ctx, a.domain)
    if v == "archive":
        return verbs.cmd_archive(ctx)
    if v == "unarchive":
        return verbs.cmd_unarchive(ctx, a.task)
    if v == "renumber":
        return verbs.cmd_renumber(ctx, a.task, a.target, a.next_, a.file, a.dry_run, a.force)
    if v == "check-collisions":
        return verbs.cmd_check_collisions(ctx, a.against, a.json)
    if v == "check-duplicate-ids":
        return verbs.cmd_check_duplicate_ids(ctx, a.json, a.baseline)
    if v == "check-empty-tasks":
        return verbs.cmd_check_empty_tasks(ctx, a.json, a.strict)
    if v == "note":
        return notes.cmd_note(ctx, " ".join(a.message), a.to)
    if v == "notes":
        return notes.cmd_notes(ctx, a.which == "all")
    if v == "clear-notes":
        return notes.cmd_clear_notes(ctx)
    if v == "claim":
        return _claims.cmd_claim(ctx, a.subject, a.source, a.intent, a.steps, a.slot)
    if v == "release":
        return _claims.cmd_release(ctx, a.ids, a.all_mine, a.all_box, a.include_other_slots)
    if v == "claims":
        return _claims.cmd_claims(ctx, a.show_all, a.json)
    if v == "claim-check":
        return _claims.cmd_claim_check(ctx, a.title, a.source, a.quiet)
    if v == "ready":
        return ready.cmd_ready(ctx, a.json, a.operator, not a.no_probes)
    if v == "lint":
        return prose.cmd_lint(ctx, a.json)
    if v == "activity":
        return activity.cmd_activity(ctx, a.json, a.hot_hours)
    if v == "closures":
        return closures.cmd_closures(ctx, a.json, a.days)
    if v == "why":
        return why.cmd_why(ctx, a.ref, a.json, a.operator, not a.no_probes)
    if v == "plan":
        return plan.cmd_plan(ctx, a.json, a.top, not a.no_probes)
    if v == "progress":
        return views.cmd_progress(ctx, a.by, a.archive, a.json)
    if v == "near-done":
        return views.cmd_near_done(ctx, a.max_open, a.min_pct, a.json, not a.no_probes)
    if v == "graph":
        return graphview.cmd_graph(ctx, a.format, a.tasks, a.open_only, a.max_nodes,
                                   a.max_edges, a.max_chars, not a.no_probes)
    if v == "needs":
        if a.needs_cmd == "registry":
            return ready.cmd_needs_registry(ctx, a.json)
        if a.needs_cmd == "audit":
            return ready.cmd_needs_audit(ctx, a.json)
        return ready.cmd_ready(ctx, a.json, a.operator, not a.no_probes)
    if v == "doctor":
        d = a.doctor_cmd
        if d == "check":
            return doctor.cmd_check(ctx, a.task, a.archive, a.staged, a.quiet)
        if d == "fix":
            return doctor.cmd_fix(ctx, a.task, a.archive, a.write, a.diff)
        if d == "selftest":
            return doctor.cmd_selftest(ctx)
        if d == "install-hook":
            return doctor.cmd_install_hook(ctx, a.command)
        return doctor.cmd_install_merge_driver(ctx, a.command)
    raise CrossboxError(f"unknown verb {v}", USAGE)


def main(argv=None, out=None, err=None, **ctx_kw) -> int:
    err_stream = err if err is not None else sys.stderr
    try:
        a = _parser().parse_args(argv)
    except SystemExit as e:
        # argparse exits 2 on a usage error, which would read as NOT EVALUABLE.
        return OK if e.code in (0, None) else USAGE
    if a.verb == "merge-driver":
        return _claims.merge_driver(a.base, a.ours, a.theirs)
    try:
        if a.machine is not None:
            why = bad_identity(a.machine)
            if why:
                raise UsageError(f"--machine {a.machine!r} is not a valid machine name ({why})")
            ctx_kw = dict(ctx_kw, machine=a.machine)
        ctx = Ctx(root=a.root, out=out, err=err, **ctx_kw)
        if not _mutates(a):
            return dispatch(ctx, a)
        if _config.machine_is_unnamed(ctx.cfg, ctx.machine):
            ctx.warn(f"crossbox: this machine has no name in the config, so it is recorded as "
                     f"{ctx.machine!r} (a hash of the host name, never the name itself). "
                     f"Name it under `machines:` (with its `hostnames:`) in the crossbox config, "
                     f"or set CROSSBOX_MACHINE.")
        # one writer per tree: the whole read-modify-write (and the rate-gate
        # count) happens under the queue lock, so parallel verbs queue instead
        # of losing each other's writes
        with lock.hold(ctx.repo, what=f"`{a.verb}`"):
            rategate.check(ctx, _gate_verb(a), _gate_refs(a, ctx))
            return dispatch(ctx, a)
    except CrossboxError as e:
        print(f"crossbox: {e.message}", file=err_stream)
        if e.code == NOT_EVALUABLE and (getattr(a, "json", False)
                                        or getattr(a, "format", None) == "json"):
            # machines read stdout: the same shape every --json verb uses
            print(json.dumps({"not_evaluable": e.message}),
                  file=out if out is not None else sys.stdout)
        return e.code
    except _claims.ClaimsDamaged as e:
        print(f"crossbox: claims file damaged: {e}", file=err_stream)
        return 2


if __name__ == "__main__":
    sys.exit(main())
