$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $PSScriptRoot
$BackendPath = Join-Path $ProjectRoot "backend"
$OutputPath = Join-Path $ProjectRoot "generated-artifacts"

if (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCommand = "py"
    $PythonPrefix = @("-3.11")
} else {
    $PythonCommand = "python"
    $PythonPrefix = @()
}

Push-Location $BackendPath
try {
    & $PythonCommand @PythonPrefix -m neuroroute_api.run_experiments `
        --duration-seconds 30 `
        --seed 42 `
        --output-dir $OutputPath
} finally {
    Pop-Location
}

Write-Host "All available experiments completed. Results and SVG charts are in $OutputPath."
