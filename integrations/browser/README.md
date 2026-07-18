# Browser userscript

Read any selected text aloud through your local tts-gateway, on any website.

## Install

1. Install a userscript manager: [Violentmonkey](https://violentmonkey.github.io/),
   [Tampermonkey](https://www.tampermonkey.net/), or Greasemonkey.
2. Open [`tts-gateway.user.js`](./tts-gateway.user.js) in the manager
   (usually: click the raw file, the manager offers to install it).
3. Make sure the gateway is running: `tts-gateway serve`.

## Use

| Shortcut        | Action                                            |
| --------------- | ------------------------------------------------- |
| **Alt+S**       | Speak the selection (interrupts current speech)   |
| **Alt+Shift+S** | Queue the selection after whatever is playing     |
| **Alt+X**       | Stop speaking and clear the queue                 |

The same actions are available from the userscript manager's menu. A small
toast in the bottom-right corner confirms each action or shows the error.

## Notes

- The script talks to `http://127.0.0.1:5111`. If you changed
  `server.port`, edit the `GATEWAY` constant at the top of the script.
- It uses `GM_xmlhttpRequest` instead of `fetch` because HTTPS pages are
  not allowed to call plain-HTTP localhost services directly (mixed
  content); the userscript manager's privileged API is exempt.
- Shortcuts are ignored while you are typing in an input, textarea, or
  editable region.
