# Convenience script: starts MongoDB (if not already running) then the FastAPI app.
# Run from the project root: .\start.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

$mongoRunning = Get-NetTCPConnection -LocalPort 27017 -ErrorAction SilentlyContinue
if (-not $mongoRunning) {
    Write-Host "Starting MongoDB..."
    $mongod = Join-Path $root "mongodb\mongodb-win32-x86_64-windows-8.0.4\bin\mongod.exe"
    $dbpath = Join-Path $root "mongodb\data"
    $logpath = Join-Path $root "mongodb\logs\mongod.log"
    Start-Process -FilePath $mongod -ArgumentList "--dbpath", $dbpath, "--logpath", $logpath, "--port", "27017" -WindowStyle Hidden
    Start-Sleep -Seconds 3
} else {
    Write-Host "MongoDB already running on port 27017."
}

$env:JAVA_HOME = Join-Path $root "jdk\jdk-17.0.20.1+1"
$env:Path = "$env:JAVA_HOME\bin;$env:Path"

Write-Host "Starting API on http://localhost:8000 ..."
& "$root\venv\Scripts\uvicorn.exe" backend.app.main:app --reload --port 8000
