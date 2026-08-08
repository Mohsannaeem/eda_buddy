#!/usr/bin/env python
"""Compatibility shim — the real module is eda_buddy.run_report.

Makefiles generated before the src/ restructure baked an absolute path to this
file into REPORT_SCRIPT. Keeping the shim means those Makefiles keep reporting
instead of silently falling back to the "not found" warning. Regenerate with
`eda-buddy gen` to move onto $(REPORT_CMD), which uses the module form.
"""

import os
import sys

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)

from eda_buddy.run_report import main  # noqa: E402  (path setup must precede import)

if __name__ == "__main__":
    main()
