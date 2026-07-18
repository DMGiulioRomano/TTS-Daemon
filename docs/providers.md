# Writing a TTS provider

A provider turns text into audio. Everything else — queueing, playback,
interruption, the HTTP/WebSocket API — is the gateway's job. If you can
write one class with two methods, you can add an engine.

## The contract

Implement `tts_gateway.core.interfaces.TTSProvider`:

```python
from tts_gateway.core.errors import SynthesisError
from tts_gateway.core.interfaces import TTSProvider
from tts_gateway.core.models import (
    AudioClip, AudioFormat, Availability, SynthesisRequest, Voice,
)


class MyEngineProvider(TTSProvider):
    name = "myengine"          # registry key, API identifier, config section

    def __init__(self, settings: dict | None = None) -> None:
        super().__init__(settings)         # self.settings = providers.myengine
        self._api_key = self.settings.get("api_key")

    def synthesize(self, request: SynthesisRequest) -> AudioClip:
        audio_bytes = ...  # call your engine with request.text
        return AudioClip(data=audio_bytes, format=AudioFormat.WAV)

    def voices(self) -> list[Voice]:
        return [Voice(id="default", name="My Engine default", language="en")]

    def availability(self) -> Availability:          # optional but recommended
        if not self._api_key:
            return Availability.unavailable(
                "no api_key set (providers.myengine.api_key in the config)"
            )
        return Availability.ok()

    def close(self) -> None:                          # optional
        ...  # release subprocesses / HTTP sessions
```

### Rules, in order of importance

1. **Raise `SynthesisError` for expected failures** (engine exited nonzero,
   API refused, voice not found). Include the actionable detail — your
   message goes verbatim to the user. Any *other* exception is treated as a
   provider bug: the utterance fails, the queue survives, the traceback is
   logged.
2. **`availability()` must explain how to fix the problem.** It is shown in
   `GET /v1/providers`, in CLI output, and drives `auto` provider selection.
   "piper binary 'piper' not found on PATH (install piper, or set
   providers.piper.binary)" is the house style.
3. **Synchronous and blocking is correct.** The gateway calls you from
   worker threads, never from the event loop, and never concurrently on the
   same instance. Don't spawn threads, don't use asyncio.
4. **`request.speed` is a rate multiplier** (1.0 normal, 2.0 double). Map it
   to your engine's native parameter; ignore it only if the engine truly has
   no rate control.
5. **`request.options` is your private namespace.** The gateway passes the
   dict through untouched. Validate it: reject unknown keys with a
   `SynthesisError` listing the supported ones (silent ignoring hides typos).
6. **Read settings from `self.settings`** (the `providers.<name>` config
   section, a plain dict). Validate lazily or in `__init__`, but construction
   must never crash the server — report problems via `availability()` /
   `SynthesisError` instead.
7. **Return a complete clip.** `AudioFormat.WAV` plays everywhere; if your
   engine emits MP3/OGG/FLAC, declare it — the player layer routes formats
   to a capable output command automatically.

## Wiring it up

### Option A — separate package (no gateway changes)

Publish your provider as its own package and declare an entry point:

```toml
# pyproject.toml of tts-gateway-myengine
[project.entry-points."tts_gateway.providers"]
myengine = "tts_gateway_myengine:MyEngineProvider"
```

`pip install tts-gateway-myengine` and restart: the gateway discovers it,
`myengine` becomes valid in requests and config, and `providers.myengine`
reaches your constructor. Built-in names win on collision, and a plugin that
fails to import is logged and skipped — it can never take the server down.

### Option B — in-tree (contributing to the gateway)

1. Add `src/tts_gateway/providers/myengine.py`.
2. Register it in `create_default_registry`
   (`src/tts_gateway/providers/registry.py`) and add the entry point to
   `pyproject.toml`.
3. Consider adding it to the default `speech.provider_priority` in
   `config.py` only if it works with zero configuration on a typical
   machine (that bar is why the list is just `[piper, tone]`).

### Option C — embedded (your own app)

```python
service.registry.register(MyEngineProvider)
```

## Testing your provider

Steal the patterns from the shipped tests:

- [`tests/unit/test_piper.py`](../tests/unit/test_piper.py) — subprocess
  engine tested against a fake executable: argument building, stdin, exit
  codes, timeouts, voice resolution.
- [`tests/unit/test_tone.py`](../tests/unit/test_tone.py) — pure-Python
  engine: output validity, speed mapping, option rejection.

Checklist:

- [ ] `synthesize` happy path returns playable audio (`clip.data` non-empty,
      correct `format`)
- [ ] unknown voice / unknown option → `SynthesisError` naming what *is*
      supported
- [ ] `availability()` false cases have actionable reasons
- [ ] `voices()` works even when the engine is not fully configured
      (return `[]` rather than raising, where possible)
- [ ] engine failure paths (nonzero exit, timeout, network error) →
      `SynthesisError`, not a raw exception

Run the gateway with only your provider to try it end to end:

```sh
TTS_GATEWAY__SPEECH__DEFAULT_PROVIDER=myengine tts-gateway serve
tts-gateway speak "testing my engine" --provider myengine
```
