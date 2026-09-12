[CmdletBinding()]
param(
    [switch]$DownloadModels
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))
$venvRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot ".venv-assist-benchmark"))
$expectedPrefix = $repoRoot.TrimEnd([System.IO.Path]::DirectorySeparatorChar) + [System.IO.Path]::DirectorySeparatorChar
if (-not $venvRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Benchmark venv resolved outside the repository: $venvRoot"
}

$appPython = Join-Path $repoRoot ".venv/Scripts/python.exe"
$benchmarkPython = Join-Path $venvRoot "Scripts/python.exe"
$lockFile = Join-Path $repoRoot "scripts/assisted-benchmark-requirements.lock.txt"
if (-not (Test-Path -LiteralPath $appPython -PathType Leaf)) {
    throw "Application Python is unavailable: $appPython"
}
if (-not (Test-Path -LiteralPath $lockFile -PathType Leaf)) {
    throw "Verified dependency lock is unavailable: $lockFile"
}

if (-not (Test-Path -LiteralPath $benchmarkPython -PathType Leaf)) {
    & $appPython -m venv $venvRoot
    if ($LASTEXITCODE -ne 0) { throw "Failed to create benchmark venv" }
}

$actualPrefix = & $benchmarkPython -c "import pathlib,sys; print(pathlib.Path(sys.prefix).resolve())"
if ($LASTEXITCODE -ne 0 -or $actualPrefix.Trim() -ne $venvRoot) {
    throw "Refusing unexpected Python environment: $actualPrefix"
}
$systemSite = Select-String -LiteralPath (Join-Path $venvRoot "pyvenv.cfg") -Pattern '^include-system-site-packages\s*=\s*true$'
if ($systemSite) { throw "Benchmark venv must not use system site packages" }

& $benchmarkPython -m pip install --require-hashes --requirement $lockFile
if ($LASTEXITCODE -ne 0) { throw "Failed to install benchmark dependency lock" }

