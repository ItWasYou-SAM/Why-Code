"""Make the retrieval fixture helpers importable by module name.

The fixtures live in ``fixtures/`` so they are not collected as tests; adding
that directory (not a package import path) keeps test imports short.
"""

from __future__ import annotations

import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
if str(FIXTURES) not in sys.path:
    sys.path.insert(0, str(FIXTURES))
