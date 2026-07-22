#!/bin/sh
# One-line installer for tts-daemon.
#
#   curl -fsSL https://raw.githubusercontent.com/DMGiulioRomano/TTS-Daemon/main/scripts/install.sh | sh
#
# It installs the `tts-daemon` gateway (isolated with pipx when present, else
# `pip install --user`), then optionally installs the Piper engine, downloads a
# default voice for your locale, and — on Linux — sets up a systemd user
# service. It never uses sudo and never mutates system directories: everything
# lands in your user environment.
#
# The script is POSIX sh and idempotent: re-running upgrades in place and skips
# work already done. Undo it with `--uninstall` (add `--purge` to also delete
# your config, cached clips and downloaded voices).
#
# Non-interactive use (piped, or CI): prompts default to "install the gateway
# only" unless you pass flags. Examples:
#   curl -fsSL .../install.sh | sh -s -- --with-piper --voice en_US-lessac-medium
#   curl -fsSL .../install.sh | sh -s -- --yes            # accept every prompt
#
# Options:
#   --with-piper / --no-piper     install (or skip) the piper-tts engine
#   --voice ID / --no-voice       download (or skip) a specific Piper voice
#   --systemd / --no-systemd      set up (or skip) the systemd user service (Linux)
#   --from-source                 install from the git repo instead of PyPI
#   --yes, -y                     assume "yes" for every prompt
#   --uninstall [--purge]         remove the gateway (and, with --purge, its data)
#   -h, --help                    show this help
set -eu

REPO_URL="https://github.com/DMGiulioRomano/TTS-Daemon"
PKG="tts-daemon"
DEFAULT_VOICE="en_US-lessac-medium"

OS=$(uname -s)

# Where the venv fallback (no pipx) lives, and where we expose its commands.
LOCAL_BIN="$HOME/.local/bin"
VENV_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/tts-daemon/venv"
INSTALL_MODE=""   # set by install_gateway: pipx | venv | user

# Option state. Tri-state prompts hold "ask" until a flag or env var pins them.
DO_UNINSTALL=0
DO_PURGE=0
ASSUME_YES="${TTS_DAEMON_ASSUME_YES:-0}"
FROM_SOURCE=0
WANT_PIPER="${TTS_DAEMON_INSTALL_PIPER:-ask}"     # ask | yes | no
WANT_VOICE="${TTS_DAEMON_INSTALL_VOICE:-ask}"     # ask | yes | no
WANT_SYSTEMD="${TTS_DAEMON_SETUP_SYSTEMD:-ask}"   # ask | yes | no
VOICE_ID="${TTS_DAEMON_VOICE:-}"
PATH_BEFORE=""

# ----------------------------------------------------------------- output

say()  { printf '>> %s\n' "$*"; }
warn() { printf '!! %s\n' "$*" >&2; }
die()  { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
    printf '%s\n' \
        "usage: install.sh [--with-piper|--no-piper] [--voice ID|--no-voice]" \
        "                  [--systemd|--no-systemd] [--from-source] [--yes]" \
        "                  [--uninstall [--purge]] [-h|--help]" \
        "" \
        "Run with no options to install the gateway and be prompted for the rest."
}

# confirm PROMPT DEFAULT  ->  0 for yes, 1 for no.
# Reads from /dev/tty so it works even under `curl | sh` (stdin is the script).
# With no terminal it falls back to DEFAULT (or "yes" when --yes was given).
confirm() {
    confirm_prompt=$1
    confirm_default=$2   # Y or N
    if [ "$ASSUME_YES" = "1" ]; then
        return 0
    fi
    if [ ! -r /dev/tty ]; then
        [ "$confirm_default" = "Y" ]
        return
    fi
    if [ "$confirm_default" = "Y" ]; then
        confirm_hint="[Y/n]"
    else
        confirm_hint="[y/N]"
    fi
    printf '%s %s ' "$confirm_prompt" "$confirm_hint" > /dev/tty
    read -r confirm_reply < /dev/tty || confirm_reply=""
    [ -z "$confirm_reply" ] && confirm_reply=$confirm_default
    case $confirm_reply in
        [Yy]*) return 0 ;;
        *) return 1 ;;
    esac
}

# ------------------------------------------------------------- environment

require_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        die "python3 is required (3.10+). Install Python and re-run."
    fi
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        found=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        die "Python 3.10+ is required, found $found. Upgrade Python and re-run."
    fi
}

user_bin_dir() {
    python3 - <<'PY'
import os
import site
import sys

base = site.getuserbase()
print(os.path.join(base, "Scripts" if sys.platform == "win32" else "bin"))
PY
}

