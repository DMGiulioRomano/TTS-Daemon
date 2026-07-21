#!/usr/bin/env python3
"""Claude Code hook: speak Claude's replies and notifications via tts-daemon.

Standard library only; drop it anywhere and point your hooks at it.

Wire-up (see README.md next to this file): register the script for the
``Stop`` hook (speaks a summary of Claude's final reply) and/or the
``Notification`` hook (speaks permission prompts and idle alerts). Claude
Code sends the hook payload as JSON on stdin.

Environment overrides:

- ``TTS_DAEMON_URL``            gateway base URL (default http://127.0.0.1:5111)
- ``TTS_DAEMON_TOKEN``          bearer token when the gateway sets server.auth_token
- ``TTS_DAEMON_SPEAK_MAX_CHARS`` truncation limit for replies (default 400)
- ``TTS_DAEMON_SPEAK_PROVIDER`` / ``TTS_DAEMON_SPEAK_VOICE`` /
  ``TTS_DAEMON_SPEAK_SPEED``    forwarded to the gateway when set

The script never fails the hook: on any problem (gateway down, malformed
payload) it exits 0 silently, because speech must never break a coding
session.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request

DEFAULT_URL = "http://127.0.0.1:5111"
DEFAULT_MAX_CHARS = 400


def main() -> int:
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Not a hook payload; treat stdin as plain text (nice for testing:
        #   echo "hello" | python3 claude_code_speak.py
        text = raw.strip()
        if text:
            speak(text)
        return 0

    event = payload.get("hook_event_name", "")
    if event == "Notification":
        text = str(payload.get("message", "")).strip()
    elif event == "Stop":
        if payload.get("stop_hook_active"):
            return 0  # do not re-trigger from our own continuation
        text = last_assistant_text(payload.get("transcript_path", ""))
    else:
        text = ""

    if text:
        speak(prepare(text))
    return 0


def last_assistant_text(transcript_path: str) -> str:
    """The text of the last assistant message in a Claude Code transcript.

    Transcripts are JSONL; assistant entries carry a message with a list of
    content blocks, of which the text blocks are what we want to hear.
    """
    if not transcript_path:
        return ""
    try:
        with open(transcript_path, encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if entry.get("type") != "assistant":
            continue
        message = entry.get("message") or {}
        blocks = message.get("content") or []
        texts = [
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(part for part in texts if part).strip()
        if text:
            return text
    return ""


def prepare(text: str) -> str:
    """Make markdown listenable: drop code blocks, links, and markup."""
    text = re.sub(r"```.*?```", " code block omitted. ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]*)`", r"\1", text)  # inline code -> bare text
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links -> label
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)  # headings
    text = re.sub(r"[*_]{1,3}([^*_]+)[*_]{1,3}", r"\1", text)  # emphasis
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.MULTILINE)  # bullets
    text = re.sub(r"\s+", " ", text).strip()

    limit = _int_env("TTS_DAEMON_SPEAK_MAX_CHARS", DEFAULT_MAX_CHARS)
    if limit > 0 and len(text) > limit:
        cut = text[:limit]
        # avoid stopping mid-word when there is a recent space to cut at
        if " " in cut[limit // 2 :]:
            cut = cut[: cut.rfind(" ")]
        text = cut + " … truncated."
    return text


def speak(text: str) -> None:
    body: dict[str, object] = {"text": text, "interrupt": True}
    provider = os.environ.get("TTS_DAEMON_SPEAK_PROVIDER")
    voice = os.environ.get("TTS_DAEMON_SPEAK_VOICE")
    if provider:
        body["provider"] = provider
    if voice:
        body["voice"] = voice
    speed = os.environ.get("TTS_DAEMON_SPEAK_SPEED")
    if speed:
        try:
            body["speed"] = float(speed)
        except ValueError:
            pass
    url = os.environ.get("TTS_DAEMON_URL", DEFAULT_URL).rstrip("/") + "/v1/speak"
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("TTS_DAEMON_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=5).close()
    except OSError:
        pass  # gateway not running: stay silent, never break the session


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except ValueError:
        return default


if __name__ == "__main__":
    sys.exit(main())
