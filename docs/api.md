# API reference

Base URL: `http://127.0.0.1:5111` (configurable). Everything under `/v1` is
versioned and stable; additions are backwards-compatible, removals or
repurposings would mean a `/v2`. A live OpenAPI UI is served at `/docs`.

All request/response bodies are JSON (`content-type: application/json`),
except `/v1/synthesize` responses, which are audio bytes.

## Errors

Errors use one shape:

```json
{ "detail": "human-readable explanation" }
```

| Status | Meaning                                                        |
| ------ | -------------------------------------------------------------- |
| 404    | Unknown provider or utterance id                               |
| 422    | Invalid request (missing/empty text, bad speed, unknown field, text over `speech.max_text_length`) |
| 429    | Playback queue is full                                         |
| 502    | The TTS engine failed to synthesize                            |
| 503    | Provider exists but is unavailable (missing binary/model); the detail says how to fix it |

## `POST /v1/speak`

Queue text for playback. Returns **202** immediately with the queued
utterance, or **200** with the final state when `wait` is true.

Request:

| Field       | Type    | Default        | Notes                                            |
| ----------- | ------- | -------------- | ------------------------------------------------ |
| `text`      | string  | *required*     | Non-empty; limit `speech.max_text_length`.       |
| `provider`  | string  | server default | Provider name (`piper`, `tone`, ...).            |
| `voice`     | string  | provider default | Provider-specific voice id.                    |
| `speed`     | number  | `1.0`          | Rate multiplier, `0 < speed <= 10`.              |
| `options`   | object  | `{}`           | Provider-specific passthrough (e.g. piper: `{"speaker": 3}`). |
| `interrupt` | boolean | `false`        | Cancel queued + current speech first.            |
| `wait`      | boolean | `false`        | Block until the utterance reaches a terminal state. |

```sh
curl -X POST localhost:5111/v1/speak -H 'content-type: application/json' \
  -d '{"text": "Deploy finished", "interrupt": true}'
```

Response:

```json
{
  "utterance": {
    "id": "5e7136b47ba1",
    "text": "Deploy finished",
    "provider": "piper",
    "voice": null,
    "speed": 1.0,
    "state": "queued",
    "error": null,
    "created_at": 1784395263.38,
    "started_at": null,
    "finished_at": null
  }
}
```

`state` is one of `queued → synthesizing → speaking → finished`, or
`cancelled` / `failed` (with `error` set). Note: with `wait: true` the HTTP
status is 200 even if the utterance ends `failed` — the failure belongs to
the utterance, not the request; check `state`.

## `POST /v1/synthesize`

Same fields as `/v1/speak` minus `interrupt`/`wait`. Returns the audio bytes
(`audio/wav` for the built-in providers) without queueing or playing —
playback stays fully client-side:

```sh
curl -X POST localhost:5111/v1/synthesize -H 'content-type: application/json' \
  -d '{"text": "saved, not spoken"}' -o clip.wav
```

## `POST /v1/audio/speech` (OpenAI-compatible)

A drop-in for OpenAI's speech endpoint: point any OpenAI TTS client at the
gateway's `/v1` base URL and it synthesizes locally instead.

