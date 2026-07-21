"""On-disk synthesis cache: keys, hit/miss, LRU eviction, bypass, corruption."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import make_config
from tts_daemon.core.cache import SynthesisCache, default_cache_dir
from tts_daemon.core.events import EventBus
from tts_daemon.core.models import AudioClip, AudioFormat, SynthesisRequest
from tts_daemon.core.service import SpeechService
from tts_daemon.players.null import NullPlayer
from tts_daemon.providers.registry import ProviderRegistry
from tts_daemon.providers.tone import ToneProvider


def _clip(payload: bytes = b"RIFFdata", fmt: AudioFormat = AudioFormat.WAV) -> AudioClip:
    return AudioClip(data=payload, format=fmt)


def test_default_cache_dir_respects_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert default_cache_dir() == tmp_path / "tts-daemon"
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    assert default_cache_dir() == Path.home() / ".cache" / "tts-daemon"


def test_key_is_stable_and_content_addressed(tmp_path: Path) -> None:
    cache = SynthesisCache(tmp_path, max_bytes=1_000_000)
    a = cache.key("tone", SynthesisRequest("hello", voice="mid", speed=1.0))
    b = cache.key("tone", SynthesisRequest("hello", voice="mid", speed=1.0))
    assert a == b and len(a) == 64
    # Any component change flips the key.
    assert a != cache.key("tone", SynthesisRequest("hello", voice="low", speed=1.0))
    assert a != cache.key("tone", SynthesisRequest("hello", voice="mid", speed=2.0))
    assert a != cache.key("tone", SynthesisRequest("world", voice="mid", speed=1.0))
    assert a != cache.key("piper", SynthesisRequest("hello", voice="mid", speed=1.0))
    assert a != cache.key("tone", SynthesisRequest("hello", voice="mid", speed=1.0), "fp")
    # Option ordering must not matter.
    o1 = cache.key("x", SynthesisRequest("t", options={"a": 1, "b": 2}))
    o2 = cache.key("x", SynthesisRequest("t", options={"b": 2, "a": 1}))
    assert o1 == o2


def test_put_then_get_roundtrips(tmp_path: Path) -> None:
    cache = SynthesisCache(tmp_path, max_bytes=1_000_000)
    assert cache.get("k") is None  # miss
    cache.put("k", _clip(b"abc", AudioFormat.MP3))
    got = cache.get("k")
    assert got is not None
    assert got.data == b"abc"
    assert got.format is AudioFormat.MP3
    stats = cache.stats()
    assert stats["entries"] == 1
    assert stats["hits"] == 1
    assert stats["misses"] == 1


def test_lru_eviction_drops_least_recently_used(tmp_path: Path) -> None:
    # Budget fits two 100-byte clips but not three.
    cache = SynthesisCache(tmp_path, max_bytes=250)
    cache.put("a", _clip(b"a" * 100))
    cache.put("b", _clip(b"b" * 100))
    assert cache.get("a") is not None  # touch 'a' so 'b' becomes least-recent
    cache.put("c", _clip(b"c" * 100))  # forces eviction of 'b'
    assert cache.get("a") is not None
    assert cache.get("c") is not None
    assert cache.get("b") is None
    assert cache.stats()["entries"] == 2


def test_oversized_clip_is_not_stored(tmp_path: Path) -> None:
    cache = SynthesisCache(tmp_path, max_bytes=10)
    cache.put("big", _clip(b"x" * 100))
    assert cache.get("big") is None
    assert cache.stats()["entries"] == 0


def test_truncated_file_is_treated_as_miss_and_dropped(tmp_path: Path) -> None:
    cache = SynthesisCache(tmp_path, max_bytes=1_000_000)
    cache.put("k", _clip(b"1234567890"))
    (tmp_path / "k").write_bytes(b"short")  # corrupt on disk
    assert cache.get("k") is None
    assert not (tmp_path / "k").exists()  # deleted
    assert cache.stats()["entries"] == 0


def test_missing_file_is_treated_as_miss(tmp_path: Path) -> None:
    cache = SynthesisCache(tmp_path, max_bytes=1_000_000)
    cache.put("k", _clip())
    (tmp_path / "k").unlink()
    assert cache.get("k") is None


def test_corrupt_index_starts_empty_without_error(tmp_path: Path) -> None:
    (tmp_path / "index.json").write_text("{ not json", encoding="utf-8")
    cache = SynthesisCache(tmp_path, max_bytes=1_000_000)
    assert cache.stats()["entries"] == 0


def test_index_persists_across_instances(tmp_path: Path) -> None:
    SynthesisCache(tmp_path, max_bytes=1_000_000).put("k", _clip(b"persisted"))
    reopened = SynthesisCache(tmp_path, max_bytes=1_000_000)
    got = reopened.get("k")
    assert got is not None and got.data == b"persisted"


def test_load_reconciles_index_against_disk(tmp_path: Path) -> None:
    SynthesisCache(tmp_path, max_bytes=1_000_000).put("k", _clip(b"data"))
    (tmp_path / "k").unlink()  # file gone but index still lists it
    reopened = SynthesisCache(tmp_path, max_bytes=1_000_000)
    assert reopened.stats()["entries"] == 0


# ---------------------------------------------------------------- service wiring


def _service_with_cache(tmp_path: Path) -> SpeechService:
    config = make_config(cache={"enabled": True, "max_mb": 200})
    registry = ProviderRegistry(config)
    registry.register(ToneProvider)
    service = SpeechService(config, registry, NullPlayer(), EventBus())
    # Redirect the cache at a temp dir regardless of the machine's XDG settings.
    service._cache = SynthesisCache(tmp_path, max_bytes=200 * 1024 * 1024)
    return service


def test_service_synthesize_uses_cache(tmp_path: Path) -> None:
    service = _service_with_cache(tmp_path)
    try:
        first = service.synthesize("cache me", provider="tone")
        second = service.synthesize("cache me", provider="tone")
        assert first.data == second.data
        stats = service.status()["cache"]
        assert stats["entries"] == 1
        assert stats["hits"] == 1
        assert stats["misses"] == 1
    finally:
        service.close()


def test_service_no_cache_option_bypasses_and_is_stripped(tmp_path: Path) -> None:
    service = _service_with_cache(tmp_path)
    try:
        # no_cache must be stripped before reaching the tone provider (which
        # rejects unknown options) and must not populate the cache.
        clip = service.synthesize("skip", provider="tone", options={"no_cache": True})
        assert clip.data  # synthesized fine, option did not error
        assert service.status()["cache"]["entries"] == 0
    finally:
        service.close()


def test_disabled_cache_reports_null(tmp_path: Path) -> None:
    service = SpeechService(
        make_config(cache={"enabled": False}),
        _tone_registry(),
        NullPlayer(),
        EventBus(),
    )
    try:
        service.synthesize("no cache here", provider="tone")
        assert service.status()["cache"] is None
    finally:
        service.close()


def _tone_registry() -> ProviderRegistry:
    registry = ProviderRegistry(make_config())
    registry.register(ToneProvider)
    return registry
