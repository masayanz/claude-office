"""Shared asset resolution for source and PyInstaller onefile execution."""

from __future__ import annotations

import sys
from pathlib import Path

# Kept stable so existing source distributions and references remain compatible.
ICON_FILENAME = "claude-office-manager.ico"


def resource_path(relative_path: Path) -> Path:
    """Resolve a bundled asset through _MEIPASS or the source repository."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        base = Path(sys._MEIPASS)
    else:
        base = Path(__file__).resolve().parents[1]
    return base / relative_path


def manager_icon_path() -> Path:
    return resource_path(Path("manager") / "assets" / ICON_FILENAME)
