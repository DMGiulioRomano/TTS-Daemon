"""Built-in Piper voice catalog and downloader (pure standard library).

The biggest onboarding cliff is voice setup. This module lets the CLI manage
its own voices::

    tts-daemon download --list --language it
    tts-daemon download it_IT-riccardo-x_low
    tts-daemon speak "Ora parlo italiano"

It fetches the community voice manifest published by the ``rhasspy/piper-voices``
project on Hugging Face and downloads the ``.onnx`` model plus its ``.onnx.json``
config into the configured models directory. Everything here uses ``urllib``
(no new runtime dependency) and imports nothing from FastAPI, so it stays usable
from the CLI without pulling in the server stack.
"""

from __future__ import annotations

import contextlib
import difflib
import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

#: Hugging Face raw-content base for the community voice repository.
DEFAULT_BASE_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

_CHUNK = 65_536
_USER_AGENT = "tts-daemon-voice-downloader"

#: ``progress(filename, downloaded_bytes, total_bytes_or_none)``.
ProgressCallback = Callable[[str, int, int | None], None]


class VoiceError(Exception):
    """A voice catalog or download problem with an actionable message."""


@dataclass(frozen=True)
class VoiceEntry:
    """One downloadable voice from the manifest."""

    id: str
    language: str | None
    quality: str | None
    num_speakers: int
    onnx: tuple[str, int | None]  # (remote path, size in bytes or None)
    config: tuple[str, int | None]

    @property
    def size_bytes(self) -> int:
        return (self.onnx[1] or 0) + (self.config[1] or 0)


def catalog_url(base_url: str = DEFAULT_BASE_URL) -> str:
    return f"{base_url}/voices.json"


def fetch_catalog(
    base_url: str = DEFAULT_BASE_URL, *, timeout: float = 30.0
) -> dict[str, VoiceEntry]:
    """Download and parse the voice manifest into ``{id: VoiceEntry}``."""
    url = catalog_url(base_url)
    try:
        with urlopen(_request(url), timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except URLError as exc:
        raise VoiceError(
            f"could not reach the voice catalog at {url}: {exc.reason}. "
            "Check your network connection."
        ) from exc
    except (ValueError, UnicodeDecodeError) as exc:
        raise VoiceError(f"the voice catalog at {url} is not valid JSON: {exc}") from exc
    return parse_catalog(raw)


def parse_catalog(raw: object) -> dict[str, VoiceEntry]:
    """Turn the manifest mapping into ``VoiceEntry`` objects (best-effort)."""
    if not isinstance(raw, dict):
        raise VoiceError("the voice catalog has an unexpected shape (expected a JSON object)")
    entries: dict[str, VoiceEntry] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        onnx = config = None
        files = value.get("files")
        if isinstance(files, dict):
            for path, meta in files.items():
                size = meta.get("size_bytes") if isinstance(meta, dict) else None
                if path.endswith(".onnx.json"):
                    config = (path, size)
                elif path.endswith(".onnx"):
                    onnx = (path, size)
        if onnx is None or config is None:
            continue  # a voice without both files is not installable
        language = value.get("language")
        code = language.get("code") if isinstance(language, dict) else None
        entries[str(key)] = VoiceEntry(
            id=str(key),
            language=code,
            quality=value.get("quality"),
            num_speakers=int(value.get("num_speakers", 1) or 1),
            onnx=onnx,
            config=config,
        )
    return entries


def filter_voices(entries: dict[str, VoiceEntry], language: str | None = None) -> list[VoiceEntry]:
    """Voices sorted by id, optionally restricted to a language code prefix."""
    values = sorted(entries.values(), key=lambda entry: entry.id)
    if not language:
        return values
    prefix = language.lower()
    return [entry for entry in values if (entry.language or "").lower().startswith(prefix)]


def download_voice(
    voice_id: str,
    models_dir: Path | str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    catalog: dict[str, VoiceEntry] | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
    timeout: float = 30.0,
) -> list[Path]:
    """Download ``voice_id``'s model and config into ``models_dir``.

    Idempotent: an already-present, correctly-sized file is skipped unless
    ``force`` is set. Returns the paths that now hold the voice. Raises
    :class:`VoiceError` (unknown id with suggestions, network failure, or a
    size mismatch) with a message safe to show the user.
    """
    entries = catalog if catalog is not None else fetch_catalog(base_url, timeout=timeout)
    entry = _lookup(entries, voice_id)
    directory = Path(models_dir).expanduser()
    directory.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for remote_path, size in (entry.onnx, entry.config):
        dest = directory / Path(remote_path).name
        if dest.exists() and not force and (size is None or dest.stat().st_size == size):
            written.append(dest)
            continue
        url = f"{base_url}/{remote_path}"
        _download_file(url, dest, size, timeout=timeout, progress=progress)
        written.append(dest)
    return written


# --------------------------------------------------------------------- internals


def _request(url: str) -> Request:
    return Request(url, headers={"User-Agent": _USER_AGENT})


def _lookup(entries: dict[str, VoiceEntry], voice_id: str) -> VoiceEntry:
    if voice_id in entries:
        return entries[voice_id]
    matches = difflib.get_close_matches(voice_id, list(entries), n=5)
    hint = f" Did you mean: {', '.join(matches)}?" if matches else ""
    raise VoiceError(
        f"unknown voice {voice_id!r}.{hint} "
        "Run `tts-daemon download --list` to see what is available."
    )


def _download_file(
    url: str,
    dest: Path,
    expected_size: int | None,
    *,
    timeout: float,
    progress: ProgressCallback | None,
) -> None:
    tmp = dest.with_name(dest.name + ".part")
    name = dest.name
    try:
        with urlopen(_request(url), timeout=timeout) as response:
            total = expected_size if expected_size is not None else _header_length(response)
            downloaded = 0
            with open(tmp, "wb") as handle:
                while True:
                    chunk = response.read(_CHUNK)
                    if not chunk:
                        break
                    handle.write(chunk)
                    downloaded += len(chunk)
                    if progress is not None:
                        progress(name, downloaded, total)
    except URLError as exc:
        _cleanup(tmp)
        raise VoiceError(
            f"could not download {name} from {url}: {exc.reason}. Check your network connection."
        ) from exc
    except OSError as exc:
        _cleanup(tmp)
        raise VoiceError(f"could not write {dest}: {exc}") from exc

    if expected_size is not None and downloaded != expected_size:
        _cleanup(tmp)
        raise VoiceError(
            f"download of {name} is incomplete (got {downloaded} of {expected_size} bytes); "
            "re-run to try again."
        )
    os.replace(tmp, dest)


def _header_length(response: object) -> int | None:
    raw = getattr(response, "headers", None)
    length = raw.get("Content-Length") if raw is not None else None
    try:
        return int(length) if length is not None else None
    except (TypeError, ValueError):
        return None


def _cleanup(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink()
