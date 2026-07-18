# Configuration

The gateway runs with **zero configuration** — every option has a sensible
default. When you do need to change something, three layers apply, highest
precedence last:

1. **Built-in defaults**
2. **YAML file** — the first of:
   - a path passed explicitly (`tts-gateway serve --config path.yaml`)
   - `$TTS_GATEWAY_CONFIG`
   - `~/.config/tts-gateway/config.yaml` (respects `$XDG_CONFIG_HOME`)
3. **Environment variables** — `TTS_GATEWAY__SECTION__KEY=value`

Generate an annotated starting point at the default location:

```sh
tts-gateway init-config
```

Unknown keys are rejected at startup with the offending location — typos
fail loudly instead of being ignored.

## Environment variable form

Double underscores separate path segments (so key names may contain single
underscores); values are parsed as YAML scalars:

```sh
TTS_GATEWAY__SERVER__PORT=6000
TTS_GATEWAY__SPEECH__DEFAULT_PROVIDER=piper
TTS_GATEWAY__SPEECH__PROVIDER_PRIORITY='[piper, tone]'
TTS_GATEWAY__PROVIDERS__PIPER__MODELS_DIR=/opt/voices
TTS_GATEWAY__PLAYBACK__BACKEND=null
```

## Reference

### `server`

| Key            | Default       | Meaning                                                       |
| -------------- | ------------- | ------------------------------------------------------------- |
| `host`         | `127.0.0.1`   | Bind address. Keep on localhost unless you trust the network (there is no authentication). |
| `port`         | `5111`        | Bind port.                                                    |
| `cors_origins` | `["*"]`       | Origins allowed for browser clients. `[]` disables CORS headers. |

### `speech`

| Key                 | Default             | Meaning                                                  |
| ------------------- | ------------------- | -------------------------------------------------------- |
| `default_provider`  | `auto`              | Provider used when a request names none. `auto` = first available in `provider_priority`. |
| `provider_priority` | `[piper, tone]`     | Order tried by `auto`.                                   |
| `queue_size`        | `64`                | Pending utterances before `/v1/speak` returns 429.       |
| `history_size`      | `50`                | Finished utterances kept in `/v1/status` and `/v1/utterances/{id}`. |
| `max_text_length`   | `10000`             | Longer texts are rejected with 422.                      |

### `playback`

| Key       | Default | Meaning                                                              |
| --------- | ------- | -------------------------------------------------------------------- |
| `backend` | `auto`  | `auto` (detect a playback command), `command` (always use `command`), `null` (synthesize but stay silent — for headless/API-only use). |
| `command` | *unset* | Playback argv override, e.g. `["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", "{file}"]`. `{file}` is replaced with a temporary audio file; without it, audio bytes are piped to stdin. |

### `logging`

| Key     | Default | Meaning                                     |
| ------- | ------- | ------------------------------------------- |
| `level` | `INFO`  | Python log level (`DEBUG`, `INFO`, ...).    |

### `providers.piper`

| Key               | Default                              | Meaning                                             |
| ----------------- | ------------------------------------ | --------------------------------------------------- |
| `binary`          | `piper`                              | Executable name or path.                            |
| `models_dir`      | `~/.local/share/tts-gateway/piper`   | Directory scanned for `*.onnx` voices.              |
| `default_voice`   | first model found                    | Model stem (`en_US-lessac-medium`) or `.onnx` path. |
| `speed_flag`      | `--length_scale`                     | CLI flag for the rate parameter (the Python CLI spells it `--length-scale`). |
| `timeout_seconds` | `120`                                | Wall-time limit per synthesis.                      |
| `extra_args`      | `[]`                                 | Appended verbatim to every piper invocation.        |

### `providers.tone`

| Key             | Default | Meaning                              |
| --------------- | ------- | ------------------------------------ |
| `default_voice` | `mid`   | `low`, `mid`, or `high` beep pitch.  |

### Third-party providers

Each provider reads its own `providers.<name>` section; the gateway passes
the mapping through untouched. Consult the provider's documentation for its
keys.

## Full example

```yaml
server:
  host: 127.0.0.1
  port: 5111

speech:
  default_provider: auto
  provider_priority: [piper, tone]
  queue_size: 64

playback:
  backend: auto

logging:
  level: INFO

providers:
  piper:
    models_dir: ~/.local/share/tts-gateway/piper
    default_voice: en_US-lessac-medium
    extra_args: ["--sentence_silence", "0.3"]
```
