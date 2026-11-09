"""Notes: append-only inboxes between machines and slots.

Addresses: ``<machine>`` (read by every slot on that machine) or
``<machine>/<slot>``. Files: ``notes/<from>-to-<to>.md`` where an address is
written ``machine`` or ``machine+slot`` (``+``, not ``@``: a
name shaped like an email address trips secret scanners). Cleared inboxes are renamed to
``notes/archive-<from>-to-<to>-<YYYYMMDD>.md``.
"""
from __future__ import annotations

from pathlib import Path

from . import gitutil, ids, model
from .errors import OK, CrossboxError, UsageError


def my_tokens(ctx) -> set:
    return {ctx.machine, f"{ctx.machine}+{ctx.slot}"}


def _from_token(ctx) -> str:
    return ctx.machine if ctx.slot == "main" else f"{ctx.machine}+{ctx.slot}"


def _local_slots(ctx) -> set:
    try:
        return {w["slot"] for w in gitutil.worktrees(ctx.repo)}
    except gitutil.GitError:
        return {"main"}


def resolve_to(ctx, to) -> str:
    machines = set(ctx.cfg.machines) | {ctx.machine}
    if to is None:
        others = sorted(set(ctx.cfg.machines) - {ctx.machine})
        if len(others) == 1:
            return others[0]
        raise UsageError("note: name the recipient with --to MACHINE or --to MACHINE/SLOT")
    machine, sep, slot = to.partition("/")
    if sep:
        why = ids.bad_identity(slot)
        if why:
            raise CrossboxError(f"recipient slot {slot!r} is not a valid slot name ({why})")
    if machine not in machines:
        raise CrossboxError(f"unknown machine {machine!r} (configured: "
                            f"{', '.join(sorted(machines))}); nobody reads that inbox")
    if not slot:
        if machine == ctx.machine:
            raise CrossboxError("that is this machine's own inbox; address a slot "
                                "with --to MACHINE/SLOT")
        return machine
    if machine == ctx.machine:
        if slot == ctx.slot:
            raise CrossboxError("that is your own slot; nobody else reads that inbox")
        if slot not in _local_slots(ctx):
            raise CrossboxError(f"no worktree slot {slot!r} on this machine (have: "
                                f"{', '.join(sorted(_local_slots(ctx)))})")
    return f"{machine}+{slot}"


def cmd_note(ctx, message: str, to=None) -> int:
    if message is None or not message.strip():
        raise CrossboxError("refusing an empty note")
    dest = resolve_to(ctx, to)
    frm = _from_token(ctx)
    path = ctx.cfg.notes / f"{frm}-to-{dest}.md"
    # belt and braces: every part was checked against the name grammar, and
    # the final path must still land directly in the notes directory
    if path.resolve().parent != ctx.cfg.notes.resolve():
        raise CrossboxError(f"note path {path} leaves {ctx.cfg.rel(ctx.cfg.notes)}; refusing")
    stamp = ctx.now.strftime("%Y-%m-%d %H:%M")
    lines = message.strip("\n").splitlines()
    entry = f"- **[{stamp}]** {lines[0]}\n" + "".join(f"  {ln}\n" for ln in lines[1:])
    if path.exists():
        text = path.read_text(encoding="utf-8")
        if text and not text.endswith("\n"):
            text += "\n"
    else:
        text = f"# Notes from {frm} to {dest}\n\n"
    model.atomic_write_text(path, text + entry)
    ctx.say(f"noted to {dest} ({ctx.cfg.rel(path)})")
    return OK


def _incoming(ctx) -> list:
    if not ctx.cfg.notes.is_dir():
        return []
    mine = my_tokens(ctx)
    out = []
    for p in sorted(ctx.cfg.notes.glob("*-to-*.md")):
        if p.name.startswith("archive-"):
            continue
        to = p.name[:-3].rsplit("-to-", 1)[-1]
        if to in mine:
            out.append(p)
    return out


def cmd_notes(ctx, show_all: bool = False) -> int:
    if show_all:
        files = sorted(p for p in ctx.cfg.notes.glob("*.md")) if ctx.cfg.notes.is_dir() else []
    else:
        files = _incoming(ctx)
    for p in files:
        ctx.say(f"== {p.name}")
        ctx.say(p.read_text(encoding="utf-8").rstrip("\n"))
    ctx.say(f"{len(files)} inbox file(s)" + ("" if show_all else f" addressed to "
                                              f"{_from_token(ctx)}"))
    return OK


def cmd_clear_notes(ctx) -> int:
    files = _incoming(ctx)
    day = ctx.today.strftime("%Y%m%d")
    for p in files:
        dest = p.with_name(f"archive-{p.stem}-{day}.md")
        if dest.exists():
            model.atomic_write_text(dest, dest.read_text(encoding="utf-8")
                                    + p.read_text(encoding="utf-8"))
            p.unlink()
        else:
            Path(p).rename(dest)
        ctx.say(f"archived {p.name} -> {dest.name}")
    ctx.say(f"cleared {len(files)} inbox file(s)")
    return OK
