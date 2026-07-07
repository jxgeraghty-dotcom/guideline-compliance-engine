"""Enable ``python -m compliance`` as an alias for the CLI entry point."""

import sys

from compliance.cli import main

if __name__ == "__main__":
    sys.exit(main())
