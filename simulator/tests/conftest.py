"""Make `simulator/` importable when pytest is invoked from the repo root.

`pytest simulator/tests/` works either way, but this lets `from bin_sensor
import BinSensor` resolve without an installed package.
"""
import sys
from pathlib import Path

_SIM_DIR = Path(__file__).resolve().parent.parent
if str(_SIM_DIR) not in sys.path:
    sys.path.insert(0, str(_SIM_DIR))
