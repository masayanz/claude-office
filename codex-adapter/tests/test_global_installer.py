import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER = Path(__file__).resolve().parents[1] / "install-global-hooks.ps1"
UNINSTALLER = Path(__file__).resolve().parents[1] / "uninstall-global-hooks.ps1"
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


def _adapter_handlers(installed: dict[str, object]) -> list[dict[str, object]]:
    return [
        handler
        for groups in installed["hooks"].values()
        for group in groups
        for handler in group["hooks"]
        if _is_adapter_handler(handler)
    ]


def _build_utf8_echo_executable(path: Path, powershell: str) -> None:
    """Build a native UTF-8 echo command for exercising the generated launcher."""
    source = r"""
using System;
using System.IO;
using System.Text;

public static class Utf8Echo
{
    public static void Main()
    {
        Console.InputEncoding = new UTF8Encoding(false, true);
        Console.OutputEncoding = new UTF8Encoding(false);
        var payload = Console.In.ReadToEnd();
        var outputPath = Environment.GetEnvironmentVariable("CLAUDE_OFFICE_TEST_STDIN");
        if (!String.IsNullOrEmpty(outputPath))
        {
            File.WriteAllText(outputPath, payload, new UTF8Encoding(false));
        }
        else
        {
            Console.Write(payload);
        }
    }
}
"""
    escaped_path = str(path).replace("'", "''")
    script = (
        "Add-Type -TypeDefinition @'\n"
        + source
        + "\n'@ -Language CSharp -OutputAssembly '"
        + escaped_path
        + "' -OutputType ConsoleApplication"
    )
    compiler = shutil.which("powershell") or powershell
    completed = subprocess.run(
        [compiler, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert completed.returncode == 0, completed.stderr
    assert path.is_file()


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
                "description": "既存の日本語説明",
                "hooks": {
                    "SessionStart": [
                        {
                            "matcher": "other",
                            "hooks": [{"type": "command", "command": "keep-日本語-session"}],
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
            },
            ensure_ascii=False,
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
    assert installed["description"] == "既存の日本語説明"
    assert installed["hooks"]["CustomEvent"] == [
        {"hooks": [{"type": "command", "command": "keep-custom"}]}
    ]
    session_groups = installed["hooks"]["SessionStart"]
    assert any(group.get("matcher") == "other" for group in session_groups)
    assert any(
        handler.get("command") == "keep-日本語-session"
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
    viewer_handlers = _adapter_handlers(installed)
    assert len(viewer_handlers) == len(HOOK_EVENTS)
    assert all(handler["timeout"] == 10 for handler in viewer_handlers)
    assert all("powershell" in str(handler["commandWindows"]).lower() for handler in viewer_handlers)
    assert any(
        handler.get("command") == "keep-tool"
        for group in installed["hooks"]["PreToolUse"]
        for handler in group["hooks"]
    )


@pytest.mark.skipif(sys.platform != "win32" or not POWERSHELLS, reason="Windows PowerShell only")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_portable_installer_records_relocated_root_and_adapter(
    tmp_path: Path, powershell: str
) -> None:
    root = tmp_path / "AI Office Viewer_日本語"
    installer = root / "runtime" / "codex-adapter" / "install-global-hooks.ps1"
    installer.parent.mkdir(parents=True)
    shutil.copy2(INSTALLER, installer)
    (root / "config").mkdir()
    (root / "config" / "app-settings.json").write_text("{}", encoding="utf-8")
    (root / "portable.flag").write_text("portable\n", encoding="utf-8")
    adapter = installer.parent / "AI-Office-Viewer-Codex-Adapter.exe"
    _build_utf8_echo_executable(adapter, powershell)
    codex_home = tmp_path / "codex-home"
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)
    environment["PATH"] = str(Path(powershell).parent)

    completed = subprocess.run(
        [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(installer)],
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
    hooks = json.loads((codex_home / "hooks.json").read_text(encoding="utf-8"))
    viewer_handlers = _adapter_handlers(hooks)
    assert len(viewer_handlers) == len(HOOK_EVENTS)
    assert all(handler["timeout"] == 10 for handler in viewer_handlers)
    shell_name = Path(powershell).name
    assert all(
        str(handler["commandWindows"]).lower().startswith(
            f"{shell_name.lower()} -noprofile"
        )
        for handler in viewer_handlers
    )
    decoded_commands = [
        base64.b64decode(str(handler["commandWindows"]).split()[-1]).decode("utf-16le")
        for handler in viewer_handlers
    ]
    assert all("claude-office-hook.ps1" in command for command in decoded_commands)
    launcher = (codex_home / "claude-office-hook.ps1").read_text(encoding="utf-8-sig")
    assert "[Console]::OpenStandardInput()" in launcher
    assert "$stdinReader.ReadToEnd()" in launcher
    assert "$OutputEncoding = New-Object System.Text.UTF8Encoding($false)" in launcher
    assert "[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)" in launcher

    payload = (
        '{"hook_event_name":"SessionStart","session_id":"stdin-forwarding",'
        '"prompt":"日本語の入力"}'
    )
    captured_stdin = tmp_path / "captured-stdin.json"
    environment["CLAUDE_OFFICE_TEST_STDIN"] = str(captured_stdin)
    hook_completed = subprocess.run(
        [os.environ["COMSPEC"], "/D", "/S", "/C", viewer_handlers[0]["commandWindows"]],
        input=payload,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        timeout=15,
    )
    assert hook_completed.returncode == 0, hook_completed.stderr
    assert json.loads(captured_stdin.read_text(encoding="utf-8")) == json.loads(payload)


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


@pytest.mark.skipif(sys.platform != "win32" or not POWERSHELLS, reason="Windows PowerShell only")
@pytest.mark.parametrize("powershell", POWERSHELLS)
def test_uninstaller_removes_viewer_hooks_without_bom_or_data_loss(
    tmp_path: Path, powershell: str
) -> None:
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    hooks_path = codex_home / "hooks.json"
    hooks_path.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {
                "description": "解除後も保持する日本語",
                "hooks": {
                    "SessionStart": [
                        {
                            "hooks": [
                                {"type": "command", "command": "keep-日本語-session"},
                                {
                                    "type": "command",
                                    "command": "python ~/.codex/claude-office-hook.py",
                                },
                            ]
                        }
                    ],
                    "CustomEvent": [
                        {"hooks": [{"type": "command", "command": "keep-custom"}]}
                    ],
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")
    )
    environment = os.environ.copy()
    environment["CODEX_HOME"] = str(codex_home)

    for attempt in range(2):
        completed = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(UNINSTALLER)],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
        )
        assert completed.returncode == 0, (
            f"attempt={attempt}: stdout={completed.stdout!r} stderr={completed.stderr!r}"
        )
        raw = hooks_path.read_bytes()
        assert not raw.startswith(b"\xef\xbb\xbf")
        assert raw, f"attempt={attempt}: stdout={completed.stdout!r} stderr={completed.stderr!r}"
        installed = json.loads(raw.decode("utf-8"))
        assert not any(
            _is_adapter_handler(handler)
            for groups in installed["hooks"].values()
            for group in groups
            for handler in group["hooks"]
        )

    assert installed["hooks"]["CustomEvent"] == [
        {"hooks": [{"type": "command", "command": "keep-custom"}]}
    ]
    assert installed["hooks"]["SessionStart"] == [
        {"hooks": [{"type": "command", "command": "keep-日本語-session"}]}
    ]
    assert installed["description"] == "解除後も保持する日本語"
