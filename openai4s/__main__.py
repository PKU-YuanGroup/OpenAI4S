"""Enable `python -m openai4s...` as the CLI entrypoint."""

import sys

from openai4s.cli import main

if __name__ == "__main__":
    sys.exit(main())
