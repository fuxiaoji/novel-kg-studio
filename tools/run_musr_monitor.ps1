$root = $PSScriptRoot
$env:PYTHONUTF8 = '1'
$py = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'scripts\monitor_musr_local.py'
$log = Join-Path $root 'musr_monitor.log'
& $py -u $script *> $log
