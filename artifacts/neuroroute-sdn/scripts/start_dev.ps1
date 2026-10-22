$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)

foreach ($Port in @(8000, 5173)) {
    $ProcessIds = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    foreach ($ListenerPid in $ProcessIds) {
        $Process = Get-Process -Id $ListenerPid -ErrorAction SilentlyContinue
        $ProcessName = if ($Process) { $Process.ProcessName } else { "unknown process" }
        Write-Host "Port $Port is already held by $ProcessName (PID $ListenerPid)."
        $Answer = Read-Host "Stop this process to continue? (y/N)"
        if ($Answer -notmatch '^(y|yes)$') {
            throw "Startup cancelled. Free port $Port before starting NeuroRoute SDN."
        }
        Stop-Process -Id $ListenerPid -ErrorAction Stop
        for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
            Start-Sleep -Milliseconds 250
            $StillListening = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
            if ($StillListening.Count -eq 0) { break }
        }
        if ($StillListening.Count -gt 0) {
            throw "Port $Port is still in use after stopping PID $ListenerPid. Startup aborted."
        }
    }
}

if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCommand = "py"
    $PythonArguments = @("-3.11", "-m", "uvicorn", "neuroroute_api.main:app", "--host", "127.0.0.1", "--port", "8000")
} else {
    $PythonCommand = "python"
    $PythonArguments = @("-m", "uvicorn", "neuroroute_api.main:app", "--host", "127.0.0.1", "--port", "8000")
}

$ApiWindowTitle = "NeuroRoute SDN API"
$ApiCommand = "& '$PythonCommand' $($PythonArguments -join ' ')"
Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoExit",
    "-Command",
    "Set-Location '$BackendPath'; `$Host.UI.RawUI.WindowTitle = '$ApiWindowTitle'; `$env:PYTHONPATH = '$BackendPath'; $ApiCommand"
) -WorkingDirectory $BackendPath

Push-Location $WorkspaceRoot
try {
    $env:BASE_PATH = "/"
    $env:PORT = "5173"
    $env:VITE_SIM_API_TARGET = "http://127.0.0.1:8000"
    pnpm --filter @workspace/neuroroute-sdn run dev
} finally {
    Remove-Item Env:VITE_SIM_API_TARGET -ErrorAction SilentlyContinue
    Pop-Location
}