# Make freshly pip-installed --user scripts callable within this run, and
# remember the PATH we started with so we can warn about a persistent fix.
add_local_bins_to_path() {
    PATH_BEFORE=$PATH
    for candidate in "$(user_bin_dir)" "$HOME/.local/bin"; do
        [ -d "$candidate" ] || continue
        case ":$PATH:" in
            *":$candidate:"*) ;;
            *) PATH="$candidate:$PATH" ;;
        esac
    done
    export PATH
}

warn_if_not_on_path() {
    command -v "$1" >/dev/null 2>&1 || return 0
    bindir=$(dirname "$(command -v "$1")")
    case ":$PATH_BEFORE:" in
        *":$bindir:"*) return 0 ;;
    esac
    warn "$1 is installed in $bindir, which is not on your PATH."
    warn "Add it to your shell profile so the command is found next time:"
    warn "    export PATH=\"$bindir:\$PATH\""
}

# ---------------------------------------------------------------- install

pipx_has() { pipx list --short 2>/dev/null | grep -q "^$1 "; }

# Symlink a console script from the fallback venv into ~/.local/bin.
link_from_venv() {
    if [ -x "$VENV_DIR/bin/$1" ]; then
        mkdir -p "$LOCAL_BIN"
        ln -sf "$VENV_DIR/bin/$1" "$LOCAL_BIN/$1"
    fi
}

install_gateway() {
    if [ "$FROM_SOURCE" = "1" ]; then
        spec="git+$REPO_URL"
        say "installing $PKG from source ($spec)"
    else
        spec="$PKG"
        say "installing $PKG from PyPI"
    fi

    if command -v pipx >/dev/null 2>&1; then
        INSTALL_MODE=pipx
        if pipx_has "$PKG"; then
            if [ "$FROM_SOURCE" = "1" ]; then
                pipx install --force "$spec"
            else
                pipx upgrade "$PKG"
            fi
        else
            pipx install "$spec"
        fi
    elif python3 -m venv "$VENV_DIR" 2>/dev/null; then
        # No pipx: an isolated venv is the robust fallback — it works on the
        # "externally managed" Pythons (Homebrew, newer Debian/Ubuntu) where
        # `pip install --user` is refused (PEP 668). Re-running reuses the venv.
        INSTALL_MODE=venv
        say "pipx not found; installing into an isolated venv at $VENV_DIR"
        "$VENV_DIR/bin/python" -m pip install --quiet --upgrade pip || true
        "$VENV_DIR/bin/python" -m pip install --upgrade "$spec"
        link_from_venv tts-daemon
    else
        # Last resort: no pipx and venv creation failed (e.g. Debian without
        # python3-venv). Try pip --user and, if PEP 668 blocks it, guide the user.
        INSTALL_MODE=user
        say "pipx and venv both unavailable; falling back to pip --user"
        if ! python3 -m pip install --user --upgrade "$spec"; then
            die "pip --user was refused (an 'externally managed' Python). Install
    pipx and re-run — it gives an isolated install without this problem:
        macOS:          brew install pipx
        Debian/Ubuntu:  sudo apt install pipx    (or python3-venv, then re-run)"
        fi
    fi
}

install_piper() {
    say "installing the piper-tts engine (this pulls onnxruntime; it may take a minute)"
    case $INSTALL_MODE in
        pipx)
            if pipx_has piper-tts; then
                pipx upgrade piper-tts
            else
                pipx install piper-tts
            fi ;;
        venv)
            "$VENV_DIR/bin/python" -m pip install --upgrade piper-tts
            link_from_venv piper ;;
        *)
            python3 -m pip install --user --upgrade piper-tts ;;
    esac
}

default_voice_for_locale() {
    # Best-effort: map the locale's language to a well-known Piper voice.
    # Anything unmapped falls back to en_US-lessac-medium; the download itself
    # is best-effort, so a stale id just prints the catalog's suggestions.
    loc=${LC_ALL:-${LANG:-}}
    loc=${loc%%.*}
    case $loc in
        en_GB*)         printf '%s\n' "en_GB-alan-medium" ;;
        it_*)           printf '%s\n' "it_IT-riccardo-x_low" ;;
        fr_*)           printf '%s\n' "fr_FR-siwis-medium" ;;
        de_*)           printf '%s\n' "de_DE-thorsten-medium" ;;
        *)              printf '%s\n' "$DEFAULT_VOICE" ;;
    esac
}

