# Installation

## Requirements

- Python **3.10+**
- Linux or macOS (Windows works for serving and `synthesize`; playback uses
  `ffplay`/`mpv` when installed, falling back to the built-in `winsound`
  module for WAV)
- For real speech: the [Piper](https://github.com/OHF-Voice/piper1-gpl) TTS
  engine (optional — the gateway beeps through the `tone` provider until an
  engine is installed)

## Install the gateway

Recommended — an isolated install with [pipx](https://pipx.pypa.io):

```sh
pipx install tts-daemon
```

Or with plain pip (ideally inside a virtualenv):

```sh
pip install tts-daemon
```

From a checkout (for development, see [development.md](development.md)):

```sh
git clone https://github.com/DMGiulioRomano/TTS-Daemon.git
cd TTS-Daemon
pip install .
```

### One-line install script

The `curl | sh` installer does the same pipx/`pip --user` install and then
offers to add the Piper engine and download a voice for your locale. It never
uses `sudo`, is safe to re-run, and can undo itself:

```sh
curl -fsSL https://raw.githubusercontent.com/DMGiulioRomano/TTS-Daemon/main/scripts/install.sh | sh
```

When piped it reads prompts from your terminal; pass flags to run it
unattended (after `--`, since the script's own arguments come after `sh`):

| Flag | Effect |
| ---- | ------ |
| `--with-piper` / `--no-piper` | install (or skip) the `piper-tts` engine |
| `--voice ID` / `--no-voice` | download (or skip) a specific Piper voice |
| `--systemd` / `--no-systemd` | set up (or skip) the systemd user service (Linux) |
| `--from-source` | install from the git repo instead of PyPI |
| `--yes` | accept every prompt (good for scripts/CI) |
| `--uninstall` `[--purge]` | remove the gateway (and, with `--purge`, its data) |

```sh
# a complete, talking setup in one shot:
curl -fsSL https://raw.githubusercontent.com/DMGiulioRomano/TTS-Daemon/main/scripts/install.sh | sh -s -- --with-piper --yes
```

The same flags are available as environment variables for the piped case
(`TTS_DAEMON_INSTALL_PIPER`, `TTS_DAEMON_INSTALL_VOICE`, `TTS_DAEMON_VOICE`,
`TTS_DAEMON_SETUP_SYSTEMD`, `TTS_DAEMON_ASSUME_YES`).

The PyPI distribution is named `tts-daemon` (the `tts-daemon` name was
already taken by an unrelated project); the command it installs is still
`tts-daemon`. Verify:

```sh
tts-daemon serve
# in another terminal:
tts-daemon speak "It works"
curl -s localhost:5111/health
```

Without a TTS engine you will hear beeps — that is the `tone` fallback
provider confirming that the server, queue, and audio output all work.

## Install Piper

### 1. The engine

```sh
pip install piper-tts
```

This provides the `piper` executable. Alternatives: your distribution's
package, or a prebuilt binary from the Piper releases page — anything that
puts `piper` on your `PATH` (or set `providers.piper.binary` to its
location).

### 2. A voice

Voices are `.onnx` model files with a `.onnx.json` sidecar. The gateway can
fetch them for you into its models directory
(`~/.local/share/tts-daemon/piper` by default), creating it if needed:

```sh
tts-daemon download en_US-lessac-medium
```

Browse the catalog first if you like — filter by language with `--language`:

```sh
tts-daemon download --list                # every voice (id, language, quality, size)
tts-daemon download --list --language it  # just the Italian voices
```

Downloads are verified against their published size, written atomically, and
skipped when already present (`--force` re-fetches). Download as many voices as
you like and select per request with `voice`. If you already keep voices
elsewhere, point the gateway at them:

```yaml
# ~/.config/tts-daemon/config.yaml
providers:
  piper:
    models_dir: /path/to/your/voices
    default_voice: en_US-lessac-medium
```

### 3. Check

```sh
tts-daemon providers
# * piper      available
#   tone       available
tts-daemon voices
tts-daemon speak "A real voice at last"
```

With `default_provider: auto` (the default), Piper is preferred as soon as
it is available — no configuration needed.

## Audio output

The gateway plays audio through the first working system command it finds:

| Platform | Tried in order                                          |
| -------- | ------------------------------------------------------- |
| Linux    | `pw-play`, `paplay`, `aplay`, `ffplay`, `mpv`, `play`   |
| macOS    | `afplay` (preinstalled), then the Linux list            |
| Windows  | `ffplay`, `mpv`, then the built-in `winsound` (WAV)     |

Most desktop Linux systems already have `paplay` (PulseAudio) or `pw-play`
(PipeWire). If none is present, install one (`sudo apt install
pulseaudio-utils` or `ffmpeg`) — or pin your own command:

```yaml
playback:
  command: ["ffplay", "-autoexit", "-nodisp", "-loglevel", "quiet", "{file}"]
```

`GET /v1/status` reports `playback_available`, and unavailable playback
comes with an explanation in the server log.

## Running as a service (optional)

### systemd (Linux)

`~/.config/systemd/user/tts-daemon.service`:

```ini
[Unit]
Description=TTS Daemon

[Service]
ExecStart=%h/.local/bin/tts-daemon serve
Restart=on-failure

[Install]
WantedBy=default.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now tts-daemon
```

(Adjust `ExecStart` to wherever pip installed the script: `which tts-daemon`.)

### launchd (macOS)

`~/Library/LaunchAgents/dev.tts-daemon.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>dev.tts-daemon</string>
  <key>ProgramArguments</key>
  <array><string>/usr/local/bin/tts-daemon</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
```

```sh
launchctl load ~/Library/LaunchAgents/dev.tts-daemon.plist
```

## Security note

By default the gateway has no authentication; anyone who can reach the port
can make your speakers talk (and submit text to your TTS engine). The default
bind of `127.0.0.1` keeps it private to your machine.

Before you change `server.host` to bind beyond localhost, set a bearer token
so `/v1` requires `Authorization: Bearer <token>`:

```sh
TTS_DAEMON__SERVER__AUTH_TOKEN="$(openssl rand -hex 32)" \
  TTS_DAEMON__SERVER__HOST=0.0.0.0 tts-daemon serve
```

See [configuration.md](configuration.md#authentication) for the details
(query-param token for browsers, CLI/client `--token`, the startup warning).
Restrict `server.cors_origins` too when you expose the gateway.

## Uninstall

If you used the install script, let it clean up (it also removes the systemd
user service and leaves your data untouched unless you add `--purge`):

```sh
curl -fsSL https://raw.githubusercontent.com/DMGiulioRomano/TTS-Daemon/main/scripts/install.sh | sh -s -- --uninstall
```

Or by hand:

```sh
pip uninstall tts-daemon     # or: pipx uninstall tts-daemon
rm -rf ~/.config/tts-daemon ~/.local/share/tts-daemon ~/.cache/tts-daemon
```
