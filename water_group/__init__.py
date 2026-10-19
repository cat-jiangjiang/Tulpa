"""Bridge package shim for water-group.

Enables standard Python import conventions:
    import water_group
    import water_group.memory_engine
    from water_group import bootstrap
    from water_group.memory_engine import get_memory_engine
    import water_group.sync_groups as sg

Resolves the Python limitation where hyphenated directory names (water-group)
cannot be imported directly as valid identifiers.
"""
import importlib
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_WG_DIR = _ROOT / "water-group"
_wg_str = str(_WG_DIR)

if _wg_str not in sys.path:
    sys.path.insert(0, _wg_str)

if _wg_str not in __path__:
    __path__.append(_wg_str)

# Pre-import key submodules so they are available immediately
for _mod_name in ("memory_engine", "bootstrap", "sync_groups", "sync_personas", "fwd"):
    try:
        _mod = importlib.import_module(f"water_group.{_mod_name}")
        globals()[_mod_name] = _mod
    except Exception:
        pass


def __getattr__(name: str):
    try:
        mod = importlib.import_module(f"water_group.{name}")
        globals()[name] = mod
        return mod
    except ImportError:
        pass
    try:
        mod = importlib.import_module(name)
        globals()[name] = mod
        return mod
    except ImportError as exc:
        raise AttributeError(f"module 'water_group' has no attribute '{name}'") from exc
