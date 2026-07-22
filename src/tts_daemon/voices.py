"""Download engines: Piper voices from ``rhasspy/piper-voices``, plus kokoro.

Pure standard library (``urllib``) so the CLI ``download`` command works without
the server's dependencies — the same rule the HTTP client follows. Nothing here
imports FastAPI or the gateway internals at import time (:class:`KokoroDownloader`
reads a couple of constants from the kokoro provider lazily, in its constructor).

:class:`VoiceCatalog` serves the ``voices.json`` manifest published under the
Hugging Face repo ``rhasspy/piper-voices``; each voice lists its ``.onnx`` model
and the ``.onnx.json`` config, with a size and an md5 digest per file, resolved
under the same repo. :class:`KokoroDownloader` fetches the fixed model+voices
pair from the pinned kokoro-onnx GitHub release (no manifest, no digest — the
``Content-Length`` is all that can be checked). Both stream each file to a
``*.part`` sidecar and rename it into place only once the byte count (and the
digest, when there is one) check out, so an interrupted or corrupted download
never leaves a half-written model that the engine would choke on.
"""

from __future__ import annotations

import difflib
import hashlib
import http.client
import json
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Repo root that hosts both ``voices.json`` and the model files.
DEFAULT_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
MANIFEST_NAME = "voices.json"

_DOWNLOAD_CHUNK = 64 * 1024
_DEFAULT_TIMEOUT = 60.0

#: Progress callback signature: ``(filename, bytes_done, bytes_total)``.
ProgressCallback = Callable[[str, int, int], None]


class VoiceCatalogError(Exception):
    """Fetching the catalog or a voice failed (offline, unknown id, bad size)."""


@dataclass(frozen=True)
class VoiceInfo:
    """One downloadable Piper voice, distilled from the manifest entry."""

    id: str
    language: str | None
    quality: str | None
    num_speakers: int
    onnx_path: str
    config_path: str
    onnx_size: int = 0
    config_size: int = 0
    onnx_md5: str | None = None
    config_md5: str | None = None

    @property
    def size_bytes(self) -> int:
        return self.onnx_size + self.config_size

    @property
    def size_mb(self) -> float:
        return self.size_bytes / (1024 * 1024)


@dataclass(frozen=True)
class DownloadResult:
    """Outcome of a :meth:`VoiceCatalog.download` call."""

    voice_id: str
    models_dir: Path
    paths: list[Path] = field(default_factory=list)
    downloaded: list[Path] = field(default_factory=list)

    @property
    def skipped(self) -> bool:
        """True when every file was already present (nothing fetched)."""
        return not self.downloaded


@dataclass(frozen=True)
class KokoroDownloadResult:
    """Outcome of a :meth:`KokoroDownloader.download` call."""

    paths: list[Path] = field(default_factory=list)
    downloaded: list[Path] = field(default_factory=list)

    @property
    def skipped(self) -> bool:
        """True when both files were already present (nothing fetched)."""
        return not self.downloaded


def _as_int(value: object, default: int = 0) -> int:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


def _as_md5(value: object) -> str | None:
    """A 32-hex-digit digest from the manifest, or None if absent/malformed."""
    if not isinstance(value, str):
        return None
    digest = value.strip().lower()
    if len(digest) != 32 or any(char not in "0123456789abcdef" for char in digest):
        return None
    return digest


def _language_code(block: object) -> str | None:
    if isinstance(block, dict):
        code = block.get("code") or block.get("family")
        return str(code) if code else None
    if isinstance(block, str):
        return block
    return None


def _find_file(files: dict[str, Any], filename: str) -> str | None:
    """The manifest path whose basename is ``filename`` (paths are repo-relative)."""
    for path in files:
        if path.rsplit("/", 1)[-1] == filename:
            return path
    return None


