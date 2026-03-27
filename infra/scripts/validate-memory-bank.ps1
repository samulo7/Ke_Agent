param(
    [string]$ExpectedStep = "",
    [string]$ExpectedDate = "",
    [switch]$RequireArchitectureDate
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$memoryBank = Join-Path $repoRoot "memory-bank"
$progressPath = Join-Path $memoryBank "progress.md"
$architecturePath = Join-Path $memoryBank "architecture.md"
$planPath = Join-Path $memoryBank "IMPLEMENTATION_PLAN.md"

$errors = New-Object System.Collections.Generic.List[string]

function Assert-FileExists {
    param(
        [string]$Path,
        [string]$Label
    )
    if (-not (Test-Path -Path $Path)) {
        $errors.Add("Missing required file: $Label ($Path)")
    }
}

Assert-FileExists -Path $progressPath -Label "progress.md"
Assert-FileExists -Path $architecturePath -Label "architecture.md"
Assert-FileExists -Path $planPath -Label "IMPLEMENTATION_PLAN.md"

$prdCandidates = @(Get-ChildItem -Path $memoryBank -File -Filter "PRD-*Agent-MVP*.md" -ErrorAction SilentlyContinue)
if (-not $prdCandidates -or $prdCandidates.Count -eq 0) {
    $errors.Add("Missing required PRD file matching pattern: memory-bank/PRD-*Agent-MVP*.md")
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Host "[FAIL] $_" -ForegroundColor Red }
    exit 1
}

$progress = Get-Content -Path $progressPath -Raw -Encoding UTF8
$architecture = Get-Content -Path $architecturePath -Raw -Encoding UTF8

if ([string]::IsNullOrWhiteSpace($progress)) {
    $errors.Add("progress.md is empty.")
}
if ([string]::IsNullOrWhiteSpace($architecture)) {
    $errors.Add("architecture.md is empty.")
}

if ($progress -notmatch "(?m)^##\s+Progress Log\s*$") {
    $errors.Add("progress.md must contain heading: '## Progress Log'.")
}

$progressRowPattern = "(?m)^\|\s*\d{4}-\d{2}-\d{2}\s*\|\s*[^|]+\|\s*(DONE|IN_PROGRESS|BLOCKED)\s*\|\s*[^|]+\|\s*[^|]+\|\s*$"
if ($progress -notmatch $progressRowPattern) {
    $errors.Add("progress.md must include at least one valid table row: Date | Step ID | Status | Verification | Notes.")
}
else {
    $progressRows = @(
        ($progress -split "`r?`n") |
        Where-Object { $_ -match "^\|\s*\d{4}-\d{2}-\d{2}\s*\|" }
    )

    if ($progressRows.Count -gt 0) {
        $rowPattern = "^\|\s*(?<date>\d{4}-\d{2}-\d{2})\s*\|\s*(?<step>[^|]+?)\s*\|\s*(?<status>DONE|IN_PROGRESS|BLOCKED)\s*\|\s*(?<verification>[^|]+?)\s*\|\s*(?<notes>[^|]+?)\s*\|\s*$"
        $parsedRows = @()

        foreach ($row in $progressRows) {
            if ($row -match $rowPattern) {
                $parsedRows += [PSCustomObject]@{
                    Date = $Matches["date"].Trim()
                    Step = $Matches["step"].Trim()
                    Status = $Matches["status"].Trim()
                    Verification = $Matches["verification"].Trim()
                    Notes = $Matches["notes"].Trim()
                    Raw = $row
                }
            }
            else {
                $errors.Add("Unable to parse progress row: $row")
            }
        }

        if ($parsedRows.Count -gt 0) {
            $latestRow = $parsedRows[-1]
            if ($latestRow.Notes -notmatch "(?i)\bSkills\s*:") {
                $errors.Add("The latest progress row must include a 'Skills:' segment in Notes for workflow traceability.")
            }

            $firstGovSkill02DoneIndex = -1
            $firstB17OrHigherIndex = -1
            $b17OrHigherPattern = "^B-(17|18|19|20)(\b|$)"

            for ($i = 0; $i -lt $parsedRows.Count; $i++) {
                $row = $parsedRows[$i]

                if ($firstGovSkill02DoneIndex -lt 0 -and $row.Step -eq "GOV-SKILL-02" -and $row.Status -eq "DONE") {
                    $firstGovSkill02DoneIndex = $i
                }
                if ($firstB17OrHigherIndex -lt 0 -and $row.Step -match $b17OrHigherPattern) {
                    $firstB17OrHigherIndex = $i
                }
            }

            if ($firstB17OrHigherIndex -ge 0 -and ($firstGovSkill02DoneIndex -lt 0 -or $firstGovSkill02DoneIndex -gt $firstB17OrHigherIndex)) {
                $bStep = $parsedRows[$firstB17OrHigherIndex].Step
                $bDate = $parsedRows[$firstB17OrHigherIndex].Date
                $errors.Add("Step '$bStep' ($bDate) requires 'GOV-SKILL-02' with status DONE to appear earlier in progress.md.")
            }

            if ($firstGovSkill02DoneIndex -ge 0) {
                for ($i = $firstGovSkill02DoneIndex + 1; $i -lt $parsedRows.Count; $i++) {
                    $row = $parsedRows[$i]
                    if ($row.Notes -notmatch "(?i)\bSkills\s*:") {
                        $errors.Add("Row '$($row.Date) | $($row.Step)' is after GOV-SKILL-02 and must include a 'Skills:' segment in Notes.")
                    }
                }
            }
        }
        else {
            $errors.Add("Unable to parse progress rows for Skills traceability checks.")
        }
    }
}

if ($architecture -notmatch "(?m)^##\s+File Responsibilities\s*$") {
    $errors.Add("architecture.md must contain heading: '## File Responsibilities'.")
}

$archRowPattern = "(?m)^\|\s*[^|]+\s*\|\s*[^|]+\s*\|\s*$"
if ($architecture -notmatch $archRowPattern) {
    $errors.Add("architecture.md must include at least one responsibilities table row.")
}

if (-not [string]::IsNullOrWhiteSpace($ExpectedStep)) {
    $escapedStep = [regex]::Escape($ExpectedStep)
    if ($progress -notmatch $escapedStep) {
        $errors.Add("Expected step '$ExpectedStep' was not found in progress.md.")
    }
}

if ([string]::IsNullOrWhiteSpace($ExpectedDate)) {
    $ExpectedDate = Get-Date -Format "yyyy-MM-dd"
}

$escapedDate = [regex]::Escape($ExpectedDate)
if ($progress -notmatch $escapedDate) {
    $errors.Add("Expected date '$ExpectedDate' was not found in progress.md.")
}

if ($RequireArchitectureDate.IsPresent -and ($architecture -notmatch $escapedDate)) {
    $errors.Add("Expected date '$ExpectedDate' was not found in architecture.md.")
}

if ($errors.Count -gt 0) {
    $errors | ForEach-Object { Write-Host "[FAIL] $_" -ForegroundColor Red }
    exit 1
}

Write-Host "[PASS] memory-bank validation succeeded." -ForegroundColor Green
Write-Host "[PASS] Checked files: progress.md, architecture.md, PRD, IMPLEMENTATION_PLAN."
Write-Host "[PASS] Matched date: $ExpectedDate"
if (-not [string]::IsNullOrWhiteSpace($ExpectedStep)) {
    Write-Host "[PASS] Matched expected step: $ExpectedStep"
}
