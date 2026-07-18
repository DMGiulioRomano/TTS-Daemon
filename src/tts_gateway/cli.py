"""The ``tts-gateway`` command line interface.

``serve`` runs the server; every other subcommand is a thin HTTP client for
a running gateway (see :mod:`tts_gateway.client`), so the CLI doubles as a
reference for the REST API.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from tts_gateway.client import GatewayClient, GatewayClientError
from tts_gateway.core.errors import ConfigError
from tts_gateway.defaults import DEFAULT_BASE_URL


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except (GatewayClientError, ConfigError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tts-gateway",
        description="Local text-to-speech gateway: speak text via interchangeable TTS providers.",
    )
    from tts_gateway import __version__

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

    init_config = subparsers.add_parser(
        "init-config", help="write an annotated config file with the defaults"
    )
    init_config.add_argument(
        "--path", metavar="FILE", help="destination (default: ~/.config/tts-gateway/config.yaml)"
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


# ------------------------------------------------------------------ handlers


def cmd_serve(args: argparse.Namespace) -> int:
    # Imported lazily: the server stack (FastAPI, uvicorn) is not needed for
    # the client subcommands, which keeps them snappy.
    import uvicorn

    from tts_gateway.api.app import create_app
    from tts_gateway.config import load_config

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
    client = GatewayClient(args.url)
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
    client = GatewayClient(args.url)
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
    result = GatewayClient(args.url).stop()
    print(f"cancelled {result['cancelled']} utterance(s)")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    status = GatewayClient(args.url).status()
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
    result = GatewayClient(args.url).voices(args.provider)
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
    result = GatewayClient(args.url).providers()
    if args.json:
        print(json.dumps(result, indent=2))
        return 0
    for provider in result["providers"]:
        marker = "*" if provider["default"] else " "
        state = "available" if provider["available"] else f"unavailable ({provider['reason']})"
        print(f"{marker} {provider['name']:<10} {state}")
    return 0


def cmd_init_config(args: argparse.Namespace) -> int:
    from tts_gateway.config import EXAMPLE_CONFIG, default_config_path

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
