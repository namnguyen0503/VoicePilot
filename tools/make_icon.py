#!/usr/bin/env python3
"""Render an SVG icon to a 256 x 256 Windows .ico file for the PyInstaller build.

Usage: python tools/make_icon.py <input.svg> <output.ico>

Uses PySide6 (QtSvg renders the SVG, QImage writes the ICO). Called by tools/build.ps1.
Exit codes: 0 written, 1 render or write failure, 2 usage error.
"""

from __future__ import annotations

import sys
from pathlib import Path

ICON_SIZE = 256


def render_icon(svg_path: Path, ico_path: Path) -> bool:
    """Render svg_path to ico_path. Returns False when the image could not be written."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        return False
    image = QImage(ICON_SIZE, ICON_SIZE, QImage.Format.Format_ARGB32)
    image.fill(QColor(0, 0, 0, 0))
    painter = QPainter(image)
    renderer.render(painter, QRectF(0, 0, ICON_SIZE, ICON_SIZE))
    painter.end()
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    return image.save(str(ico_path), "ICO")


def main(argv: list[str]) -> int:
    """Command-line entry point."""
    if len(argv) != 3:
        print("usage: make_icon.py <input.svg> <output.ico>", file=sys.stderr)
        return 2
    svg_path, ico_path = Path(argv[1]), Path(argv[2])
    if not svg_path.is_file():
        print(f"error: SVG not found: {svg_path}", file=sys.stderr)
        return 1
    if not render_icon(svg_path, ico_path):
        print("error: could not render or write the icon (is QtSvg available?)", file=sys.stderr)
        return 1
    print(f"icon written: {ico_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