download_voice() {
    voice=$1
    # The 'download' subcommand is newer than the current PyPI release, so a
    # PyPI install may not have it yet. Detect that instead of dumping an
    # "invalid choice: 'download'" error on the user.
    if ! tts-daemon download --help >/dev/null 2>&1; then
        warn "the installed tts-daemon has no 'download' command yet (the published"
        warn "release predates the built-in voice downloader), so the default voice"
        warn "was not fetched. To get a talking setup now, re-run with --from-source:"
        warn "    curl -fsSL $REPO_URL/raw/main/scripts/install.sh | sh -s -- --with-piper --from-source"
        warn "or download a voice by hand from https://huggingface.co/rhasspy/piper-voices"
        warn "into ${XDG_DATA_HOME:-$HOME/.local/share}/tts-daemon/piper/."
        return 0
    fi
    say "downloading Piper voice: $voice"
    if tts-daemon download "$voice"; then
        return 0
    fi
    warn "voice download did not complete. You can retry later:"
    warn "    tts-daemon download $voice"
    warn "or browse the catalog for your language:"
    warn "    tts-daemon download --list --language <code>"
}

# --------------------------------------------------------------- playback

playback_candidates() {
    # Mirrors CommandPlayer's per-platform detection order.
    case $OS in
        Darwin) printf '%s\n' "afplay pw-play paplay aplay ffplay mpv play" ;;
        *)      printf '%s\n' "pw-play paplay aplay ffplay mpv play" ;;
    esac
}

check_playback() {
    for cmd in $(playback_candidates); do
        if command -v "$cmd" >/dev/null 2>&1; then
            say "audio playback: found '$cmd'"
            return 0
        fi
    done
    warn "no audio playback command found (looked for: $(playback_candidates))."
    case $OS in
        Darwin)
            warn "macOS ships 'afplay'; if it is missing, install ffmpeg:  brew install ffmpeg" ;;
        *)
            warn "install one, for example:"
            warn "    apt install pulseaudio-utils     # provides paplay"
            warn "    apt install ffmpeg               # provides ffplay" ;;
    esac
    warn "or pin your own command via 'playback.command' in the config."
    warn "The gateway still works in API mode (POST /v1/synthesize) with no audio device."
    return 1
}

# ---------------------------------------------------------------- systemd

systemd_unit_path() {
    printf '%s\n' "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/tts-daemon.service"
}

setup_systemd() {
    if [ "$OS" != "Linux" ]; then
        return 0
    fi
    if ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl not found; skipping the service setup."
        return 0
    fi
    bin=$(command -v tts-daemon 2>/dev/null || true)
    [ -n "$bin" ] || bin="$HOME/.local/bin/tts-daemon"
    unit=$(systemd_unit_path)
    mkdir -p "$(dirname "$unit")"
    cat > "$unit" <<EOF
[Unit]
Description=TTS Daemon

[Service]
ExecStart=$bin serve
Restart=on-failure

[Install]
WantedBy=default.target
EOF
    say "wrote $unit"
    if systemctl --user daemon-reload 2>/dev/null &&
        systemctl --user enable --now tts-daemon 2>/dev/null; then
        say "service started:  systemctl --user status tts-daemon"
    else
        warn "could not start the user service now (no user D-Bus session in this shell?)."
        warn "start it from a desktop login with:  systemctl --user enable --now tts-daemon"
    fi
}

# -------------------------------------------------------------- uninstall

