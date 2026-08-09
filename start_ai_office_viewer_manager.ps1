<#[CmdletBinding()]#>
param()
$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
Set-Location $root
$exe = Join-Path $root "dist\AI-Office-Viewer-Manager.exe"
if (Test-Path -LiteralPath $exe) {
    Start-Process -FilePath $exe -WorkingDirectory $root
} else {
    py -3.13 -m manager.main
}
