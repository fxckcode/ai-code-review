"""Pytest configuration: add the scripts directory to sys.path.

This lets tests import ``review``, ``adapters.registry``, etc. directly
without installing the package.
"""

import sys
from pathlib import Path

# .github/scripts/tests/ → .github/scripts/
_SCRIPTS_DIR = str(Path(__file__).parent.parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)
