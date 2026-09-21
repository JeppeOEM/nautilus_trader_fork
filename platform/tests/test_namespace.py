"""
The `platform/` directory guard (DDD spine AD-D13).

`platform` is also a Python standard-library module. A directory without `__init__.py` is only a
namespace *portion*, which never shadows a regular module found later on `sys.path`; a
`platform/__init__.py` would turn it into a regular package and break `nautilus_trader`'s own
`import platform`. Both halves are asserted here, with the repo root first on `sys.path` exactly as
a script run from the repo root would see it.
"""

import subprocess
import sys
from pathlib import Path


PLATFORM_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PLATFORM_DIR.parent


def test_platform_dir_is_a_namespace_directory() -> None:
    assert PLATFORM_DIR.name == "platform"
    assert not (PLATFORM_DIR / "__init__.py").exists()


def test_stdlib_platform_wins_with_repo_root_on_sys_path() -> None:
    code = (
        "import importlib.util, sys; sys.path.insert(0, sys.argv[1]); "
        "print(importlib.util.find_spec('platform').origin)"
    )
    origin = subprocess.run(
        [sys.executable, "-c", code, str(REPO_ROOT)],
        check=True,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    ).stdout.strip()
    assert origin.endswith("platform.py"), origin
    assert not origin.startswith(str(REPO_ROOT)), origin
