<#
Start AI Office Viewer backend, frontend, readiness checks, and browser.
Internal CLAUDE_OFFICE_* environment variables remain for compatibility.
#>

[CmdletBinding()]
param()

& (Join-Path $PSScriptRoot "start_claude_office.ps1")
exit $LASTEXITCODE
