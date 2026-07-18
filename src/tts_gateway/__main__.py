"""Allow ``python -m tts_gateway`` as an alternative to the console script."""

import sys

from tts_gateway.cli import main

if __name__ == "__main__":
    sys.exit(main())
