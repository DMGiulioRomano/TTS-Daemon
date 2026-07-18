"""SpeechService: the application facade.

Every entry point (HTTP routes, WebSocket, embedding the gateway as a
library) talks to this one class; it owns provider resolution, validation,
the playback queue, and shutdown. Nothing here knows about FastAPI.
"""

from __future__ import annotations

import logging
from typing import Any

from tts_gateway.config import GatewayConfig
from tts_gateway.core.errors import ProviderUnavailableError
from tts_gateway.core.events import EventBus
from tts_gateway.core.interfaces import AudioPlayer, TTSProvider
from tts_gateway.core.models import AudioClip, SynthesisRequest, Utterance
from tts_gateway.core.queue import PlaybackQueue
from tts_gateway.providers.registry import ProviderRegistry

logger = logging.getLogger(__name__)

AUTO_PROVIDER = "auto"


class SpeechService:
    """Coordinates providers, the playback queue, and status reporting."""

    def __init__(
        self,
        config: GatewayConfig,
        registry: ProviderRegistry,
        player: AudioPlayer,
        events: EventBus,
    ) -> None:
        self._config = config
        self._registry = registry
        self._player = player
        self.events = events
        self._queue = PlaybackQueue(
            player,
            events,
            max_size=config.speech.queue_size,
            history_size=config.speech.history_size,
        )
        self._closed = False

    @property
    def registry(self) -> ProviderRegistry:
        """The provider registry, exposed so embedders can add providers at runtime."""
        return self._registry

    # ---------------------------------------------------------------- speak

    def speak(
        self,
        text: str,
        *,
        provider: str | None = None,
        voice: str | None = None,
        speed: float = 1.0,
        options: dict[str, Any] | None = None,
        interrupt: bool = False,
    ) -> Utterance:
        """Queue ``text`` for playback and return immediately.

        With ``interrupt=True`` everything already queued or playing is
        cancelled first, so the new text starts as soon as it is synthesized.
        Raises ``ValueError`` for invalid input, ``UnknownProviderError`` /
        ``ProviderUnavailableError`` for provider problems, and
        ``QueueFullError`` when at capacity.
        """
        request = self._build_request(text, voice=voice, speed=speed, options=options)
        chosen = self.resolve_provider(provider)
        utterance = Utterance(request, chosen.name)
        if interrupt:
            self._queue.clear()
        self._queue.submit(utterance, lambda: chosen.synthesize(request))
        return utterance

    def synthesize(
        self,
        text: str,
        *,
        provider: str | None = None,
        voice: str | None = None,
        speed: float = 1.0,
        options: dict[str, Any] | None = None,
    ) -> AudioClip:
        """Synthesize and return audio without queueing or playing it."""
        request = self._build_request(text, voice=voice, speed=speed, options=options)
        return self.resolve_provider(provider).synthesize(request)

    def stop(self) -> int:
        """Cancel pending speech and interrupt playback; returns count affected."""
        return self._queue.clear()

    def wait_for(self, utterance: Utterance, timeout: float | None = None) -> bool:
        """Block until ``utterance`` reaches a terminal state."""
        return utterance.wait(timeout)

    # --------------------------------------------------------------- queries

    def status(self) -> dict[str, Any]:
        default_name, default_error = self._default_provider_status()
        return {
            "queue": self._queue.snapshot(),
            "default_provider": default_name,
            "default_provider_error": default_error,
            "playback_available": self._player.availability().available,
        }

    def find_utterance(self, utterance_id: str) -> dict[str, Any] | None:
        return self._queue.find(utterance_id)

    def providers_info(self) -> list[dict[str, Any]]:
        """Availability report for every registered provider."""
        default_name, _ = self._default_provider_status()
        info = []
        for name in self._registry.names():
            provider = self._registry.get(name)
            availability = provider.availability()
            info.append(
                {
                    "name": name,
                    "available": availability.available,
                    "reason": availability.reason or None,
                    "default": name == default_name,
                }
            )
        return info

    def voices(self, provider: str | None = None) -> list[dict[str, Any]]:
        """Voices grouped across providers, each entry tagged with its provider.

        With ``provider`` set, errors propagate (the caller asked for that
        specific engine); otherwise a provider whose listing fails is skipped
        so one broken engine cannot hide the others.
        """
        if provider is not None:
            chosen = self._registry.get(provider)
            return [{**voice.to_dict(), "provider": chosen.name} for voice in chosen.voices()]
        collected: list[dict[str, Any]] = []
        for name in self._registry.names():
            try:
                for voice in self._registry.get(name).voices():
                    collected.append({**voice.to_dict(), "provider": name})
            except Exception:
                logger.exception("Listing voices failed for provider %r", name)
        return collected

    # ------------------------------------------------------------ providers

    def resolve_provider(self, name: str | None = None) -> TTSProvider:
        """Turn an optional provider name into a usable provider instance.

        ``None`` falls back to the configured default; the special name
        ``auto`` picks the first available provider in
        ``speech.provider_priority``.
        """
        requested = name or self._config.speech.default_provider
        if requested == AUTO_PROVIDER:
            return self._resolve_auto()
        provider = self._registry.get(requested)
        availability = provider.availability()
        if not availability.available:
            raise ProviderUnavailableError(requested, availability.reason)
        return provider

    def _resolve_auto(self) -> TTSProvider:
        reasons: list[str] = []
        for candidate in self._config.speech.provider_priority:
            if candidate not in self._registry:
                reasons.append(f"{candidate}: not registered")
                continue
            provider = self._registry.get(candidate)
            availability = provider.availability()
            if availability.available:
                return provider
            reasons.append(f"{candidate}: {availability.reason or 'unavailable'}")
        raise ProviderUnavailableError(
            AUTO_PROVIDER,
            "no provider in speech.provider_priority is available ("
            + "; ".join(reasons or ["priority list is empty"])
            + ")",
        )

    def _default_provider_status(self) -> tuple[str | None, str | None]:
        """Resolved default provider name, or the reason resolution fails."""
        try:
            return self.resolve_provider(None).name, None
        except Exception as exc:
            return None, str(exc)

    # ------------------------------------------------------------- lifecycle

    def _build_request(
        self,
        text: str,
        *,
        voice: str | None,
        speed: float,
        options: dict[str, Any] | None,
    ) -> SynthesisRequest:
        if not text or not text.strip():
            raise ValueError("text must not be empty")
        limit = self._config.speech.max_text_length
        if len(text) > limit:
            raise ValueError(
                f"text is {len(text)} characters; the limit is {limit} (speech.max_text_length)"
            )
        if speed <= 0 or speed > 10:
            raise ValueError(f"speed must be in (0, 10], got {speed}")
        return SynthesisRequest(text=text, voice=voice, speed=speed, options=dict(options or {}))

    def close(self) -> None:
        """Shut down the queue worker and release provider/player resources."""
        if self._closed:
            return
        self._closed = True
        self._queue.close()
        self._registry.close()
        try:
            self._player.close()
        except Exception:
            logger.exception("Error closing player")
