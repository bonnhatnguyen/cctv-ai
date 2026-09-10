[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [switch]$Stop
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BackendDirectory = Join-Path $ProjectRoot "backend"
$FrontendDirectory = Join-Path $ProjectRoot "frontend"
$ModelPath = Join-Path $BackendDirectory "weights\yolo26n.pt"
$LauncherDirectory = Join-Path $ProjectRoot "data\v1-launcher"
$StatePath = Join-Path $LauncherDirectory "launcher-state.json"
$BackendPort = 8000
$FrontendPort = 5173
$BackendUrl = $null
$FrontendUrl = $null

$rootBytes = [System.Text.Encoding]::UTF8.GetBytes($ProjectRoot.ToLowerInvariant())
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $InstanceId = -join (($sha.ComputeHash($rootBytes) | Select-Object -First 8) | ForEach-Object { $_.ToString("x2") })
}
finally {
    $sha.Dispose()
}

function Write-LauncherLog([string]$Message) {
    Write-Host "[V1] $Message"
}

function Update-ServiceUrls {
    $script:BackendUrl = "http://127.0.0.1:$BackendPort"
    $script:FrontendUrl = "http://127.0.0.1:$FrontendPort"
}

function Find-FreePort([int[]]$Candidates, [string]$ServiceName) {
    foreach ($candidate in $Candidates) {
        if (-not (Test-TcpPort $candidate)) { return $candidate }
    }
    throw "No safe loopback port is available for $ServiceName. No existing process was stopped."
}

function Test-TcpPort([int]$Port) {
    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.ConnectAsync("127.0.0.1", $Port)
        return $pending.Wait(300) -and $client.Connected
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

function Get-Json([string]$Url) {
    try {
        return Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 2
    }
    catch {
        return $null
    }
}

function Test-BackendIdentity {
    $health = Get-Json "$BackendUrl/api/v1/health"
    return $null -ne $health -and
        "instance_id" -in $health.PSObject.Properties.Name -and
        $health.service -eq "v1-person-tracking" -and
        $health.version -eq "1" -and
        $health.instance_id -eq $InstanceId
}

function Test-BackendReady {
    $health = Get-Json "$BackendUrl/api/v1/health"
    return $null -ne $health -and
        "instance_id" -in $health.PSObject.Properties.Name -and
        $health.service -eq "v1-person-tracking" -and
        $health.version -eq "1" -and
        $health.instance_id -eq $InstanceId -and
        $health.ready -eq $true
}

function Test-FrontendIdentity {
    $identity = Get-Json "$FrontendUrl/__v1_identity"
    return $null -ne $identity -and
        "instance_id" -in $identity.PSObject.Properties.Name -and
        $identity.service -eq "v1-person-tracking-ui" -and
        $identity.version -eq "1" -and
        $identity.network -eq "loopback-only" -and
        $identity.instance_id -eq $InstanceId
}

function Resolve-Python {
    $candidates = @(
        (Join-Path $ProjectRoot ".venv\Scripts\python.exe"),
        (Join-Path $BackendDirectory ".venv\Scripts\python.exe"),
        (Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
    )
    $installed = Get-Command python.exe -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $installed) { $candidates += $installed.Source }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        & $candidate -c "import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)" 2>$null
        if ($LASTEXITCODE -eq 0) { return [System.IO.Path]::GetFullPath($candidate) }
    }
    throw "Python 3.12 was not found. Create .venv at the project root before launching V1."
}

function Resolve-Pnpm {
    $candidates = @(
        (Join-Path $FrontendDirectory "node_modules\.bin\pnpm.cmd")
    )
    $installed = Get-Command pnpm.cmd -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($null -ne $installed) { $candidates += $installed.Source }
    $candidates += Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd"

    foreach ($candidate in $candidates | Select-Object -Unique) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        & $candidate --version *> $null
        if ($LASTEXITCODE -eq 0) { return [System.IO.Path]::GetFullPath($candidate) }
    }
    throw "pnpm was not found. Install pnpm locally before launching V1."
}

