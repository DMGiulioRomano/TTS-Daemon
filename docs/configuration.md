# Configuration

The gateway runs with **zero configuration** — every option has a sensible
default. When you do need to change something, three layers apply, highest
precedence last:

1. **Built-in defaults**
2. **YAML file** — the first of:
   - a path passed explicitly (`tts-daemon serve --config path.yaml`)
   - `$TTS_DAEMON_CONFIG`
   - `~/.config/tts-daemon/config.yaml` (respects `$XDG_CONFIG_HOME`)
3. **Environment variables** — `TTS_DAEMON__SECTION__KEY=value`

Generate an annotated starting point at the default location:

```sh
tts-daemon init-config
```

Unknown keys are rejected at startup with the offending location — typos
fail loudly instead of being ignored.

## Environment variable form

Double underscores separate path segments (so key names may contain single
underscores); values are parsed as YAML scalars:

```sh
TTS_DAEMON__SERVER__PORT=6000
TTS_DAEMON__SPEECH__DEFAULT_PROVIDER=piper
TTS_DAEMON__SPEECH__PROVIDER_PRIORITY='[piper, tone]'
TTS_DAEMON__PROVIDERS__PIPER__MODELS_DIR=/opt/voices
TTS_DAEMON__PLAYBACK__BACKEND=null
```

## Reference

### `server`

| Key            | Default       | Meaning                                                       |
| -------------- | ------------- | ------------------------------------------------------------- |
| `host`         | `127.0.0.1`   | Bind address. Keep on localhost unless you trust the network or set `auth_token`. |
| `port`         | `5111`        | Bind port.                                                    |
| `cors_origins` | `["*"]`       | Origins allowed for browser clients. `[]` disables CORS headers. |
| `auth_token`   | *unset*       | When set, every `/v1` route requires this bearer token (see below). `null` leaves the gateway open. |

#### Authentication

By default the gateway is unauthenticated — fine on loopback. Set
`server.auth_token` (or the `TTS_DAEMON__SERVER__AUTH_TOKEN` env var) before
binding beyond localhost:

```sh
TTS_DAEMON__SERVER__AUTH_TOKEN="$(openssl rand -hex 32)" \
  TTS_DAEMON__SERVER__HOST=0.0.0.0 tts-daemon serve
```

When a token is set:

- every `/v1/*` route requires `Authorization: Bearer <token>`;
- `/v1/ws` and `/v1/events` also accept the token as a `?token=<token>` query
  parameter, because browsers cannot set headers on `WebSocket`/`EventSource`;
- `GET /health` and the playground at `/` stay open (the playground prompts for
  a token and remembers it in `localStorage`);
- a wrong or missing token returns `401` with the usual `{"detail": …}` body.

The token is compared in constant time. The CLI and Python client pick it up
from `--token` or the `TTS_DAEMON_TOKEN` env var:

```sh
TTS_DAEMON_TOKEN=… tts-daemon speak "authenticated hello" --url http://host:5111
```

The gateway logs a warning at startup when `server.host` is not loopback and no
token is set.

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

### `cache`

Caches synthesized clips on disk so repeated phrases (notifications, hook
messages) replay instantly. The key covers the provider, voice, speed,
options, and text — plus a provider fingerprint (piper folds in the voice
model's mtime), so swapping a model invalidates stale clips.

| Key       | Default | Meaning                                                              |
| --------- | ------- | -------------------------------------------------------------------- |
| `enabled` | `true`  | Turn the cache off with `false`.                                     |
| `max_mb`  | `200`   | Size budget; least-recently-used clips are evicted once exceeded.    |
| `dir`     | *unset* | Storage directory; defaults to `$XDG_CACHE_HOME/tts-daemon` (`~/.cache/tts-daemon`). |

Bypass the cache for a single request with the `no_cache` option, e.g.
`{"text": "...", "options": {"no_cache": true}}`. Stats are reported by
`GET /v1/status`.

### `logging`

| Key     | Default | Meaning                                     |
| ------- | ------- | ------------------------------------------- |
| `level` | `INFO`  | Python log level (`DEBUG`, `INFO`, ...).    |

### `providers.piper`

| Key               | Default                              | Meaning                                             |
| ----------------- | ------------------------------------ | --------------------------------------------------- |
| `binary`          | `piper`                              | Executable name or path.                            |
| `models_dir`      | `~/.local/share/tts-daemon/piper`   | Directory scanned for `*.onnx` voices.              |
| `default_voice`   | first model found                    | Model stem (`en_US-lessac-medium`) or `.onnx` path. |
| `speed_flag`      | `--length_scale`                     | CLI flag for the rate parameter (the Python CLI spells it `--length-scale`). |
| `timeout_seconds` | `120`                                | Wall-time limit per synthesis.                      |
| `extra_args`      | `[]`                                 | Appended verbatim to every piper invocation.        |

### `providers.tone`

| Key             | Default | Meaning                              |
| --------------- | ------- | ------------------------------------ |
| `default_voice` | `mid`   | `low`, `mid`, or `high` beep pitch.  |

### `providers.edge` (optional extra)

Free Microsoft neural voices via the [`edge-tts`](https://pypi.org/project/edge-tts/)
package — hundreds of voices, no API key, no local model. Install the extra to
enable it:

```sh
pip install 'tts-daemon[edge]'
```

| Key              | Default             | Meaning                                                    |
| ---------------- | ------------------- | ---------------------------------------------------------- |
| `default_voice`  | `en-US-AriaNeural`  | Voice used when a request names none. List them with `tts-daemon voices --provider edge`. |
| `default_pitch`  | *unset*             | edge `pitch` applied when a request omits it, e.g. `"+10Hz"`. |
| `default_volume` | *unset*             | edge `volume` applied when a request omits it, e.g. `"-20%"`. |

Per-request `options` accepts `pitch` and `volume` (same string format);
`speed` maps to the edge rate (`1.5` → `+50%`). Output is MP3, which the player
routes to a capable command (`ffplay`/`mpv`/`afplay`).

> **Privacy / reliability note.** edge-tts is a **cloud** provider: the text you
> synthesize is sent to Microsoft's servers, over an **unofficial** endpoint
> that can change or break at any time, and it needs network access. Prefer
> Piper for anything private or offline. This is why `edge` is an opt-in extra
> and is not in the default `provider_priority`.

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
    models_dir: ~/.local/share/tts-daemon/piper
    default_voice: en_US-lessac-medium
    extra_args: ["--sentence_silence", "0.3"]
```
