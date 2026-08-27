"""
Makes `import tinysim` work when running these scripts straight from a clone.

Importing this module puts the repository root on the import path.  If you have
installed TinySim (`pip install -e .`), you do not need it -- but importing it
does no harm either.
"""

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
