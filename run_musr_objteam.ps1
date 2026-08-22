$root = $PSScriptRoot
$env:PYTHONUTF8 = '1'
$py = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'scripts\eval_musr_local.py'
$log = Join-Path $root 'musr_local.log'
& $py -u $script --skip-build --domains object_placements team_allocation --build-workers 4 --answer-workers 10 *> $log
