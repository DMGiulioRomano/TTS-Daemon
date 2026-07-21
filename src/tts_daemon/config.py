"""Layered configuration for the gateway.

Precedence (lowest to highest):

1. Built-in defaults (every field has one; the gateway runs with no config).
2. A YAML file: an explicit path, else ``$TTS_DAEMON_CONFIG``, else
   ``~/.config/tts-daemon/config.yaml`` (respecting ``$XDG_CONFIG_HOME``).
3. Environment variables of the form ``TTS_DAEMON__SECTION__KEY=value``,
   e.g. ``TTS_DAEMON__SERVER__PORT=6000`` or
   ``TTS_DAEMON__PROVIDERS__PIPER__MODELS_DIR=/opt/voices``. Path segments
   are separated by double underscores so key names may contain single
   underscores; values are parsed as YAML scalars (numbers, booleans, lists).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from tts_daemon.core.errors import ConfigError
from tts_daemon.defaults import DEFAULT_HOST, DEFAULT_PORT

ENV_PREFIX = "TTS_DAEMON__"
CONFIG_PATH_ENV = "TTS_DAEMON_CONFIG"


class _StrictModel(BaseModel):
    """Base model that rejects unknown keys, so typos fail loudly at startup."""

    model_config = ConfigDict(extra="forbid")


class ServerConfig(_StrictModel):
    """HTTP server binding and CORS."""

    host: str = DEFAULT_HOST
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    # The gateway is a local service; permissive CORS lets browser userscripts
    # on any page reach it. Restrict this list if you bind beyond localhost.
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])


class SpeechConfig(_StrictModel):
    """Provider selection and queue behaviour."""

    # "auto" resolves to the first available provider in provider_priority.
    default_provider: str = "auto"
    provider_priority: list[str] = Field(default_factory=lambda: ["piper", "tone"])
    queue_size: int = Field(default=64, ge=1)
    history_size: int = Field(default=50, ge=0)
    max_text_length: int = Field(default=10_000, ge=1)


class PlaybackConfig(_StrictModel):
    """Audio output selection.

    ``backend`` chooses the player implementation; ``command`` overrides the
    auto-detected playback command. The command is an argv list where the
    placeholder ``{file}`` is replaced with the path of a temporary audio
    file; without the placeholder, audio bytes are piped to stdin.
    """

    backend: Literal["auto", "command", "null"] = "auto"
    command: list[str] | None = None

    @field_validator("backend", mode="before")
    @classmethod
    def _yaml_null_means_null_backend(cls, value: object) -> object:
        # YAML parses the bare word ``null`` as None (both in config files and
        # in TTS_DAEMON__* env values); for this field the user clearly meant
        # the null backend.
        return "null" if value is None else value


class OpenAICompatConfig(_StrictModel):
    """The OpenAI-compatible ``POST /v1/audio/speech`` endpoint."""

    # Maps OpenAI voice names (alloy, nova, ...) to provider voice ids. An
    # unmapped OpenAI voice falls back to the provider default; any other
    # value is passed to the provider unchanged (treated as a voice id).
    voice_aliases: dict[str, str] = Field(default_factory=dict)


class CacheConfig(_StrictModel):
    """On-disk cache of synthesized clips (repeated phrases replay instantly)."""

    enabled: bool = True
    max_mb: int = Field(default=200, ge=0)
    # Where clips are stored; null -> $XDG_CACHE_HOME/tts-daemon (~/.cache/...).
    dir: str | None = None


class LoggingConfig(_StrictModel):
    level: str = "INFO"


class GatewayConfig(_StrictModel):
    """Root configuration object handed to the application factory."""

    server: ServerConfig = Field(default_factory=ServerConfig)
    speech: SpeechConfig = Field(default_factory=SpeechConfig)
    playback: PlaybackConfig = Field(default_factory=PlaybackConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)
    openai_compat: OpenAICompatConfig = Field(default_factory=OpenAICompatConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    # Free-form per-provider settings; each provider validates its own section.
    providers: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def provider_settings(self, name: str) -> dict[str, Any]:
        """Settings mapping for provider ``name`` (empty dict if absent)."""
        return dict(self.providers.get(name, {}))


def default_config_path(env: Mapping[str, str] | None = None) -> Path:
    """``$XDG_CONFIG_HOME/tts-daemon/config.yaml`` (or the ~/.config fallback)."""
    environ = os.environ if env is None else env
    xdg = environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "tts-daemon" / "config.yaml"


def _read_yaml_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Invalid YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a YAML mapping at the top level")
    return data


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively merge ``override`` into ``base`` (override wins)."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _env_overrides(env: Mapping[str, str]) -> dict[str, Any]:
    """Turn ``TTS_DAEMON__A__B=v`` variables into a nested mapping."""
    result: dict[str, Any] = {}
    for raw_key, raw_value in env.items():
        if not raw_key.startswith(ENV_PREFIX):
            continue
        path = [part.lower() for part in raw_key[len(ENV_PREFIX) :].split("__") if part]
        if not path:
            continue
        try:
            value = yaml.safe_load(raw_value)
        except yaml.YAMLError:
            value = raw_value  # treat unparseable values as plain strings
        node = result
        for part in path[:-1]:
            existing = node.get(part)
            if not isinstance(existing, dict):
                existing = {}
                node[part] = existing
            node = existing
        node[path[-1]] = value
    return result


def load_config(
    path: str | Path | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> GatewayConfig:
    """Build the effective configuration.

    ``path`` forces a specific YAML file (it must exist); otherwise the file
    named by ``$TTS_DAEMON_CONFIG`` is used (it must exist too, since the
    user asked for it), else the default location is used if present.
    ``env`` defaults to ``os.environ`` and is injectable for tests.
    """
    environ = os.environ if env is None else env

    data: dict[str, Any] = {}
    file_path: Path | None = None
    if path is not None:
        file_path = Path(path).expanduser()
        if not file_path.is_file():
            raise ConfigError(f"Config file not found: {file_path}")
    elif environ.get(CONFIG_PATH_ENV, "").strip():
        file_path = Path(environ[CONFIG_PATH_ENV]).expanduser()
        if not file_path.is_file():
            raise ConfigError(f"${CONFIG_PATH_ENV} points to a missing file: {file_path}")
    else:
        candidate = default_config_path(environ)
        if candidate.is_file():
            file_path = candidate

    if file_path is not None:
        data = _read_yaml_file(file_path)

    data = _deep_merge(data, _env_overrides(environ))

    try:
        return GatewayConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(_format_validation_error(exc, file_path)) from exc


def _format_validation_error(exc: ValidationError, file_path: Path | None) -> str:
    source = f" in {file_path}" if file_path else ""
    lines = [f"Invalid configuration{source}:"]
    for err in exc.errors():
        location = ".".join(str(part) for part in err["loc"]) or "<root>"
        lines.append(f"  - {location}: {err['msg']}")
    return "\n".join(lines)


#: Annotated template written by ``tts-daemon init-config`` and shipped as
#: ``config.example.yaml``. Keep in sync with the models above (tested).
EXAMPLE_CONFIG = """\
# tts-daemon configuration.
# Default location: ~/.config/tts-daemon/config.yaml
# Every key is optional; the values shown here are the built-in defaults
# unless noted otherwise. Environment variables override this file, e.g.
#   TTS_DAEMON__SERVER__PORT=6000
#   TTS_DAEMON__PROVIDERS__PIPER__MODELS_DIR=/opt/voices

