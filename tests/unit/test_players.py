"""Audio players: command building, interruption, detection, and the factory."""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

from tests.conftest import make_clip
from tts_gateway.config import PlaybackConfig
from tts_gateway.core.errors import ConfigError, PlaybackError
from tts_gateway.core.models import AudioClip, AudioFormat
from tts_gateway.players import create_player
from tts_gateway.players.command import CommandPlayer
from tts_gateway.players.null import NullPlayer

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="tests drive POSIX commands")


class TestNullPlayer:
    def test_counts_and_never_blocks(self) -> None:
        player = NullPlayer()
        clip = make_clip()
        assert player.play(clip) is True
        assert player.play(clip) is True
        assert player.played_count == 2
        player.stop()  # no-op, must not raise
        assert player.availability().available


class TestCommandPlayerFileMode:
    def test_writes_temp_file_and_cleans_up(self, tmp_path: Path) -> None:
        captured = tmp_path / "captured.wav"
        record = tmp_path / "path.txt"
        player = CommandPlayer(
            ["/bin/sh", "-c", f'echo "$1" > {record} && cp "$1" {captured}', "play", "{file}"]
        )
        clip = make_clip("file mode")
        assert player.play(clip) is True
        assert captured.read_bytes() == clip.data
        temp_file = Path(record.read_text().strip())
        assert not temp_file.exists(), "temp audio file must be deleted after playback"


class TestCommandPlayerStdinMode:
    def test_pipes_bytes_when_no_placeholder(self, tmp_path: Path) -> None:
        captured = tmp_path / "captured.wav"
        player = CommandPlayer(["/bin/sh", "-c", f"cat > {captured}"])
        clip = make_clip("stdin mode")
        assert player.play(clip) is True
        assert captured.read_bytes() == clip.data


class TestCommandPlayerFailures:
    def test_nonzero_exit_raises_with_stderr(self) -> None:
        player = CommandPlayer(["/bin/sh", "-c", "echo kaboom >&2; exit 3"])
        with pytest.raises(PlaybackError, match=r"status 3.*kaboom"):
            player.play(make_clip())

    def test_missing_command_raises(self) -> None:
        player = CommandPlayer(["definitely-not-a-player-xyz"])
        with pytest.raises(PlaybackError, match="not found"):
            player.play(make_clip())

    def test_availability_of_missing_configured_command(self) -> None:
        player = CommandPlayer(["definitely-not-a-player-xyz"])
        availability = player.availability()
        assert not availability.available
        assert "not found on PATH" in availability.reason


class TestCommandPlayerStop:
    def test_stop_interrupts_and_returns_false(self) -> None:
        player = CommandPlayer(["/bin/sh", "-c", "sleep 5"])
        result: list[bool] = []
        thread = threading.Thread(target=lambda: result.append(player.play(make_clip())))
        started = time.monotonic()
        thread.start()
        time.sleep(0.2)  # let the subprocess start
        player.stop()
        thread.join(timeout=3)
        assert not thread.is_alive(), "play() must return promptly after stop()"
        assert result == [False]
        assert time.monotonic() - started < 4, "must not wait for the full sleep"

    def test_stop_with_nothing_playing_is_noop(self) -> None:
        CommandPlayer(["/bin/true"]).stop()


class TestDetection:
    def test_detects_only_installed_commands(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tts_gateway.players.command as command_module

        monkeypatch.setattr(
            command_module.shutil, "which", lambda name: "/usr/bin/x" if name == "ffplay" else None
        )
        candidates = CommandPlayer.detect_candidates()
        assert [candidate.executable for candidate in candidates] == ["ffplay"]

    def test_format_constraints_route_to_capable_command(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import tts_gateway.players.command as command_module

        installed = {"aplay": "/usr/bin/aplay", "mpv": "/usr/bin/mpv"}
        monkeypatch.setattr(command_module.shutil, "which", installed.get)
        player = CommandPlayer()
        assert player._argv_for(AudioFormat.WAV)[0] == "aplay"  # first capable match
        assert player._argv_for(AudioFormat.MP3)[0] == "mpv"  # aplay cannot decode mp3

    def test_no_commands_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import tts_gateway.players.command as command_module

        monkeypatch.setattr(command_module.shutil, "which", lambda name: None)
        player = CommandPlayer()
        availability = player.availability()
        assert not availability.available
        assert "playback.command" in availability.reason
        with pytest.raises(PlaybackError, match="No playback command found"):
            player.play(AudioClip(data=b"x", format=AudioFormat.WAV))


class TestCreatePlayer:
    def test_null_backend(self) -> None:
        assert isinstance(create_player(PlaybackConfig(backend="null")), NullPlayer)

    def test_command_backend_requires_command(self) -> None:
        with pytest.raises(ConfigError, match=r"playback\.command is not set"):
            create_player(PlaybackConfig(backend="command"))

    def test_command_backend_uses_command(self) -> None:
        player = create_player(PlaybackConfig(backend="command", command=["/bin/true"]))
        assert isinstance(player, CommandPlayer)

    def test_auto_prefers_configured_command(self) -> None:
        player = create_player(PlaybackConfig(backend="auto", command=["/bin/true"]))
        assert isinstance(player, CommandPlayer)
        assert player._configured_argv == ("/bin/true",)

    def test_auto_without_command_detects(self) -> None:
        assert isinstance(create_player(PlaybackConfig(backend="auto")), CommandPlayer)
