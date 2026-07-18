"""Provider registry: name -> provider class, with lazy instantiation.

The registry is the single place that knows which providers exist. Everything
else (service, API, CLI) asks it by name, which is what keeps the server
completely decoupled from concrete engines.

Third-party providers are discovered from the ``tts_gateway.providers`` entry
point group, so installing a package such as ``tts-gateway-kokoro`` that
declares::

    [project.entry-points."tts_gateway.providers"]
    kokoro = "tts_gateway_kokoro:KokoroProvider"

makes ``kokoro`` usable in requests and configuration with no gateway change.
"""

from __future__ import annotations

import logging
from importlib.metadata import entry_points

from tts_gateway.config import GatewayConfig
from tts_gateway.core.errors import UnknownProviderError
from tts_gateway.core.interfaces import TTSProvider

logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "tts_gateway.providers"


class ProviderRegistry:
    """Maps provider names to classes and manages one instance per name.

    Instances are created lazily on first use with the settings from the
    ``providers.<name>`` config section, then cached: providers may hold
    subprocesses or HTTP sessions, so they should be built once.
    """

    def __init__(self, config: GatewayConfig) -> None:
        self._config = config
        self._classes: dict[str, type[TTSProvider]] = {}
        self._instances: dict[str, TTSProvider] = {}

    def register(self, provider_class: type[TTSProvider], *, replace: bool = False) -> None:
        """Register a provider class under its ``name`` attribute."""
        name = provider_class.name
        if not name:
            raise ValueError(f"{provider_class.__qualname__} has an empty 'name' attribute")
        if not replace and name in self._classes:
            raise ValueError(f"Provider {name!r} is already registered")
        self._classes[name] = provider_class
        self._instances.pop(name, None)

    def names(self) -> list[str]:
        return sorted(self._classes)

    def __contains__(self, name: str) -> bool:
        return name in self._classes

    def get(self, name: str) -> TTSProvider:
        """Return the (cached) provider instance for ``name``."""
        if name not in self._classes:
            raise UnknownProviderError(name, known=self.names())
        if name not in self._instances:
            settings = self._config.provider_settings(name)
            self._instances[name] = self._classes[name](settings)
        return self._instances[name]

    def load_entry_points(self) -> None:
        """Discover third-party providers; a broken plugin must not kill the server."""
        for entry_point in entry_points(group=ENTRY_POINT_GROUP):
            if entry_point.name in self._classes:
                continue  # built-ins and earlier plugins win
            try:
                provider_class = entry_point.load()
            except Exception:
                logger.exception("Failed to load provider entry point %r", entry_point.name)
                continue
            if not (isinstance(provider_class, type) and issubclass(provider_class, TTSProvider)):
                logger.error(
                    "Entry point %r does not resolve to a TTSProvider subclass (got %r)",
                    entry_point.name,
                    provider_class,
                )
                continue
            try:
                self.register(provider_class)
            except ValueError:
                logger.exception("Failed to register provider entry point %r", entry_point.name)

    def close(self) -> None:
        """Close every instantiated provider."""
        for instance in self._instances.values():
            try:
                instance.close()
            except Exception:
                logger.exception("Error closing provider %r", instance.name)
        self._instances.clear()


def create_default_registry(config: GatewayConfig) -> ProviderRegistry:
    """Registry with the built-in providers plus any entry-point plugins."""
    # Imported here so that the registry module itself stays import-light and
    # a syntax error in one provider cannot break registry imports elsewhere.
    from tts_gateway.providers.piper import PiperProvider
    from tts_gateway.providers.tone import ToneProvider

    registry = ProviderRegistry(config)
    registry.register(PiperProvider)
    registry.register(ToneProvider)
    registry.load_entry_points()
    return registry