def parse_catalog(manifest: dict[str, Any]) -> dict[str, VoiceInfo]:
    """Turn the raw ``voices.json`` mapping into ``{id: VoiceInfo}``.

    Entries that do not expose both an ``.onnx`` and a matching ``.onnx.json``
    are skipped rather than raising: a partial manifest still lists the voices
    that can actually be installed.
    """
    catalog: dict[str, VoiceInfo] = {}
    for key, entry in manifest.items():
        if not isinstance(entry, dict):
            continue
        files = entry.get("files")
        if not isinstance(files, dict):
            continue
        voice_id = str(entry.get("key") or key)
        onnx_path = _find_file(files, f"{voice_id}.onnx")
        config_path = _find_file(files, f"{voice_id}.onnx.json")
        if not onnx_path or not config_path:
            continue
        catalog[voice_id] = VoiceInfo(
            id=voice_id,
            language=_language_code(entry.get("language")),
            quality=(str(entry["quality"]) if entry.get("quality") else None),
            num_speakers=_as_int(entry.get("num_speakers"), default=1),
            onnx_path=onnx_path,
            config_path=config_path,
            onnx_size=_as_int(files.get(onnx_path, {}).get("size_bytes")),
            config_size=_as_int(files.get(config_path, {}).get("size_bytes")),
            onnx_md5=_as_md5(files.get(onnx_path, {}).get("md5_digest")),
            config_md5=_as_md5(files.get(config_path, {}).get("md5_digest")),
        )
    return catalog


def closest_matches(query: str, ids: Iterable[str], limit: int = 6) -> list[str]:
    """Voice ids most likely meant by ``query`` — substring hits first, then fuzzy."""
    all_ids = list(ids)
    lowered = query.lower()
    ordered: list[str] = [i for i in all_ids if lowered in i.lower()]
    for candidate in difflib.get_close_matches(query, all_ids, n=limit, cutoff=0.4):
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered[:limit]


def _language_matches(voice_language: str | None, query: str) -> bool:
    if not voice_language:
        return False
    voice = voice_language.lower()
    wanted = query.lower()
    return voice == wanted or voice.startswith(f"{wanted}_") or voice.split("_")[0] == wanted


