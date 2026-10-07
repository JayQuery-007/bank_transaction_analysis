# Starts the local MongoDB (portable copy in .\mongodb, if present) and the dashboard.
# Usage: .\start.ps1

$root = $PSScriptRoot
$mongod = Get-ChildItem "$root\mongodb\*\bin\mongod.exe" -ErrorAction SilentlyContinue | Select-Object -First 1

if ($mongod -and -not (Get-NetTCPConnection -LocalPort 27017 -State Listen -ErrorAction SilentlyContinue)) {
    Write-Host "Starting MongoDB..."
    New-Item -ItemType Directory -Force "$root\mongodb\data", "$root\mongodb\logs" | Out-Null
    Start-Process $mongod.FullName -ArgumentList "--dbpath", "$root\mongodb\data", "--logpath", "$root\mongodb\logs\mongod.log", "--port", "27017" -WindowStyle Hidden
    Start-Sleep -Seconds 3
}

& "$root\venv\Scripts\python.exe" "$root\run.py"
