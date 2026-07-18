"""ProviderRegistry: registration, lazy instances, entry points, teardown."""

from __future__ import annotations

import pytest

from tests.conftest import make_clip, make_config
from tts_gateway.core.errors import UnknownProviderError
from tts_gateway.core.interfaces import TTSProvider
from tts_gateway.core.models import AudioClip, SynthesisRequest, Voice
from tts_gateway.providers import registry as registry_module
from tts_gateway.providers.registry import ProviderRegistry, create_default_registry


class EchoProvider(TTSProvider):
    name = "echo"
    closed = False

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        return make_clip(request.text)

    def voices(self) -> list[Voice]:
        return []

    def close(self) -> None:
        self.closed = True


class TestRegistration:
    def test_register_and_get_caches_instance(self) -> None:
        registry = ProviderRegistry(make_config())
        registry.register(EchoProvider)
        assert "echo" in registry
        assert registry.names() == ["echo"]
        assert registry.get("echo") is registry.get("echo")

    def test_settings_are_passed_to_constructor(self) -> None:
        config = make_config(providers={"echo": {"volume": 3}})
        registry = ProviderRegistry(config)
        registry.register(EchoProvider)
        assert registry.get("echo").settings == {"volume": 3}

    def test_duplicate_name_rejected_unless_replace(self) -> None:
        registry = ProviderRegistry(make_config())
        registry.register(EchoProvider)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(EchoProvider)
        registry.register(EchoProvider, replace=True)  # explicit replace is fine

    def test_replace_drops_cached_instance(self) -> None:
        registry = ProviderRegistry(make_config())
        registry.register(EchoProvider)
        old = registry.get("echo")
        registry.register(EchoProvider, replace=True)
        assert registry.get("echo") is not old

    def test_empty_name_rejected(self) -> None:
        class Nameless(EchoProvider):
            name = ""

        registry = ProviderRegistry(make_config())
        with pytest.raises(ValueError, match="empty 'name'"):
            registry.register(Nameless)

    def test_unknown_provider_error_lists_known(self) -> None:
        registry = ProviderRegistry(make_config())
        registry.register(EchoProvider)
        with pytest.raises(UnknownProviderError, match=r"'nope'.*echo"):
            registry.get("nope")


class TestEntryPoints:
    class FakeEntryPoint:
        def __init__(self, name: str, target: object, broken: bool = False) -> None:
            self.name = name
            self._target = target
            self._broken = broken

        def load(self) -> object:
            if self._broken:
                raise ImportError("plugin is broken")
            return self._target

    def _patch_entry_points(self, monkeypatch: pytest.MonkeyPatch, entries: list) -> None:
        monkeypatch.setattr(registry_module, "entry_points", lambda group: entries if group else [])

    def test_plugin_provider_is_discovered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_entry_points(monkeypatch, [self.FakeEntryPoint("echo", EchoProvider)])
        registry = ProviderRegistry(make_config())
        registry.load_entry_points()
        assert "echo" in registry

    def test_existing_name_is_not_overridden(self, monkeypatch: pytest.MonkeyPatch) -> None:
        class Impostor(EchoProvider):
            pass

        self._patch_entry_points(monkeypatch, [self.FakeEntryPoint("echo", Impostor)])
        registry = ProviderRegistry(make_config())
        registry.register(EchoProvider)
        registry.load_entry_points()
        assert type(registry.get("echo")) is EchoProvider

    def test_broken_plugin_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_entry_points(
            monkeypatch,
            [
                self.FakeEntryPoint("bad", None, broken=True),
                self.FakeEntryPoint("echo", EchoProvider),
            ],
        )
        registry = ProviderRegistry(make_config())
        registry.load_entry_points()  # must not raise
        assert registry.names() == ["echo"]

    def test_non_provider_object_is_skipped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._patch_entry_points(monkeypatch, [self.FakeEntryPoint("junk", object)])
        registry = ProviderRegistry(make_config())
        registry.load_entry_points()
        assert "junk" not in registry


class TestDefaultRegistryAndClose:
    def test_default_registry_has_builtins(self) -> None:
        registry = create_default_registry(make_config())
        assert "piper" in registry
        assert "tone" in registry

    def test_close_closes_instances_and_survives_errors(self) -> None:
        class GrumpyProvider(EchoProvider):
            name = "grumpy"

            def close(self) -> None:
                raise RuntimeError("refuses to close")

        registry = ProviderRegistry(make_config())
        registry.register(EchoProvider)
        registry.register(GrumpyProvider)
        echo = registry.get("echo")
        registry.get("grumpy")
        registry.close()  # must not raise despite GrumpyProvider
        assert echo.closed
