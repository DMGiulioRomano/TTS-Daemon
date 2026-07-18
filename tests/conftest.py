"""Shared fixtures and test doubles.

The doubles here are deliberately tiny and deterministic:

- ``ControllablePlayer`` blocks inside ``play`` until the test releases it,
  which lets queue/interrupt behaviour be tested without sleeps.
- ``BlockingProvider`` blocks inside ``synthesize`` for the same purpose.
- ``make_config`` builds an isolated ``GatewayConfig`` (tone provider, null
  playback) so no test ever touches the network, sound devices, or the
  user's real configuration.
"""

from __future__ import annotations

import queue as queue_module
import threading
from typing import Any

import pytest

from tts_gateway.config import GatewayConfig
from tts_gateway.core.errors import SynthesisError
from tts_gateway.core.events import EventBus
from tts_gateway.core.interfaces import AudioPlayer, TTSProvider
from tts_gateway.core.models import AudioClip, Availability, SynthesisRequest, Voice
from tts_gateway.providers.tone import ToneProvider


def make_config(**overrides: Any) -> GatewayConfig:
    """A GatewayConfig for tests: tone provider, silent playback, no file/env IO."""
    data: dict[str, Any] = {
        "speech": {"default_provider": "tone", "queue_size": 8, "history_size": 10},
        "playback": {"backend": "null"},
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key] = {**data[key], **value}
        else:
            data[key] = value
    return GatewayConfig.model_validate(data)


def make_clip(text: str = "test clip") -> AudioClip:
    """A real little WAV clip (via the tone provider) for player tests."""
    return ToneProvider({}).synthesize(SynthesisRequest(text=text))


class ControllablePlayer(AudioPlayer):
    """A player the test can hold open, finish, or interrupt deterministically."""

    def __init__(self) -> None:
        self.play_started = threading.Semaphore(0)  # released when a play() begins
        self._proceed: queue_module.Queue[str] = queue_module.Queue()
        self._stop = threading.Event()
        self.played: list[AudioClip] = []
        self.stop_count = 0

    def play(self, clip: AudioClip) -> bool:
        self.played.append(clip)
        self.play_started.release()
        deadline = 5.0
        step = 0.005
        waited = 0.0
        while waited < deadline:  # poll both signals; tiny step keeps tests fast
            if self._stop.is_set():
                self._stop.clear()
                return False
            try:
                self._proceed.get(timeout=step)
                return True
            except queue_module.Empty:
                waited += step
        raise AssertionError("ControllablePlayer.play was never released by the test")

    def stop(self) -> None:
        self.stop_count += 1
        self._stop.set()

    def finish_current(self) -> None:
        """Let the in-progress (or next) play() return normally."""
        self._proceed.put("finish")


class BlockingProvider(TTSProvider):
    """Synthesis blocks until ``release()``; lets tests fill the queue."""

    name = "blocking"

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)
        self.entered = threading.Semaphore(0)
        self._gate = threading.Event()

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        self.entered.release()
        if not self._gate.wait(timeout=5.0):
            raise AssertionError("BlockingProvider was never released by the test")
        return make_clip("blocking")

    def release(self) -> None:
        self._gate.set()

    def voices(self) -> list[Voice]:
        return []


class FailingProvider(TTSProvider):
    """Always raises SynthesisError; for error-path tests."""

    name = "failing"

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        raise SynthesisError("this provider always fails")

    def voices(self) -> list[Voice]:
        raise RuntimeError("voices listing is broken too")


class UnavailableProvider(TTSProvider):
    """Registered but never available; for auto-resolution tests."""

    name = "unavailable"

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        raise AssertionError("must never be called")

    def voices(self) -> list[Voice]:
        return []

    def availability(self) -> Availability:
        return Availability.unavailable("switched off for testing")


@pytest.fixture()
def events() -> EventBus:
    return EventBus()


@pytest.fixture()
def recorded_events(events: EventBus) -> list:
    seen: list = []
    events.subscribe(seen.append)
    return seen