# Remove ~/.local/bin/<name> only if it is a symlink we created into the venv,
# never a real binary the user put there themselves.
remove_our_symlink() {
    target="$LOCAL_BIN/$1"
    if [ -L "$target" ]; then
        dest=$(readlink "$target" 2>/dev/null || true)
        case $dest in
            "$VENV_DIR"/*) rm -f "$target" ;;
        esac
    fi
}

uninstall() {
    say "uninstalling $PKG"
    if [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
        unit=$(systemd_unit_path)
        if [ -f "$unit" ]; then
            systemctl --user disable --now tts-daemon 2>/dev/null || true
            rm -f "$unit"
            systemctl --user daemon-reload 2>/dev/null || true
            say "removed the systemd user service"
        fi
    fi

    removed=0

    # pipx app
    if command -v pipx >/dev/null 2>&1 && pipx_has "$PKG"; then
        pipx uninstall "$PKG" || true
        removed=1
    fi

    # venv fallback: our symlinks (only if they point into the venv) + the venv
    remove_our_symlink tts-daemon
    remove_our_symlink piper
    if [ -d "$VENV_DIR" ]; then
        rm -rf "$VENV_DIR"
        say "removed the isolated venv"
        removed=1
    fi

    # pip --user install: only when a real (non-symlink) console script sits in
    # the user bin — so we never disturb a system-wide or editable/dev install.
    ubin=$(user_bin_dir)
    if [ -f "$ubin/tts-daemon" ] && [ ! -L "$ubin/tts-daemon" ]; then
        python3 -m pip uninstall -y "$PKG" >/dev/null 2>&1 || true
        say "removed the pip --user install"
        removed=1
    fi

    [ "$removed" = "1" ] || warn "no managed $PKG install found (installed another way?)."

    data_dir="${XDG_DATA_HOME:-$HOME/.local/share}/tts-daemon"
    conf_dir="${XDG_CONFIG_HOME:-$HOME/.config}/tts-daemon"
    cache_dir="${XDG_CACHE_HOME:-$HOME/.cache}/tts-daemon"
    if [ "$DO_PURGE" = "1" ]; then
        rm -rf "$data_dir" "$conf_dir" "$cache_dir"
        say "removed your config, cached clips and downloaded voices."
    else
        say "left your data in place. To remove voices, config and cache too:"
        say "    rm -rf \"$data_dir\" \"$conf_dir\" \"$cache_dir\""
        say "    (or re-run:  install.sh --uninstall --purge)"
    fi
    say "done."
}

# ------------------------------------------------------------- next steps

print_next_steps() {
    printf '\n'
    say "$PKG is ready. Next steps:"
    cat <<'EOF'

    tts-daemon serve                 # start the gateway on http://127.0.0.1:5111
    tts-daemon speak "hello there"   # in another terminal: say something

    # No audio device (containers, servers)? Use API mode instead:
    curl -X POST localhost:5111/v1/synthesize \
         -H 'content-type: application/json' \
         -d '{"text":"hello there"}' --output hello.wav
EOF
    if ! command -v piper >/dev/null 2>&1; then
        printf '\n'
        say "You are on the built-in 'tone' engine (beeps). For real speech, add Piper:"
        say "    curl -fsSL $REPO_URL/raw/main/scripts/install.sh | sh -s -- --with-piper"
    fi
    printf '\nDocs: %s#readme\n' "$REPO_URL"
}

# ------------------------------------------------------------------- main

parse_args() {
    while [ $# -gt 0 ]; do
        case $1 in
            --uninstall) DO_UNINSTALL=1 ;;
            --purge) DO_PURGE=1 ;;
            --yes|-y) ASSUME_YES=1 ;;
            --with-piper) WANT_PIPER=yes ;;
            --no-piper) WANT_PIPER=no ;;
            --voice)
                shift
                [ $# -gt 0 ] || die "--voice needs a voice id (e.g. --voice en_US-lessac-medium)"
                VOICE_ID=$1
                WANT_VOICE=yes ;;
            --voice=*) VOICE_ID=${1#*=}; WANT_VOICE=yes ;;
            --no-voice) WANT_VOICE=no ;;
            --systemd) WANT_SYSTEMD=yes ;;
            --no-systemd) WANT_SYSTEMD=no ;;
            --from-source) FROM_SOURCE=1 ;;
            -h|--help) usage; exit 0 ;;
            *) warn "unknown option: $1"; usage; exit 2 ;;
        esac
        shift
    done
}

main() {
    parse_args "$@"

    if [ "$DO_UNINSTALL" = "1" ]; then
        uninstall
        return 0
    fi

    require_python
    install_gateway
    add_local_bins_to_path
    warn_if_not_on_path tts-daemon

    # Piper engine (default yes when asked interactively — it is what makes it talk).
    do_piper=0
    case $WANT_PIPER in
        yes) do_piper=1 ;;
        no) do_piper=0 ;;
        *) if command -v piper >/dev/null 2>&1; then
               say "piper is already installed."
           elif confirm "Install the Piper engine for real speech?" Y; then
               do_piper=1
           fi ;;
    esac
    if [ "$do_piper" = "1" ]; then
        install_piper
    fi

    # A default voice — only useful once Piper is (or will be) present.
    if command -v piper >/dev/null 2>&1 || [ "$do_piper" = "1" ]; then
        [ -n "$VOICE_ID" ] || VOICE_ID=$(default_voice_for_locale)
        do_voice=0
        case $WANT_VOICE in
            yes) do_voice=1 ;;
            no) do_voice=0 ;;
            *) if confirm "Download the default voice '$VOICE_ID' now?" Y; then
                   do_voice=1
               fi ;;
        esac
        if [ "$do_voice" = "1" ]; then
            download_voice "$VOICE_ID" || true
        fi
    fi

    check_playback || true

    # systemd user service (Linux only; default no when asked — it is opt-in).
    case $WANT_SYSTEMD in
        yes) setup_systemd ;;
        no) ;;
        *) if [ "$OS" = "Linux" ] && command -v systemctl >/dev/null 2>&1; then
               if confirm "Set up a systemd user service (auto-start on login)?" N; then
                   setup_systemd
               fi
           fi ;;
    esac

    print_next_steps
}

main "$@"
