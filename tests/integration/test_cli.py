"""CLI behaviour that doesn't need a live server, plus client error paths."""

from __future__ import annotations

from pathlib import Path

import pytest

from tts_daemon.cli import build_parser, main
from tts_daemon.client import GatewayClient, GatewayClientError
from tts_daemon.config import EXAMPLE_CONFIG


class TestParser:
    def test_requires_a_command(self) -> None:
        with pytest.raises(SystemExit) as excinfo:
            build_parser().parse_args([])
        assert excinfo.value.code == 2

    def test_all_documented_commands_parse(self) -> None:
        parser = build_parser()
        for argv in (
            ["serve", "--host", "0.0.0.0", "--port", "6000"],
            ["speak", "hello", "world", "--interrupt", "--wait", "--speed", "1.5"],
            ["synthesize", "hi", "-o", "out.wav", "--provider", "tone"],
            ["stop", "--token", "abc"],
            ["status", "--json"],
            ["voices", "--provider", "piper"],
            ["providers"],
            ["init-config", "--force"],
        ):
            args = parser.parse_args(argv)
            assert callable(args.handler)

    def test_token_flag_overrides_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TTS_DAEMON_TOKEN", "from-env")
        parser = build_parser()  # env default captured at build time
        assert parser.parse_args(["status"]).token == "from-env"
        assert parser.parse_args(["status", "--token", "explicit"]).token == "explicit"


class TestInitConfig:
    def test_writes_example_config(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "config.yaml"
        assert main(["init-config", "--path", str(target)]) == 0
        assert target.read_text(encoding="utf-8") == EXAMPLE_CONFIG

    def test_refuses_to_overwrite_without_force(self, tmp_path: Path) -> None:
        target = tmp_path / "config.yaml"
        target.write_text("precious: true\n")
        assert main(["init-config", "--path", str(target)]) == 1
        assert target.read_text() == "precious: true\n"
        assert main(["init-config", "--path", str(target), "--force"]) == 0
        assert target.read_text(encoding="utf-8") == EXAMPLE_CONFIG


class TestClientErrors:
    def test_unreachable_server_has_actionable_message(self) -> None:
        client = GatewayClient("http://127.0.0.1:1", timeout=1)
        with pytest.raises(GatewayClientError, match="tts-daemon serve"):
            client.health()

    def test_cli_surfaces_client_error_as_exit_1(self, capsys: pytest.CaptureFixture) -> None:
        assert main(["stop", "--url", "http://127.0.0.1:1"]) == 1
        assert "error:" in capsys.readouterr().err

    def test_speak_without_text_or_stdin_exits_2(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sys

        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        with pytest.raises(SystemExit) as excinfo:
            main(["speak"])
        assert excinfo.value.code == 2
