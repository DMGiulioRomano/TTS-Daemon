"""SpeechService: provider resolution, validation, interrupt, aggregation."""

from __future__ import annotations

import pytest

from tests.conftest import (
    ControllablePlayer,
    FailingProvider,
    UnavailableProvider,
    make_config,
)
from tts_gateway.core.errors import ProviderUnavailableError, UnknownProviderError
from tts_gateway.core.events import EventBus
from tts_gateway.core.models import UtteranceState
from tts_gateway.core.service import SpeechService
from tts_gateway.players.null import NullPlayer
from tts_gateway.providers.registry import ProviderRegistry
from tts_gateway.providers.tone import ToneProvider


def build_service(
    config=None,
    player=None,
    provider_classes=(ToneProvider,),
) -> SpeechService:
    config = config or make_config()
    registry = ProviderRegistry(config)
    for provider_class in provider_classes:
        registry.register(provider_class)
    return SpeechService(config, registry, player or NullPlayer(), EventBus())


@pytest.fixture()
def service() -> SpeechService:
    svc = build_service()
    yield svc
    svc.close()


class TestSpeak:
    def test_speak_uses_default_provider(self, service: SpeechService) -> None:
        utterance = service.speak("hello")
        assert utterance.provider_name == "tone"
        assert service.wait_for(utterance, timeout=2)
        assert utterance.state is UtteranceState.FINISHED

    def test_explicit_provider(self, service: SpeechService) -> None:
        utterance = service.speak("hello", provider="tone", voice="high", speed=2.0)
        assert service.wait_for(utterance, timeout=2)
        snapshot = utterance.snapshot()
        assert snapshot["voice"] == "high"
        assert snapshot["speed"] == 2.0

    def test_unknown_provider(self, service: SpeechService) -> None:
        with pytest.raises(UnknownProviderError):
            service.speak("x", provider="imaginary")

    def test_unavailable_provider(self) -> None:
        service = build_service(provider_classes=(ToneProvider, UnavailableProvider))
        try:
            with pytest.raises(ProviderUnavailableError, match="switched off"):
                service.speak("x", provider="unavailable")
        finally:
            service.close()

    def test_interrupt_clears_previous(self) -> None:
        player = ControllablePlayer()
        service = build_service(player=player)
        try:
            first = service.speak("first utterance")
            assert player.play_started.acquire(timeout=2)
            second = service.speak("second utterance", interrupt=True)
            assert service.wait_for(first, timeout=2)
            assert first.state is UtteranceState.CANCELLED
            assert player.play_started.acquire(timeout=2)
            player.finish_current()
            assert service.wait_for(second, timeout=2)
            assert second.state is UtteranceState.FINISHED
        finally:
            service.close()

    def test_synthesize_returns_clip_without_queueing(self, service: SpeechService) -> None:
        clip = service.synthesize("direct")
        assert clip.data.startswith(b"RIFF")
        assert service.status()["queue"]["current"] is None

    def test_stop_delegates_to_queue(self, service: SpeechService) -> None:
        assert service.stop() == 0


class TestValidation:
    def test_empty_text(self, service: SpeechService) -> None:
        with pytest.raises(ValueError, match="empty"):
            service.speak("   ")

    def test_text_too_long(self) -> None:
        service = build_service(config=make_config(speech={"max_text_length": 10}))
        try:
            with pytest.raises(ValueError, match="limit is 10"):
                service.speak("x" * 11)
        finally:
            service.close()

    def test_bad_speed(self, service: SpeechService) -> None:
        with pytest.raises(ValueError, match="speed"):
            service.speak("x", speed=0)
        with pytest.raises(ValueError, match="speed"):
            service.speak("x", speed=11)


class TestAutoResolution:
    def test_auto_picks_first_available(self) -> None:
        config = make_config(
            speech={"default_provider": "auto", "provider_priority": ["unavailable", "tone"]}
        )
        service = build_service(config=config, provider_classes=(ToneProvider, UnavailableProvider))
        try:
            assert service.resolve_provider().name == "tone"
        finally:
            service.close()

    def test_auto_skips_unregistered_names(self) -> None:
        config = make_config(
            speech={"default_provider": "auto", "provider_priority": ["missing", "tone"]}
        )
        service = build_service(config=config)
        try:
            assert service.resolve_provider().name == "tone"
        finally:
            service.close()

    def test_auto_with_nothing_available_explains_each(self) -> None:
        config = make_config(
            speech={"default_provider": "auto", "provider_priority": ["unavailable", "missing"]}
        )
        service = build_service(config=config, provider_classes=(UnavailableProvider,))
        try:
            with pytest.raises(ProviderUnavailableError) as excinfo:
                service.resolve_provider()
            message = str(excinfo.value)
            assert "unavailable: switched off for testing" in message
            assert "missing: not registered" in message
        finally:
            service.close()


class TestQueries:
    def test_providers_info_marks_default(self, service: SpeechService) -> None:
        info = service.providers_info()
        assert info == [{"name": "tone", "available": True, "reason": None, "default": True}]

    def test_status_shape(self, service: SpeechService) -> None:
        status = service.status()
        assert status["default_provider"] == "tone"
        assert status["default_provider_error"] is None
        assert status["playback_available"] is True
        assert status["queue"]["size"] == 0

    def test_status_reports_unresolvable_default(self) -> None:
        config = make_config(speech={"default_provider": "auto", "provider_priority": []})
        service = build_service(config=config)
        try:
            status = service.status()
            assert status["default_provider"] is None
            assert "priority" in status["default_provider_error"]
        finally:
            service.close()

    def test_voices_aggregates_and_tags_provider(self, service: SpeechService) -> None:
        voices = service.voices()
        assert {voice["provider"] for voice in voices} == {"tone"}
        assert {voice["id"] for voice in voices} == {"low", "mid", "high"}

    def test_voices_skips_broken_provider_in_aggregate(self) -> None:
        service = build_service(provider_classes=(ToneProvider, FailingProvider))
        try:
            voices = service.voices()
            assert {voice["provider"] for voice in voices} == {"tone"}
        finally:
            service.close()

    def test_voices_propagates_for_explicit_provider(self) -> None:
        service = build_service(provider_classes=(ToneProvider, FailingProvider))
        try:
            with pytest.raises(RuntimeError, match="broken"):
                service.voices("failing")
        finally:
            service.close()

    def test_find_utterance(self, service: SpeechService) -> None:
        utterance = service.speak("findable")
        assert service.wait_for(utterance, timeout=2)
        assert service.find_utterance(utterance.id)["id"] == utterance.id
        assert service.find_utterance("nope") is None


def test_close_is_idempotent() -> None:
    service = build_service()
    service.close()
    service.close()
