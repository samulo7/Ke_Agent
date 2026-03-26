Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Fail {
    param([string]$Message)
    Write-Host "[FAIL] $Message" -ForegroundColor Red
    exit 1
}

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Set-Location $repoRoot

try {
    $isRepo = & git rev-parse --is-inside-work-tree 2>$null
    $gitExitCode = $LASTEXITCODE
} catch {
    $isRepo = ""
    $gitExitCode = 1
}

if ($gitExitCode -ne 0 -or $isRepo -ne "true") {
    Fail "Current directory is not a Git repository. Run this script inside a repo that contains this project."
}

$hookPath = Join-Path $repoRoot ".githooks\pre-commit"
if (-not (Test-Path $hookPath)) {
    Fail "Missing hook file: $hookPath"
}

& git config core.hooksPath .githooks
if ($LASTEXITCODE -ne 0) {
    Fail "Failed to set core.hooksPath to .githooks"
}

Write-Host "[PASS] Git hooks path configured: .githooks" -ForegroundColor Green
Write-Host "[PASS] pre-commit hook will run: infra/scripts/validate-memory-bank.ps1" -ForegroundColor Green
