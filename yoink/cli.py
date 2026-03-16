"""
yoink/cli.py — Click CLI layer for Yoink v6.

All personality messages → stderr via Rich Console.
Structured data (file paths, clipboard JSON, dry-run previews) → stdout.
Progress bars use Rich Live with one task per active file.
One-liner paste syntax parsed by scanning raw tokens for 'paste'.
"""

from __future__ import annotations

import asyncio
import glob as glob_module
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    SpinnerColumn,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)
from rich.table import Table
from rich.text import Text
from rich.tree import Tree

from .core import (
    YoinkClipboardEmptyError,
    YoinkClipboardExpiredError,
    YoinkPermissionError,
    YoinkUndoNotAvailableError,
    build_copy_history,
    build_move_history,
    build_rename_history,
    clipboard_clear,
    clipboard_load,
    clipboard_read,
    clipboard_save,
    copy_files,
    delete_file,
    get_size_tree,
    history_push,
    human_readable_size,
    move_files,
    rename_pattern,
    undo_last_action,
)
from . import audio as _audio

# ---------------------------------------------------------------------------
# Consoles — err for personality/progress, out for structured data
# ---------------------------------------------------------------------------

err = Console(stderr=True)
out = Console(stderr=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _expand_globs(paths: list[str]) -> list[str]:
    """Expand glob patterns in paths. Covers Windows CMD users too."""
    result: list[str] = []
    for p in paths:
        if "*" in p or "?" in p:
            expanded = glob_module.glob(p)
            if expanded:
                result.extend(sorted(expanded))
            else:
                result.append(p)
        else:
            result.append(p)
    return result


def _split_on_paste(
    tokens: tuple[str, ...],
) -> tuple[list[str], list[str]]:
    """Split raw token list on the literal word 'paste'.

    Returns (sources, destinations). Flags (starting with '-') that appear
    before any source are dropped here — Click already parsed them.
    """
    token_list = list(tokens)
    try:
        idx = token_list.index("paste")
        sources = [t for t in token_list[:idx] if not t.startswith("-")]
        destinations = [t for t in token_list[idx + 1:] if not t.startswith("-")]
    except ValueError:
        sources = [t for t in token_list if not t.startswith("-")]
        destinations = []
    return sources, destinations


def _make_progress() -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[bold cyan]{task.description}", justify="right"),
        BarColumn(bar_width=None),
        DownloadColumn(),
        TransferSpeedColumn(),
        TimeRemainingColumn(),
        console=err,
        transient=True,
    )


def _handle_err(exc: Exception) -> None:
    """Print a personality error message to stderr and exit with code 1."""
    if isinstance(exc, YoinkPermissionError):
        err.print(str(exc))
    elif isinstance(exc, YoinkClipboardEmptyError):
        err.print("clipboard is empty bro.")
    elif isinstance(exc, YoinkClipboardExpiredError):
        err.print("clipboard expired bro. run yoink copy again.")
    elif isinstance(exc, YoinkUndoNotAvailableError):
        err.print(str(exc))
    elif isinstance(exc, FileNotFoundError):
        err.print(f"that path doesn't exist bro. {exc}")
    else:
        err.print(f"[red]{exc}[/red]")
    raise SystemExit(1)


def _print_results_stdout(results: list[dict[str, Any]], dry_run: bool) -> None:
    """Emit destination paths to stdout (structured data channel)."""
    for r in results:
        status = r.get("status", "")
        if dry_run or status == "dry_run":
            out.print(f"[dry-run] {r['src']} → {r['dst']}")
        elif status == "skipped":
            pass  # silent skip
        else:
            out.print(r["dst"])


# ---------------------------------------------------------------------------
# Async runners
# ---------------------------------------------------------------------------

