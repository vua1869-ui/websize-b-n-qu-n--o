# Package clean release zip for AURA STUDIO on Windows
param(
    [string]$OutputFile = ""
)

$ErrorActionPreference = "Stop"
$RootDir = Split-Path -Parent $PSScriptRoot
Set-Location $RootDir

if ($OutputFile) {
    python scripts/package_release.py --output $OutputFile
} else {
    python scripts/package_release.py
}
