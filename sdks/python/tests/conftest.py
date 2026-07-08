"""Make the cost_monitor package importable regardless of pytest's working dir.

Without this, a bare `pytest` run from the CMA repo root collects this test
directory but fails to import `cost_monitor` (the package lives under
sdks/python, which isn't on sys.path). Inserting the package root here fixes
collection for any invocation; the documented `cd sdks/python && pytest` path
is unaffected.
"""

import sys
from pathlib import Path

_PKG_ROOT = Path(__file__).resolve().parents[1]  # -> sdks/python
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))
