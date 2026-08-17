<#
Install AI Office Viewer's Codex hooks in the current user's global Codex layer.

The script only edits ~/.codex/hooks.json. Existing hook groups and handlers are
preserved; only handlers previously installed by this integration are replaced.
#>

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"

function Get-CodexHome {
    if ($env:CODEX_HOME) {
        return [Environment]::ExpandEnvironmentVariables($env:CODEX_HOME)
    }
    return Join-Path ([Environment]::GetFolderPath("UserProfile")) ".codex"
}

function Test-ClaudeOfficeHandler([object]$handler) {
    $command = [string]$handler.command
    $windowsCommand = [string]$handler.commandWindows
    return $command -match "claude-office-hook" -or $windowsCommand -match "claude-office-hook"
}

function Remove-ClaudeOfficeHandlers([object]$hooks) {
    foreach ($eventName in @(
        "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
        "PostToolUse", "SubagentStart", "SubagentStop", "Stop"
    )) {
        $property = @($hooks.PSObject.Properties | Where-Object { $_.Name -eq $eventName })[0]
        $groups = if ($null -eq $property) { @() } else { @($property.Value) }
        if ($groups.Count -eq 0) {
            continue
        }

        $keptGroups = @()
        foreach ($group in $groups) {
            $keptHandlers = @($group.hooks | Where-Object { -not (Test-ClaudeOfficeHandler $_) })
            if ($keptHandlers.Count -gt 0) {
                $group.hooks = $keptHandlers
                $keptGroups += $group
            }
        }
        $hooks | Add-Member -MemberType NoteProperty -Name $eventName -Value $keptGroups -Force
    }
}

function New-ClaudeOfficeHandler([string]$launcherPath, [string]$shellPath) {
    return [pscustomobject]@{
        type = "command"
        command = "python3 ~/.codex/claude-office-hook.py"
        commandWindows = "`"$shellPath`" -NoProfile -ExecutionPolicy Bypass -File `"$launcherPath`""
        timeout = 2
    }
}

function New-ClaudeOfficeGroup([object]$handler, [string]$matcher) {
    $group = [pscustomobject]@{ hooks = @($handler) }
    if ($matcher) {
        Add-Member -InputObject $group -MemberType NoteProperty -Name matcher -Value $matcher
    }
    return $group
}

function Write-Utf8NoBomAtomic([string]$path, [string]$content) {
    <# Windows PowerShell 5.1's Set-Content -Encoding UTF8 writes a BOM,
       which Codex does not accept for hooks.json. #>
    $directory = Split-Path -Parent $path
    $temporary = Join-Path $directory ((Split-Path -Leaf $path) + ".tmp-" + [guid]::NewGuid().ToString("N"))
    $backup = "$temporary.backup"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temporary, $content, $encoding)
        if (Test-Path -LiteralPath $path) {
            [System.IO.File]::Replace($temporary, $path, $backup, $true)
        } else {
            [System.IO.File]::Move($temporary, $path)
        }
    } finally {
        if (Test-Path -LiteralPath $temporary) {
            Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        }
        if (Test-Path -LiteralPath $backup) {
            Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
        }
    }
}

function Find-ViewerRoot {
    $candidate = (Resolve-Path $PSScriptRoot).Path
    while ($candidate) {
        if ((Test-Path -LiteralPath (Join-Path $candidate "config\app-settings.json")) -and
            ((Test-Path -LiteralPath (Join-Path $candidate "portable.flag")) -or
             (Test-Path -LiteralPath (Join-Path $candidate "codex-adapter\hook.py")))) {
            return $candidate
        }
        $parent = Split-Path -Parent $candidate
        if (-not $parent -or $parent -eq $candidate) { break }
        $candidate = $parent
    }
    throw "AI Office Viewerの配布ルートを特定できません。"
}

$repoRoot = Find-ViewerRoot
$codexHome = Get-CodexHome
$hooksPath = Join-Path $codexHome "hooks.json"
$configPath = Join-Path $codexHome "claude-office-config.json"
$launcherPath = Join-Path $codexHome "claude-office-hook.ps1"
$pythonLauncherPath = Join-Path $codexHome "claude-office-hook.py"
$portableAdapterPath = Join-Path $repoRoot "runtime\codex-adapter\AI-Office-Viewer-Codex-Adapter.exe"
$sourceAdapterPath = Join-Path $repoRoot "codex-adapter\hook.py"
$portableMode = Test-Path -LiteralPath (Join-Path $repoRoot "portable.flag")
$adapterPath = if ($portableMode) { $portableAdapterPath } else { $sourceAdapterPath }
if (-not (Test-Path -LiteralPath $adapterPath)) {
    $modeName = if ($portableMode) { "Portable" } else { "開発版" }
    throw "$modeName Codex Adapterが見つかりません: $adapterPath"
}
$backupDir = Join-Path $codexHome "backups"
$shellCommand = (Get-Command powershell.exe -ErrorAction SilentlyContinue).Source
if (-not $shellCommand) {
    $shellCommand = (Get-Command pwsh.exe -ErrorAction SilentlyContinue).Source
}
if (-not $shellCommand) {
    throw "PowerShell executable was not found."
}

