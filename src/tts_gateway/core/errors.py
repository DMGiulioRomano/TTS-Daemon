"""Exception hierarchy for the gateway.

Every error raised by gateway code derives from :class:`GatewayError`, so
callers (the API layer, the CLI) can distinguish domain failures from bugs.
The API layer maps each subclass to an HTTP status code in one place.
"""

from __future__ import annotations


class GatewayError(Exception):
    """Base class for all gateway-domain errors."""


class ConfigError(GatewayError):
    """The configuration file or environment overrides are invalid."""


class UnknownProviderError(GatewayError):
    """A provider name was requested that is not registered."""

    def __init__(self, name: str, known: list[str] | None = None) -> None:
        self.name = name
        self.known = known or []
        detail = f"Unknown provider: {name!r}"
        if self.known:
            detail += f" (registered: {', '.join(sorted(self.known))})"
        super().__init__(detail)


class ProviderUnavailableError(GatewayError):
    """A provider exists but cannot run (missing binary, model, API key...)."""

    def __init__(self, name: str, reason: str = "") -> None:
        self.name = name
        self.reason = reason
        detail = f"Provider {name!r} is not available"
        if reason:
            detail += f": {reason}"
        super().__init__(detail)


class SynthesisError(GatewayError):
    """A provider failed while turning text into audio."""


class PlaybackError(GatewayError):
    """The audio player could not play a clip."""


class QueueFullError(GatewayError):
    """The playback queue is at capacity; the request was rejected."""