def _run_copy_op(
    sources: list[str],
    destinations: list[str],
    *,
    use_link: bool,
    force: bool,
    skip: bool,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Execute copy_files with a Rich Live progress display."""
    progress = _make_progress()
    file_tasks: dict[str, TaskID] = {}

    def _cb(src: str, bytes_done: int) -> None:
        name = Path(src).name
        if name in file_tasks:
            progress.update(file_tasks[name], completed=bytes_done)
        else:
            file_tasks[name] = progress.add_task(name, total=bytes_done)
            progress.update(file_tasks[name], completed=bytes_done)

    # Pre-register tasks for known sources so bars appear immediately.
    for s in sources:
        p = Path(s)
        if p.is_file():
            tid = progress.add_task(p.name, total=p.stat().st_size)
            file_tasks[p.name] = tid

    results: list[dict[str, Any]] = []

    async def _run() -> None:
        nonlocal results
        results = await copy_files(
            sources, destinations,
            use_link=use_link,
            force=force,
            skip=skip,
            dry_run=dry_run,
            progress_cb=_cb,
        )

    with Live(progress, console=err, refresh_per_second=20):
        asyncio.run(_run())

    return results


def _run_move_op(
    sources: list[str],
    destinations: list[str],
    *,
    force: bool,
    skip: bool,
    dry_run: bool,
) -> list[dict[str, Any]]:
    """Execute move_files with a Rich Live progress display."""
    progress = _make_progress()
    file_tasks: dict[str, TaskID] = {}

    def _cb(src: str, bytes_done: int) -> None:
        name = Path(src).name
        if name in file_tasks:
            progress.update(file_tasks[name], completed=bytes_done)

    for s in sources:
        p = Path(s)
        if p.is_file():
            file_tasks[p.name] = progress.add_task(p.name, total=p.stat().st_size)

    results: list[dict[str, Any]] = []

    async def _run() -> None:
        nonlocal results
        results = await move_files(
            sources, destinations,
            force=force,
            skip=skip,
            dry_run=dry_run,
            progress_cb=_cb,
        )

    with Live(progress, console=err, refresh_per_second=20):
        asyncio.run(_run())

    return results


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
def cli() -> None:
    """yoink — faster file ops for the command line."""


# ---------------------------------------------------------------------------
# copy / c
# ---------------------------------------------------------------------------

_COPY_SETTINGS = dict(
    ignore_unknown_options=True,
    allow_extra_args=True,
)


@cli.command(name="copy", context_settings=_COPY_SETTINGS)
@click.argument("tokens", nargs=-1, type=click.UNPROCESSED)
@click.option("--force", "-f", is_flag=True, help="Overwrite without prompt.")
@click.option("--skip", is_flag=True, help="Skip if destination already exists.")
@click.option("--link", is_flag=True, help="Hardlink instead of copy.")
@click.option("--dry-run", is_flag=True, help="Preview without executing.")
def copy_cmd(
    tokens: tuple[str, ...],
    force: bool,
    skip: bool,
    link: bool,
    dry_run: bool,
) -> None:
    """Copy files. Optionally inline paste with: yoink copy SRC… paste DST…"""
    sources, destinations = _split_on_paste(tokens)
    sources = _expand_globs(sources)

    if not sources:
        err.print("no sources given bro.")
        raise SystemExit(1)

    if destinations:
        # One-liner: copy directly to destinations.
        destinations = _expand_globs(destinations)
        try:
            results = _run_copy_op(
                sources, destinations,
                use_link=link, force=force, skip=skip, dry_run=dry_run,
            )
        except Exception as exc:
            _handle_err(exc)
            return

        if not dry_run:
            err.print("yoinked.")
            history_push(build_copy_history(results))
        _print_results_stdout(results, dry_run)
    else:
        # Save to clipboard.
        if dry_run:
            for s in sources:
                out.print(f"[dry-run] would copy to clipboard: {s}")
            return
        abs_sources = [str(Path(s).resolve()) for s in sources]
        clipboard_save(abs_sources, "copy")
        err.print("yoinked.")


# Register alias.
cli.add_command(copy_cmd, name="c")


# ---------------------------------------------------------------------------
# cut
# ---------------------------------------------------------------------------

@cli.command(name="cut", context_settings=_COPY_SETTINGS)
@click.argument("tokens", nargs=-1, type=click.UNPROCESSED)
@click.option("--force", "-f", is_flag=True)
@click.option("--skip", is_flag=True)
@click.option("--dry-run", is_flag=True)
def cut_cmd(
    tokens: tuple[str, ...],
    force: bool,
    skip: bool,
    dry_run: bool,
) -> None:
    """Cut files. Inline paste: yoink cut SRC… paste DST…"""
    sources, destinations = _split_on_paste(tokens)
    sources = _expand_globs(sources)

    if not sources:
        err.print("no sources given bro.")
        raise SystemExit(1)

    if destinations:
        destinations = _expand_globs(destinations)
        try:
            results = _run_move_op(
                sources, destinations,
                force=force, skip=skip, dry_run=dry_run,
            )
        except Exception as exc:
            _handle_err(exc)
            return

        if not dry_run:
            err.print("yoinked.")
            history_push(build_move_history(results))
        _print_results_stdout(results, dry_run)
    else:
        if dry_run:
            for s in sources:
                out.print(f"[dry-run] would cut to clipboard: {s}")
            return
        abs_sources = [str(Path(s).resolve()) for s in sources]
        clipboard_save(abs_sources, "cut")
        err.print("yoinked.")


# ---------------------------------------------------------------------------
# paste / p
# ---------------------------------------------------------------------------

@cli.command(name="paste", context_settings=_COPY_SETTINGS)
@click.argument("destinations", nargs=-1, type=click.UNPROCESSED)
@click.option("--force", "-f", is_flag=True)
@click.option("--skip", is_flag=True)
@click.option("--dry-run", is_flag=True)
def paste_cmd(
    destinations: tuple[str, ...],
    force: bool,
    skip: bool,
    dry_run: bool,
) -> None:
    """Paste from clipboard. Omit destinations to paste into current directory."""
    try:
        cb = clipboard_load()
    except Exception as exc:
        _handle_err(exc)
        return

    sources: list[str] = cb["sources"]
    mode: str = cb["mode"]

    if not destinations:
        # Paste all sources into cwd, preserving filenames.
        dst_list = [str(Path.cwd() / Path(s).name) for s in sources]
    else:
        dst_list = list(destinations)

    # If single destination is a directory, paste into it.
    if len(dst_list) == 1 and Path(dst_list[0]).is_dir():
        base_dir = Path(dst_list[0])
        dst_list = [str(base_dir / Path(s).name) for s in sources]

    try:
        if mode == "cut":
            results = _run_move_op(
                sources, dst_list,
                force=force, skip=skip, dry_run=dry_run,
            )
            if not dry_run:
                clipboard_clear()
                err.print("yoinked.")
                history_push(build_move_history(results))
        else:
            results = _run_copy_op(
                sources, dst_list,
                use_link=False, force=force, skip=skip, dry_run=dry_run,
            )
            if not dry_run:
                err.print("yoinked.")
                history_push(build_copy_history(results))
    except Exception as exc:
        _handle_err(exc)
        return

    _print_results_stdout(results, dry_run)


cli.add_command(paste_cmd, name="p")


# ---------------------------------------------------------------------------
# delete / r
# ---------------------------------------------------------------------------

@cli.command(name="delete")
@click.argument("path")
@click.option("--force", "-f", is_flag=True, help="Permanently delete without recycle bin.")
@click.option("--dry-run", is_flag=True)
def delete_cmd(path: str, force: bool, dry_run: bool) -> None:
    """Delete a file or directory."""
    target = Path(path)
    if not target.exists():
        err.print(f"that path doesn't exist bro. {path}")
        raise SystemExit(1)

    if dry_run:
        out.print(f"[dry-run] would delete: {path}")
        return

    if force:
        confirmed = click.confirm(
            "-f detected. this is permanent. you sure?",
            err=True,
        )
        if not confirmed:
            err.print("aborted.")
            return

    try:
        result = asyncio.run(delete_file(target, permanent=force))
        err.print(result["message"])
    except RuntimeError as exc:
        # TrashPermissionError path.
        msg = str(exc)
        if "trash failed" in msg:
            confirmed = click.confirm(
                "trash failed on this drive. permanently delete instead?",
                err=True,
            )
            if confirmed:
                try:
                    asyncio.run(delete_file(target, permanent=True))
                    err.print("gone. rip.")
                except Exception as inner:
                    _handle_err(inner)
            else:
                err.print("aborted.")
        else:
            _handle_err(exc)
    except Exception as exc:
        _handle_err(exc)


cli.add_command(delete_cmd, name="r")


# ---------------------------------------------------------------------------
# convert / cv
# ---------------------------------------------------------------------------

@cli.command(name="convert", context_settings=dict(ignore_unknown_options=True))
@click.argument("tokens", nargs=-1, type=click.UNPROCESSED)
@click.option("--format", "fmt", default=None, help="Target format for batch convert, e.g. mp3.")
@click.option("--bitrate", default=None, help="Audio bitrate, e.g. 320k.")
@click.option("--samplerate", default=None, type=int, help="Sample rate, e.g. 44100.")
@click.option("--channels", default=None, type=int, help="1 (mono) or 2 (stereo).")
@click.option("--force", "-f", is_flag=True, help="Overwrite existing output.")
@click.option("--dry-run", is_flag=True)
def convert_cmd(
    tokens: tuple[str, ...],
    fmt: str | None,
    bitrate: str | None,
    samplerate: int | None,
    channels: int | None,
    force: bool,
    dry_run: bool,
) -> None:
    """Convert file formats using ffmpeg. Source is never modified."""
    # Strip flag tokens that leaked through.
    args = [t for t in tokens if not t.startswith("-")]

    if len(args) < 2:
        err.print("usage: yoink convert INPUT OUTPUT [--format EXT] [options]")
        raise SystemExit(1)

    input_pattern = args[0]
    output_arg = args[1]

    # Expand globs.
    sources = _expand_globs([input_pattern])

    is_batch = fmt is not None or Path(output_arg).is_dir() or output_arg.endswith("/")

    if is_batch and fmt is None:
        err.print("specify --format for batch convert.")
        raise SystemExit(1)

    if dry_run:
        if is_batch:
            out_dir = Path(output_arg)
            for s in sources:
                dst = out_dir / (Path(s).stem + "." + fmt.lstrip("."))
                out.print(f"[dry-run] {s} → {dst}")
        else:
            out.print(f"[dry-run] {sources[0]} → {output_arg}")
        return

    progress = _make_progress()
    conv_tasks: dict[str, TaskID] = {}

    def _cb(dst_path: str) -> None:
        name = Path(dst_path).name
        if name in conv_tasks:
            progress.update(conv_tasks[name], completed=1)

    for s in sources:
        p = Path(s)
        tid = progress.add_task(p.name, total=1)
        dst_name = p.stem + "." + (fmt or Path(output_arg).suffix.lstrip("."))
        conv_tasks[dst_name] = tid

    async def _run() -> list[dict[str, Any]]:
        if is_batch:
            return await _audio.batch_convert(
                sources, output_arg, fmt,
                bitrate=bitrate, samplerate=samplerate,
                channels=channels, overwrite=force, progress_cb=_cb,
            )
        else:
            src = Path(sources[0])
            dst = Path(output_arg)
            # Same-name collision check.
            if dst.exists() and not force:
                confirmed = click.confirm(
                    "same name detected. overwrite original?",
                    err=True,
                )
                if not confirmed:
                    stem = dst.stem
                    dst = dst.with_name(f"{stem}_converted{dst.suffix}")
                    err.print(f"saving as {dst.name}")
            return [
                await _audio.convert_file(
                    src, dst,
                    bitrate=bitrate, samplerate=samplerate,
                    channels=channels, overwrite=force, progress_cb=_cb,
                )
            ]

    try:
        with Live(progress, console=err, refresh_per_second=20):
            results = asyncio.run(_run())
    except Exception as exc:
        _handle_err(exc)
        return

    for r in results:
        if r["status"] == "ok":
            err.print(r["message"])
            out.print(r["dst"])
        else:
            err.print(f"[red]{r['message']}[/red]")


cli.add_command(convert_cmd, name="cv")


# ---------------------------------------------------------------------------
# undo
# ---------------------------------------------------------------------------

@cli.command(name="undo")
def undo_cmd() -> None:
    """Reverse the last undoable action (copy, move, rename)."""
    try:
        results = asyncio.run(undo_last_action())
    except YoinkUndoNotAvailableError as exc:
        err.print(str(exc))
        raise SystemExit(1)
    except Exception as exc:
        _handle_err(exc)
        return

    for r in results:
        err.print(r.get("message", "restored."))
        if r.get("status") == "ok":
            out.print(r["dst"])


# ---------------------------------------------------------------------------
# clipboard
# ---------------------------------------------------------------------------

@cli.command(name="clipboard")
def clipboard_cmd() -> None:
    """Show current clipboard state."""
    data = clipboard_read()
    if not data or not data.get("sources"):
        err.print("clipboard is empty bro.")
        return

    ts = data.get("timestamp", 0)
    age = time.time() - ts
    remaining = 3600 - age

    if remaining <= 0:
        err.print("clipboard expired bro. run yoink copy again.")
        return

    mins, secs = divmod(int(remaining), 60)
    expiry_str = f"{mins}m {secs}s"

    # Structured JSON to stdout.
    payload = {
        "mode": data["mode"],
        "count": len(data["sources"]),
        "sources": data["sources"],
        "expires_in": expiry_str,
    }
    out.print(json.dumps(payload, indent=2))

    # Human-readable summary to stderr.
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_row("[bold]mode[/bold]", data["mode"])
    table.add_row("[bold]files[/bold]", str(len(data["sources"])))
    table.add_row("[bold]expires in[/bold]", expiry_str)
    err.print(Panel(table, title="clipboard", border_style="cyan"))
    for s in data["sources"]:
        err.print(f"  {s}")


# ---------------------------------------------------------------------------
# size
# ---------------------------------------------------------------------------

@cli.command(name="size")
@click.argument("path", default=".")
def size_cmd(path: str) -> None:
    """Display a Rich tree view of folder sizes. .yoink_bak/ is excluded."""
    target = Path(path)
    if not target.exists():
        err.print(f"that path doesn't exist bro. {path}")
        raise SystemExit(1)

    try:
        tree_data = asyncio.run(get_size_tree(target))
    except Exception as exc:
        _handle_err(exc)
        return

    def _build_tree(node: dict, parent: Tree) -> None:
        size_str = human_readable_size(node["size"])
        label = Text()
        label.append(node["name"], style="bold")
        label.append(f"  {size_str}", style="dim")
        branch = parent.add(label)
        for child in node.get("children", []):
            _build_tree(child, branch)

    root_label = Text()
    root_label.append(tree_data["name"], style="bold magenta")
    root_label.append(f"  {human_readable_size(tree_data['size'])}", style="dim")
    rich_tree = Tree(root_label)

    for child in tree_data.get("children", []):
        _build_tree(child, rich_tree)

    err.print(rich_tree)


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------

@cli.command(name="rename")
@click.argument("pattern_glob")
@click.argument("template")
@click.option("--dry-run", is_flag=True, help="Preview renames without executing.")
def rename_cmd(pattern_glob: str, template: str, dry_run: bool) -> None:
    """Bulk rename matching files. Use # as auto-incrementing counter.

    Examples:

      yoink rename "*.jpg" "vacation_#.jpg"
    """
    sources = _expand_globs([pattern_glob])
    if not sources:
        err.print(f"that path doesn't exist bro. {pattern_glob}")
        raise SystemExit(1)

    try:
        results = asyncio.run(rename_pattern(sources, template, dry_run=dry_run))
    except Exception as exc:
        _handle_err(exc)
        return

    if dry_run:
        for r in results:
            out.print(f"[dry-run] {r['src']} → {r['dst']}")
    else:
        history_push(build_rename_history(results))
        for r in results:
            if r.get("status") == "ok":
                err.print(f"{Path(r['src']).name} → {Path(r['dst']).name}")
                out.print(r["dst"])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    cli()


if __name__ == "__main__":
    main()
