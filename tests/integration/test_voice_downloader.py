"""Piper voice catalog + downloader, exercised against a local HTTP server.

No network in CI: a throwaway ``http.server`` serves a tiny manifest and fake
model files from a temp directory, mirroring the Hugging Face layout.
"""

from __future__ import annotations

import functools
import json
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from tts_daemon import cli
from tts_daemon.voices import (
    VoiceError,
    download_voice,
    fetch_catalog,
    filter_voices,
    parse_catalog,
)

_MANIFEST = {
    "en_US-lessac-medium": {
        "language": {"code": "en_US"},
        "quality": "medium",
        "num_speakers": 1,
        "files": {
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx": {"size_bytes": 20},
            "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json": {"size_bytes": 12},
        },
    },
    "it_IT-riccardo-x_low": {
        "language": {"code": "it_IT"},
        "quality": "x_low",
        "num_speakers": 1,
        "files": {
            "it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx": {"size_bytes": 15},
            "it/it_IT/riccardo/x_low/it_IT-riccardo-x_low.onnx.json": {"size_bytes": 8},
        },
    },
    # Declared size deliberately larger than the file we write, to test the
    # incomplete-download guard.
    "broken-voice": {
        "language": {"code": "xx_XX"},
        "quality": "low",
        "files": {
            "x/broken.onnx": {"size_bytes": 999},
            "x/broken.onnx.json": {"size_bytes": 3},
        },
    },
}


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # silence the test server
        pass


@pytest.fixture()
def catalog_server(tmp_path: Path) -> Iterator[str]:
    root = tmp_path / "srv"
    root.mkdir()
    (root / "voices.json").write_text(json.dumps(_MANIFEST), encoding="utf-8")
    for entry in _MANIFEST.values():
        for path, meta in entry["files"].items():
            file = root / path
            file.parent.mkdir(parents=True, exist_ok=True)
            declared = meta["size_bytes"]
            # 'broken' files are written short on purpose; the rest match.
            size = 5 if path.startswith("x/") else declared
            file.write_bytes(b"a" * size)

    handler = functools.partial(_QuietHandler, directory=str(root))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        thread.join()


# ------------------------------------------------------------------- parsing


def test_parse_catalog_skips_incomplete_entries() -> None:
    entries = parse_catalog(
        {
            "good": {"files": {"a.onnx": {"size_bytes": 1}, "a.onnx.json": {"size_bytes": 2}}},
            "no-config": {"files": {"b.onnx": {"size_bytes": 1}}},
            "not-a-dict": 5,
        }
    )
    assert set(entries) == {"good"}
    assert entries["good"].size_bytes == 3


def test_parse_catalog_rejects_non_object() -> None:
    with pytest.raises(VoiceError):
        parse_catalog([1, 2, 3])


def test_fetch_and_filter(catalog_server: str) -> None:
    catalog = fetch_catalog(catalog_server)
    assert set(catalog) == {"en_US-lessac-medium", "it_IT-riccardo-x_low", "broken-voice"}
    italian = filter_voices(catalog, "it")
    assert [voice.id for voice in italian] == ["it_IT-riccardo-x_low"]
    entry = catalog["en_US-lessac-medium"]
    assert entry.language == "en_US"
    assert entry.quality == "medium"
    assert entry.size_bytes == 32


# ------------------------------------------------------------------ download


def test_download_writes_model_and_config(catalog_server: str, tmp_path: Path) -> None:
    models = tmp_path / "models"
    seen: list[str] = []
    written = download_voice(
        "en_US-lessac-medium",
        models,
        base_url=catalog_server,
        progress=lambda name, done, total: seen.append(name),
    )
    names = {path.name for path in written}
    assert names == {"en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json"}
    assert (models / "en_US-lessac-medium.onnx").stat().st_size == 20
    assert (models / "en_US-lessac-medium.onnx.json").stat().st_size == 12
    assert seen  # progress was reported
    assert not list(models.glob("*.part"))  # temp files cleaned up


def test_download_is_idempotent(catalog_server: str, tmp_path: Path) -> None:
    models = tmp_path / "models"
    download_voice("en_US-lessac-medium", models, base_url=catalog_server)
    seen: list[str] = []
    download_voice(
        "en_US-lessac-medium",
        models,
        base_url=catalog_server,
        progress=lambda name, done, total: seen.append(name),
    )
    assert seen == []  # nothing re-downloaded when files already present


def test_download_force_refetches(catalog_server: str, tmp_path: Path) -> None:
    models = tmp_path / "models"
    download_voice("en_US-lessac-medium", models, base_url=catalog_server)
    seen: list[str] = []
    download_voice(
        "en_US-lessac-medium",
        models,
        base_url=catalog_server,
        force=True,
        progress=lambda name, done, total: seen.append(name),
    )
    assert seen  # --force re-downloads


def test_unknown_voice_suggests_matches(catalog_server: str, tmp_path: Path) -> None:
    with pytest.raises(VoiceError, match=r"Did you mean.*en_US-lessac-medium"):
        download_voice("en_US-lessac-mediumm", tmp_path, base_url=catalog_server)


def test_incomplete_download_is_detected(catalog_server: str, tmp_path: Path) -> None:
    models = tmp_path / "models"
    with pytest.raises(VoiceError, match="incomplete"):
        download_voice("broken-voice", models, base_url=catalog_server)
    assert not list(models.glob("*.part"))  # partial file removed


def test_offline_catalog_is_actionable() -> None:
    # Port 1 is closed: connection refused surfaces as an actionable message.
    with pytest.raises(VoiceError, match="network"):
        fetch_catalog("http://127.0.0.1:1", timeout=2.0)


# ---------------------------------------------------------------- CLI --list


def test_cli_download_list(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    catalog = parse_catalog(_MANIFEST)
    monkeypatch.setattr("tts_daemon.voices.fetch_catalog", lambda *a, **k: catalog)
    assert cli.main(["download", "--list"]) == 0
    out = capsys.readouterr().out
    assert "en_US-lessac-medium" in out
    assert "it_IT-riccardo-x_low" in out

    monkeypatch.setattr("tts_daemon.voices.fetch_catalog", lambda *a, **k: catalog)
    assert cli.main(["download", "--list", "--language", "it"]) == 0
    out = capsys.readouterr().out
    assert "it_IT-riccardo-x_low" in out
    assert "en_US-lessac-medium" not in out


def test_cli_download_without_voice_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    monkeypatch.setattr("tts_daemon.voices.fetch_catalog", lambda *a, **k: parse_catalog(_MANIFEST))
    assert cli.main(["download"]) == 2
    assert "voice id" in capsys.readouterr().err
