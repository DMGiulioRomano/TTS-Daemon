"""Content-addressed on-disk cache of synthesized audio clips.

Real usage repeats phrases constantly ("Build finished", "Claude needs your
permission…", hook notifications). Caching turns those repeats into a zero-cost
file read, which matters most for the heavier neural engines the project wants
to attract. The cache sits *around* the provider call in
:class:`~tts_daemon.core.service.SpeechService`, so providers and the queue are
oblivious to it.

Layout: one file per clip (named by the cache key) plus a small ``index.json``
recording each entry's format, size, and last-access time for size-based LRU
eviction. Writes are atomic (``.tmp`` + ``os.replace``); a cache file that has
gone missing or been truncated is treated as a miss and dropped, never an error.
The index is a best-effort optimisation — it is reconciled against the actual
files on load, so a corrupt or stale index can never surface bad audio.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any

from tts_daemon.core.models import AudioClip, AudioFormat, SynthesisRequest

logger = logging.getLogger(__name__)

_INDEX_NAME = "index.json"
_INDEX_VERSION = 1


def default_cache_dir() -> Path:
    """``$XDG_CACHE_HOME/tts-daemon`` with the ``~/.cache`` fallback."""
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "tts-daemon"


class SynthesisCache:
    """A thread-safe, size-bounded cache mapping request fingerprints to clips."""

    def __init__(self, directory: Path, max_bytes: int) -> None:
        self._dir = Path(directory)
        self._max_bytes = max(0, int(max_bytes))
        self._lock = threading.Lock()
        self._entries: dict[str, dict[str, Any]] = {}
        self._hits = 0
        self._misses = 0
        with self._lock:
            self._load_locked()

    # ------------------------------------------------------------------ api

    def key(self, provider: str, request: SynthesisRequest, fingerprint: str = "") -> str:
        """SHA-256 over everything that affects the synthesized audio.

        ``fingerprint`` lets a provider fold in engine/voice-file state (piper
        mixes in its model file mtime) so that swapping a model invalidates the
        cache without a version bump.
        """
        material = json.dumps(
            {
                "provider": provider,
                "voice": request.voice,
                "speed": round(float(request.speed), 6),
                "options": request.options,
                "text": request.text,
                "fingerprint": fingerprint,
            },
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> AudioClip | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._misses += 1
                return None
            try:
                data = (self._dir / key).read_bytes()
            except OSError:
                self._drop_locked(key)  # vanished under us
                self._misses += 1
                return None
            if len(data) != entry["size"]:
                self._drop_locked(key)  # truncated / corrupted
                self._misses += 1
                return None
            entry["atime"] = time.time()
            self._hits += 1
            return AudioClip(data=data, format=AudioFormat(entry["format"]))

    def put(self, key: str, clip: AudioClip) -> None:
        data = clip.data
        # A clip that cannot fit under the whole budget is pointless to store.
        if self._max_bytes <= 0 or len(data) > self._max_bytes:
            return
        with self._lock:
            try:
                self._dir.mkdir(parents=True, exist_ok=True)
                path = self._dir / key
                tmp = path.with_name(key + ".tmp")
                tmp.write_bytes(data)
                os.replace(tmp, path)
            except OSError:
                logger.exception("Failed to write cache entry %s", key)
                with contextlib.suppress(OSError):
                    tmp.unlink()
                return
            self._entries[key] = {
                "format": clip.format.value,
                "size": len(data),
                "atime": time.time(),
            }
            self._evict_locked()
            self._save_locked()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            size = sum(entry["size"] for entry in self._entries.values())
            return {
                "entries": len(self._entries),
                "size_mb": round(size / (1024 * 1024), 3),
                "hits": self._hits,
                "misses": self._misses,
            }

    # ------------------------------------------------------------- internals

    def _drop_locked(self, key: str) -> None:
        self._entries.pop(key, None)
        with contextlib.suppress(OSError):
            (self._dir / key).unlink()
        self._save_locked()

    def _evict_locked(self) -> None:
        total = sum(entry["size"] for entry in self._entries.values())
        if total <= self._max_bytes:
            return
        # Least-recently-used first, until back under budget.
        for key, entry in sorted(self._entries.items(), key=lambda kv: kv[1]["atime"]):
            if total <= self._max_bytes:
                break
            total -= entry["size"]
            self._entries.pop(key, None)
            with contextlib.suppress(OSError):
                (self._dir / key).unlink()

    def _save_locked(self) -> None:
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            index = self._dir / _INDEX_NAME
            tmp = index.with_name(_INDEX_NAME + ".tmp")
            tmp.write_text(
                json.dumps({"version": _INDEX_VERSION, "entries": self._entries}),
                encoding="utf-8",
            )
            os.replace(tmp, index)
        except OSError:
            logger.exception("Failed to persist cache index")

    def _load_locked(self) -> None:
        index = self._dir / _INDEX_NAME
        try:
            raw = json.loads(index.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return  # missing or corrupt index: start empty, files become orphans
        entries = raw.get("entries") if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            return
        cleaned: dict[str, dict[str, Any]] = {}
        for key, entry in entries.items():
            if not (isinstance(entry, dict) and "size" in entry and "format" in entry):
                continue
            try:
                stat = (self._dir / key).stat()
            except OSError:
                continue  # index references a file that is gone
            if stat.st_size != entry["size"]:
                continue  # index disagrees with disk: distrust it
            entry.setdefault("atime", time.time())
            cleaned[key] = entry
        self._entries = cleaned
        self._evict_locked()  # honour a shrunken budget on startup