New-Item -ItemType Directory -Path $codexHome -Force | Out-Null
New-Item -ItemType Directory -Path $backupDir -Force | Out-Null

if (Test-Path -LiteralPath $hooksPath) {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backupPath = Join-Path $backupDir "hooks-$stamp.json"
    Copy-Item -LiteralPath $hooksPath -Destination $backupPath -Force
    Get-ChildItem -LiteralPath $backupDir -Filter "hooks-*.json" -File |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip 5 |
        Remove-Item -Force
    try {
        $config = Get-Content -LiteralPath $hooksPath -Raw | ConvertFrom-Json
    } catch {
        throw "hooks.json is not valid JSON. Review the backup at $backupPath before repairing."
    }
} else {
    $config = [pscustomobject]@{}
}

$hooksProperty = @($config.PSObject.Properties | Where-Object { $_.Name -eq "hooks" })[0]
if ($null -eq $hooksProperty) {
    Add-Member -InputObject $config -MemberType NoteProperty -Name hooks -Value ([pscustomobject]@{})
}

Remove-ClaudeOfficeHandlers $config.hooks

$hookEvents = @(
    @{ Name = "SessionStart"; Matcher = "" },
    @{ Name = "SessionEnd"; Matcher = "" },
    @{ Name = "UserPromptSubmit"; Matcher = "" },
    @{ Name = "PreToolUse"; Matcher = "*" },
    @{ Name = "PostToolUse"; Matcher = "*" },
    @{ Name = "SubagentStart"; Matcher = "*" },
    @{ Name = "SubagentStop"; Matcher = "*" },
    @{ Name = "Stop"; Matcher = "" }
)
$handler = New-ClaudeOfficeHandler $launcherPath $shellCommand
foreach ($event in $hookEvents) {
    $group = New-ClaudeOfficeGroup $handler $event.Matcher
    # Remove-ClaudeOfficeHandlers leaves unrelated groups/handlers intact.
    # Append our fresh group instead of replacing the event array, because
    # Codex permits more than one handler group for the same lifecycle event.
    $property = @($config.hooks.PSObject.Properties | Where-Object { $_.Name -eq $event.Name })[0]
    $existingGroups = if ($null -eq $property) { @() } else { @($property.Value) }
    $mergedGroups = @($existingGroups)
    $mergedGroups += $group
    $config.hooks | Add-Member -MemberType NoteProperty -Name $event.Name -Value $mergedGroups -Force
}

$hooksJson = $config | ConvertTo-Json -Depth 20
Write-Utf8NoBomAtomic $hooksPath $hooksJson

$launcher = @'
<# AI Office Viewer global Codex hook launcher. Generated by install-global-hooks.ps1. #>
$ErrorActionPreference = "SilentlyContinue"
try {
    $configPath = Join-Path $PSScriptRoot "claude-office-config.json"
    $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
    $root = [string]$config.root
    if (-not $root) { exit 0 }
    $adapterPath = [string]$config.adapter
    if (-not $adapterPath -or -not (Test-Path -LiteralPath $adapterPath)) { exit 0 }
    $env:CLAUDE_OFFICE_ROOT = $root
    if ($adapterPath.ToLowerInvariant().EndsWith(".exe")) {
        & $adapterPath
    } else {
        $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($pyLauncher) {
            & $pyLauncher.Source -3.13 $adapterPath
        } else {
            $pythonLauncher = (Get-Command python.exe -ErrorAction SilentlyContinue).Source
            if (-not $pythonLauncher) {
                $pythonLauncher = (Get-Command python3.exe -ErrorAction SilentlyContinue).Source
            }
            if ($pythonLauncher) { & $pythonLauncher $adapterPath }
        }
    }
} catch {
    # AI Office Viewer is an optional observer. Never interrupt Codex.
}
exit 0
'@
$launcher | Set-Content -LiteralPath $launcherPath -Encoding UTF8

$pythonLauncher = @'
"""Fallback launcher for non-Windows Codex hook command resolution."""
from pathlib import Path
import json
import subprocess
import sys

try:
    codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    config = json.loads((codex_home / "claude-office-config.json").read_text())
    root = str(config["root"])
    hook = Path(config["adapter"])
    if not root or not hook.is_file():
        raise RuntimeError("AI Office Viewer adapter is unavailable")
    os.environ["CLAUDE_OFFICE_ROOT"] = root
    subprocess.run([sys.executable, str(hook)], check=False)
except Exception:
    pass
'@
$pythonLauncher | Set-Content -LiteralPath $pythonLauncherPath -Encoding UTF8

$configData = [pscustomobject]@{
    root = $repoRoot
    adapter = $adapterPath
    installedAt = (Get-Date).ToUniversalTime().ToString("o")
}
$configJson = $configData | ConvertTo-Json
Write-Utf8NoBomAtomic $configPath $configJson

Write-Host "AI Office Viewer Codex global hooks installed."
Write-Host "Saved to: $hooksPath"
Write-Host "adapter root: $repoRoot"
Write-Host "Backup directory: $backupDir"
Write-Host "The hooks apply to new Codex sessions."