function Assert-Preflight([string]$Python, [string]$Pnpm) {
    if (-not (Test-Path -LiteralPath $ModelPath -PathType Leaf)) {
        throw "Missing local YOLO26n weights: $ModelPath. V1 will not download model files."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $FrontendDirectory "node_modules\vite\bin\vite.js") -PathType Leaf)) {
        throw "Frontend dependencies are missing. Run pnpm install in $FrontendDirectory while following the project's private/offline policy."
    }

    $env:YOLO_OFFLINE = "true"
    $env:WANDB_DISABLED = "true"
    $env:DO_NOT_TRACK = "1"
    & $Python -c "import fastapi, uvicorn, ultralytics, torch, cv2, sqlalchemy; print('Python dependencies verified')"
    if ($LASTEXITCODE -ne 0) { throw "Required Python packages could not be imported from $Python." }

    foreach ($tool in @("ffmpeg", "ffprobe")) {
        $command = Get-Command "$tool.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($null -eq $command) { throw "$tool was not found on PATH." }
        & $command.Source -version *> $null
        if ($LASTEXITCODE -ne 0) { throw "$tool exists but did not run successfully: $($command.Source)" }
    }

    & $Pnpm --dir $FrontendDirectory exec vite --version *> $null
    if ($LASTEXITCODE -ne 0) { throw "Vite could not be executed through $Pnpm." }
}

function Wait-Until([scriptblock]$Condition, [string]$Description, [int]$Seconds = 60) {
    $deadline = [DateTime]::UtcNow.AddSeconds($Seconds)
    do {
        if (& $Condition) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Description. See logs in $LauncherDirectory."
}

function Read-OwnedState {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) { return $null }
    try {
        $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
        if ($state.project_root -ne $ProjectRoot -or $state.instance_id -ne $InstanceId) { return $null }
        return $state
    }
    catch {
        return $null
    }
}

function Stop-OwnedProcess([object]$ProcessId, [string]$ExpectedCommand) {
    if ($null -eq $ProcessId) { return }
    $id = [int]$ProcessId
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $id" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return }
    if ($process.CommandLine -notlike "*$ExpectedCommand*") {
        throw "Refusing to stop PID $id because its command line does not match the recorded V1 service."
    }
    & taskkill.exe /PID $id /T /F *> $null
}

Update-ServiceUrls

if ($Stop) {
    $owned = Read-OwnedState
    if ($null -eq $owned) {
        Write-LauncherLog "No launcher-owned V1 services were recorded. Nothing was stopped."
        exit 0
    }
    if ("backend_port" -in $owned.PSObject.Properties.Name) { $BackendPort = [int]$owned.backend_port }
    if ("frontend_port" -in $owned.PSObject.Properties.Name) { $FrontendPort = [int]$owned.frontend_port }
    Update-ServiceUrls
    if ((Test-TcpPort $BackendPort) -and -not (Test-BackendIdentity)) {
        throw "Port $BackendPort is not this launcher's V1 backend. Nothing was stopped."
    }
    if ((Test-TcpPort $FrontendPort) -and -not (Test-FrontendIdentity)) {
        throw "Port $FrontendPort is not the V1 frontend. Nothing was stopped."
    }
    Stop-OwnedProcess $owned.frontend_pid "vite"
    Stop-OwnedProcess $owned.backend_pid "app.v1.api:app"
    Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    Write-LauncherLog "Stopped launcher-owned V1 services."
    exit 0
}

