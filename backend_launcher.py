"""PyInstaller entry point for the Portable AI Office Viewer Backend."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _viewer_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parents[2]
    return Path(__file__).resolve().parent


def main() -> int:
    root = _viewer_root()
    data_dir = root / "data"
    static_dir = root / "runtime" / "frontend"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("AI_OFFICE_PORTABLE", "1")
    os.environ.setdefault("AI_OFFICE_ROOT", str(root))
    os.environ.setdefault("CLAUDE_OFFICE_ROOT", str(root))
    os.environ.setdefault("AI_OFFICE_STATIC_DIR", str(static_dir))
    os.environ.setdefault("AI_OFFICE_DATABASE_PATH", str(data_dir / "visualizer.db"))
    os.environ.setdefault("SERVE_STATIC", "1")

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    args, _unknown = parser.parse_known_args()

    import uvicorn

    from app.main import app

    uvicorn.run(app, host=args.host, port=args.port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
