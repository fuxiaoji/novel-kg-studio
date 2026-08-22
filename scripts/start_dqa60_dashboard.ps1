$root = Resolve-Path (Join-Path $PSScriptRoot '..')
$exp = Join-Path $root 'outputs\four_datasets\dqa60_single9'
$web = Join-Path $root 'outputs\four_datasets'

$monitor = Start-Process `
    -FilePath 'python' `
    -ArgumentList (Join-Path $PSScriptRoot 'monitor_dqa60.py') `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $exp 'monitor.log') `
    -RedirectStandardError (Join-Path $exp 'monitor.err') `
    -PassThru
$monitor.Id | Set-Content -Encoding ascii (Join-Path $exp 'monitor.pid')

$server = Start-Process `
    -FilePath 'python' `
    -ArgumentList @('-m', 'http.server', '8765', '--bind', '127.0.0.1', '--directory', $web) `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $exp 'http.log') `
    -RedirectStandardError (Join-Path $exp 'http.err') `
    -PassThru
$server.Id | Set-Content -Encoding ascii (Join-Path $exp 'http.pid')