if ($DownloadModels) {
    $modelRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "data/v1/assisted-models"))
    if (-not $modelRoot.StartsWith($expectedPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Model root resolved outside the repository: $modelRoot"
    }
    $driveName = [System.IO.Path]::GetPathRoot($modelRoot).Substring(0, 1)
    $freeBytes = (Get-PSDrive -Name $driveName).Free
    Write-Host "Model sources: Google MediaPipe version 1 (~8 MiB); Hugging Face DINO commit a2bb814 (~690 MiB)"
    Write-Host ("Free disk before model setup: {0:N2} GiB" -f ($freeBytes / 1GB))
    if ($freeBytes -lt 5GB) { throw "At least 5 GiB free disk is required" }
    New-Item -ItemType Directory -Force -Path $modelRoot | Out-Null

    function Assert-FileHash([string]$Path, [string]$Expected) {
        if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
            throw "Required model artifact is missing: $Path"
        }
        $actual = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $Expected) { throw "Hash mismatch for model artifact: $Path" }
    }

    $mpRoot = Join-Path $modelRoot "mediapipe-hand-landmarker"
    New-Item -ItemType Directory -Force -Path $mpRoot | Out-Null
    $mpTask = Join-Path $mpRoot "hand_landmarker.task"
    $mpCard = Join-Path $mpRoot "model-card.pdf"
    if (-not (Test-Path -LiteralPath $mpTask -PathType Leaf)) {
        Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task" -OutFile $mpTask
    }
    if (-not (Test-Path -LiteralPath $mpCard -PathType Leaf)) {
        Invoke-WebRequest -Uri "https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Hand%20Tracking%20(Lite_Full)%20with%20Fairness%20Oct%202021.pdf" -OutFile $mpCard
    }
    Assert-FileHash $mpTask "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
    Assert-FileHash $mpCard "43127ff8a92e22717f0d8c30dab3efef4641a29cbf50b15ae3de001be94ab76b"
    $mpAsset = [ordered]@{
        model_id = "mediapipe-hand-landmarker"
        revision = "google-storage-generation-1682480004222387"
        files_sha256 = [ordered]@{
            "hand_landmarker.task" = "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1"
            "model-card.pdf" = "43127ff8a92e22717f0d8c30dab3efef4641a29cbf50b15ae3de001be94ab76b"
        }
        license_source = "https://storage.googleapis.com/mediapipe-assets/Model%20Card%20Hand%20Tracking%20(Lite_Full)%20with%20Fairness%20Oct%202021.pdf"
        license_sha256 = "43127ff8a92e22717f0d8c30dab3efef4641a29cbf50b15ae3de001be94ab76b"
        telemetry_policy = "allowed_by_user"
    }
    [System.IO.File]::WriteAllText((Join-Path $mpRoot "asset.json"), ($mpAsset | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))

    $dinoRoot = Join-Path $modelRoot "grounding-dino-tiny"
    New-Item -ItemType Directory -Force -Path $dinoRoot | Out-Null
    $env:ASSIST_BENCHMARK_DINO_ROOT = $dinoRoot
    try {
        & $benchmarkPython -c "import os; from huggingface_hub import snapshot_download; snapshot_download(repo_id='IDEA-Research/grounding-dino-tiny', revision='a2bb814dd30d776dcf7e30523b00659f4f141c71', local_dir=os.environ['ASSIST_BENCHMARK_DINO_ROOT'], allow_patterns=['README.md','config.json','model.safetensors','preprocessor_config.json','special_tokens_map.json','tokenizer.json','tokenizer_config.json','vocab.txt'])"
        if ($LASTEXITCODE -ne 0) { throw "Grounding DINO download failed" }
    }
    finally {
        Remove-Item Env:ASSIST_BENCHMARK_DINO_ROOT -ErrorAction SilentlyContinue
    }
    $dinoHashes = [ordered]@{
        "README.md" = "cf46f74c7b6850f1d5cbe406028324d8798148726016d46a69a365b4a2d3e89f"
        "config.json" = "eec82c5ab66e16df12a9a212e68ac011779927c2536cf9078658e35d85f0c67a"
        "model.safetensors" = "1a2412ef99bd74bcd3c2a246fa1e48581f8889a1300c9051974741314fc042f3"
        "preprocessor_config.json" = "8454179ba95e2ad22947835aad7b45862a601fc0055ab88bf1ee70892d3aea60"
        "special_tokens_map.json" = "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3"
        "tokenizer.json" = "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66"
        "tokenizer_config.json" = "d40ab645b68211910b9170d22433d43186a6ec8ee6fd10ba170524b25bf4fb56"
        "vocab.txt" = "07eced375cec144d27c900241f3e339478dec958f92fddbc551f295c992038a3"
    }
    foreach ($entry in $dinoHashes.GetEnumerator()) {
        Assert-FileHash (Join-Path $dinoRoot $entry.Key) $entry.Value
    }
    $dinoAsset = [ordered]@{
        model_id = "IDEA-Research/grounding-dino-tiny"
        revision = "a2bb814dd30d776dcf7e30523b00659f4f141c71"
        files_sha256 = $dinoHashes
        license_source = "https://huggingface.co/IDEA-Research/grounding-dino-tiny/blob/a2bb814dd30d776dcf7e30523b00659f4f141c71/README.md"
        license_sha256 = "cf46f74c7b6850f1d5cbe406028324d8798148726016d46a69a365b4a2d3e89f"
        telemetry_policy = "not_applicable"
    }
    [System.IO.File]::WriteAllText((Join-Path $dinoRoot "asset.json"), ($dinoAsset | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))

    $environment = [ordered]@{
        created_at = [DateTimeOffset]::UtcNow.ToString("o")
        python = (& $benchmarkPython --version 2>&1 | Out-String).Trim()
        pip_freeze = @(& $benchmarkPython -m pip freeze --all)
        torch = (& $benchmarkPython -c "import torch; print(torch.__version__, torch.version.cuda, torch.cuda.is_available())")
        ffmpeg = (& ffmpeg -version 2>&1 | Select-Object -First 1)
        mediapipe_telemetry_policy = "allowed_by_user"
    }
    [System.IO.File]::WriteAllText((Join-Path $modelRoot "environment.json"), ($environment | ConvertTo-Json -Depth 4), [System.Text.UTF8Encoding]::new($false))
}

Write-Host "Benchmark environment ready: $venvRoot"
