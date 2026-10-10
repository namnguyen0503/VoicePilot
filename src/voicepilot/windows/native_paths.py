"""Path aliases for native Windows libraries that still use narrow file APIs."""

from pathlib import Path


def short_windows_path(path: Path) -> Path:
    """Get the 8.3 alias when the volume provides one; keep Windows imports lazy."""
    import ctypes

    api = ctypes.WinDLL("kernel32", use_last_error=True).GetShortPathNameW
    api.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint]
    api.restype = ctypes.c_uint
    size = api(str(path), None, 0)
    if not size:
        raise ctypes.WinError(ctypes.get_last_error())
    buffer = ctypes.create_unicode_buffer(size)
    written = api(str(path), buffer, size)
    if not written or written >= size:
        raise OSError("Cannot resolve the short data path")
    return Path(buffer.value)
