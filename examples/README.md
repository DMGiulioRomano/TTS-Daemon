# Examples

All examples expect a running gateway (`tts-daemon serve`).

| File                                             | Shows                                                     |
| ------------------------------------------------ | --------------------------------------------------------- |
| [`curl.sh`](./curl.sh)                           | Every REST endpoint from the shell                        |
| [`python_client.py`](./python_client.py)         | The bundled `GatewayClient`: speak, wait, interrupt       |
| [`websocket_client.py`](./websocket_client.py)   | The WebSocket protocol and live utterance events          |
| [`openai_compat.py`](./openai_compat.py)         | The official OpenAI client pointed at the local gateway   |

Also see the ready-to-use integrations:

- [`integrations/browser`](../integrations/browser) — userscript: speak any
  selected text on any website.
- [`integrations/claude-code`](../integrations/claude-code) — hooks that
  speak Claude Code's replies and notifications.

Quick shell aliases you may enjoy:

```sh
# say — speak from the terminal:  say "build finished"
say() { tts-daemon speak "$*"; }

# hush — stop speaking immediately
alias hush='tts-daemon stop'

# speak the result of any long command:  make test; say "exit $?"
```
