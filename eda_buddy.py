#!/usr/bin/env python
"""Compatibility shim for the pre-package layout.

`python eda_buddy.py --gen-makefile` is the invocation documented in README
section 2 and baked into existing user scripts, so it keeps working after the
move to src/eda_buddy/. It forwards argv unchanged to the real entry point.

Prefer the installed console script:

    pip install -e .
    eda-buddy gen
"""

import os
import sys

# Running from a checkout: put src/ ahead of everything so `eda_buddy` resolves
# to the package rather than to this file. When EDA Buddy is pip-installed and
# this shim is absent, the console script reaches cli:main directly.
_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)

from eda_buddy.cli import main  # noqa: E402  (path setup must precede import)

if __name__ == "__main__":
    print("[EDA Buddy] note: 'python eda_buddy.py' is deprecated; "
          "install the package and use the 'eda-buddy' command instead.",
          file=sys.stderr)
    main()