Request (OpenAI's schema):

| Field             | Type   | Default    | Notes                                                        |
| ----------------- | ------ | ---------- | ------------------------------------------------------------ |
| `model`           | string | *required* | `tts-1`/`tts-1-hd` → gateway default provider; a registered provider name (`piper`, `tone`) selects it explicitly. |
| `input`           | string | *required* | Text to speak.                                               |
| `voice`           | string | *required* | Mapped via `openai_compat.voice_aliases`; an unmapped standard OpenAI voice (`alloy`, `nova`, …) falls back to the provider default; anything else is passed through as a provider voice id. |
| `response_format` | string | *unset*    | Only `wav` is supported today; other values → 422.          |
| `speed`           | number | `1.0`      | OpenAI range `0.25`–`4.0`.                                   |

Returns raw audio bytes (`audio/wav`). An `Authorization` header is accepted
and ignored (until [#18](https://github.com/DMGiulioRomano/TTS-Daemon/issues/18)).

```python
from openai import OpenAI
client = OpenAI(base_url="http://127.0.0.1:5111/v1", api_key="unused")
client.audio.speech.create(
    model="tts-1", voice="alloy", input="Hello", response_format="wav",
).stream_to_file("out.wav")  # → synthesized by the gateway's provider, locally
```

See [examples/openai_compat.py](../examples/openai_compat.py).

## `POST /v1/stop`

Cancel every queued utterance and interrupt the one playing.

```json
{ "cancelled": 3 }
```

## `GET /v1/status`

```json
{
  "queue": {
    "current":  { "id": "...", "state": "speaking", ... },
    "queued":   [ { "id": "...", "state": "queued", ... } ],
    "history":  [ { "id": "...", "state": "finished", ... } ],
    "size": 1,
    "max_size": 64
  },
  "default_provider": "piper",
  "default_provider_error": null,
  "playback_available": true,
  "cache": { "entries": 12, "size_mb": 3.4, "hits": 87, "misses": 20 }
}
```

`default_provider` is the *resolved* default (what `auto` picked); when
nothing is available it is `null` and `default_provider_error` explains why.
`cache` reports the synthesis cache (`hits`/`misses` count since the server
started) and is `null` when the cache is disabled (`cache.enabled: false`).

## `GET /v1/utterances/{id}`

State of a live or recently finished utterance (history keeps the last
`speech.history_size`). 404 once an id has expired from history.

## `GET /v1/voices[?provider=name]`

```json
{
  "voices": [
    { "id": "en_US-lessac-medium", "name": "lessac", "language": "en_US",
      "description": "lessac medium", "provider": "piper" }
  ]
}
```

Without `?provider=`, voices of all providers are combined; a provider whose
listing fails is skipped (logged server-side) so one broken engine cannot
hide the rest.

## `GET /v1/providers`

```json
{
  "providers": [
    { "name": "piper", "available": true,  "reason": null, "default": true },
    { "name": "tone",  "available": true,  "reason": null, "default": false }
  ]
}
```

`reason` tells you how to fix an unavailable provider ("piper binary not
found on PATH...").

## `GET /health`

```json
{ "status": "ok", "version": "0.1.0" }
```

## `GET /v1/events`

A read-only live event stream as [Server-Sent Events][sse] — the same
payloads the WebSocket pushes as `event` messages, but consumable with plain
`curl` or a browser `EventSource`, no WebSocket client required.

```sh
curl -N localhost:5111/v1/events
# : connected
# event: utterance.speaking
# data: {"type":"utterance.speaking","data":{"id":"5e7136b47ba1",...},"timestamp":...}
```

Each event is one SSE frame: an `event:` line (the event type) followed by a
`data:` line (the JSON payload). Lines beginning with `:` are comments — an
opening `: connected` and a `: ping` heartbeat every ~15 s so idle streams
survive proxies.

| Query   | Notes                                                                  |
| ------- | ---------------------------------------------------------------------- |
| `types` | Comma-separated event types to include, e.g. `?types=utterance.finished,queue.cleared`. All events when omitted. |

Same event types, snapshot semantics, and slow-consumer policy (oldest
dropped, 256-event buffer) as the WebSocket stream below. In a browser:

```js
const es = new EventSource("/v1/events");
es.addEventListener("utterance.finished", (e) => console.log(JSON.parse(e.data)));
```

[sse]: https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events

## WebSocket: `/v1/ws`

One socket gives you both a command channel and a live event stream (all
gateway events are pushed to every connected socket automatically).

### Commands (client → server)

```jsonc
{ "type": "speak", "text": "hi", "provider": "piper", "voice": null,
  "speed": 1.0, "options": {}, "interrupt": false, "id": 7 }
{ "type": "stop",   "id": 8 }
{ "type": "status", "id": 9 }
{ "type": "ping",   "id": 10 }
```

`id` is an optional correlation value (any JSON value) echoed back in the
matching response. WebSocket `speak` never waits — watch the events instead.

### Responses (server → client)

```jsonc
{ "type": "result", "id": 7, "request": "speak", "data": { "utterance": { ... } } }
{ "type": "error",  "id": 7, "detail": "text: Field required" }
{ "type": "pong",   "id": 10 }
```

### Events (server → client, unsolicited)

```jsonc
{ "type": "event",
  "event": { "type": "utterance.speaking",
             "data": { "id": "...", "state": "speaking", ...,
                       "duration_seconds": 2.4 },
             "timestamp": 1784395263.39 } }
```

Event types: `utterance.queued`, `utterance.synthesizing`,
`utterance.speaking` (payload includes `duration_seconds` when known),
`utterance.finished`, `utterance.cancelled`, `utterance.failed`, and
`queue.cleared` (`{"cancelled": n}`).

Delivery notes: each event's `data` is a snapshot taken at publish time and
is authoritative; cross-event *ordering* is best-effort. Slow consumers have
oldest events dropped rather than stalling playback (buffer: 256 events).

## Client libraries

- Python (stdlib-only, bundled): `tts_daemon.client.GatewayClient` — see
  [examples/python_client.py](../examples/python_client.py).
- Any language: it's plain JSON over HTTP; [examples/curl.sh](../examples/curl.sh)
  shows every endpoint.
