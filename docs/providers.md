# Writing a TTS provider

A provider turns text into audio. Everything else — queueing, playback,
interruption, the HTTP/WebSocket API — is the gateway's job. If you can
write one class with two methods, you can add an engine.

## The contract

Implement `tts_daemon.core.interfaces.TTSProvider`:

```python
from tts_daemon.core.errors import SynthesisError
from tts_daemon.core.interfaces import TTSProvider
from tts_daemon.core.models import (
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
# pyproject.toml of tts-daemon-myengine
[project.entry-points."tts_daemon.providers"]
myengine = "tts_daemon_myengine:MyEngineProvider"
```

`pip install tts-daemon-myengine` and restart: the gateway discovers it,
`myengine` becomes valid in requests and config, and `providers.myengine`
reaches your constructor. Built-in names win on collision, and a plugin that
fails to import is logged and skipped — it can never take the server down.

### Option B — in-tree (contributing to the gateway)

1. Add `src/tts_daemon/providers/myengine.py`.
2. Register it in `create_default_registry`
   (`src/tts_daemon/providers/registry.py`) and add the entry point to
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
TTS_DAEMON__SPEECH__DEFAULT_PROVIDER=myengine tts-daemon serve
tts-daemon speak "testing my engine" --provider myengine
```

## Bundled providers

| Name    | Install                        | Local? | Output | Notes |
| ------- | ------------------------------ | ------ | ------ | ----- |
| `piper` | `pip install piper-tts` + voice models | ✅ local | WAV | The default engine; audio never leaves the machine. |
| `tone`  | built in                       | ✅ local | WAV | Dependency-free beeps; the always-available fallback. |
| `edge`  | `pip install 'tts-daemon[edge]'` | ☁️ cloud | MP3 | Free Microsoft neural voices, no key. |
| `kokoro`| `pip install 'tts-daemon[kokoro]'` + model files | ✅ local | WAV | High-quality neural (ONNX, CPU); needs the model + voices download. |
| `openai`| built in (needs an API key)    | ☁️ cloud | MP3 | OpenAI TTS (`tts-1`, `gpt-4o-mini-tts`, …). |
| `elevenlabs`| built in (needs an API key) | ☁️ cloud | MP3 | ElevenLabs premium voices. |

### `edge` — privacy & reliability

The `edge` provider is **cloud-backed**: your text is sent to Microsoft's
online voice service over an **unofficial** endpoint. That buys hundreds of
high-quality voices with zero setup, but means:

- **Text leaves your machine** — do not use it for anything sensitive; prefer
  `piper` (fully local) there.
- It needs **network access**; offline synthesis raises a `SynthesisError`.
- The endpoint is undocumented and can change without notice.

Configure a default voice and pass edge's `pitch`/`volume` through per request:

```yaml
providers:
  edge:
    default_voice: it-IT-ElsaNeural
```

```sh
tts-daemon speak "ciao" --provider edge
curl -X POST localhost:5111/v1/speak -H 'content-type: application/json' \
  -d '{"provider":"edge","text":"hi","options":{"pitch":"+10Hz","volume":"-10%"}}'
```

### `kokoro` — local neural TTS

`kokoro` runs a small (~82M param) neural model locally through ONNX Runtime —
fully offline, better prosody than Piper. After `pip install 'tts-daemon[kokoro]'`
download the two artifacts from the
[kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases)
into `~/.local/share/tts-daemon/kokoro/` (or point the config at them):

- `kokoro-v1.0.onnx` (the model)
- `voices-v1.0.bin` (the voice pack)

```yaml
providers:
  kokoro:
    model_path: ~/.local/share/tts-daemon/kokoro/kokoro-v1.0.onnx
    voices_path: ~/.local/share/tts-daemon/kokoro/voices-v1.0.bin
    default_voice: af_sarah
```

`tts-daemon providers` tells you exactly which piece is missing (package, model,
or voices file) with the download link.

### `openai` / `elevenlabs` — cloud, opt-in

Both use only stdlib `urllib` (no SDK) and need an API key. **Privacy & cost:**
your text is sent to the provider and billed per character — keep them for cases
where the premium voice is worth it, and use a local engine otherwise.

```yaml
providers:
  openai:
    api_key: sk-...          # or the OPENAI_API_KEY env var
    model: gpt-4o-mini-tts   # or tts-1 / tts-1-hd
    default_voice: alloy
  elevenlabs:
    api_key: ...             # or the ELEVENLABS_API_KEY env var
    model_id: eleven_multilingual_v2
    default_voice: 21m00Tcm4TlvDq8ikWAM
```

```sh
tts-daemon speak "premium voice" --provider openai --voice nova
# ElevenLabs voice_settings pass through as options:
curl -X POST localhost:5111/v1/speak -H 'content-type: application/json' \
  -d '{"provider":"elevenlabs","text":"hi","options":{"stability":0.4,"similarity_boost":0.8}}'
```
