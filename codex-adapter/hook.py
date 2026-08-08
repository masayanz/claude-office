"""Repository-local launcher for Codex project hooks."""

from __future__ import annotations

import sys
from pathlib import Path


def _run() -> int:
    """Load the source-tree package without requiring an installation."""
    try:
        source_dir = Path(__file__).resolve().parent / "src"
        sys.path.insert(0, str(source_dir))
        from claude_office_codex_adapter.main import main

        return main()
    except Exception:
        # Import and bootstrap failures must not interrupt Codex.
        return 0


if __name__ == "__main__":
    raise SystemExit(_run())
