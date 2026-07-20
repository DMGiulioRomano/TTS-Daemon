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
| **Alt+A**       | Toggle auto-read of new chat replies on this site |

The same actions are available from the userscript manager's menu. A small
toast in the bottom-right corner confirms each action or shows the error.

## Auto-read chat replies

`Alt+A` turns on **auto-read** for the current site: every new assistant
reply is spoken as soon as it finishes streaming, with no selection needed.
A small `🔊 auto` badge in the bottom-left shows it is active (click it, or
press `Alt+A` again, to stop). The choice is remembered per site across
reloads, and the existing conversation is never re-read when you enable it
or reload — only replies that arrive afterwards.

It finds the assistant's messages with a CSS selector. Sensible defaults
ship for **ChatGPT**, **Claude**, and **Gemini**; for any other chat (or if
a site changes its markup and auto-read goes quiet or reads the wrong
thing), set the selector yourself from the userscript manager's menu →
*"Set assistant selector for this site"*. Your value is stored per site.
Code blocks (`<pre>`/`<code>`) are skipped so code is not read aloud, and
replies are queued so they play in order.

## Notes

- The script talks to `http://127.0.0.1:5111`. If you changed
  `server.port`, edit the `GATEWAY` constant at the top of the script.
- It uses `GM_xmlhttpRequest` instead of `fetch` because HTTPS pages are
  not allowed to call plain-HTTP localhost services directly (mixed
  content); the userscript manager's privileged API is exempt.
- Shortcuts are ignored while you are typing in an input, textarea, or
  editable region.
