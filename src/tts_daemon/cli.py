"""The ``tts-daemon`` command line interface.

``serve`` runs the server; every other subcommand is a thin HTTP client for
a running gateway (see :mod:`tts_daemon.client`), so the CLI doubles as a
reference for the REST API.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from tts_daemon.client import GatewayClient, GatewayClientError
from tts_daemon.core.errors import ConfigError
from tts_daemon.defaults import DEFAULT_BASE_URL
from tts_daemon.voices import KokoroDownloader, VoiceCatalog, VoiceCatalogError

#: Reserved ``download`` target that fetches the kokoro model+voices pair
#: instead of a Piper voice (Piper ids look like ``en_US-lessac-medium``, so the
#: bare word never collides with one).
KOKORO_TARGET = "kokoro"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (GatewayClientError, ConfigError, VoiceCatalogError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tts-daemon",
        description="Local text-to-speech gateway: speak text via interchangeable TTS providers.",
    )
    from tts_daemon import __version__

    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    serve = subparsers.add_parser("serve", help="run the gateway server")
    serve.add_argument("--config", metavar="PATH", help="path to a YAML config file")
    serve.add_argument("--host", help="bind address (overrides config)")
    serve.add_argument("--port", type=int, help="bind port (overrides config)")
    serve.set_defaults(handler=cmd_serve)

    speak = subparsers.add_parser("speak", help="queue text for playback on a running gateway")
    speak.add_argument("text", nargs="*", help="text to speak (reads stdin when omitted)")
    speak.add_argument("--provider", help="provider name (default: server's default)")
    speak.add_argument("--voice", help="voice id")
    speak.add_argument("--speed", type=float, help="rate multiplier, 1.0 = normal")
    speak.add_argument("--interrupt", action="store_true", help="cancel current speech first")
    speak.add_argument("--wait", action="store_true", help="block until playback finishes")
    _add_url(speak)
    speak.set_defaults(handler=cmd_speak)

    synthesize = subparsers.add_parser(
        "synthesize", help="synthesize audio to a file instead of playing it"
    )
    synthesize.add_argument("text", nargs="*", help="text to synthesize (reads stdin when omitted)")
    synthesize.add_argument(
        "-o", "--output", required=True, metavar="FILE", help="output file ('-' for stdout)"
    )
    synthesize.add_argument("--provider", help="provider name")
    synthesize.add_argument("--voice", help="voice id")
    synthesize.add_argument("--speed", type=float, help="rate multiplier")
    _add_url(synthesize)
    synthesize.set_defaults(handler=cmd_synthesize)

    stop = subparsers.add_parser("stop", help="stop playback and clear the queue")
    _add_url(stop)
    stop.set_defaults(handler=cmd_stop)

    status = subparsers.add_parser("status", help="show queue and provider status")
    status.add_argument("--json", action="store_true", help="print raw JSON")
    _add_url(status)
    status.set_defaults(handler=cmd_status)

    voices = subparsers.add_parser("voices", help="list available voices")
    voices.add_argument("--provider", help="restrict to one provider")
    voices.add_argument("--json", action="store_true", help="print raw JSON")
    _add_url(voices)
    voices.set_defaults(handler=cmd_voices)

    providers = subparsers.add_parser("providers", help="list providers and availability")
    providers.add_argument("--json", action="store_true", help="print raw JSON")
    _add_url(providers)
    providers.set_defaults(handler=cmd_providers)

    download = subparsers.add_parser(
        "download",
        help="download a Piper voice, or the kokoro model pair (download kokoro)",
    )
    download.add_argument(
        "voice",
        nargs="?",
        help="voice id to download (e.g. en_US-lessac-medium), or 'kokoro' for "
        "the kokoro model+voices pair",
    )
    download.add_argument(
        "--list",
        action="store_true",
        dest="list_voices",
        help="list the Piper catalog instead of downloading",
    )
    download.add_argument(
        "--language", help="with --list, filter by language (e.g. 'it' or 'it_IT')"
    )
    download.add_argument(
        "--models-dir",
        help="destination directory (default: the piper models_dir, or the kokoro "
        "dir for 'download kokoro', from config)",
    )
    download.add_argument(
        "--force", action="store_true", help="re-download even if the files already exist"
    )
    download.set_defaults(handler=cmd_download)

    init_config = subparsers.add_parser(
        "init-config", help="write an annotated config file with the defaults"
    )
    init_config.add_argument(
        "--path", metavar="FILE", help="destination (default: ~/.config/tts-daemon/config.yaml)"
    )
    init_config.add_argument("--force", action="store_true", help="overwrite an existing file")
    init_config.set_defaults(handler=cmd_init_config)

    return parser


def _add_url(subparser: argparse.ArgumentParser) -> None:
    subparser.add_argument(
        "--url",
        default=DEFAULT_BASE_URL,
        help=f"gateway base URL (default: {DEFAULT_BASE_URL})",
    )
    subparser.add_argument(
        "--token",
        default=os.environ.get("TTS_DAEMON_TOKEN"),
        help="bearer token for an authenticated gateway (env: TTS_DAEMON_TOKEN)",
    )


# ------------------------------------------------------------------ handlers


def cmd_serve(args: argparse.Namespace) -> int:
    # Imported lazily: the server stack (FastAPI, uvicorn) is not needed for
    # the client subcommands, which keeps them snappy.
    import uvicorn

    from tts_daemon.api.app import create_app
    from tts_daemon.config import load_config

    config = load_config(args.config)
    server_overrides = {
        key: value for key, value in (("host", args.host), ("port", args.port)) if value
    }
    if server_overrides:
        server = config.server.model_copy(update=server_overrides)
        config = config.model_copy(update={"server": server})

    level = config.logging.level.upper()
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    app = create_app(config)
    uvicorn.run(
        app,
        host=config.server.host,
        port=config.server.port,
        log_level=level.lower(),
    )
    return 0


def _gather_text(args: argparse.Namespace) -> str:
    if args.text:
        return " ".join(args.text)
    if sys.stdin.isatty():
        print("error: no text given (pass it as arguments or pipe it in)", file=sys.stderr)
        raise SystemExit(2)
    return sys.stdin.read()


def cmd_speak(args: argparse.Namespace) -> int:
    client = GatewayClient(args.url, token=args.token)
    result = client.speak(
        _gather_text(args),
        provider=args.provider,
        voice=args.voice,
        speed=args.speed,
        interrupt=args.interrupt,
        wait=args.wait,
    )
    utterance = result["utterance"]
    state = utterance["state"]
    line = f"[{utterance['id']}] {state} via {utterance['provider']}"
    if utterance.get("error"):
        print(f"{line}: {utterance['error']}", file=sys.stderr)
        return 1
    print(line)
    return 0


def cmd_synthesize(args: argparse.Namespace) -> int:
    client = GatewayClient(args.url, token=args.token)
    audio = client.synthesize(
        _gather_text(args), provider=args.provider, voice=args.voice, speed=args.speed
    )
    if args.output == "-":
        sys.stdout.buffer.write(audio)
    else:
        Path(args.output).write_bytes(audio)
        print(f"wrote {len(audio)} bytes to {args.output}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    result = GatewayClient(args.url, token=args.token).stop()
    print(f"cancelled {result['cancelled']} utterance(s)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    status = GatewayClient(args.url, token=args.token).status()
    if args.json:
        print(json.dumps(status, indent=2))
        return 0
    queue = status["queue"]
    current = queue["current"]
    print(f"default provider : {status['default_provider'] or 'none'}")
    if status.get("default_provider_error"):
        print(f"provider problem : {status['default_provider_error']}")
    print(f"playback         : {'available' if status['playback_available'] else 'UNAVAILABLE'}")
    if current:
        print(f"speaking         : [{current['id']}] {_ellipsis(current['text'])}")
    else:
        print("speaking         : (idle)")
    print(f"queued           : {queue['size']}/{queue['max_size']}")
    for utterance in queue["queued"]:
        print(f"  - [{utterance['id']}] {_ellipsis(utterance['text'])}")
    return 0


def cmd_voices(args: argparse.Namespace) -> int:
    result = GatewayClient(args.url, token=args.token).voices(args.provider)
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    voices = result["voices"]
    if not voices:
        print("no voices found")
        return 0
    width = max(len(voice["id"]) for voice in voices)
    for voice in voices:
        language = voice.get("language") or ""
        print(f"{voice['id']:<{width}}  {voice['provider']:<8} {language:<8} {voice['name']}")
    return 0


def cmd_providers(args: argparse.Namespace) -> int:
    result = GatewayClient(args.url, token=args.token).providers()
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for provider in result["providers"]:
        marker = "*" if provider["default"] else " "
        state = "available" if provider["available"] else f"unavailable ({provider['reason']})"
        print(f"{marker} {provider['name']:<10} {state}")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    if args.voice == KOKORO_TARGET:
        # A fixed pair of files, so --list has nothing to enumerate; ignore it.
        return _download_kokoro(args)
    catalog = VoiceCatalog()
    if args.list_voices:
        return _list_voices(catalog, args.language)
    if not args.voice:
        print(
            "error: give a voice id to download, or use --list to browse the catalog",
            file=sys.stderr,
        )
        return 2

    models_dir = _resolve_models_dir(args.models_dir)
    result = catalog.download(args.voice, models_dir, force=args.force, progress=_make_progress())
    if not sys.stderr.isatty():
        pass  # no in-place progress line to close
    elif result.downloaded:
        print(file=sys.stderr)  # end the progress line

    if result.skipped:
        print(f"{result.voice_id} already present in {result.models_dir} (use --force to refetch)")
    else:
        print(f"downloaded {result.voice_id} to {result.models_dir}")
    print(f'try it: tts-daemon speak "Hello" --voice {result.voice_id}')
    return 0


def _download_kokoro(args: argparse.Namespace) -> int:
    model_path, voices_path = _resolve_kokoro_paths(args.models_dir)
    result = KokoroDownloader().download(
        model_path, voices_path, force=args.force, progress=_make_progress()
    )
    if sys.stderr.isatty() and result.downloaded:
        print(file=sys.stderr)  # end the progress line

    if result.skipped:
        print("kokoro model and voices already present (use --force to refetch):")
    else:
        print("downloaded the kokoro model and voices:")
    for path in result.paths:
        state = "downloaded" if path in result.downloaded else "present"
        print(f"  {path} ({state})")
    print('try it: tts-daemon speak "Hello" --provider kokoro')
    return 0


def _list_voices(catalog: VoiceCatalog, language: str | None) -> int:
    voices = catalog.list(language)
    if not voices:
        suffix = f" for language {language!r}" if language else ""
        print(f"no voices found{suffix}")
        return 0
    width = max(len(voice.id) for voice in voices)
    for voice in voices:
        language_label = voice.language or ""
        quality = voice.quality or ""
        print(f"{voice.id:<{width}}  {language_label:<7} {quality:<7} {voice.size_mb:6.1f} MB")
    return 0


def _resolve_models_dir(explicit: str | None) -> Path:
    """Where to put voices: --models-dir, else the piper models_dir, else the default."""
    if explicit:
        return Path(explicit).expanduser()
    from tts_daemon.providers.piper import default_models_dir

    try:
        from tts_daemon.config import load_config

        settings = load_config().provider_settings("piper")
    except ConfigError:
        return default_models_dir()
    configured = settings.get("models_dir")
    return Path(configured).expanduser() if configured else default_models_dir()


def _resolve_kokoro_paths(models_dir: str | None) -> tuple[Path, Path]:
    """Where to put the kokoro pair: --models-dir wins, else config, else defaults.

    Mirrors ``KokoroProvider``'s own path resolution so a downloaded pair lands
    exactly where the provider will look for it (respecting the
    ``providers.kokoro.model_path`` / ``voices_path`` overrides).
    """
    from tts_daemon.providers.kokoro import _MODEL_FILE, _VOICES_FILE, default_kokoro_dir

    if models_dir:
        base = Path(models_dir).expanduser()
        return base / _MODEL_FILE, base / _VOICES_FILE

    try:
        from tts_daemon.config import load_config

        settings = load_config().provider_settings("kokoro")
    except ConfigError:
        settings = {}
    base = default_kokoro_dir()
    model = settings.get("model_path")
    voices = settings.get("voices_path")
    return (
        Path(model).expanduser() if model else base / _MODEL_FILE,
        Path(voices).expanduser() if voices else base / _VOICES_FILE,
    )


def _make_progress():
    """A terminal progress printer, or None when stderr is not a TTY (tests, pipes)."""
    if not sys.stderr.isatty():
        return None

    def progress(name: str, done: int, total: int) -> None:
        pct = int(done * 100 / total) if total else 0
        mib = done / (1024 * 1024)
        print(f"\r  {name}: {pct:3d}% ({mib:.1f} MiB)", end="", file=sys.stderr, flush=True)

    return progress


def cmd_init_config(args: argparse.Namespace) -> int:
    from tts_daemon.config import EXAMPLE_CONFIG, default_config_path

    path = Path(args.path).expanduser() if args.path else default_config_path()
    if path.exists() and not args.force:
        print(f"error: {path} already exists (use --force to overwrite)", file=sys.stderr)
        return 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(EXAMPLE_CONFIG, encoding="utf-8")
    print(f"wrote {path}")
    return 0


def _ellipsis(text: str, limit: int = 60) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


if __name__ == "__main__":
    sys.exit(main())
