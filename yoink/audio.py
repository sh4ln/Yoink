"""
yoink/audio.py — Async ffmpeg conversion wrapper for Yoink v6.

ffmpeg-python builds the argument list; asyncio.create_subprocess_exec
runs it non-blocking. Batch conversions are capped at 4 concurrent
ffmpeg processes via asyncio.Semaphore(4).

Auto-downloads a static ffmpeg binary on first use via static-ffmpeg
unless YOINK_OFFLINE_MODE=1 is set.

Metadata (-map_metadata 0) is injected automatically for intra-domain
conversions (audio→audio, video→video). Cross-domain conversions skip
it. If ffmpeg errors with metadata, retries silently without it.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Any, Callable, Sequence

import ffmpeg as ffmpeg_python

# ---------------------------------------------------------------------------
# Domain tables
# ---------------------------------------------------------------------------

_AUDIO_EXTS: frozenset[str] = frozenset({
    ".mp3", ".wav", ".flac", ".ogg", ".aac", ".m4a", ".wma", ".opus",
    ".ape", ".alac", ".aiff", ".aif", ".ac3", ".dts", ".eac3", ".amr",
    ".mp2", ".mka", ".ra", ".rm",
})

_VIDEO_EXTS: frozenset[str] = frozenset({
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v",
    ".ts", ".mts", ".m2ts", ".3gp", ".ogv", ".vob", ".divx", ".xvid",
    ".mpg", ".mpeg", ".f4v", ".asf",
})

# ---------------------------------------------------------------------------
# Semaphore — module-level so it is shared across all callers
# ---------------------------------------------------------------------------

CONVERT_SEM: asyncio.Semaphore = asyncio.Semaphore(4)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _domain(ext: str) -> str:
    """Return 'audio', 'video', or 'other' for a file extension."""
    e = ext.lower()
    if e in _AUDIO_EXTS:
        return "audio"
    if e in _VIDEO_EXTS:
        return "video"
    return "other"


def is_intra_domain(src_path: str | Path, dst_path: str | Path) -> bool:
    """Return True when src and dst share the same media domain."""
    src_domain = _domain(Path(src_path).suffix)
    dst_domain = _domain(Path(dst_path).suffix)
    return src_domain == dst_domain and src_domain != "other"


async def _ensure_ffmpeg() -> str:
    """Return the path to an ffmpeg binary, downloading if necessary.

    Raises
    ------
    RuntimeError
        When ffmpeg is not found and YOINK_OFFLINE_MODE=1 is set,
        or when static-ffmpeg is not installed.
    """
    path = await asyncio.to_thread(shutil.which, "ffmpeg")
    if path:
        return path

    if os.environ.get("YOINK_OFFLINE_MODE") == "1":
        raise RuntimeError("ffmpeg not found and offline mode is on bro.")

    try:
        import static_ffmpeg  # type: ignore[import-untyped]

        await asyncio.to_thread(static_ffmpeg.add_paths)
        path = await asyncio.to_thread(shutil.which, "ffmpeg")
        if path:
            return path
        raise RuntimeError(
            "static-ffmpeg was loaded but ffmpeg binary still not found in PATH."
        )
    except ImportError as exc:
        raise RuntimeError(
            "ffmpeg not found. install static-ffmpeg: pip install static-ffmpeg"
        ) from exc


def _build_ffmpeg_args(
    src: Path,
    dst: Path,
    *,
    bitrate: str | None,
    samplerate: int | None,
    channels: int | None,
    inject_metadata: bool,
    overwrite: bool,
) -> list[str]:
    """Build the ffmpeg argument list using ffmpeg-python."""
    output_kwargs: dict[str, Any] = {}
    if bitrate:
        output_kwargs["b:a"] = bitrate
    if samplerate:
        output_kwargs["ar"] = str(samplerate)
    if channels:
        output_kwargs["ac"] = str(channels)
    if inject_metadata:
        output_kwargs["map_metadata"] = "0"

    stream = ffmpeg_python.input(str(src))
    stream = ffmpeg_python.output(stream, str(dst), **output_kwargs)
    return ffmpeg_python.compile(stream, overwrite_output=overwrite)


async def _run_ffmpeg(args: list[str]) -> tuple[int, bytes, bytes]:
    """Run ffmpeg as an async subprocess and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    return proc.returncode, stdout, stderr


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def convert_file(
    src: str | Path,
    dst: str | Path,
    *,
    bitrate: str | None = None,
    samplerate: int | None = None,
    channels: int | None = None,
    overwrite: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """Convert a single file from *src* to *dst* using ffmpeg.

    Parameters
    ----------
    src : source file path
    dst : destination file path (format inferred from extension)
    bitrate : audio bitrate string, e.g. ``"320k"`` (maps to ``-b:a``)
    samplerate : sample rate in Hz, e.g. ``44100`` (maps to ``-ar``)
    channels : number of audio channels (maps to ``-ac``)
    overwrite : if True, overwrite *dst* without prompting
    progress_cb : optional callback receiving the destination path string
        when conversion completes

    Returns
    -------
    dict with keys ``src``, ``dst``, ``status`` ("ok" | "error"), ``message``.
    """
    src = Path(src)
    dst = Path(dst)

    if not src.exists():
        raise FileNotFoundError(f"that path doesn't exist bro. {src}")

    ffmpeg_path = await _ensure_ffmpeg()

    intra = is_intra_domain(src, dst)
    dst.parent.mkdir(parents=True, exist_ok=True)

    # First attempt — with metadata if intra-domain.
    args = _build_ffmpeg_args(
        src, dst,
        bitrate=bitrate,
        samplerate=samplerate,
        channels=channels,
        inject_metadata=intra,
        overwrite=overwrite,
    )
    # Replace 'ffmpeg' in compiled args with the resolved path.
    args[0] = ffmpeg_path

    returncode, _, stderr_bytes = await _run_ffmpeg(args)

    if returncode != 0 and intra:
        # Retry without metadata injection.
        args2 = _build_ffmpeg_args(
            src, dst,
            bitrate=bitrate,
            samplerate=samplerate,
            channels=channels,
            inject_metadata=False,
            overwrite=True,
        )
        args2[0] = ffmpeg_path
        returncode, _, stderr_bytes = await _run_ffmpeg(args2)

    if returncode != 0:
        src_ext = src.suffix.lstrip(".") or "?"
        dst_ext = dst.suffix.lstrip(".") or "?"
        return {
            "src": str(src),
            "dst": str(dst),
            "status": "error",
            "message": (
                f"no idea how to convert {src_ext} to {dst_ext} bro. "
                f"ffmpeg said: {stderr_bytes.decode(errors='replace')[-200:].strip()}"
            ),
        }

    if progress_cb:
        progress_cb(str(dst))

    return {
        "src": str(src),
        "dst": str(dst),
        "status": "ok",
        "message": "converted. fingers crossed it works.",
    }


async def batch_convert(
    sources: Sequence[str | Path],
    output_dir: str | Path,
    target_format: str,
    *,
    bitrate: str | None = None,
    samplerate: int | None = None,
    channels: int | None = None,
    overwrite: bool = False,
    progress_cb: Callable[[str], None] | None = None,
) -> list[dict[str, Any]]:
    """Convert multiple files concurrently, capped at 4 parallel ffmpeg instances.

    Parameters
    ----------
    sources : list of source file paths
    output_dir : directory where converted files are written
    target_format : extension without dot, e.g. ``"mp3"``
    bitrate, samplerate, channels : passed through to convert_file
    overwrite : passed through to convert_file
    progress_cb : called with destination path string after each conversion

    Returns
    -------
    list of result dicts (same shape as convert_file return value)
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    ext = target_format.lstrip(".")

    async def _one(src: Path) -> dict[str, Any]:
        dst = out_dir / (src.stem + "." + ext)
        async with CONVERT_SEM:
            return await convert_file(
                src, dst,
                bitrate=bitrate,
                samplerate=samplerate,
                channels=channels,
                overwrite=overwrite,
                progress_cb=progress_cb,
            )

    tasks = [_one(Path(s)) for s in sources]
    return list(await asyncio.gather(*tasks))