server:
  host: 127.0.0.1        # bind address; keep on localhost unless you trust the network
  port: 5111
  cors_origins: ["*"]    # origins allowed for browser clients

speech:
  default_provider: auto # a provider name, or "auto" to pick the first available
  provider_priority:     # order tried by "auto"
    - piper
    - tone
  queue_size: 64         # utterances held before /v1/speak returns 429
  history_size: 50       # finished utterances kept in /v1/status
  max_text_length: 10000 # longer texts are rejected with 422

playback:
  backend: auto          # auto | command | null (null = synthesize but stay silent)
  command: null          # override the playback argv, e.g.
                         #   ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", "{file}"]
                         # "{file}" is replaced with a temp audio file; without
                         # it, audio bytes are piped to the command's stdin.

cache:
  enabled: true          # cache synthesized clips so repeated phrases replay instantly
  max_mb: 200            # size budget; least-recently-used clips are evicted
  dir: null              # storage dir; default: $XDG_CACHE_HOME/tts-daemon (~/.cache/...)

openai_compat:           # POST /v1/audio/speech (drop-in for OpenAI TTS clients)
  voice_aliases: {}      # map OpenAI voice names to provider voices, e.g.
                         #   alloy: en_US-lessac-medium

logging:
  level: INFO

providers:
  piper:
    binary: piper        # path to the piper executable
    models_dir: null     # where *.onnx voices live; default: ~/.local/share/tts-daemon/piper
    default_voice: null  # model name (without .onnx) or full path; default: first model found
    extra_args: []       # appended verbatim to the piper command line
  tone: {}               # dependency-free beep provider; no settings
"""
