⁹# Internal helpers — state files
# ---------------------------------------------------------------------------

def _lock(path: Path) -> FileLock:
    return FileLock(str(path) + ".lock")


def _read_json(path: Path) -> Any:
    """Synchronous JSON read guarded by FileLock."""
    lock = _lock(path)
    lock.acquire()
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    finally:
        lock.release()


def _write_json(path: Path, data: Any) -> None:
    """Synchronous JSON write guarded by FileLock."""
    lock = _lock(path)
    lock.acquire()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Clipboard
# ---------------------------------------------------------------------------

def clipboard_save(sources: Sequence[str], mode: str) -> None:
    """Persist sources to the clipboard file.

    Parameters
    ----------
    sources : sequence of absolute path strings
    mode    : "copy" or "cut"
    """
    payload = {
        "mode": mode,
        "sources": list(sources),
        "timestamp": time.time(),
    }
    _write_json(CLIPBOARD_PATH, payload)


def clipboard_load() -> dict[str, Any]:
    """Load and validate the clipboard.

    Returns the raw dict with keys ``mode``, ``sources``, ``timestamp``.

    Raises
    ------
    YoinkClipboardEmptyError
        When the clipboard file does not exist or is empty.
    YoinkClipboardExpiredError
        When the entry is older than CLIPBOARD_TTL.
    """
    data = _read_json(CLIPBOARD_PATH)
    if not data or not data.get("sources"):
        raise YoinkClipboardEmptyError("clipboard is empty bro.")
    age = time.time() - data.get("timestamp", 0)
    if age > CLIPBOARD_TTL:
        raise YoinkClipboardExpiredError(
            "clipboard expired bro. run yoink copy again."
        )
    return data


def clipboard_read() -> dict[str, Any] | None:
    """Return the raw clipboard dict without raising, or None."""
    return _read_json(CLIPBOARD_PATH)


def clipboard_clear() -> None:
    """Delete the clipboard file if it exists."""
    if CLIPBOARD_PATH.exists():
        CLIPBOARD_PATH.unlink()


# ---------------------------------------------------------------------------
# History
# ---------------------------------------------------------------------------

def _purge_bak(bak_dir: Path) -> None:
    """Remove .yoink_bak/ entries older than BAK_MAX_AGE (synchronous)."""
    if not bak_dir.is_dir():
        return
    now = time.time()
    for entry in bak_dir.iterdir():
        try:
            if now - entry.stat().st_mtime > BAK_MAX_AGE:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
        except OSError:
            pass


def history_push(action: dict[str, Any]) -> None:
    """Append an action record to history (keeps last HISTORY_MAX entries).

    Also triggers .yoink_bak/ purge for any bak_dir referenced in the
    oldest entry being evicted.
    """
    data = _read_json(HISTORY_PATH) or []
    data.append(action)
    if len(data) > HISTORY_MAX:
        data = data[-HISTORY_MAX:]
    _write_json(HISTORY_PATH, data)

    # Purge stale backups associated with *any* bak_dir recorded in history.
    all_bak_dirs: set[str] = set()
    for record in data:
        bd = record.get("bak_dir")
        if bd:
            all_bak_dirs.add(bd)
    for bd in all_bak_dirs:
        _purge_bak(Path(bd))


def history_peek() -> dict[str, Any] | None:
    """Return the most recent history entry, or None."""
    data = _read_json(HISTORY_PATH) or []
    return data[-1] if data else None