try {
    $Python = Resolve-Python
    $Pnpm = Resolve-Pnpm
    Write-LauncherLog "Python: $Python"
    Write-LauncherLog "pnpm: $Pnpm"
    Assert-Preflight $Python $Pnpm
    Write-LauncherLog "V1 startup preflight passed."

    if ($CheckOnly) { exit 0 }

    New-Item -ItemType Directory -Path $LauncherDirectory -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $backendProcess = $null
    $frontendProcess = $null
    $owned = Read-OwnedState
    $recordedBackendPid = if ($null -ne $owned) { $owned.backend_pid } else { $null }
    $recordedFrontendPid = if ($null -ne $owned) { $owned.frontend_pid } else { $null }

    if ($null -ne $owned -and "backend_port" -in $owned.PSObject.Properties.Name -and "frontend_port" -in $owned.PSObject.Properties.Name) {
        $BackendPort = [int]$owned.backend_port
        $FrontendPort = [int]$owned.frontend_port
        Update-ServiceUrls
    }
    else {
        $recordedBackendPid = $null
        $recordedFrontendPid = $null
    }
    if ((Test-TcpPort $BackendPort) -and -not (Test-BackendIdentity)) {
        $occupiedPort = $BackendPort
        $BackendPort = Find-FreePort (8001..8010) "the V1 backend"
        $recordedBackendPid = $null
        Update-ServiceUrls
        Write-LauncherLog "Port $occupiedPort belongs to another service; using safe loopback port $BackendPort."
    }
    if ((Test-TcpPort $FrontendPort) -and -not (Test-FrontendIdentity)) {
        $occupiedPort = $FrontendPort
        $FrontendPort = Find-FreePort (5174..5183) "the V1 frontend"
        $recordedFrontendPid = $null
        Update-ServiceUrls
        Write-LauncherLog "Port $occupiedPort belongs to another service; using safe loopback port $FrontendPort."
    }

    if (Test-TcpPort $BackendPort) {
        if (-not (Test-BackendIdentity)) {
            throw "Port $BackendPort is occupied by a service that is not this V1 backend. It was not stopped or reused."
        }
        if (-not (Test-BackendReady)) {
            throw "The matching V1 backend on port $BackendPort is not ready. See its existing logs."
        }
        Write-LauncherLog "Reusing verified V1 backend at $BackendUrl."
    }
    else {
        $env:PYTHONUNBUFFERED = "1"
        $env:V1_TRACKING_MODEL_PATH = $ModelPath
        $env:V1_TRACKING_DATA_DIR = Join-Path $ProjectRoot "data\v1"
        $env:V1_TRACKING_INSTANCE_ID = $InstanceId
        $backendOut = Join-Path $LauncherDirectory "backend-$timestamp.log"
        $backendErr = Join-Path $LauncherDirectory "backend-$timestamp.error.log"
        $backendProcess = Start-Process -FilePath $Python `
            -ArgumentList @("-m", "uvicorn", "app.v1.api:app", "--host", "127.0.0.1", "--port", "$BackendPort") `
            -WorkingDirectory $BackendDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
        Wait-Until { Test-BackendReady } "the V1 backend"
        Write-LauncherLog "Started verified V1 backend at $BackendUrl (PID $($backendProcess.Id))."
    }

    if (Test-TcpPort $FrontendPort) {
        if (-not (Test-FrontendIdentity)) {
            throw "Port $FrontendPort is occupied by a service that is not the V1 frontend. It was not stopped or reused."
        }
        Write-LauncherLog "Reusing verified V1 frontend at $FrontendUrl."
    }
    else {
        $frontendOut = Join-Path $LauncherDirectory "frontend-$timestamp.log"
        $frontendErr = Join-Path $LauncherDirectory "frontend-$timestamp.error.log"
        $env:V1_BACKEND_PORT = "$BackendPort"
        $env:V1_TRACKING_INSTANCE_ID = $InstanceId
        $frontendProcess = Start-Process -FilePath $Pnpm `
            -ArgumentList @("exec", "vite", "--host", "127.0.0.1", "--port", "$FrontendPort", "--strictPort") `
            -WorkingDirectory $FrontendDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
        Wait-Until { Test-FrontendIdentity } "the V1 frontend"
        Write-LauncherLog "Started verified V1 frontend at $FrontendUrl (PID $($frontendProcess.Id))."
    }

    $backendPid = if ($null -ne $backendProcess) { $backendProcess.Id } else { $recordedBackendPid }
    $frontendPid = if ($null -ne $frontendProcess) { $frontendProcess.Id } else { $recordedFrontendPid }
    [ordered]@{
        project_root = $ProjectRoot
        instance_id = $InstanceId
        backend_pid = $backendPid
        frontend_pid = $frontendPid
        backend_port = $BackendPort
        frontend_port = $FrontendPort
        backend_url = $BackendUrl
        frontend_url = $FrontendUrl
        updated_at = [DateTime]::UtcNow.ToString("o")
    } | ConvertTo-Json | Set-Content -LiteralPath $StatePath -Encoding UTF8

    if (-not $NoBrowser) {
        Start-Process $FrontendUrl | Out-Null
    }
    Write-LauncherLog "V1 is ready: $FrontendUrl"
    Write-LauncherLog "Logs: $LauncherDirectory"
}
catch {
    Write-Error "V1 startup failed: $($_.Exception.Message)"
    Write-Host "Diagnostics: $LauncherDirectory"
    exit 1
}
