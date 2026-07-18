# Claude Code integration

Have Claude Code talk to you: hear a summary of each reply when Claude
finishes, and hear notifications (permission requests, idle prompts) as they
happen — all through your local tts-gateway.

## Setup

1. Start the gateway (`tts-gateway serve`) — or install it as a service, see
   [docs/installation.md](../../docs/installation.md).
2. Copy [`claude_code_speak.py`](./claude_code_speak.py) somewhere stable,
   e.g. `~/.claude/hooks/claude_code_speak.py`.
3. Register it in `~/.claude/settings.json` (user-wide) or
   `.claude/settings.json` (per project):

```json
{
  "hooks": {
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/claude_code_speak.py"
          }
        ]
      }
    ],
    "Notification": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 ~/.claude/hooks/claude_code_speak.py"
          }
        ]
      }
    ]
  }
}
```

Keep only the `Stop` entry if you just want replies spoken, or only
`Notification` if you just want alerts.

## What it does

- **Stop hook** — extracts the text of Claude's final reply from the session
  transcript, strips markdown (code blocks become "code block omitted"),
  truncates to a listenable length, and sends it to the gateway with
  `interrupt: true`.
- **Notification hook** — speaks the notification message ("Claude needs
  your permission…", "Claude is waiting for your input").
- **Any error** (gateway not running, malformed payload) exits silently with
  status 0 — speech must never break your coding session.

## Tuning (environment variables)

| Variable                      | Default                  | Meaning                        |
| ----------------------------- | ------------------------ | ------------------------------ |
| `TTS_GATEWAY_URL`             | `http://127.0.0.1:5111`  | Gateway base URL               |
| `TTS_GATEWAY_SPEAK_MAX_CHARS` | `400`                    | Truncation limit (0 = no cut)  |
| `TTS_GATEWAY_SPEAK_PROVIDER`  | server default           | TTS provider to use            |
| `TTS_GATEWAY_SPEAK_VOICE`     | provider default         | Voice id                       |
| `TTS_GATEWAY_SPEAK_SPEED`     | `1.0`                    | Rate multiplier                |

Set them in the hook command itself if you like:

```json
"command": "TTS_GATEWAY_SPEAK_SPEED=1.3 python3 ~/.claude/hooks/claude_code_speak.py"
```

## Try it without Claude Code

The script doubles as a plain pipe target, which is handy to verify your
setup end to end:

```sh
echo "The gateway hears me" | python3 claude_code_speak.py
```
