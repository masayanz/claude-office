<#
Remove only AI Office Viewer's Codex handlers from the current user's global layer.
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

function Write-Utf8NoBomAtomic([string]$targetFile, [string]$jsonText) {
    <# Windows PowerShell 5.1's Set-Content -Encoding UTF8 writes a BOM,
       which Codex does not accept for hooks.json. #>
    if ([string]::IsNullOrWhiteSpace($targetFile)) {
        throw "hooks.jsonの保存先が空です。"
    }
    $directory = [System.IO.Path]::GetDirectoryName($targetFile)
    $temporary = [System.IO.Path]::Combine(
        $directory,
        [System.IO.Path]::GetFileName($targetFile) + ".tmp-" + [guid]::NewGuid().ToString("N")
    )
    $backup = "$temporary.backup"
    $encoding = New-Object System.Text.UTF8Encoding($false)
    try {
        [System.IO.File]::WriteAllText($temporary, $jsonText, $encoding)
        if ([System.IO.File]::Exists($targetFile)) {
            [System.IO.File]::Replace($temporary, $targetFile, $backup, $true)
        } else {
            [System.IO.File]::Move($temporary, $targetFile)
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

function Read-Utf8Text([string]$targetFile) {
    $encoding = New-Object System.Text.UTF8Encoding($false, $true)
    return [System.IO.File]::ReadAllText($targetFile, $encoding)
}

if (-not (Test-Path -LiteralPath (Join-Path (Get-CodexHome) "hooks.json"))) {
    Write-Host "AI Office Viewerのglobal hooksは設定されていません。"
    exit 0
}

$script:codexHomePath = Get-CodexHome
$script:hooksJsonPath = [System.IO.Path]::Combine($script:codexHomePath, "hooks.json")
$rawHooks = Read-Utf8Text $script:hooksJsonPath
$rawHooks = $rawHooks.TrimStart([char[]]@([char]0xFEFF))
$document = ConvertFrom-Json -InputObject $rawHooks
if ($null -eq $document.hooks) {
    Write-Host "AI Office Viewerのglobal hooksは設定されていません。"
    exit 0
}
$hooks = $document.hooks

foreach ($eventName in @(
    "SessionStart", "SessionEnd", "UserPromptSubmit", "PreToolUse",
    "PostToolUse", "SubagentStart", "SubagentStop", "Stop"
)) {
    $property = $hooks.PSObject.Properties | Where-Object { $_.Name -eq $eventName } | Select-Object -First 1
    if ($null -eq $property) {
        continue
    }
    $groups = @($property.Value)
    $keptGroups = @()
    foreach ($group in $groups) {
        $keptHandlers = @($group.hooks | Where-Object { -not (Test-ClaudeOfficeHandler $_) })
        if ($keptHandlers.Count -gt 0) {
            $group.hooks = $keptHandlers
            $keptGroups += $group
        }
    }
    $property.Value = @($keptGroups)
}

$hooksJson = ConvertTo-Json -InputObject $document -Depth 20
Write-Utf8NoBomAtomic -targetFile $script:hooksJsonPath -jsonText $hooksJson
Write-Host "AI Office Viewerのglobal hooksだけを解除しました。既存の他hookは保持しています。"
Write-Host "adapter本体とlauncherは削除していません。"
