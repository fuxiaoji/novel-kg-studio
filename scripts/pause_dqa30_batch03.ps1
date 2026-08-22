[CmdletBinding()]
param(
    [string]$PipelineRoot = "D:\desktop\coding\科研\novel-kg-studio\outputs\four_datasets\dqa30_attention\batch03",
    [switch]$InstallForNextMidnight
)

$ErrorActionPreference = "Stop"
$scriptPath = $MyInvocation.MyCommand.Path
$taskName = "NovelKG-Pause-DQA30-Batch03"

if ($InstallForNextMidnight) {
    $now = Get-Date
    $midnight = $now.Date.AddDays(1)
    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$scriptPath`""
    $trigger = New-ScheduledTaskTrigger -Once -At $midnight
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries

    Register-ScheduledTask `
        -TaskName $taskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Description "Save DQA30 batch03 progress and pause the graph-building pipeline before power-off." `
        -Force | Out-Null

    Write-Output "Scheduled '$taskName' for $($midnight.ToString('yyyy-MM-dd HH:mm:ss'))."
    exit 0
}

if (-not (Test-Path -LiteralPath $PipelineRoot)) {
    throw "Pipeline root does not exist: $PipelineRoot"
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$snapshotRoot = Join-Path $PipelineRoot "_pause_snapshots\$timestamp"
New-Item -ItemType Directory -Path $snapshotRoot -Force | Out-Null

# Chunk and graph caches are already written atomically by the pipeline.  Copy the
# small control files and logs so the exact stop point remains easy to audit.
$controlNames = @(
    "progress.json",
    "pipeline_progress.json",
    "build_progress.json",
    "status.json",
    "summary.json"
)
$copied = [System.Collections.Generic.List[string]]::new()
foreach ($name in $controlNames) {
    Get-ChildItem -LiteralPath $PipelineRoot -Recurse -File -Filter $name -ErrorAction SilentlyContinue |
        ForEach-Object {
            $relative = $_.FullName.Substring($PipelineRoot.Length).TrimStart('\')
            $safeName = $relative -replace '[\\/:*?"<>|]', '_'
            Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $snapshotRoot $safeName) -Force
            $copied.Add($_.FullName)
        }
}
Get-ChildItem -LiteralPath $PipelineRoot -File -ErrorAction SilentlyContinue |
    Where-Object { $_.Extension -in @(".log", ".txt") } |
    ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $snapshotRoot -Force
        $copied.Add($_.FullName)
    }

$selfPid = $PID
$targets = Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $selfPid -and
    $_.CommandLine -and
    $_.CommandLine -match 'dqa30_attention' -and
    $_.CommandLine -match 'batch03' -and
    $_.Name -match '^(python|pythonw|pwsh|powershell|cmd)\.exe$'
}

$manifest = [ordered]@{
    paused_at = (Get-Date).ToString("o")
    pipeline_root = $PipelineRoot
    reason = "Scheduled pause before power-off"
    copied_control_files = @($copied)
    target_processes = @($targets | ForEach-Object {
        [ordered]@{
            process_id = $_.ProcessId
            parent_process_id = $_.ParentProcessId
            name = $_.Name
            command_line = $_.CommandLine
        }
    })
}
$manifestPath = Join-Path $snapshotRoot "pause_manifest.json"
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $manifestPath -Encoding utf8
$manifest | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $PipelineRoot "PAUSED_BY_SCHEDULE.json") -Encoding utf8

# Stop only the batch03 orchestration process. Completed chunk outputs remain in
# their normal cache locations; at worst, the single in-flight model call reruns.
foreach ($target in ($targets | Sort-Object ProcessId -Descending)) {
    Stop-Process -Id $target.ProcessId -ErrorAction SilentlyContinue
}

$result = [ordered]@{
    ok = $true
    paused_at = (Get-Date).ToString("o")
    stopped_process_ids = @($targets.ProcessId)
    snapshot = $snapshotRoot
    resume_note = "Restart the existing batch03 pipeline; completed cached chunks will be reused."
}
$result | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath (Join-Path $snapshotRoot "pause_result.json") -Encoding utf8
$result | ConvertTo-Json -Depth 3
