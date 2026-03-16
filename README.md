# Yoink

> Faster, smarter, human-readable file operations for the command line.

Yoink replaces native file operations with async, concurrent alternatives that are faster and more expressive. Copy, cut, paste, delete, rename, inspect, and convert files — all from one unified CLI with live Rich progress output.

---

## Install

```bash
pip install yoink
```

Requires **Python 3.10+**. `ffmpeg` is auto-downloaded on first `convert` if not found in `$PATH`. Set `YOINK_OFFLINE_MODE=1` to disable.

---

## Commands

```
yoink copy  "src1" "src2"                          save to clipboard
yoink copy  "src1" "src2" paste "dst1" "dst2"      one-liner, no clipboard
yoink paste "dst1" "dst2"                           paste from clipboard
yoink paste                                         paste to current directory
yoink cut   "src1" "src2"                           cut to clipboard
yoink cut   "src1" "src2" paste "dst1"              one-liner cut
yoink delete "path"                                 recycle bin
yoink delete "path" -f                              permanent delete
yoink convert "input.wav" "output.mp3"              convert format
yoink convert "folder/*.wav" "out/" --format mp3    batch convert
yoink undo                                          reverse last action
yoink clipboard                                     show clipboard state
yoink size  "path"                                  pretty size tree
yoink rename "*.jpg" "vacation_#.jpg"               bulk rename
```

## Flags

| Flag | Description |
|---|---|
| `-f` / `--force` | Overwrite without prompt |
| `--skip` | Skip if destination already exists |
| `--link` | Hardlink instead of copy (same drive only) |
| `--dry-run` | Preview without executing |
| `--bitrate` | Audio bitrate (e.g. `320k`) |
| `--samplerate` | Audio sample rate (e.g. `44100`) |
| `--channels` | `stereo` or `mono` |
| `--format` | Target format for batch convert (e.g. `mp3`) |

---

## How it works

- All file I/O is **fully async** via `asyncio` + `aiofiles`
- On **Linux**, `os.sendfile()` provides zero-copy kernel-space transfers
- On **Windows**, a single read stream is fanned to multiple concurrent write streams
- Multiple files move at once with a live **Rich** progress dashboard
- Clipboard state saved to `~/.yoink_clipboard.json` (expires after 1 hour)
- Last 3 actions stored in `~/.yoink_history.json` for undo
- `undo` uses hybrid mtime detection — restores cleanly or keeps both versions if destination was modified
- `.yoink_bak/` holds pre-overwrite backups and is auto-purged after 24 hours
- All state files protected by `filelock.FileLock`

---

## Project structure

```
yoink/
├── yoink/
│   ├── __init__.py
│   ├── core.py      ← async engine (copy, move, delete, rename, undo, clipboard, history)
│   ├── cli.py       ← Click CLI layer
│   └── audio.py     ← ffmpeg-python convert wrapper
├── pyproject.toml
├── README.md
└── LICENSE
```

---

## License

MIT — see [LICENSE](LICENSE)

**Author:** SH4LN  
**Repo:** https://github.com/sh4ln/yoink
