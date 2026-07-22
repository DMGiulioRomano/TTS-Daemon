"""Kokoro model/voices downloader, exercised against a local HTTP server.

Same hermetic pattern as ``test_voices.py``: a throwaway ``http.server`` serves
the two kokoro release artifacts on localhost, so no test touches the network.
The kokoro release ships no manifest and no per-file digest, so verification is
limited to the server's ``Content-Length`` — these tests pin that a truncated
download is refused and that the pair is otherwise fetched idempotently.
"""

from __future__ import annotations

import functools
import http.server
import threading
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

from tts_daemon.providers.kokoro import _MODEL_FILE, _VOICES_FILE
from tts_daemon.voices import KokoroDownloader, VoiceCatalogError

MODEL_BYTES = b"KOKORO-ONNX-MODEL-BYTES" * 64
VOICES_BYTES = b"KOKORO-VOICES-BIN-BYTES" * 32


class _QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args: object) -> None:  # keep test output clean
        pass


def _serve(root: Path) -> tuple[str, http.server.HTTPServer, threading.Thread]:
    handler = functools.partial(_QuietHandler, directory=str(root))
    httpd = http.server.HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return f"http://127.0.0.1:{httpd.server_address[1]}", httpd, thread


@pytest.fixture()
def server(tmp_path: Path) -> Iterator[SimpleNamespace]:
    root = tmp_path / "release"
    root.mkdir()
    (root / _MODEL_FILE).write_bytes(MODEL_BYTES)
    (root / _VOICES_FILE).write_bytes(VOICES_BYTES)
    base_url, httpd, thread = _serve(root)
    try:
        yield SimpleNamespace(base_url=base_url, root=root)
    finally:
        httpd.shutdown()
        thread.join()


@pytest.fixture()
def downloader(server: SimpleNamespace) -> KokoroDownloader:
    return KokoroDownloader(server.base_url, timeout=5.0)


class TestDownload:
    def test_downloads_both_files(self, downloader: KokoroDownloader, tmp_path: Path) -> None:
        kokoro_dir = tmp_path / "kokoro"
        model = kokoro_dir / _MODEL_FILE
        voices = kokoro_dir / _VOICES_FILE

        result = downloader.download(model, voices)

        assert model.read_bytes() == MODEL_BYTES
        assert voices.read_bytes() == VOICES_BYTES
        assert result.skipped is False
        assert set(result.downloaded) == {model, voices}
        assert set(result.paths) == {model, voices}
        assert list(kokoro_dir.glob("*.part")) == []  # no leftover partials

    def test_creates_missing_parent_dirs(
        self, downloader: KokoroDownloader, tmp_path: Path
    ) -> None:
        model = tmp_path / "deep" / "nested" / _MODEL_FILE
        voices = tmp_path / "other" / "place" / _VOICES_FILE
        downloader.download(model, voices)
        assert model.is_file()
        assert voices.is_file()

    def test_honours_independent_paths(self, downloader: KokoroDownloader, tmp_path: Path) -> None:
        # model_path and voices_path can point at different directories (the
        # provider allows overriding each independently).
        model = tmp_path / "models" / "custom-model.onnx"
        voices = tmp_path / "voices" / "custom-voices.bin"
        downloader.download(model, voices)
        assert model.read_bytes() == MODEL_BYTES
        assert voices.read_bytes() == VOICES_BYTES

    def test_idempotent_skips_when_present(
        self, downloader: KokoroDownloader, tmp_path: Path
    ) -> None:
        model = tmp_path / _MODEL_FILE
        voices = tmp_path / _VOICES_FILE
        downloader.download(model, voices)
        stamp = model.stat().st_mtime_ns

        result = downloader.download(model, voices)
        assert result.skipped is True
        assert result.downloaded == []
        assert model.stat().st_mtime_ns == stamp  # untouched

    def test_force_refetches(self, downloader: KokoroDownloader, tmp_path: Path) -> None:
        model = tmp_path / _MODEL_FILE
        voices = tmp_path / _VOICES_FILE
        downloader.download(model, voices)
        result = downloader.download(model, voices, force=True)
        assert result.skipped is False
        assert set(result.downloaded) == {model, voices}

    def test_only_missing_file_is_fetched(
        self, downloader: KokoroDownloader, tmp_path: Path
    ) -> None:
        # The model is already there; only the voices file should be downloaded.
        model = tmp_path / _MODEL_FILE
        voices = tmp_path / _VOICES_FILE
        model.write_bytes(MODEL_BYTES)

        result = downloader.download(model, voices)
        assert result.downloaded == [voices]
        assert result.skipped is False
        assert voices.read_bytes() == VOICES_BYTES

    def test_progress_callback_receives_totals(
        self, downloader: KokoroDownloader, tmp_path: Path
    ) -> None:
        seen: list[tuple[str, int, int]] = []
        downloader.download(
            tmp_path / _MODEL_FILE,
            tmp_path / _VOICES_FILE,
            progress=lambda *args: seen.append(args),
        )
        assert seen  # at least one progress tick
        assert all(0 <= done <= total for _, done, total in seen)
        assert seen[-1][1] == seen[-1][2]  # last tick reaches 100%


