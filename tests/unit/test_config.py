"""Configuration loading: defaults, YAML file, env overrides, and errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from tts_gateway.config import (
    EXAMPLE_CONFIG,
    GatewayConfig,
    default_config_path,
    load_config,
)
from tts_gateway.core.errors import ConfigError


def isolated_env(tmp_path: Path, **extra: str) -> dict[str, str]:
    """Env that cannot see the developer's real ~/.config."""
    return {"XDG_CONFIG_HOME": str(tmp_path / "xdg"), **extra}


class TestDefaults:
    def test_runs_with_no_file_and_no_env(self, tmp_path: Path) -> None:
        config = load_config(env=isolated_env(tmp_path))
        assert config.server.host == "127.0.0.1"
        assert config.server.port == 5111
        assert config.speech.default_provider == "auto"
        assert config.speech.provider_priority == ["piper", "tone"]
        assert config.playback.backend == "auto"
        assert config.providers == {}

    def test_default_config_path_respects_xdg(self, tmp_path: Path) -> None:
        env = {"XDG_CONFIG_HOME": str(tmp_path)}
        assert default_config_path(env) == tmp_path / "tts-gateway" / "config.yaml"

    def test_default_config_path_falls_back_to_home(self) -> None:
        assert default_config_path({}) == Path.home() / ".config" / "tts-gateway" / "config.yaml"


class TestFileLoading:
    def test_explicit_path(self, tmp_path: Path) -> None:
        path = tmp_path / "conf.yaml"
        path.write_text("server: {port: 6001}\n")
        config = load_config(path, env=isolated_env(tmp_path))
        assert config.server.port == 6001

    def test_env_var_path(self, tmp_path: Path) -> None:
        path = tmp_path / "conf.yaml"
        path.write_text("speech: {default_provider: tone}\n")
        env = isolated_env(tmp_path, TTS_GATEWAY_CONFIG=str(path))
        assert load_config(env=env).speech.default_provider == "tone"

    def test_default_location_is_used_when_present(self, tmp_path: Path) -> None:
        env = isolated_env(tmp_path)
        path = default_config_path(env)
        path.parent.mkdir(parents=True)
        path.write_text("server: {port: 7777}\n")
        assert load_config(env=env).server.port == 7777

    def test_empty_file_is_fine(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.yaml"
        path.write_text("")
        assert load_config(path, env=isolated_env(tmp_path)).server.port == 5111

    def test_provider_section_reaches_provider_settings(self, tmp_path: Path) -> None:
        path = tmp_path / "conf.yaml"
        path.write_text("providers: {piper: {binary: /opt/piper, extra_args: [--foo]}}\n")
        config = load_config(path, env=isolated_env(tmp_path))
        expected = {"binary": "/opt/piper", "extra_args": ["--foo"]}
        assert config.provider_settings("piper") == expected
        assert config.provider_settings("missing") == {}


class TestEnvOverrides:
    def test_scalar_and_nested(self, tmp_path: Path) -> None:
        env = isolated_env(
            tmp_path,
            TTS_GATEWAY__SERVER__PORT="6002",
            TTS_GATEWAY__SPEECH__DEFAULT_PROVIDER="tone",
            TTS_GATEWAY__PROVIDERS__PIPER__MODELS_DIR="/opt/voices",
        )
        config = load_config(env=env)
        assert config.server.port == 6002
        assert config.speech.default_provider == "tone"
        assert config.provider_settings("piper")["models_dir"] == "/opt/voices"

    def test_list_value(self, tmp_path: Path) -> None:
        env = isolated_env(tmp_path, TTS_GATEWAY__SPEECH__PROVIDER_PRIORITY="[tone, piper]")
        assert load_config(env=env).speech.provider_priority == ["tone", "piper"]

    def test_env_beats_file(self, tmp_path: Path) -> None:
        path = tmp_path / "conf.yaml"
        path.write_text("server: {port: 6001}\n")
        env = isolated_env(tmp_path, TTS_GATEWAY__SERVER__PORT="6002")
        assert load_config(path, env=env).server.port == 6002

    def test_backend_null_word_means_null_backend(self, tmp_path: Path) -> None:
        # YAML parses the bare word "null" as None; the config must still
        # understand the user meant the null backend.
        env = isolated_env(tmp_path, TTS_GATEWAY__PLAYBACK__BACKEND="null")
        assert load_config(env=env).playback.backend == "null"

    def test_unparseable_value_stays_string(self, tmp_path: Path) -> None:
        env = isolated_env(tmp_path, TTS_GATEWAY__LOGGING__LEVEL="{not: [valid")
        assert load_config(env=env).logging.level == "{not: [valid"


class TestErrors:
    def test_missing_explicit_file(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            load_config(tmp_path / "nope.yaml", env=isolated_env(tmp_path))

    def test_missing_env_file(self, tmp_path: Path) -> None:
        env = isolated_env(tmp_path, TTS_GATEWAY_CONFIG=str(tmp_path / "gone.yaml"))
        with pytest.raises(ConfigError, match="missing file"):
            load_config(env=env)

    def test_invalid_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.yaml"
        path.write_text("server: [unclosed")
        with pytest.raises(ConfigError, match="Invalid YAML"):
            load_config(path, env=isolated_env(tmp_path))

    def test_non_mapping_yaml(self, tmp_path: Path) -> None:
        path = tmp_path / "list.yaml"
        path.write_text("- just\n- a list\n")
        with pytest.raises(ConfigError, match="mapping"):
            load_config(path, env=isolated_env(tmp_path))

    def test_unknown_key_fails_loudly(self, tmp_path: Path) -> None:
        path = tmp_path / "typo.yaml"
        path.write_text("serverr: {port: 1}\n")
        with pytest.raises(ConfigError, match="serverr"):
            load_config(path, env=isolated_env(tmp_path))

    def test_invalid_value_reports_location(self, tmp_path: Path) -> None:
        path = tmp_path / "badval.yaml"
        path.write_text("server: {port: 99999}\n")
        with pytest.raises(ConfigError, match=r"server\.port"):
            load_config(path, env=isolated_env(tmp_path))


class TestExampleConfig:
    def test_example_parses_to_defaults_compatible_config(self) -> None:
        import yaml

        config = GatewayConfig.model_validate(yaml.safe_load(EXAMPLE_CONFIG))
        assert config.server.port == 5111
        assert config.speech.provider_priority == ["piper", "tone"]
        # the example documents the piper defaults explicitly
        assert config.provider_settings("piper")["binary"] == "piper"

    def test_repo_example_file_matches_packaged_template(self) -> None:
        repo_example = Path(__file__).resolve().parents[2] / "config.example.yaml"
        if not repo_example.is_file():
            pytest.skip("repo checkout not available (installed package)")
        assert repo_example.read_text(encoding="utf-8") == EXAMPLE_CONFIG
