import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install-global-hooks.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
POWERSHELLS = tuple(
    dict.fromkeys(
        shell for shell in (shutil.which("powershell"), shutil.which("pwsh")) if shell
    )
)
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


@pytest.mark.skipif(sys.platform != "win32" or not POWERSHELLS, reason="Windows PowerShell only")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_installer_preserves_other_groups_and_handlers(
    tmp_path: Path, powershell: str
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
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
        ).encode("utf-8"),
    )
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)

    for _ in range(2):
        completed = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(INSTALLER)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert completed.returncode == 0, completed.stderr

    for json_path in (hooks_path, codex_home / "claude-office-config.json"):
        raw = json_path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        json.loads(raw.decode("utf-8"))

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


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="Windows PowerShell only")
def test_portable_installer_records_relocated_root_and_adapter(tmp_path: Path) -> None:
    root = tmp_path / "AI Office Viewer_日本語"
    installer = root / "runtime" / "codex-adapter" / "install-global-hooks.ps1"
    installer.parent.mkdir(parents=True)
    shutil.copy2(INSTALLER, installer)
    (root / "config").mkdir()
    (root / "config" / "app-settings.json").write_text("{}", encoding="utf-8")
    (root / "portable.flag").write_text("portable\n", encoding="utf-8")
    adapter = installer.parent / "AI-Office-Viewer-Codex-Adapter.exe"
    adapter.write_bytes(b"")
    codex_home = tmp_path / "codex-home"
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)

    completed = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    config = json.loads((codex_home / "claude-office-config.json").read_text(encoding="utf-8"))
    assert Path(config["root"]).resolve() == root.resolve()
    assert Path(config["adapter"]).resolve() == adapter.resolve()


@pytest.mark.skipif(sys.platform != "win32" or POWERSHELL is None, reason="Windows PowerShell only")
def test_portable_installer_fails_when_current_mode_adapter_is_missing(tmp_path: Path) -> None:
    root = tmp_path / "portable"
    installer = root / "runtime" / "codex-adapter" / "install-global-hooks.ps1"
    installer.parent.mkdir(parents=True)
    shutil.copy2(INSTALLER, installer)
    (root / "config").mkdir()
    (root / "config" / "app-settings.json").write_text("{}", encoding="utf-8")
    (root / "portable.flag").write_text("portable\n", encoding="utf-8")
    # A source adapter must not mask a broken Portable kit.
    source = root / "codex-adapter"
    source.mkdir()
    (source / "hook.py").write_text("# source", encoding="utf-8")
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(tmp_path / "codex-home")

    completed = subprocess.run(
        [str(POWERSHELL), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )

    assert completed.returncode != 0
    assert "Portable Codex Adapter" in f"{completed.stdout}\n{completed.stderr}"
