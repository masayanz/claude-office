[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$targets = @(
    "publish-release.ps1"
    "build-portable.ps1"
    "tools/emergency-stop.ps1"
)
$failureCount = 0

foreach ($relativePath in $targets) {
    $path = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        Write-Error "構文検査対象が見つかりません: $relativePath"
        $failureCount++
        continue
    }

    $tokens = $null
    $parseErrors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $path,
        [ref]$tokens,
        [ref]$parseErrors
    ) | Out-Null

    if ($parseErrors.Count -eq 0) {
        Write-Host "PASS: $relativePath"
        continue
    }

    $failureCount += $parseErrors.Count
    foreach ($parseError in $parseErrors) {
        Write-Host ("FAIL: {0}:{1}:{2} {3}" -f
            $relativePath,
            $parseError.Extent.StartLineNumber,
            $parseError.Extent.StartColumnNumber,
            $parseError.Message
        ) -ForegroundColor Red
    }
}

if ($failureCount -gt 0) {
    Write-Error "PowerShell構文エラー: $failureCount 件"
    exit 1
}

Write-Host "PowerShell構文検査に成功しました。" -ForegroundColor Green
exit 0
