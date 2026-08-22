param(
    [Parameter(Mandatory = $true)]
    [int]$PilotProcessId,
    [string]$ExperimentRoot = ""
)

$ErrorActionPreference = "Stop"
$projectRoot = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $ExperimentRoot) {
    $ExperimentRoot = Join-Path $projectRoot "outputs\four_datasets\dqa30_attention"
}
$experiment = [System.IO.Path]::GetFullPath($ExperimentRoot)
$graphRoot = Join-Path $experiment "batch03"
$statusPath = Join-Path $experiment "handoff_status.json"
$python = Join-Path $projectRoot ".venv_recovered\Scripts\python.exe"
$pipeline = Join-Path $PSScriptRoot "run_dqa30_pipeline.py"
$log = Join-Path $experiment "pipeline.log"
$err = Join-Path $experiment "pipeline.err"

function Write-Status([string]$state, [string]$detail) {
    @{
        updated = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
        state = $state
        detail = $detail
        pilot_pid = $PilotProcessId
    } | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding utf8
}

Write-Status "waiting_for_pilot" "novel 104 quality-gated pilot is running"
Wait-Process -Id $PilotProcessId -ErrorAction SilentlyContinue
$qualityPath = Join-Path $graphRoot "novels\104\quality_report.json"
$graphPath = Join-Path $graphRoot "novels\104\graph.json"
if (-not (Test-Path -LiteralPath $qualityPath) -or -not (Test-Path -LiteralPath $graphPath)) {
    Write-Status "pilot_failed" "pilot exited without a completed graph and quality report"
    exit 2
}
$quality = Get-Content -LiteralPath $qualityPath -Raw -Encoding utf8 | ConvertFrom-Json
if (-not $quality.passed) {
    Write-Status "pilot_rejected" ($quality.failures -join "; ")
    exit 3
}

Write-Status "pipeline_running" "pilot passed; building ten novels then evaluating eight paper methods"
$env:PYTHONUTF8 = "1"
& $python -u $pipeline --root $experiment 1> $log 2> $err
$code = $LASTEXITCODE
if ($code -ne 0) {
    Write-Status "pipeline_failed" "pipeline exit code $code"
    exit $code
}
Write-Status "complete" "ten-novel build and evaluation completed"
