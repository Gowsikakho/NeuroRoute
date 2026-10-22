$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)

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
