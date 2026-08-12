import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install-global-hooks.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
HOOK_EVENTS = (
    "SessionStart",
    "SessionEnd",
    "UserPromptSubmit",
    "PreToolUse",
    "PostToolUse",
    "SubagentStart",
    "SubagentStop",
    "Stop",
)


def _is_adapter_handler(handler: dict[str, object]) -> bool:
    return "claude-office-hook" in (
        f"{handler.get('command', '')} {handler.get('commandWindows', '')}"
    )


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="Windows PowerShell only")
def test_installer_preserves_other_groups_and_handlers(tmp_path: Path) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_text(
        json.dumps(
            {
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "other",
                            "hooks": [{"type": "command", "command": "keep-session"}],
                        },
                        {
                            "hooks": [
                                {"type": "command", "command": "keep-with-adapter"},
                                {
                                    "type": "command",
                                    "command": "python ~/.codex/claude-office-hook.py",
                                },
                            ]
                        },
                    ],
                    "PreToolUse": [
                        {
                            "matcher": "*",
                            "hooks": [{"type": "command", "command": "keep-tool"}],
                        }
                    ],
                    "CustomEvent": [{"hooks": [{"type": "command", "command": "keep-custom"}]}],
                }
            }
        ),
        encoding="utf-8",
    )
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)

    for _ in range(2):
        completed = subprocess.run(
            [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALLER)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr

    installed = json.loads(hooks_path.read_text(encoding="utf-8"))
    assert installed["hooks"]["CustomEvent"] == [
        {"hooks": [{"type": "command", "command": "keep-custom"}]}
    ]
    session_groups = installed["hooks"]["SessionStart"]
    assert any(group.get("matcher") == "other" for group in session_groups)
    assert any(
        handler.get("command") == "keep-session"
        for group in session_groups
        for handler in group["hooks"]
    )
    assert any(
        handler.get("command") == "keep-with-adapter"
        for group in session_groups
        for handler in group["hooks"]
    )
    for event_name in HOOK_EVENTS:
        handlers = [
            handler
            for group in installed["hooks"][event_name]
            for handler in group["hooks"]
        ]
        assert sum(_is_adapter_handler(handler) for handler in handlers) == 1
    assert any(
        handler.get("command") == "keep-tool"
        for group in installed["hooks"]["PreToolUse"]
        for handler in group["hooks"]
    )
