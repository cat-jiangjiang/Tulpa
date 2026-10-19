"""Test suite for Phase 3: water_group module compatibility shim."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_imports():
    # 1. Standard package import
    import water_group
    assert hasattr(water_group, "memory_engine")
    assert hasattr(water_group, "bootstrap")

    # 2. Submodule direct import
    import water_group.memory_engine
    assert hasattr(water_group.memory_engine, "MemoryEngine")
    assert hasattr(water_group.memory_engine, "get_memory_engine")

    # 3. from ... import ...
    from water_group import bootstrap
    assert hasattr(bootstrap, "ensure_napcat")
    assert hasattr(bootstrap, "ensure_tulpa")
    assert hasattr(bootstrap, "check_tulpa_mcp_health")

    # 4. Deep import
    from water_group.memory_engine import get_memory_engine, MemoryEngine
    assert callable(get_memory_engine)
    assert isinstance(MemoryEngine, type)

    # 5. Aliased import
    import water_group.sync_groups as sg
    assert hasattr(sg, "inspect_and_sync")

    # 6. Test fwd module
    import water_group.fwd as fwd
    assert fwd is not None

    print("✓ All water_group import variations succeeded seamlessly!")


if __name__ == "__main__":
    test_imports()
    print("PASS: water_group module compatibility suite passed.")