def history_pop() -> dict[str, Any] | None:
    """Remove and return the most recent history entry."""
    lock = _lock(HISTORY_PATH)
    lock.acquire()
    try:
        if not HISTORY_PATH.exists():
            return None
        with open(HISTORY_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not data:
            return None
        entry = data.pop()
        with open(HISTORY_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return entry
    finally:
        lock.release()


# ---------------------------------------------------------------------------
# Low-level async copy helpers
# ---------------------------------------------------------------------------

async def _apply_metadata(src: str | Path, dst: str | Path) -> None:
    """Copy mtime/atime (utime) and extended stat (copystat) asynchronously."""
    src, dst = str(src), str(dst)
    src_stat = await asyncio.to_thread(os.stat, src)
    await asyncio.to_thread(
        os.utime, dst, (src_stat.st_atime, src_stat.st_mtime)
    )
    await asyncio.to_thread(shutil.copystat, src, dst)


async def _copy_chunked(src: str | Path, dst: str | Path) -> None:
    """aiofiles-based chunked copy (Windows fallback or non-Linux)."""
    async with aiofiles.open(str(src), "rb") as r_fh:
        async with aiofiles.open(str(dst), "wb") as w_fh:
            while True:
                chunk = await r_fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                await w_fh.write(chunk)
    await _apply_metadata(src, dst)


async def _copy_sendfile(src: str | Path, dst: str | Path) -> None:
    """Linux zero-copy via os.sendfile()."""
    src_path = Path(src)
    dst_path = Path(dst)
    file_size = src_path.stat().st_size
    loop = asyncio.get_running_loop()

    def _do_sendfile() -> None:
        with open(src_path, "rb") as in_fh, open(dst_path, "wb") as out_fh:
            offset = 0
            remaining = file_size
            while remaining > 0:
                sent = os.sendfile(out_fh.fileno(), in_fh.fileno(), offset, remaining)
                if sent == 0:
                    break
                offset += sent
                remaining -= sent

    await loop.run_in_executor(None, _do_sendfile)
    await _apply_metadata(src, dst)


async def _copy_single_file(src: Path, dst: Path, use_link: bool = False) -> None:
    """Copy or hardlink a single file to a single destination.

    Falls back to physical copy if hardlink is not supported.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)

    if use_link:
        try:
            await asyncio.to_thread(os.link, str(src), str(dst))
            return
        except OSError:
            pass  # fall through to physical copy

    try:
        if IS_LINUX:
            await _copy_sendfile(src, dst)
        else:
            await _copy_chunked(src, dst)
    except PermissionError as exc:
        raise YoinkPermissionError(exc, src) from exc


async def _copy_file_to_many(
    src: Path,
    destinations: Sequence[Path],
    use_link: bool = False,
    progress_cb: Callable[[int], None] | None = None,
) -> None:
    """Fan-out: read src once, write to all destinations concurrently.

    On Linux uses os.sendfile per-destination (kernel-space).
    On Windows reads with aiofiles and fans to N concurrent writers.
    """
    for dst in destinations:
        dst.parent.mkdir(parents=True, exist_ok=True)

    if use_link:
        tasks = [_copy_single_file(src, dst, use_link=True) for dst in destinations]
        await asyncio.gather(*tasks)
        return

    if IS_LINUX:
        # Each sendfile call is its own kernel transfer — already concurrent.
        tasks = [_copy_sendfile(src, dst) for dst in destinations]
        await asyncio.gather(*tasks)
        return

    # Windows: single aiofiles read stream fanned to multiple async writers.
    write_handles: list[Any] = []
    try:
        for dst in destinations:
            fh = await aiofiles.open(str(dst), "wb")
            write_handles.append(fh)

        async with aiofiles.open(str(src), "rb") as r_fh:
            bytes_transferred = 0
            while True:
                chunk = await r_fh.read(CHUNK_SIZE)
                if not chunk:
                    break
                bytes_transferred += len(chunk)
                await asyncio.gather(*[fh.write(chunk) for fh in write_handles])
                if progress_cb:
                    progress_cb(bytes_transferred)
    finally:
        for fh in write_handles:
            await fh.close()

    # Apply metadata to all destinations.
    await asyncio.gather(*[_apply_metadata(src, dst) for dst in destinations])


# ---------------------------------------------------------------------------
# Backup helpers
# ---------------------------------------------------------------------------

def _bak_dir_for(dst: Path) -> Path:
    """Return the .yoink_bak directory co-located with dst's parent."""
    return dst.parent / BAK_DIR_NAME


def _bak_path_for(dst: Path) -> Path:
    """Derive a backup path for a destination file."""
    bak_dir = _bak_dir_for(dst)
    # Encode timestamp into backup filename to avoid collisions.
    ts = int(time.time())
    return bak_dir / f"{dst.name}.{ts}.bak"


async def _backup_existing(dst: Path) -> Path | None:
    """Move an existing destination file to .yoink_bak/ before overwriting.

    Returns the backup path, or None if the destination did not exist.
    """
    if not dst.exists():
        return None
    bak_path = _bak_path_for(dst)
    bak_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        await asyncio.to_thread(shutil.move, str(dst), str(bak_path))
    except PermissionError as exc:
        raise YoinkPermissionError(exc, dst) from exc
    return bak_path


# ---------------------------------------------------------------------------
# Collision resolution helpers
# ---------------------------------------------------------------------------

def _numbered_path(path: Path, n: int = 1) -> Path:
    """Return path with ' (n)' injected before the suffix, incrementing n
    until the path does not exist."""
    stem = path.stem
    suffix = path.suffix
    candidate = path.with_name(f"{stem} ({n}){suffix}")
    if candidate.exists():
        return _numbered_path(path, n + 1)
    return candidate


# ---------------------------------------------------------------------------
# Public API — copy / cut / paste
# ---------------------------------------------------------------------------

async def copy_files(
    sources: Sequence[str | Path],
    destinations: Sequence[str | Path],
    *,
    use_link: bool = False,
    force: bool = False,
    skip: bool = False,
    dry_run: bool = False,
    progress_cb: Callable[[str, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Copy sources to destinations.

    For a single source with multiple destinations the file is read once and
    fanned to all destinations concurrently.  For multiple sources each maps
    independently to its corresponding destination.

    Returns a list of result dicts with keys:
      ``src``, ``dst``, ``status`` (str: "ok" | "skipped" | "dry_run"),
      ``bak_path`` (str | None).
    """
    src_paths = [Path(s) for s in sources]
    dst_paths = [Path(d) for d in destinations]

    results: list[dict[str, Any]] = []

    async def _transfer_one(src: Path, dst: Path) -> dict[str, Any]:
        if not src.exists():
            raise FileNotFoundError(f"that path doesn't exist bro. {src}")

        if src.is_dir():
            return await _copy_directory(
                src, dst,
                use_link=use_link,
                force=force,
                skip=skip,
                dry_run=dry_run,
                progress_cb=progress_cb,
            )

        # Collision handling for single file.
        bak_path: str | None = None
        effective_dst = dst

        if dst.exists():
            if skip:
                return {"src": str(src), "dst": str(dst), "status": "skipped", "bak_path": None}
            if not force:
                effective_dst = _numbered_path(dst)
            else:
                if not dry_run:
                    bak = await _backup_existing(dst)
                    bak_path = str(bak) if bak else None

        if dry_run:
            return {"src": str(src), "dst": str(effective_dst), "status": "dry_run", "bak_path": None}

        effective_dst.parent.mkdir(parents=True, exist_ok=True)
        await _copy_single_file(src, effective_dst, use_link=use_link)
        if progress_cb:
            progress_cb(str(src), effective_dst.stat().st_size)

        return {"src": str(src), "dst": str(effective_dst), "status": "ok", "bak_path": bak_path}

    # Fan-out optimisation: single source → multiple destinations.
    if len(src_paths) == 1 and len(dst_paths) > 1:
        src = src_paths[0]
        if not src.exists():
            raise FileNotFoundError(f"that path doesn't exist bro. {src}")
        if not src.is_dir():
            effective_dsts: list[Path] = []
            bak_paths: list[str | None] = []
            for dst in dst_paths:
                bak_path = None
                effective_dst = dst
                if dst.exists():
                    if skip:
                        results.append({"src": str(src), "dst": str(dst), "status": "skipped", "bak_path": None})
                        continue
                    if not force:
                        effective_dst = _numbered_path(dst)
                    else:
                        if not dry_run:
                            bak = await _backup_existing(dst)
                            bak_path = str(bak) if bak else None
                if dry_run:
                    results.append({"src": str(src), "dst": str(effective_dst), "status": "dry_run", "bak_path": None})
                    continue
                effective_dsts.append(effective_dst)
                bak_paths.append(bak_path)
            if effective_dsts:
                await _copy_file_to_many(src, effective_dsts, use_link=use_link)
                for ed, bp in zip(effective_dsts, bak_paths):
                    results.append({"src": str(src), "dst": str(ed), "status": "ok", "bak_path": bp})
            return results

    # General case: parallel transfers, one per source→dest pair.
    if len(src_paths) != len(dst_paths):
        raise ValueError(
            f"Source count ({len(src_paths)}) does not match destination count ({len(dst_paths)})."
        )
    tasks = [_transfer_one(src, dst) for src, dst in zip(src_paths, dst_paths)]
    results = list(await asyncio.gather(*tasks))
    return results


async def _copy_directory(
    src: Path,
    dst: Path,
    *,
    use_link: bool,
    force: bool,
    skip: bool,
    dry_run: bool,
    progress_cb: Callable[[str, int], None] | None,
) -> dict[str, Any]:
    """Recursively copy a directory tree, excluding .yoink_bak/."""
    file_pairs: list[tuple[Path, Path]] = []
    for root, dirs, files in os.walk(str(src)):
        dirs[:] = [d for d in dirs if d != BAK_DIR_NAME]
        root_path = Path(root)
        rel = root_path.relative_to(src)
        target_root = dst / rel
        if not dry_run:
            target_root.mkdir(parents=True, exist_ok=True)
        for fname in files:
            file_pairs.append((root_path / fname, target_root / fname))

    if dry_run:
        return {
            "src": str(src),
            "dst": str(dst),
            "status": "dry_run",
            "bak_path": None,
            "files": [str(p) for _, p in file_pairs],
        }

    async def _copy_pair(fsrc: Path, fdst: Path) -> None:
        bak: Path | None = None
        if fdst.exists():
            if skip:
                return
            if force:
                bak = await _backup_existing(fdst)
            else:
                fdst = _numbered_path(fdst)
        await _copy_single_file(fsrc, fdst, use_link=use_link)

    await asyncio.gather(*[_copy_pair(fs, fd) for fs, fd in file_pairs])
    return {"src": str(src), "dst": str(dst), "status": "ok", "bak_path": None}


async def move_files(
    sources: Sequence[str | Path],
    destinations: Sequence[str | Path],
    *,
    force: bool = False,
    skip: bool = False,
    dry_run: bool = False,
    progress_cb: Callable[[str, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Cut-paste: move sources to destinations.

    Same-drive moves use os.rename() (atomic, instant).
    Cross-drive moves copy first, then delete source only on success.

    Returns same result dicts as copy_files.
    """
    src_paths = [Path(s) for s in sources]
    dst_paths = [Path(d) for d in destinations]
    if len(src_paths) != len(dst_paths):
        raise ValueError(
            f"Source count ({len(src_paths)}) does not match destination count ({len(dst_paths)})."
        )

    async def _move_one(src: Path, dst: Path) -> dict[str, Any]:
        if not src.exists():
            raise FileNotFoundError(f"that path doesn't exist bro. {src}")

        bak_path: str | None = None
        effective_dst = dst

        if dst.exists():
            if skip:
                return {"src": str(src), "dst": str(dst), "status": "skipped", "bak_path": None}
            if not force:
                effective_dst = _numbered_path(dst)
            else:
                if not dry_run:
                    bak = await _backup_existing(dst)
                    bak_path = str(bak) if bak else None

        if dry_run:
            return {"src": str(src), "dst": str(effective_dst), "status": "dry_run", "bak_path": None}

        effective_dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            # Attempt atomic rename (same drive).
            await asyncio.to_thread(os.rename, str(src), str(effective_dst))
        except OSError:
            # Cross-drive: copy then delete.
            await _copy_single_file(src, effective_dst)
            try:
                if src.is_dir():
                    await asyncio.to_thread(shutil.rmtree, str(src))
                else:
                    await asyncio.to_thread(os.remove, str(src))
            except PermissionError as exc:
                raise YoinkPermissionError(exc, src) from exc

        return {"src": str(src), "dst": str(effective_dst), "status": "ok", "bak_path": bak_path}

    results = list(await asyncio.gather(*[_move_one(s, d) for s, d in zip(src_paths, dst_paths)]))
    return results


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

async def delete_file(
    path: str | Path,
    *,
    permanent: bool = False,
) -> dict[str, Any]:
    """Delete a file or directory.

    Parameters
    ----------
    permanent : bool
        If True, permanently delete via os.remove / shutil.rmtree.
        If False, send to recycle bin via send2trash.

    Returns
    -------
    dict with keys ``path``, ``status`` ("ok" | "error"), ``message``.
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"that path doesn't exist bro. {target}")

    if permanent:
        try:
            if target.is_dir():
                await asyncio.to_thread(shutil.rmtree, str(target))
            else:
                await asyncio.to_thread(os.remove, str(target))
        except PermissionError as exc:
            raise YoinkPermissionError(exc, target) from exc
        return {"path": str(target), "status": "ok", "message": "gone. rip."}

    # Recycle bin path.
    try:
        import send2trash  # optional dependency; not installed in core tests
        await asyncio.to_thread(send2trash.send2trash, str(target))
        return {"path": str(target), "status": "ok", "message": "gone. rip."}
    except ImportError:
        raise RuntimeError("send2trash is not installed. run: pip install send2trash")
    except Exception as exc:
        # send2trash.TrashPermissionError or similar.
        raise RuntimeError(f"trash failed on this drive: {exc}") from exc


# ---------------------------------------------------------------------------
# Rename
# ---------------------------------------------------------------------------

async def rename_pattern(
    sources: Sequence[str | Path],
    pattern: str,
    *,
    dry_run: bool = False,
) -> list[dict[str, Any]]:
    """Pattern-based bulk rename.  # in *pattern* is replaced by an
    auto-incrementing integer that skips numbers already in use.

    Returns a list of result dicts: ``src``, ``dst``, ``status``.
    """
    paths = sorted(Path(s) for s in sources)
    parent = paths[0].parent if paths else Path(".")

    # Collect existing filenames in the parent directory to skip occupied numbers.
    existing_names: set[str] = {p.name for p in parent.iterdir()} if parent.is_dir() else set()

    results: list[dict[str, Any]] = []
    counter = 1

    async def _rename_one(src: Path, name: str) -> dict[str, Any]:
        dst = src.parent / name
        if dry_run:
            return {"src": str(src), "dst": str(dst), "status": "dry_run"}
        try:
            await asyncio.to_thread(os.rename, str(src), str(dst))
        except PermissionError as exc:
            raise YoinkPermissionError(exc, src) from exc
        return {"src": str(src), "dst": str(dst), "status": "ok"}

    tasks = []
    for src in paths:
        suffix = src.suffix
        # Build the target name, inserting the counter where # appears.
        while True:
            candidate_name = pattern.replace("#", str(counter))
            # Ensure the suffix is preserved if pattern does not contain extension.
            if "." not in Path(candidate_name).name:
                candidate_name = Path(candidate_name).stem + suffix
            counter += 1
            if candidate_name not in existing_names:
                break
        existing_names.add(candidate_name)
        tasks.append(_rename_one(src, candidate_name))

    results = list(await asyncio.gather(*tasks))
    return results


# ---------------------------------------------------------------------------
# Undo / rollback
# ---------------------------------------------------------------------------

async def _restore_one(
    bak_path: Path,
    dst: Path,
    recorded_mtime: float,
    sem: asyncio.Semaphore,
) -> dict[str, Any]:
    """Restore one file from backup with hybrid mtime logic."""
    async with sem:
        if not bak_path.exists():
            return {
                "dst": str(dst),
                "status": "error",
                "message": "can't undo, file's already gone bro.",
            }

        # Ensure destination directory exists.
        if not dst.parent.exists():
            await asyncio.to_thread(os.makedirs, str(dst.parent), exist_ok=True)

        if dst.exists():
            actual_mtime = dst.stat().st_mtime
            if abs(actual_mtime - recorded_mtime) > 1:
                # Destination was modified — keep both.
                renamed_dst = _numbered_path(dst)
                await asyncio.to_thread(shutil.move, str(dst), str(renamed_dst))
                await asyncio.to_thread(shutil.move, str(bak_path), str(dst))
                return {
                    "dst": str(dst),
                    "status": "ok",
                    "message": "file was changed since the copy. keeping both, restoring original.",
                    "kept": str(renamed_dst),
                }
            else:
                # Untouched — delete and restore clean.
                await asyncio.to_thread(os.remove, str(dst))
                await asyncio.to_thread(shutil.move, str(bak_path), str(dst))
                return {
                    "dst": str(dst),
                    "status": "ok",
                    "message": "original is untouched. rolling back clean.",
                }
        else:
            await asyncio.to_thread(shutil.move, str(bak_path), str(dst))
            return {
                "dst": str(dst),
                "status": "ok",
                "message": "original is untouched. rolling back clean.",
            }


async def undo_last_action() -> list[dict[str, Any]]:
    """Reverse the most recent undoable action.

    Undoable actions: copy, move/cut, rename.
    Delete (permanent) is not undoable.

    Returns a list of restore result dicts.

    Raises
    ------
    YoinkUndoNotAvailableError
        When there is no history entry or it is not undoable.
    """
    entry = history_pop()
    if not entry:
        raise YoinkUndoNotAvailableError("nothing to undo.")

    action = entry.get("action")
    if action not in ("copy", "move", "rename"):
        raise YoinkUndoNotAvailableError(
            f"cannot undo action '{action}'."
        )

    restores = entry.get("restores", [])
    if not restores:
        raise YoinkUndoNotAvailableError("nothing to undo.")

    sem = asyncio.Semaphore(4)
    tasks = []
    for r in restores:
        bak_path = Path(r["bak_path"])
        dst = Path(r["dst"])
        recorded_mtime = float(r.get("mtime", 0.0))
        tasks.append(_restore_one(bak_path, dst, recorded_mtime, sem))

    results = list(await asyncio.gather(*tasks))
    return results


# ---------------------------------------------------------------------------
# History record builders
# ---------------------------------------------------------------------------

def build_copy_history(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a history record from copy/move results."""
    restores = []
    for r in results:
        if r.get("bak_path") and r.get("status") == "ok":
            dst_path = Path(r["dst"])
            mtime = dst_path.stat().st_mtime if dst_path.exists() else 0.0
            restores.append({
                "dst": r["dst"],
                "bak_path": r["bak_path"],
                "mtime": mtime,
            })
    return {
        "action": "copy",
        "timestamp": time.time(),
        "restores": restores,
    }


def build_move_history(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    record = build_copy_history(results)
    record["action"] = "move"
    return record


def build_rename_history(
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a history record for rename operations (no backup files involved)."""
    reversals = []
    for r in results:
        if r.get("status") == "ok":
            reversals.append({"src": r["src"], "dst": r["dst"]})
    return {
        "action": "rename",
        "timestamp": time.time(),
        "restores": [],
        "reversals": reversals,
    }


# ---------------------------------------------------------------------------
# Size / tree traversal
# ---------------------------------------------------------------------------

async def _dir_size_recursive(
    path: Path,
) -> tuple[int, dict[str, Any]]:
    """Recursively compute sizes; excludes .yoink_bak/ directories.

    Returns (total_bytes, tree_dict).
    tree_dict = {
        "name": str,
        "size": int,
        "children": [tree_dict, ...]
    }
    """
    if path.is_file():
        size = path.stat().st_size
        return size, {"name": path.name, "size": size, "children": []}

    children_info: list[tuple[int, dict[str, Any]]] = []
    entries = [
        e for e in path.iterdir()
        if not (e.is_dir() and e.name == BAK_DIR_NAME)
    ]

    async def _stat_entry(entry: Path) -> tuple[int, dict[str, Any]]:
        return await _dir_size_recursive(entry)

    results = await asyncio.gather(*[_stat_entry(e) for e in entries])
    total = sum(r[0] for r in results)
    children_info = [r[1] for r in results]

    return total, {"name": path.name, "size": total, "children": sorted(
        children_info, key=lambda x: x["size"], reverse=True
    )}


async def get_size_tree(path: str | Path) -> dict[str, Any]:
    """Return a size-tree dict for *path*, excluding .yoink_bak/ contents.

    Keys: ``name``, ``size`` (bytes), ``children`` (list of same shape).
    """
    target = Path(path)
    if not target.exists():
        raise FileNotFoundError(f"that path doesn't exist bro. {target}")
    _, tree = await _dir_size_recursive(target)
    return tree


# ---------------------------------------------------------------------------
# Utility helpers for CLI layer
# ---------------------------------------------------------------------------

def human_readable_size(size_bytes: int) -> str:
    """Convert a byte count to a human-readable string."""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024 or unit == "TB":
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes} B"


def is_same_drive(path_a: Path, path_b: Path) -> bool:
    """Return True if both paths reside on the same filesystem/mount."""
    try:
        return os.stat(path_a).st_dev == os.stat(path_b.parent if not path_b.exists() else path_b).st_dev
    except OSError:
        return False
