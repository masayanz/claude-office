"""PyInstaller entry point for the Portable Codex Adapter."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def _viewer_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parent


_ROOT = _viewer_root()
os.environ.setdefault("CLAUDE_OFFICE_ROOT", str(_ROOT))
os.environ.setdefault("AI_OFFICE_ROOT", str(_ROOT))

from claude_office_codex_adapter.main import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
