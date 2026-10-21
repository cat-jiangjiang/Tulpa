"""Guards the single module identity of the water-group subsystem.

`water-group` contains a hyphen, so it cannot be imported as a package name
directly and the bridge package `water_group` exists to give every file exactly
one canonical import path. Before this suite, five call sites imported the
memory engine as the top-level module `memory_engine` while three others used
`water_group.memory_engine`. The engine holds a module-level singleton, so the
process silently ended up with two engines: two seed runs, two locks, two sets
of state.

The suite checks three layers:
  1. No source file imports a water-group module under a bare top-level name.
  2. Importing the package does not eagerly pull in optional dependencies.
  3. In a clean subprocess, both import paths resolve to the same module object
     and the singleton is shared.
"""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Files whose only job is to import through the bridge; a top-level import here
# would be the bug this suite exists to catch.
SCANNED = [
    "chatlocal",
    "water-group",
    "water_group",
    "scripts",
    "app.py",
    "app_mcp.py",
]
WATER_GROUP_MODULES = {
    "memory_engine", "memory_commands", "memory_format",
    "bootstrap", "sync_groups",
}

RUNTIME_PROBE = """
import sys
import water_group
from water_group.memory_engine import get_memory_engine

# One module object per file: no top-level alias was created.
assert 'memory_engine' not in sys.modules, sorted(k for k in sys.modules if 'memory_engine' in k)
assert sys.modules['water_group.memory_engine'] is water_group.memory_engine

# One engine: the singleton is shared, not duplicated per import path.
assert get_memory_engine() is get_memory_engine()
print('PROBE_OK')
"""

STAR_PROBE = """
ns = {}
exec('from water_group import *', ns)
for name in ['memory_engine','memory_commands','memory_format','bootstrap',
             'sync_groups']:
    assert name in ns, name
assert 'sys' not in ns and 'Path' not in ns and 'importlib' not in ns
print('STAR_OK')
"""

LAZY_PROBE = """
import sys
import water_group
assert 'water_group.bootstrap' not in sys.modules, 'bridge must not eager-import bootstrap'
assert sorted(water_group.__all__) == sorted([
    'memory_engine','memory_commands','memory_format',
    'bootstrap','sync_groups'])
print('LAZY_OK')
"""


def test_no_bare_imports():
    """No file may import a water-group module under a top-level name."""
    violations = []
    for target in SCANNED:
        base = ROOT / target
        paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError as exc:
                raise AssertionError(f"{path} 无法解析: {exc}") from exc
            for node in ast.walk(tree):
                names = []
                if isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module or ""]
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                for name in names:
                    if name in WATER_GROUP_MODULES or name.split(".")[0] in WATER_GROUP_MODULES:
                        if name.split(".")[0] != "water_group":
                            violations.append(f"{path.relative_to(ROOT)}:{node.lineno} imports {name!r}")
    assert not violations, "water-group 模块必须通过 water_group 包名导入:\n  " + "\n  ".join(violations)


def test_no_string_import():
    """importlib.import_module would bypass the AST check above."""
    import re
    pattern = re.compile(r"""import_module\(\s*['"](?:memory_engine|memory_commands|memory_format|bootstrap|sync_groups)['"]""")
    hits = []
    for target in SCANNED:
        base = ROOT / target
        paths = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in paths:
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{path.relative_to(ROOT)}:{line}")
    assert not hits, "禁止用字符串导入 water-group 模块:\n  " + "\n  ".join(hits)


def test_bridge_lazy():
    """Importing the bridge must not eagerly load optional dependencies."""
    done = subprocess.run([sys.executable, "-c", LAZY_PROBE], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "LAZY_OK" in done.stdout


def test_single_module_identity():
    """In a clean interpreter both paths must be the same module object."""
    done = subprocess.run([sys.executable, "-c", RUNTIME_PROBE], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "PROBE_OK" in done.stdout


def test_star_import_stays_clean():
    """Star import must expose the submodules and nothing from the bridge."""
    done = subprocess.run([sys.executable, "-c", STAR_PROBE], cwd=ROOT, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    assert "STAR_OK" in done.stdout


if __name__ == "__main__":
    test_no_bare_imports()
    print("✓ 无裸顶层导入（AST 全量扫描）")
    test_no_string_import()
    print("✓ 无字符串式 import_module 导入")
    test_bridge_lazy()
    print("✓ 桥接包懒加载，不 eager-import bootstrap")
    test_single_module_identity()
    print("✓ 干净子进程中两路径同一模块、单例唯一")
    test_star_import_stays_clean()
    print("✓ 星号导入命名空间干净")
    print("PASS: water_group module identity suite passed.")
