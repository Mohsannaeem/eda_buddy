#!/usr/bin/env python
"""Compatibility shim — the real module is eda_buddy.push_result.

Existing runtime YAMLs hardcode this path in regression post-hooks, e.g.

    post: python /path/to/eda_buddy/scripts/push_result.py --id X --total {total} ...

so the path stays valid after the move to src/eda_buddy/. New hooks should use
the module form, which survives the package moving between venvs:

    post: python -m eda_buddy.push_result --id X --total {total} ...
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)

from eda_buddy.push_result import main  # noqa: E402  (path setup must precede import)

if __name__ == "__main__":
    main()
