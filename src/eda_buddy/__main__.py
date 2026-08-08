"""Allows `python -m eda_buddy ...` as an alternative to the console script,
which is useful when the interpreter's Scripts directory is not on PATH.
"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
