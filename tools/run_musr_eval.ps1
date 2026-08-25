$root = $PSScriptRoot
$env:PYTHONUTF8 = '1'
if (-not $env:DEEPSEEK_API_KEY) { throw 'Set DEEPSEEK_API_KEY before running this script.' }
$py = Join-Path $root '.venv\Scripts\python.exe'
$script = Join-Path $root 'scripts\eval_musr.py'
$log = Join-Path $root 'musr_eval.log'
& $py -u $script --sample 6 --model deepseek-v4-flash --build-model deepseek-chat --workers 4 --case-workers 4 *> $log
