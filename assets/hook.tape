# VHS tape for assets/hook.gif — https://github.com/charmbracelet/vhs
#
# The Claude Code hook: Claude's replies (and notifications) spoken aloud
# through the gateway. Regenerate after changes with:
#
#     vhs assets/hook.tape
#
# Requirements: vhs on PATH, tts-gateway installed, the hook script at
# integrations/claude-code/claude_code_speak.py.

Output assets/hook.gif

Set Theme "Catppuccin Mocha"
Set FontSize 20
Set Width 800
Set Height 500
Set Padding 16
Set TypingSpeed 40ms

Hide
Type "tts-gateway serve > /tmp/tts-gateway-hook.log 2>&1 &"
Enter
Sleep 2s
Type "clear"
Enter
Type "alias claude_code_speak.py='python3 integrations/claude-code/claude_code_speak.py'"
Enter
Type "clear"
Enter
Show

Type "# Stop hook -> Claude's final reply is spoken when it finishes."
Enter
Type "# Pipe text in to try it without Claude Code:"
Enter
Type `echo "All 188 tests pass. The **gateway** is ready." | claude_code_speak.py`
Enter
Sleep 1.5s

Type "tts-gateway status"
Enter
Sleep 3s

Type "# Notification hook -> permission prompts and idle alerts, too."
Enter
Type `echo '{"hook_event_name":"Notification","message":"Claude needs your permission"}' | claude_code_speak.py`
Enter
Sleep 3s

Hide
Type "kill %1"
Enter
