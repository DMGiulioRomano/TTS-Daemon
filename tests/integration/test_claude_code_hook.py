"""Keep the Claude Code hook script honest.

The script lives outside the package (integrations/), so it is loaded by
path. These tests cover the pure parts (transcript parsing, markdown
preparation) and the never-fail contract; no HTTP calls are made.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

HOOK_PATH = (
    Path(__file__).resolve().parents[2] / "integrations" / "claude-code" / "claude_code_speak.py"
)


@pytest.fixture(scope="module")
def hook():
    if not HOOK_PATH.is_file():
        pytest.skip("repo checkout not available (installed package)")
    spec = importlib.util.spec_from_file_location("claude_code_speak", HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestPrepare:
    def test_strips_markdown(self, hook, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TTS_GATEWAY_SPEAK_MAX_CHARS", raising=False)
        text = (
            "## Result\n\n"
            "The fix is in `main.py`, see [the docs](https://example.com).\n\n"
            "```python\nprint('hidden')\n```\n\n"
            "- **bold point** one\n- point _two_\n"
        )
        spoken = hook.prepare(text)
        assert "```" not in spoken
        assert "print" not in spoken
        assert "code block omitted" in spoken
        assert "https://example.com" not in spoken
        assert "the docs" in spoken
        assert "#" not in spoken
        assert "**" not in spoken
        assert "main.py" in spoken

    def test_truncates_to_limit(self, hook, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TTS_GATEWAY_SPEAK_MAX_CHARS", "50")
        spoken = hook.prepare("word " * 100)
        assert len(spoken) < 80
        assert spoken.endswith("truncated.")

    def test_zero_limit_disables_truncation(self, hook, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TTS_GATEWAY_SPEAK_MAX_CHARS", "0")
        long_text = "word " * 200
        assert "truncated" not in hook.prepare(long_text)


class TestTranscript:
    def write_transcript(self, tmp_path: Path, entries: list[dict]) -> str:
        path = tmp_path / "transcript.jsonl"
        path.write_text("\n".join(json.dumps(entry) for entry in entries))
        return str(path)

    def assistant(self, *texts: str) -> dict:
        return {
            "type": "assistant",
            "message": {"content": [{"type": "text", "text": text} for text in texts]},
        }

    def test_finds_last_assistant_text(self, hook, tmp_path: Path) -> None:
        path = self.write_transcript(
            tmp_path,
            [
                self.assistant("first reply"),
                {"type": "user", "message": {"content": "question"}},
                self.assistant("final", "reply"),
            ],
        )
        assert hook.last_assistant_text(path) == "final\nreply"

    def test_skips_tool_only_messages(self, hook, tmp_path: Path) -> None:
        tool_only = {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {}}]},
        }
        path = self.write_transcript(tmp_path, [self.assistant("spoken part"), tool_only])
        assert hook.last_assistant_text(path) == "spoken part"

    def test_tolerates_garbage_lines(self, hook, tmp_path: Path) -> None:
        path = tmp_path / "transcript.jsonl"
        path.write_text('not json at all\n{"type": "weird"}\n')
        assert hook.last_assistant_text(str(path)) == ""

    def test_missing_file_is_empty(self, hook, tmp_path: Path) -> None:
        assert hook.last_assistant_text(str(tmp_path / "gone.jsonl")) == ""
        assert hook.last_assistant_text("") == ""


class TestNeverFail:
    def run_main(self, hook, monkeypatch: pytest.MonkeyPatch, stdin: str) -> tuple[int, list]:
        sent: list = []
        monkeypatch.setattr(hook, "speak", sent.append)
        monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
        return hook.main(), sent

    def test_notification_payload_is_spoken(self, hook, monkeypatch) -> None:
        code, sent = self.run_main(
            hook,
            monkeypatch,
            json.dumps({"hook_event_name": "Notification", "message": "Claude needs input"}),
        )
        assert code == 0
        assert sent == ["Claude needs input"]

    def test_plain_text_stdin_is_spoken(self, hook, monkeypatch) -> None:
        code, sent = self.run_main(hook, monkeypatch, "just some words")
        assert code == 0
        assert sent == ["just some words"]

    def test_unknown_event_is_silent_success(self, hook, monkeypatch) -> None:
        code, sent = self.run_main(
            hook, monkeypatch, json.dumps({"hook_event_name": "SomethingNew"})
        )
        assert code == 0
        assert sent == []

    def test_stop_hook_active_guard(self, hook, monkeypatch) -> None:
        code, sent = self.run_main(
            hook,
            monkeypatch,
            json.dumps({"hook_event_name": "Stop", "stop_hook_active": True}),
        )
        assert code == 0
        assert sent == []
