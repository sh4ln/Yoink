"""
yoink/audio.py — ffmpeg-python conversion wrapper for Yoink v6.

Wraps ffmpeg as an async subprocess via asyncio.
Batch conversions are capped at 4 concurrent ffmpeg instances
using asyncio.Semaphore(4) to prevent CPU thrashing.
Auto-downloads a static ffmpeg binary on first use via static-ffmpeg
unless YOINK_OFFLINE_MODE=1 is set.
"""

# TODO: implement audio.py
