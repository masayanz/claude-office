<#[CmdletBinding()]#>
param()
$ErrorActionPreference = "Stop"
$root = (Resolve-Path $PSScriptRoot).Path
Set-Location $root
py -3.13 -m manager.main
