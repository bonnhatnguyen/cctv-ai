[CmdletBinding()]
param(
    [switch]$CheckOnly,
    [switch]$NoBrowser,
    [switch]$Stop,
    [int]$BackendPort = 8000,
    [int]$FrontendPort = 5173,
    [string]$LauncherDirectory = "",
    [string]$DataDirectory = "",
    [ValidateRange(1, 100)][int]$AlternatePortCount = 10,
    [ValidateRange(1, 300)][int]$ReadyTimeoutSeconds = 60
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$BackendDirectory = Join-Path $ProjectRoot "backend"
$FrontendDirectory = Join-Path $ProjectRoot "frontend"
$ModelPath = Join-Path $BackendDirectory "weights\yolo26n.pt"
if ([string]::IsNullOrWhiteSpace($DataDirectory)) {
    $DataDirectory = Join-Path $ProjectRoot "data\v1"
}
else {
    $DataDirectory = [System.IO.Path]::GetFullPath($DataDirectory)
}
if ([string]::IsNullOrWhiteSpace($LauncherDirectory)) {
    $LauncherDirectory = Join-Path $ProjectRoot "data\v1-launcher"
}
else {
    $LauncherDirectory = [System.IO.Path]::GetFullPath($LauncherDirectory)
}
$StatePath = Join-Path $LauncherDirectory "launcher-state.json"
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

function Find-FreePort([int]$RequestedPort, [string]$ServiceName) {
    if (-not (Test-TcpPort $RequestedPort)) { return $RequestedPort }
    foreach ($candidate in (($RequestedPort + 1)..($RequestedPort + $AlternatePortCount))) {
        if (-not (Test-TcpPort $candidate)) { return $candidate }
    }
    throw "No safe loopback port is available for $ServiceName. No existing process was stopped."
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
    return (Test-BackendIdentity) -and $null -ne $health -and $health.ready -eq $true
}

function Test-FrontendIdentity {
    $identity = Get-Json "$FrontendUrl/__v1_identity"
    return $null -ne $identity -and
        "instance_id" -in $identity.PSObject.Properties.Name -and
        "backend_url" -in $identity.PSObject.Properties.Name -and
        "backend_port" -in $identity.PSObject.Properties.Name -and
        $identity.service -eq "v1-person-tracking-ui" -and
        $identity.version -eq "1" -and
        $identity.network -eq "loopback-only" -and
        $identity.instance_id -eq $InstanceId -and
        $identity.backend_url -eq $BackendUrl -and
        [int]$identity.backend_port -eq $BackendPort
}

function Test-FrontendReady {
    if (-not (Test-FrontendIdentity)) { return $false }
    $proxied = Get-Json "$FrontendUrl/api/v1/health"
    return $null -ne $proxied -and
        "instance_id" -in $proxied.PSObject.Properties.Name -and
        $proxied.service -eq "v1-person-tracking" -and
        $proxied.version -eq "1" -and
        $proxied.instance_id -eq $InstanceId -and
        $proxied.ready -eq $true
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
    $candidates = @((Join-Path $FrontendDirectory "node_modules\.bin\pnpm.cmd"))
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
        throw "Frontend dependencies are missing. Install them locally before launching V1."
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

function Wait-Until([scriptblock]$Condition, [string]$Description) {
    $deadline = [DateTime]::UtcNow.AddSeconds($ReadyTimeoutSeconds)
    do {
        if (& $Condition) { return }
        Start-Sleep -Milliseconds 250
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Timed out waiting for $Description. See logs in $LauncherDirectory."
}

function Get-ProcessSnapshot([int]$ProcessId) {
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    return [pscustomobject][ordered]@{
        pid = [int]$process.ProcessId
        parent_pid = [int]$process.ParentProcessId
        created_utc = ([DateTime]$process.CreationDate).ToUniversalTime().ToString("o")
        executable = [string]$process.ExecutablePath
        command = [string]$process.CommandLine
    }
}

function Get-ListenerPid([int]$Port) {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Where-Object { $_.LocalAddress -eq "127.0.0.1" } |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($listeners.Count -ne 1) { return $null }
    return [int]$listeners[0]
}

function Test-Descendant([int]$ProcessId, [int]$AncestorId) {
    if ($ProcessId -eq $AncestorId) { return $true }
    $current = $ProcessId
    foreach ($depth in 1..16) {
        $snapshot = Get-ProcessSnapshot $current
        if ($null -eq $snapshot -or $snapshot.parent_pid -le 0) { return $false }
        if ($snapshot.parent_pid -eq $AncestorId) { return $true }
        $current = $snapshot.parent_pid
    }
    return $false
}

function New-PendingServiceRecord([string]$Kind, [System.Diagnostics.Process]$Launcher, [int]$Port, [string]$BackendTarget = "") {
    $launcherSnapshot = Get-ProcessSnapshot $Launcher.Id
    if ($null -eq $launcherSnapshot) {
        throw "Could not record the exact $Kind process generation immediately after startup."
    }
    return [pscustomobject][ordered]@{
        kind = $Kind
        phase = "starting"
        generation = [Guid]::NewGuid().ToString("n")
        project_root = $ProjectRoot
        port = $Port
        url = "http://127.0.0.1:$Port"
        backend_url = $BackendTarget
        launcher = $launcherSnapshot
        listener = $null
    }
}

function Complete-ServiceRecord([object]$Record) {
    $listenerPid = Get-ListenerPid ([int]$Record.port)
    if ($null -eq $listenerPid) {
        throw "Could not record the exact $($Record.kind) listener."
    }
    $listenerSnapshot = Get-ProcessSnapshot $listenerPid
    if ($null -eq $listenerSnapshot -or -not (Test-Descendant $listenerPid ([int]$Record.launcher.pid))) {
        throw "The $($Record.kind) listener is not owned by the process started by this invocation."
    }
    $Record.phase = "ready"
    $Record.listener = $listenerSnapshot
    return $Record
}

function Assert-ExpectedInvocation([object]$Record, [string]$Kind) {
    $command = [string]$Record.launcher.command
    $requiredTokens = if ($Kind -eq "backend") {
        @("-m uvicorn", "app.v1.api:app", "--app-dir", $BackendDirectory, "--host 127.0.0.1", "--port $($Record.port)")
    }
    else {
        @("exec vite", $FrontendDirectory, (Join-Path $FrontendDirectory "vite.config.ts"), "--host 127.0.0.1", "--port $($Record.port)", "--strictPort")
    }
    foreach ($token in $requiredTokens) {
        if ($command.IndexOf($token, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "Refusing to stop or reuse the $Kind because its exact project-root invocation does not match."
        }
    }
}

function Assert-SnapshotMatches([object]$Expected, [string]$Label) {
    if ($null -eq $Expected -or "pid" -notin $Expected.PSObject.Properties.Name) {
        throw "Refusing to stop or reuse $Label because its ownership record is incomplete."
    }
    $actual = Get-ProcessSnapshot ([int]$Expected.pid)
    if ($null -eq $actual) { return $false }
    if (([DateTime]$actual.created_utc).ToUniversalTime().Ticks -ne
        ([DateTime]$Expected.created_utc).ToUniversalTime().Ticks) {
        throw "Refusing to stop or reuse $Label because PID $($Expected.pid) belongs to a different process generation."
    }
    foreach ($field in @("pid", "parent_pid", "executable", "command")) {
        if ([string]$actual.$field -cne [string]$Expected.$field) {
            throw "Refusing to stop or reuse $Label because PID $($Expected.pid) belongs to a different process generation."
        }
    }
    return $true
}

function Assert-OwnedService([object]$Record, [string]$Kind) {
    if ($null -eq $Record -or "launcher" -notin $Record.PSObject.Properties.Name -or
        "listener" -notin $Record.PSObject.Properties.Name -or "phase" -notin $Record.PSObject.Properties.Name -or
        "port" -notin $Record.PSObject.Properties.Name) {
        throw "Refusing to stop or reuse the $Kind because its ownership record is incomplete."
    }
    if ($Record.project_root -cne $ProjectRoot -or $Record.kind -cne $Kind -or
        $Record.phase -notin @("starting", "ready")) {
        throw "Refusing to stop or reuse the $Kind because its project ownership does not match."
    }
    Assert-ExpectedInvocation $Record $Kind
    $launcherAlive = Assert-SnapshotMatches $Record.launcher "$Kind launcher"
    if ($Record.phase -eq "starting") {
        if (-not $launcherAlive) {
            if (Test-TcpPort ([int]$Record.port)) {
                throw "Refusing to stop or reuse the starting $Kind because its launcher exited while the port is occupied."
            }
            return $false
        }
        $pendingListenerPid = Get-ListenerPid ([int]$Record.port)
        if ($null -ne $pendingListenerPid -and
            -not (Test-Descendant $pendingListenerPid ([int]$Record.launcher.pid))) {
            throw "Refusing to stop the starting $Kind because its listener is not launcher-owned."
        }
        return $true
    }
    if ($null -eq $Record.listener) {
        throw "Refusing to stop or reuse the ready $Kind because its listener ownership is missing."
    }
    $listenerAlive = Assert-SnapshotMatches $Record.listener "$Kind listener"
    if (-not $launcherAlive -and -not $listenerAlive -and -not (Test-TcpPort ([int]$Record.port))) { return $false }
    if (-not $launcherAlive -or -not $listenerAlive) {
        throw "Refusing to stop or reuse the $Kind because only part of its recorded process tree exists."
    }
    $owningPid = Get-ListenerPid ([int]$Record.port)
    if ($null -eq $owningPid -or $owningPid -ne [int]$Record.listener.pid -or
        -not (Test-Descendant ([int]$Record.listener.pid) ([int]$Record.launcher.pid))) {
        throw "Refusing to stop or reuse the $Kind because its listener ownership does not match."
    }
    return $true
}

function Stop-OwnedService([object]$Record, [string]$Kind) {
    if (-not (Assert-OwnedService $Record $Kind)) { return }
    & taskkill.exe /PID ([int]$Record.launcher.pid) /T /F *> $null
    if ($LASTEXITCODE -ne 0) { throw "Failed to terminate the verified $Kind process tree." }
    $deadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        $launcher = Get-ProcessSnapshot ([int]$Record.launcher.pid)
        $listener = if ($Record.phase -eq "ready") { Get-ProcessSnapshot ([int]$Record.listener.pid) } else { $null }
        if ($null -eq $launcher -and $null -eq $listener -and -not (Test-TcpPort ([int]$Record.port))) { return }
        Start-Sleep -Milliseconds 100
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "The verified $Kind process tree did not terminate; launcher state was preserved."
}

function New-EmptyState {
    return [pscustomobject][ordered]@{
        schema_version = 2
        project_root = $ProjectRoot
        instance_id = $InstanceId
        backend = $null
        frontend = $null
        updated_at = [DateTime]::UtcNow.ToString("o")
    }
}

function Read-OwnedState {
    if (-not (Test-Path -LiteralPath $StatePath -PathType Leaf)) { return New-EmptyState }
    $state = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
    if ("schema_version" -notin $state.PSObject.Properties.Name -or $state.schema_version -ne 2 -or
        $state.project_root -cne $ProjectRoot -or $state.instance_id -cne $InstanceId) {
        throw "Existing launcher state cannot be verified. It was not overwritten or used to stop a process."
    }
    return $state
}

function Write-OwnedState([object]$State) {
    New-Item -ItemType Directory -Path $LauncherDirectory -Force | Out-Null
    $State.updated_at = [DateTime]::UtcNow.ToString("o")
    $temporary = Join-Path $LauncherDirectory ("launcher-state.{0}.tmp" -f [Guid]::NewGuid().ToString("n"))
    try {
        $State | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $temporary -Encoding UTF8
        [System.IO.File]::Move($temporary, $StatePath, $true)
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Remove-StateIfEmpty([object]$State) {
    if ($null -eq $State.backend -and $null -eq $State.frontend) {
        Remove-Item -LiteralPath $StatePath -Force -ErrorAction SilentlyContinue
    }
    else {
        Write-OwnedState $State
    }
}

Update-ServiceUrls
$state = $null
$createdBackend = $null
$createdFrontend = $null

try {
    if ($Stop) {
        $state = Read-OwnedState
        if ($null -eq $state.backend -and $null -eq $state.frontend) {
            Write-LauncherLog "No launcher-owned V1 services were recorded. Nothing was stopped."
            exit 0
        }
        if ($null -ne $state.frontend) {
            Stop-OwnedService $state.frontend "frontend"
            $state.frontend = $null
            Write-OwnedState $state
        }
        if ($null -ne $state.backend) {
            Stop-OwnedService $state.backend "backend"
            $state.backend = $null
            Write-OwnedState $state
        }
        Remove-StateIfEmpty $state
        Write-LauncherLog "Stopped launcher-owned V1 services."
        exit 0
    }

    $Python = Resolve-Python
    $Pnpm = Resolve-Pnpm
    Write-LauncherLog "Python: $Python"
    Write-LauncherLog "pnpm: $Pnpm"
    Assert-Preflight $Python $Pnpm
    Write-LauncherLog "V1 startup preflight passed."
    if ($CheckOnly) { exit 0 }

    New-Item -ItemType Directory -Path $LauncherDirectory -Force | Out-Null
    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $state = Read-OwnedState

    if ($null -ne $state.backend) {
        if ($state.backend.phase -eq "starting") {
            Write-LauncherLog "Cleaning up a verified launcher-owned backend left in the starting phase."
            Stop-OwnedService $state.backend "backend"
            $state.backend = $null
            Write-OwnedState $state
        }
    }

    if ($null -ne $state.backend) {
        $BackendPort = [int]$state.backend.port
        Update-ServiceUrls
        if (-not (Assert-OwnedService $state.backend "backend") -or -not (Test-BackendReady)) {
            throw "The recorded V1 backend is not both owned and ready. It was not reused."
        }
        Write-LauncherLog "Reusing verified V1 backend at $BackendUrl."
    }
    else {
        $requestedBackendPort = $BackendPort
        $BackendPort = Find-FreePort $BackendPort "the V1 backend"
        Update-ServiceUrls
        if ($BackendPort -ne $requestedBackendPort) {
            Write-LauncherLog "Port $requestedBackendPort belongs to another service; using safe loopback port $BackendPort."
        }
        $env:PYTHONUNBUFFERED = "1"
        $env:V1_TRACKING_MODEL_PATH = $ModelPath
        $env:V1_TRACKING_DATA_DIR = $DataDirectory
        $env:V1_TRACKING_INSTANCE_ID = $InstanceId
        $backendOut = Join-Path $LauncherDirectory "backend-$timestamp.log"
        $backendErr = Join-Path $LauncherDirectory "backend-$timestamp.error.log"
        $backendProcess = Start-Process -FilePath $Python `
            -ArgumentList @("-m", "uvicorn", "app.v1.api:app", "--app-dir", $BackendDirectory, "--host", "127.0.0.1", "--port", "$BackendPort") `
            -WorkingDirectory $BackendDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $backendOut -RedirectStandardError $backendErr
        $createdBackend = New-PendingServiceRecord "backend" $backendProcess $BackendPort
        $state.backend = $createdBackend
        Write-OwnedState $state
        $state = Read-OwnedState
        $createdBackend = $state.backend
        Wait-Until { Test-BackendReady } "the V1 backend"
        $createdBackend = Complete-ServiceRecord $createdBackend
        $state.backend = $createdBackend
        Write-OwnedState $state
        $state = Read-OwnedState
        $createdBackend = $state.backend
        Write-LauncherLog "Started verified V1 backend at $BackendUrl (PID $($backendProcess.Id))."
    }

    if ($null -ne $state.frontend) {
        if ($state.frontend.phase -eq "starting") {
            Write-LauncherLog "Cleaning up a verified launcher-owned frontend left in the starting phase."
            Stop-OwnedService $state.frontend "frontend"
            $state.frontend = $null
            Write-OwnedState $state
        }
    }

    if ($null -ne $state.frontend) {
        $FrontendPort = [int]$state.frontend.port
        Update-ServiceUrls
        $ownedFrontend = Assert-OwnedService $state.frontend "frontend"
        $targetMatches = $state.frontend.backend_url -eq $BackendUrl -and (Test-FrontendReady)
        if ($ownedFrontend -and -not $targetMatches) {
            Write-LauncherLog "Replacing verified launcher-owned frontend because its backend target is stale."
            Stop-OwnedService $state.frontend "frontend"
            $state.frontend = $null
            Write-OwnedState $state
        }
        elseif (-not $ownedFrontend -or -not $targetMatches) {
            throw "The recorded frontend cannot be safely reused or replaced."
        }
        else {
            Write-LauncherLog "Reusing verified V1 frontend at $FrontendUrl."
        }
    }

    if ($null -eq $state.frontend) {
        $requestedFrontendPort = $FrontendPort
        $FrontendPort = Find-FreePort $FrontendPort "the V1 frontend"
        Update-ServiceUrls
        if ($FrontendPort -ne $requestedFrontendPort) {
            Write-LauncherLog "Port $requestedFrontendPort belongs to another service; using safe loopback port $FrontendPort."
        }
        $frontendOut = Join-Path $LauncherDirectory "frontend-$timestamp.log"
        $frontendErr = Join-Path $LauncherDirectory "frontend-$timestamp.error.log"
        $env:V1_BACKEND_PORT = "$BackendPort"
        $env:V1_BACKEND_URL = $BackendUrl
        $env:V1_TRACKING_INSTANCE_ID = $InstanceId
        $frontendProcess = Start-Process -FilePath $Pnpm `
            -ArgumentList @("exec", "vite", $FrontendDirectory, "--config", (Join-Path $FrontendDirectory "vite.config.ts"), "--host", "127.0.0.1", "--port", "$FrontendPort", "--strictPort") `
            -WorkingDirectory $FrontendDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $frontendOut -RedirectStandardError $frontendErr
        $createdFrontend = New-PendingServiceRecord "frontend" $frontendProcess $FrontendPort $BackendUrl
        $state.frontend = $createdFrontend
        Write-OwnedState $state
        $state = Read-OwnedState
        $createdFrontend = $state.frontend
        Wait-Until { Test-FrontendReady } "the V1 frontend and its backend proxy"
        $createdFrontend = Complete-ServiceRecord $createdFrontend
        $state.frontend = $createdFrontend
        Write-OwnedState $state
        $state = Read-OwnedState
        $createdFrontend = $state.frontend
        Write-LauncherLog "Started verified V1 frontend at $FrontendUrl (PID $($frontendProcess.Id))."
    }

    if (-not $NoBrowser) { Start-Process $FrontendUrl | Out-Null }
    Write-LauncherLog "V1 is ready: $FrontendUrl"
    Write-LauncherLog "Logs: $LauncherDirectory"
}
catch {
    $originalError = $_.Exception.Message
    $cleanupErrors = @()
    $cleanupNeeded = $null -ne $createdFrontend -or $null -ne $createdBackend
    if ($null -ne $createdFrontend) {
        try {
            Stop-OwnedService $createdFrontend "frontend"
            $state.frontend = $null
        }
        catch { $cleanupErrors += $_.Exception.Message }
    }
    if ($null -ne $createdBackend) {
        try {
            Stop-OwnedService $createdBackend "backend"
            $state.backend = $null
        }
        catch { $cleanupErrors += $_.Exception.Message }
    }
    if ($cleanupNeeded -and $null -ne $state) {
        try { Remove-StateIfEmpty $state } catch { $cleanupErrors += $_.Exception.Message }
    }
    [Console]::Error.WriteLine("V1 startup failed: $originalError")
    [Console]::Error.WriteLine("Diagnostics: $LauncherDirectory")
    foreach ($cleanupError in $cleanupErrors) {
        [Console]::Error.WriteLine("Cleanup warning: $cleanupError")
    }
    exit 1
}