class VoiceCatalog:
    """Reads the Piper voice manifest and downloads voices, using only stdlib.

    The manifest is fetched once and cached for the life of the instance (the
    CLI builds one per invocation), so ``--list`` followed by a ``download`` in
    the same process hits the network a single time for the catalog.
    """

    def __init__(
        self, base_url: str = DEFAULT_BASE_URL, *, timeout: float = _DEFAULT_TIMEOUT
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._voices: dict[str, VoiceInfo] | None = None

    def voices(self) -> dict[str, VoiceInfo]:
        """The whole catalog as ``{id: VoiceInfo}`` (fetched once, then cached)."""
        if self._voices is None:
            raw = self._fetch_json(f"{self.base_url}/{MANIFEST_NAME}")
            if not isinstance(raw, dict):
                raise VoiceCatalogError("the Piper voices manifest is not a JSON object")
            self._voices = parse_catalog(raw)
        return self._voices

    def list(self, language: str | None = None) -> list[VoiceInfo]:
        """Catalog voices sorted by id, optionally filtered by language.

        ``language`` accepts a family (``it``) or a full code (``it_IT``).
        """
        voices = self.voices().values()
        if language:
            voices = [v for v in voices if _language_matches(v.language, language)]
        return sorted(voices, key=lambda voice: voice.id)

    def get(self, voice_id: str) -> VoiceInfo:
        """Look up one voice, raising a helpful error (with suggestions) if absent."""
        voices = self.voices()
        info = voices.get(voice_id)
        if info is None:
            raise VoiceCatalogError(_unknown_voice_message(voice_id, voices))
        return info

    def download(
        self,
        voice_id: str,
        models_dir: str | Path,
        *,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> DownloadResult:
        """Download ``voice_id`` (``.onnx`` + ``.onnx.json``) into ``models_dir``.

        The directory is created if missing. Already-present files are skipped
        unless ``force`` is set. Each file streams to ``<name>.part`` and is
        renamed into place only once its byte count checks out, so the operation
        is safe to interrupt and repeat.
        """
        info = self.get(voice_id)
        directory = Path(models_dir).expanduser()
        directory.mkdir(parents=True, exist_ok=True)

        specs = (
            (info.onnx_path, f"{info.id}.onnx", info.onnx_size, info.onnx_md5),
            (info.config_path, f"{info.id}.onnx.json", info.config_size, info.config_md5),
        )
        paths: list[Path] = []
        downloaded: list[Path] = []
        for remote_path, name, size, md5 in specs:
            dest = directory / name
            paths.append(dest)
            if dest.is_file() and not force:
                continue
            self._download_file(
                f"{self.base_url}/{remote_path}",
                dest,
                expected_size=size,
                expected_md5=md5,
                progress=progress,
            )
            downloaded.append(dest)
        return DownloadResult(
            voice_id=info.id, models_dir=directory, paths=paths, downloaded=downloaded
        )

    # ------------------------------------------------------------- internals

    def _fetch_json(self, url: str) -> Any:
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as response:
                data = response.read()
        except urllib.error.URLError as exc:
            raise VoiceCatalogError(_offline_message(exc)) from exc
        try:
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise VoiceCatalogError(
                f"the Piper voices manifest at {url} is not valid JSON: {exc}"
            ) from exc

    def _download_file(
        self,
        url: str,
        dest: Path,
        *,
        expected_size: int = 0,
        expected_md5: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> None:
        _stream_to_file(
            url,
            dest,
            timeout=self.timeout,
            expected_size=expected_size,
            expected_md5=expected_md5,
            progress=progress,
        )


class KokoroDownloader:
    """Downloads the kokoro model+voices pair from the pinned kokoro-onnx release.

    Kokoro needs two artifacts — an ONNX model and a voices file — published at
    fixed names under a single GitHub release. Unlike the Piper catalog there is
    no manifest and **no per-file digest**, so integrity checking is limited to
    the server's ``Content-Length`` (documented in ``docs/configuration.md``):
    a file whose byte count does not match what the server promised is rejected
    rather than installed. Each file streams to a ``*.part`` sidecar and is
    renamed into place atomically, so the download is safe to interrupt and
    repeat — the same guarantees the Piper downloader gives.
    """

    def __init__(self, base_url: str | None = None, *, timeout: float = _DEFAULT_TIMEOUT) -> None:
        # The release URL and file names are owned by the provider (single source
        # of truth); import them lazily so this module stays free of gateway
        # internals at import time, mirroring the CLI's lazy piper import.
        from tts_daemon.providers.kokoro import _MODEL_FILE, _RELEASE_URL, _VOICES_FILE

        self.base_url = (base_url or _RELEASE_URL).rstrip("/")
        self.model_file = _MODEL_FILE
        self.voices_file = _VOICES_FILE
        self.timeout = timeout

    def download(
        self,
        model_path: str | Path,
        voices_path: str | Path,
        *,
        force: bool = False,
        progress: ProgressCallback | None = None,
    ) -> KokoroDownloadResult:
        """Download the model to ``model_path`` and the voices to ``voices_path``.

        Parent directories are created as needed. A file already on disk is
        skipped unless ``force`` is set, so the command is idempotent — the two
        destinations mirror ``providers.kokoro.model_path`` / ``voices_path``.
        """
        specs = (
            (f"{self.base_url}/{self.model_file}", Path(model_path).expanduser()),
            (f"{self.base_url}/{self.voices_file}", Path(voices_path).expanduser()),
        )
        paths: list[Path] = []
        downloaded: list[Path] = []
        for url, dest in specs:
            paths.append(dest)
            if dest.is_file() and not force:
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            _stream_to_file(
                url,
                dest,
                timeout=self.timeout,
                progress=progress,
                offline_message=_kokoro_offline_message,
            )
            downloaded.append(dest)
        return KokoroDownloadResult(paths=paths, downloaded=downloaded)


def _stream_to_file(
    url: str,
    dest: Path,
    *,
    timeout: float,
    expected_size: int = 0,
    expected_md5: str | None = None,
    progress: ProgressCallback | None = None,
    offline_message: Callable[[Exception], str] | None = None,
) -> None:
    """Stream ``url`` into ``dest`` via a ``*.part`` sidecar, then verify + rename.

    Shared by the Piper catalog and the kokoro downloader. The file is renamed
    into place only once its byte count (and md5, when the caller supplies one)
    check out, so an interrupted or corrupt download never leaves a
    usable-looking file. ``offline_message`` lets each caller phrase the
    unreachable-host error in its own terms (Piper catalog vs. kokoro release).
    """
    offline = offline_message or _offline_message
    part = dest.with_name(f"{dest.name}.part")
    header_len = 0
    written = 0
    # Integrity check, not a security boundary: any digest ships alongside the
    # file over the same TLS connection, so this catches corruption and stale
    # mirrors, not a hostile server.
    digest = hashlib.md5(usedforsecurity=False)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            header_len = _as_int(response.headers.get("Content-Length"))
            total = expected_size or header_len
            with open(part, "wb") as handle:
                while True:
                    chunk = response.read(_DOWNLOAD_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                    if progress is not None and total:
                        progress(dest.name, min(written, total), total)
    except (urllib.error.URLError, http.client.IncompleteRead) as exc:
        part.unlink(missing_ok=True)
        raise VoiceCatalogError(offline(exc)) from exc
    except OSError as exc:
        part.unlink(missing_ok=True)
        raise VoiceCatalogError(f"cannot write {dest}: {exc}") from exc

    problem = _verify_size(url, written, header_len, expected_size) or _verify_md5(
        url, digest.hexdigest(), expected_md5
    )
    if problem is not None:
        part.unlink(missing_ok=True)
        raise VoiceCatalogError(problem)
    part.replace(dest)  # atomic on the same filesystem


def _verify_size(url: str, written: int, header_len: int, expected_size: int) -> str | None:
    """Return an error message when the download does not match what was promised."""
    if header_len and written != header_len:
        return f"download of {url} was truncated: got {written} of {header_len} bytes"
    if expected_size and header_len and header_len != expected_size:
        return (
            f"size mismatch for {url}: the server sent {header_len} bytes but the catalog "
            f"lists {expected_size}; the voice may have changed — retry, or download it manually"
        )
    if expected_size and not header_len and written != expected_size:
        return f"download of {url} was truncated: got {written} of {expected_size} bytes"
    return None


def _verify_md5(url: str, actual: str, expected: str | None) -> str | None:
    """Return an error message when the bytes do not match the catalog digest."""
    if expected and actual != expected:
        return (
            f"checksum mismatch for {url}: got md5 {actual}, the catalog lists {expected}; "
            "the download is corrupt or the voice changed — retry, and if it keeps failing "
            "download the .onnx and .onnx.json files manually"
        )
    return None


def _offline_message(exc: Exception) -> str:
    reason = getattr(exc, "reason", None) or exc
    return (
        f"cannot reach the Piper voice catalog ({reason}). Check your internet "
        "connection, or download the .onnx and .onnx.json files manually into your "
        "models_dir."
    )


def _kokoro_offline_message(exc: Exception) -> str:
    reason = getattr(exc, "reason", None) or exc
    return (
        f"cannot download the kokoro model files ({reason}). Check your internet "
        "connection, or download kokoro-v1.0.onnx and voices-v1.0.bin manually "
        "into your kokoro directory."
    )


def _unknown_voice_message(voice_id: str, voices: dict[str, VoiceInfo]) -> str:
    suggestions = closest_matches(voice_id, voices)
    hint = f" Did you mean: {', '.join(suggestions)}?" if suggestions else ""
    return (
        f"unknown Piper voice {voice_id!r}.{hint} "
        "Run 'tts-daemon download --list' to browse the catalog."
    )
