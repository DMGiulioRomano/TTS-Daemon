"""Default network location of the gateway.

Lives in its own dependency-free module so that :mod:`tts_gateway.client`
(and anyone vendoring it) can import the defaults without pulling in the
server's dependencies.
"""

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5111
DEFAULT_BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