class TestVerification:
    def test_truncated_download_is_rejected(self, tmp_path: Path) -> None:
        # The server advertises a Content-Length larger than the body it sends,
        # so the download must refuse rather than install a truncated model.
        class _ShortBodyHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = b"only-a-few-bytes"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body) + 4096))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: object) -> None:
                pass

        httpd = http.server.HTTPServer(("127.0.0.1", 0), _ShortBodyHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            base_url = f"http://127.0.0.1:{httpd.server_address[1]}"
            downloader = KokoroDownloader(base_url, timeout=5.0)
            kokoro_dir = tmp_path / "kokoro"
            with pytest.raises(VoiceCatalogError):
                downloader.download(kokoro_dir / _MODEL_FILE, kokoro_dir / _VOICES_FILE)
            # Nothing installed and no partial left behind.
            assert not (kokoro_dir / _MODEL_FILE).exists()
            assert list(kokoro_dir.glob("*.part")) == []
        finally:
            httpd.shutdown()
            thread.join()


class TestOffline:
    def test_unreachable_release_is_actionable(self, tmp_path: Path) -> None:
        # Port 1 is unusable, so the fetch fails fast with a friendly message
        # that names the two files to grab by hand.
        downloader = KokoroDownloader("http://127.0.0.1:1", timeout=1.0)
        with pytest.raises(VoiceCatalogError, match="cannot download the kokoro model files"):
            downloader.download(tmp_path / _MODEL_FILE, tmp_path / _VOICES_FILE)
        assert list(tmp_path.glob("*.part")) == []


class TestDownloadKokoroCli:
    """The `tts-daemon download kokoro` command, pointed at the local server."""

    @pytest.fixture()
    def cli_downloader(self, server: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
        from tts_daemon import cli

        monkeypatch.setattr(
            cli,
            "KokoroDownloader",
            functools.partial(KokoroDownloader, server.base_url, timeout=5.0),
        )

    def test_download_into_models_dir(
        self, cli_downloader: None, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from tts_daemon.cli import main

        kokoro_dir = tmp_path / "k"
        assert main(["download", "kokoro", "--models-dir", str(kokoro_dir)]) == 0
        assert (kokoro_dir / _MODEL_FILE).read_bytes() == MODEL_BYTES
        assert (kokoro_dir / _VOICES_FILE).read_bytes() == VOICES_BYTES
        out = capsys.readouterr().out
        assert "downloaded the kokoro model and voices" in out
        assert "--provider kokoro" in out

    def test_idempotent_reports_present(
        self, cli_downloader: None, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from tts_daemon.cli import main

        kokoro_dir = tmp_path / "k"
        assert main(["download", "kokoro", "--models-dir", str(kokoro_dir)]) == 0
        capsys.readouterr()  # drain
        assert main(["download", "kokoro", "--models-dir", str(kokoro_dir)]) == 0
        out = capsys.readouterr().out
        assert "already present" in out
        assert "(use --force to refetch)" in out

    def test_force_refetches(
        self, cli_downloader: None, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        from tts_daemon.cli import main

        kokoro_dir = tmp_path / "k"
        assert main(["download", "kokoro", "--models-dir", str(kokoro_dir)]) == 0
        capsys.readouterr()
        assert main(["download", "kokoro", "--models-dir", str(kokoro_dir), "--force"]) == 0
        out = capsys.readouterr().out
        assert "downloaded the kokoro model and voices" in out
