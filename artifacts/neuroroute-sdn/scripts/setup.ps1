param(
    [switch]$InstallResearchModels
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$WorkspaceRoot = Split-Path -Parent (Split-Path -Parent $ProjectRoot)

if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCommand = "py"
    $PythonPrefix = @("-3.11")
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCommand = "python"
    $PythonPrefix = @()
} else {
    throw "Python 3.11 is required. Install it from python.org, then rerun setup.ps1."
}

$Version = & $PythonCommand @PythonPrefix --version
if ($Version -notmatch "Python 3\.11") {
    throw "NeuroRoute-SDN requires Python 3.11; found '$Version'."
}

Push-Location $BackendPath
try {
    & $PythonCommand @PythonPrefix -m pip install --upgrade pip
    & $PythonCommand @PythonPrefix -m pip install -r requirements.txt
    if ($InstallResearchModels) {
        & $PythonCommand @PythonPrefix -m pip install -r requirements-ml.txt
    }
} finally {
    Pop-Location
}

Push-Location $WorkspaceRoot
try {
    pnpm install
} finally {
    Pop-Location
}

Write-Host "Setup complete. Run scripts/start_dev.ps1 to launch the dashboard and API."
