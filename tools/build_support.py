"""Keep native builds independent of unrelated DLLs on the caller's PATH."""

import os
from pathlib import Path


def windows_build_path(python_base: Path, windows_root: Path) -> str:
    """Use Python and Windows directories; package hooks add their own DLL folders.

    Poppler's ICU uses versioned symbols, while Qt uses Windows ICU's unversioned
    symbols. Collecting Poppler's icuuc.dll via an inherited PATH breaks QtCore.
    """
    return os.pathsep.join(str(path) for path in
                           (python_base, python_base / "DLLs", windows_root / "System32", windows_root))
