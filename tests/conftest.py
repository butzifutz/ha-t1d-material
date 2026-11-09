"""Lädt model.py/const.py ohne __init__.py (das Home Assistant importiert)."""

import sys
import types
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "custom_components" / "t1d_material"
if "t1d_material" not in sys.modules:
    mod = types.ModuleType("t1d_material")
    mod.__path__ = [str(PKG)]
    sys.modules["t1d_material"] = mod
