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
download and place them for you (into `~/.local/share/tts-daemon/piper` by
default, or your configured `providers.piper.models_dir`):

```sh
tts-daemon download --list --language en   # browse the catalog
tts-daemon download en_US-lessac-medium    # fetch a voice (skips if present; --force to refetch)
```

Any voice from the [Piper voice catalogue](https://github.com/OHF-Voice/piper1-gpl/blob/main/docs/VOICES.md)
works; download as many as you like and select per request with `voice`.
(The manual equivalent is `python3 -m piper.download_voices <id> --data-dir <models_dir>`.)
If you already keep voices elsewhere, point the gateway at them:

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

The gateway has no authentication; anyone who can reach the port can make
your speakers talk (and submit text to your TTS engine). The default bind of
`127.0.0.1` keeps it private to your machine. If you change `server.host`,
do it only on a network where that is acceptable, and restrict
`server.cors_origins` accordingly.

## Uninstall

```sh
pip uninstall tts-daemon     # or: pipx uninstall tts-daemon
rm -rf ~/.config/tts-daemon ~/.local/share/tts-daemon
```
