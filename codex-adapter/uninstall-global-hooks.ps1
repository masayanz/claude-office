<#
Remove only Claude Office's Codex handlers from the current user's global layer.
Existing hooks, the adapter source, and unrelated settings remain untouched.
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

if (-not (Test-Path -LiteralPath (Join-Path (Get-CodexHome) "hooks.json"))) {
    Write-Host "Claude Officeのglobal hooksは設定されていません。"
    exit 0
}

$codexHome = Get-CodexHome
$hooksPath = Join-Path $codexHome "hooks.json"
$config = Get-Content -LiteralPath $hooksPath -Raw | ConvertFrom-Json
if (-not $config.PSObject.Properties["hooks"]) {
    Write-Host "Claude Officeのglobal hooksは設定されていません。"
    exit 0
}

foreach ($eventName in @(
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
    "PostToolUse", "SubagentStart", "SubagentStop", "Stop"
)) {
    $property = $config.hooks.PSObject.Properties[$eventName]
    $groups = if ($null -eq $property) { @() } else { @($property.Value) }
    $keptGroups = @()
    foreach ($group in $groups) {
        $keptHandlers = @($group.hooks | Where-Object { -not (Test-ClaudeOfficeHandler $_) })
        if ($keptHandlers.Count -gt 0) {
            $group.hooks = $keptHandlers
            $keptGroups += $group
        }
    }
    $config.hooks | Add-Member -MemberType NoteProperty -Name $eventName -Value $keptGroups -Force
}

$config | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $hooksPath -Encoding UTF8
Write-Host "Claude Officeのglobal hooksだけを解除しました。既存の他hookは保持しています。"
Write-Host "adapter本体とlauncherは削除していません。"
