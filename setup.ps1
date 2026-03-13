#Requires -Version 5.1
<#
.SYNOPSIS
    EmberOS-Windows Master Setup Script.
    Run once to set up the entire environment: embedded Python, dependencies,
    BitNet build, model download, and configuration.
#>

Set-StrictMode -Version Latest
# Use Continue so pip/git stderr warnings don't abort the script.
# Real failures are caught explicitly with $LASTEXITCODE / Test-Path checks.
$ErrorActionPreference = "Continue"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition

# ── Self-elevate if VS Build Tools are absent (install requires admin) ─────────
$_earlyVsWhere = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $_earlyVsWhere)) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    if (-not $isAdmin) {
        Write-Host ""
        Write-Host "  VS Build Tools not found. Re-launching as Administrator to install them..." -ForegroundColor Cyan
        Write-Host "  A UAC prompt will appear. Please click Yes." -ForegroundColor Yellow
        Start-Process powershell.exe "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs -Wait
        exit
    }
}

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  EmberOS-Windows Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Helper: invoke a .bat and import its env vars ──────────────────────────────
function Invoke-CmdScript {
    param([string]$ScriptPath, [string]$Arguments = "")
    $tempFile = [System.IO.Path]::GetTempFileName()
    $cmd = "`"$ScriptPath`" $Arguments && set > `"$tempFile`""
    cmd.exe /c $cmd 2>&1 | Out-Null
    if (Test-Path $tempFile) {
        Get-Content $tempFile | ForEach-Object {
            if ($_ -match "^([^=]+)=(.*)$") {
                [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], "Process")
            }
        }
        Remove-Item $tempFile -Force
    }
}

# ── Helper: write text file without BOM (PS5.1 Set-Content -Encoding UTF8 adds BOM) ─────
function Write-NoBom {
    param([string]$Path, [string]$Content)
    $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

# ==============================================================================
# STEP 1: CPU and GPU Detection
# ==============================================================================
Write-Host "[1/7] Detecting hardware..." -ForegroundColor Yellow

$cpuArch = "x86_64"
if ([System.Environment]::Is64BitOperatingSystem) {
    if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { $cpuArch = "arm64" }
}

$cpuCountObj = Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum
$cpuCount = [int]$cpuCountObj.Sum
if (-not $cpuCount) { $cpuCount = [int]$env:NUMBER_OF_PROCESSORS }

$quantType = "i2_s"
if ($cpuArch -eq "arm64") { $quantType = "tl1" }

Write-Host "  CPU: $cpuArch, $cpuCount logical cores"
Write-Host "  Quant type: $quantType"

$gpuMode    = "cpu"
$gpuName    = ""
$gpuVram    = 0
$cudaVersion = ""

try {
    $nvsmiOut = & nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits 2>$null
    if ($LASTEXITCODE -eq 0 -and $nvsmiOut) {
        $parts   = $nvsmiOut.Split(",")
        $gpuName = $parts[0].Trim()
        $gpuVram = [int]($parts[1].Trim())
        Write-Host "  GPU: $gpuName, $gpuVram MB VRAM"

        $nvsmiAll = (& nvidia-smi 2>$null) -join " "
        if ($nvsmiAll -match "CUDA Version:\s*([\d.]+)") {
            $cudaVersion = $Matches[1]
            $cudaParts   = $cudaVersion.Split(".")
            $cudaMajMin  = [double]($cudaParts[0] + "." + $cudaParts[1])
            if ($cudaMajMin -ge 11.8) { $gpuMode = "cuda" }
        }
        Write-Host "  CUDA: $cudaVersion, mode: $gpuMode"
    }
} catch {
    Write-Host "  No NVIDIA GPU detected - CPU mode"
}

$gpuLayersVal = 0
if ($gpuMode -eq "cuda") { $gpuLayersVal = 99 }

$ramObj = Get-CimInstance Win32_ComputerSystem
$ramGb  = [math]::Round($ramObj.TotalPhysicalMemory / 1GB, 1)

$hardwareData = [ordered]@{
    cpu_arch      = $cpuArch
    cpu_cores     = [int]($cpuCount / 2)
    cpu_threads   = [int]$cpuCount
    ram_gb        = $ramGb
    gpu_available = ($gpuMode -eq "cuda")
    gpu_name      = $gpuName
    gpu_vram_mb   = $gpuVram
    gpu_mode      = $gpuMode
    cuda_version  = $cudaVersion
    quant_type    = $quantType
    gpu_layers    = $gpuLayersVal
}
$hardwareJson = $hardwareData | ConvertTo-Json -Depth 5
New-Item -ItemType Directory -Force -Path "$ROOT\config" | Out-Null
Write-NoBom -Path "$ROOT\config\hardware.json" -Content $hardwareJson
Write-Host "  Hardware config saved." -ForegroundColor Green

# ==============================================================================
# STEP 2: Prerequisites Check
# ==============================================================================
Write-Host ""
Write-Host "[2/7] Checking prerequisites..." -ForegroundColor Yellow

$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Host "  ERROR: Git not found in PATH." -ForegroundColor Red
    Write-Host "  Install from https://git-scm.com/download/win then re-run."
    exit 1
}
$gitVerRaw = & git --version
$gitVer    = $gitVerRaw -replace "git version ", ""
Write-Host "  Git OK: $gitVer"

# VS2022 is only needed if building BitNet from source.
# setup_env.py will try pre-built binaries first — VS is optional.
$hasVS   = $false
$vsPath  = $null
$vsWherePath = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (Test-Path $vsWherePath) {
    # -products * is required to detect Build Tools (not just full VS IDE)
    $vsPath = & $vsWherePath -products * -latest -property installationPath 2>$null
    if ($vsPath) {
        $hasVS = $true
        Write-Host "  Visual Studio / Build Tools OK: $vsPath"
    }
}
if (-not $hasVS) {
    Write-Host "  VS Build Tools not found - installing now..." -ForegroundColor Cyan
    Write-Host "  Downloading ~4 GB of components. This will take 15-30 minutes." -ForegroundColor Yellow

    New-Item -ItemType Directory -Force -Path "$ROOT\env" | Out-Null
    $vsBTUrl = "https://aka.ms/vs/17/release/vs_buildtools.exe"
    $vsBTExe = "$ROOT\env\vs_buildtools.exe"

    Write-Host "  Downloading VS Build Tools bootstrapper..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $vsBTUrl -OutFile $vsBTExe -UseBasicParsing

    Write-Host "  Running silent installer (this window will appear to hang - that is normal)..."
    $btArgs = "--quiet --wait --norestart --nocache" +
              " --add Microsoft.VisualStudio.Workload.VCTools" +
              " --add Microsoft.VisualStudio.Component.VC.Llvm.Clang" +
              " --add Microsoft.VisualStudio.Component.VC.Llvm.ClangToolset" +
              " --add Microsoft.VisualStudio.ComponentGroup.NativeDesktop.Llvm.Clang" +
              " --add Microsoft.VisualStudio.Component.VC.CMake.Project" +
              " --add Microsoft.VisualStudio.Component.Windows11SDK.22621" +
              " --includeRecommended"
    Start-Process -FilePath $vsBTExe -ArgumentList $btArgs -Wait -NoNewWindow
    Remove-Item $vsBTExe -Force -ErrorAction SilentlyContinue

    # Re-detect after install
    if (Test-Path $vsWherePath) {
        $vsPath = & $vsWherePath -latest -property installationPath 2>$null
        if ($vsPath) {
            $hasVS = $true
            Write-Host "  VS Build Tools installed: $vsPath" -ForegroundColor Green
        }
    }
    if (-not $hasVS) {
        Write-Host "  WARNING: VS Build Tools install may not have completed correctly." -ForegroundColor Yellow
    }
}

if ($hasVS) {
    $cmakeCmd = Get-Command cmake -ErrorAction SilentlyContinue
    if (-not $cmakeCmd) {
        $vsCmakeBin = Join-Path $vsPath "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
        $vsCmakeExe = Join-Path $vsCmakeBin "cmake.exe"
        if (Test-Path $vsCmakeExe) {
            $env:PATH = $vsCmakeBin + ";" + $env:PATH
            Write-Host "  CMake OK (from VS)"
        }
    } else {
        $cmakeVerLine = & cmake --version | Select-Object -First 1
        Write-Host "  CMake OK: $cmakeVerLine"
    }
}

$condaCmd = Get-Command conda -ErrorAction SilentlyContinue
if ($condaCmd) {
    Write-Host "  Conda detected - will use embedded Python venv instead"
}

# ==============================================================================
# STEP 3: Embedded Python Setup
# ==============================================================================
Write-Host ""
Write-Host "[3/7] Setting up embedded Python environment..." -ForegroundColor Yellow

# The embeddable Python zip does not include the venv stdlib module.
# We install pip directly into the embedded Python and use it as-is —
# the embedded package is already isolated (no system site-packages).
$envDir     = "$ROOT\env"
$embedDir   = "$envDir\python-embed"
$venvPython = "$embedDir\python.exe"
$venvPip    = "$embedDir\Scripts\pip.exe"

New-Item -ItemType Directory -Force -Path $embedDir | Out-Null

$pyZipUrl  = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
$pyZipFile = "$envDir\python-embed.zip"

if (Test-Path $venvPython) {
    Write-Host "  Embedded Python already present - skipping download."
} else {
    Write-Host "  Downloading Python 3.11.9 embeddable..."
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $pyZipUrl -OutFile $pyZipFile -UseBasicParsing
    Expand-Archive -Path $pyZipFile -DestinationPath $embedDir -Force
    Remove-Item $pyZipFile -Force
    Write-Host "  Python extracted."
}

# Enable import site so pip-installed packages are importable
$pthFile = Get-ChildItem "$embedDir\*.pth" -ErrorAction SilentlyContinue |
           Where-Object { $_.Name -match "python\d+\._pth" } |
           Select-Object -First 1
if (-not $pthFile) {
    $pthFile = Get-ChildItem "$embedDir\*._pth" -ErrorAction SilentlyContinue |
               Select-Object -First 1
}
if ($pthFile) {
    $pthContent = Get-Content $pthFile.FullName -Raw
    if ($pthContent -match "#import site") {
        $pthContent = $pthContent -replace "#import site", "import site"
        Set-Content -Path $pthFile.FullName -Value $pthContent -NoNewline
        $pthName = $pthFile.Name
        Write-Host "  Enabled import site in $pthName"
    }
}

# Bootstrap pip if not already installed
if (-not (Test-Path $venvPip)) {
    $getPipUrl  = "https://bootstrap.pypa.io/get-pip.py"
    $getPipFile = "$envDir\get-pip.py"
    Write-Host "  Downloading get-pip.py..."
    Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipFile -UseBasicParsing
    & $venvPython $getPipFile --no-warn-script-location 2>&1 | Out-Null
    Remove-Item $getPipFile -Force -ErrorAction SilentlyContinue
    Write-Host "  pip installed." -ForegroundColor Green
} else {
    Write-Host "  pip already present."
}

# ==============================================================================
# STEP 4: Python Dependencies
# ==============================================================================
Write-Host ""
Write-Host "[4/7] Installing Python dependencies..." -ForegroundColor Yellow

& $venvPip install --upgrade pip --quiet --no-warn-script-location 2>$null

$coreDeps = @(
    "pywin32",
    "pystray",
    "Pillow",
    "requests",
    "psutil",
    "pyperclip",
    "pygetwindow",
    "huggingface_hub",
    "transformers",
    "numpy",
    "pydantic",
    "rich",
    "click",
    "chromadb",
    "sentence-transformers",
    "poetry-core"  # required build backend for gguf-py (BitNet llama.cpp submodule)
)

if ($gpuMode -eq "cuda") {
    Write-Host "  Installing PyTorch with CUDA 12.1..."
    & $venvPip install torch --index-url https://download.pytorch.org/whl/cu121 --quiet --no-warn-script-location 2>$null
} else {
    Write-Host "  Installing PyTorch CPU..."
    & $venvPip install torch --index-url https://download.pytorch.org/whl/cpu --quiet --no-warn-script-location 2>$null
}

Write-Host "  Installing core packages..."
foreach ($dep in $coreDeps) {
    & $venvPip install $dep --quiet --no-warn-script-location 2>$null
    Write-Host "    $dep"
}
Write-Host "  All dependencies installed." -ForegroundColor Green

# Write a .pth file into embedded Python site-packages so 'import emberos' works
# without needing PYTHONPATH to be set externally (service, REPL, etc.)
$sitePkgsDir = "$embedDir\Lib\site-packages"
if (Test-Path $sitePkgsDir) {
    # Use .NET WriteAllText with explicit no-BOM UTF8 encoding.
    # PS5.1's Set-Content -Encoding UTF8 writes a BOM which Python's .pth processor rejects.
    $pthPath  = "$sitePkgsDir\emberos_root.pth"
    $noBomUtf8 = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($pthPath, $ROOT + "`n", $noBomUtf8)
    Write-Host "  emberos root path registered in site-packages." -ForegroundColor Green
}

# ==============================================================================
# STEP 5: Model Download
# ==============================================================================
Write-Host ""
Write-Host "[5/7] Downloading BitNet model..." -ForegroundColor Yellow

$modelsDir = "$ROOT\models\BitNet-b1.58-2B-4T"
$ggufFiles = @(Get-ChildItem "$modelsDir\*.gguf" -ErrorAction SilentlyContinue)

if ($ggufFiles.Count -gt 0) {
    $ggufCount = $ggufFiles.Count
    Write-Host "  Model already present ($ggufCount GGUF files). Skipping download."
} else {
    & $venvPython "$ROOT\scripts\download_model.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: Model download may have failed." -ForegroundColor Yellow
    } else {
        Write-Host "  Model download complete." -ForegroundColor Green
    }
}

$modelFile = "$modelsDir\ggml-model-$quantType.gguf"
if (-not (Test-Path $modelFile)) {
    $anyGguf = Get-ChildItem "$modelsDir\*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($anyGguf) {
        $ggufName = $anyGguf.Name
        Write-Host "  Using model: $ggufName"
        $cfgPath = "$ROOT\config\default.json"
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $cfg.model_path = "models/BitNet-b1.58-2B-4T/$ggufName"
        $cfgJson = $cfg | ConvertTo-Json -Depth 5
        Write-NoBom -Path $cfgPath -Content $cfgJson
    }
}

# ==============================================================================
# STEP 6: BitNet Build
# ==============================================================================
Write-Host ""
Write-Host "[6/7] Building BitNet inference server..." -ForegroundColor Yellow

$bitnetDir   = "$ROOT\bitnet"
$bitnetSrc   = "$bitnetDir\src"
$llamaServer = "$bitnetDir\llama-server.exe"

if (Test-Path $llamaServer) {
    Write-Host "  Binary already exists - skipping build."
} else {
    New-Item -ItemType Directory -Force -Path $bitnetDir | Out-Null

    if (-not (Test-Path "$bitnetSrc\.git")) {
        Write-Host "  Cloning BitNet..."
        & git clone --recursive https://github.com/microsoft/BitNet.git $bitnetSrc
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: Failed to clone BitNet." -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  BitNet already cloned."
    }

    $bitnetReqs = "$bitnetSrc\requirements.txt"
    if (Test-Path $bitnetReqs) {
        Write-Host "  Installing BitNet Python requirements..."
        & $venvPip install -r $bitnetReqs --quiet --no-warn-script-location 2>$null
    }

    if ($hasVS) {
        Write-Host "  Loading VS build environment..."
        $vsDevCmd = Join-Path $vsPath "Common7\Tools\VsDevCmd.bat"
        if (Test-Path $vsDevCmd) {
            Invoke-CmdScript $vsDevCmd "-arch=x64"
        }
    }

    Write-Host "  Running BitNet setup_env.py (downloads pre-built binaries or builds from source)..."
    # setup_env.py accepted flags: --model-dir, --quant-type (no --use-cuda flag exists)
    $setupArgs = @("$bitnetSrc\setup_env.py", "--model-dir", $modelsDir, "--quant-type", $quantType)

    Push-Location $bitnetSrc
    try {
        & $venvPython @setupArgs 2>&1 | ForEach-Object { Write-Host "    $_" }
    } finally {
        Pop-Location
    }

    $searchPaths = @(
        "$bitnetSrc\build\bin\Release",
        "$bitnetSrc\build\bin",
        "$bitnetSrc\build\Release\bin"
    )
    $buildBinDir = $null
    foreach ($sp in $searchPaths) {
        if (Test-Path "$sp\llama-server.exe") { $buildBinDir = $sp; break }
    }
    if (-not $buildBinDir) {
        $found = Get-ChildItem "$bitnetSrc\build" -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue |
                 Select-Object -First 1
        if ($found) { $buildBinDir = $found.DirectoryName }
    }

    if ($buildBinDir -and (Test-Path "$buildBinDir\llama-server.exe")) {
        Copy-Item "$buildBinDir\llama-server.exe" $llamaServer -Force
        # Copy required DLLs (ggml.dll, llama.dll) alongside the exe
        Get-ChildItem "$buildBinDir\*.dll" -ErrorAction SilentlyContinue | ForEach-Object {
            Copy-Item $_.FullName "$bitnetDir\$($_.Name)" -Force
        }
        Write-Host "  Copied llama-server.exe + DLLs" -ForegroundColor Green
        if (Test-Path "$buildBinDir\llama-cli.exe") {
            Copy-Item "$buildBinDir\llama-cli.exe" "$bitnetDir\llama-cli.exe" -Force
        }
        if (Test-Path "$buildBinDir\llama-quantize.exe") {
            Copy-Item "$buildBinDir\llama-quantize.exe" "$bitnetDir\llama-quantize.exe" -Force
        }
    } else {
        # Also check if setup_env.py placed the binary inside bitnetSrc directly
        $altBin = Get-ChildItem $bitnetSrc -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($altBin) {
            Copy-Item $altBin.FullName $llamaServer -Force
            Write-Host "  Copied llama-server.exe from $($altBin.DirectoryName)" -ForegroundColor Green
        } else {
            Write-Host "  WARNING: llama-server.exe not found in build output." -ForegroundColor Yellow
            if (-not $hasVS) {
                Write-Host "  Pre-built binary unavailable for this platform." -ForegroundColor Yellow
                Write-Host "  To build from source, install VS2022 with C++ Desktop workload:" -ForegroundColor Yellow
                Write-Host "  https://visualstudio.microsoft.com/downloads/" -ForegroundColor Yellow
            } else {
                Write-Host "  Check: $bitnetSrc\build" -ForegroundColor Yellow
            }
        }
    }
}

$hwRaw = Get-Content "$ROOT\config\hardware.json" -Raw | ConvertFrom-Json
$hwRaw | Add-Member -NotePropertyName "bitnet_binary" -NotePropertyValue "bitnet/llama-server.exe" -Force
$hwJson = $hwRaw | ConvertTo-Json -Depth 5
Write-NoBom -Path "$ROOT\config\hardware.json" -Content $hwJson

# ==============================================================================
# STEP 7: Finalize Config and Register Service
# ==============================================================================
Write-Host ""
Write-Host "[7/7] Finalizing configuration..." -ForegroundColor Yellow

$cfgPath = "$ROOT\config\default.json"
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfg.threads    = [int]$cpuCount
$cfg.gpu_mode   = $gpuMode
$cfg.quant_type = $quantType
$cfg.gpu_layers = $gpuLayersVal
$cfgFinal = $cfg | ConvertTo-Json -Depth 5
Write-NoBom -Path $cfgPath -Content $cfgFinal
Write-Host "  Config updated."

New-Item -ItemType Directory -Force -Path "$ROOT\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$ROOT\data"  | Out-Null

Write-Host "  Registering Windows Service..."
try {
    $env:PYTHONPATH = $ROOT
    & $venvPython -m emberos.service install 2>&1 | ForEach-Object { Write-Host "    $_" }
    Write-Host "  Windows Service registered." -ForegroundColor Green
} catch {
    Write-Host "  Note: Service registration requires Admin privileges." -ForegroundColor Yellow
    Write-Host "  Run as Admin: $venvPython -m emberos.service install"
}

# ==============================================================================
# Done
# ==============================================================================
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  EmberOS-Windows Setup Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Quick start:"
Write-Host "  Start service : python -m emberos.service" -ForegroundColor Cyan
Write-Host "  Terminal REPL : .\emberos.bat" -ForegroundColor Cyan
Write-Host "  GUI window    : .\emberos.bat gui" -ForegroundColor Cyan
Write-Host ""
$hwLine = "Hardware: $cpuArch, $cpuCount threads, $ramGb GB RAM"
Write-Host $hwLine
if ($gpuMode -eq "cuda") {
    $gpuLine = "GPU: $gpuName - $gpuVram MB VRAM - CUDA $cudaVersion"
    Write-Host $gpuLine -ForegroundColor Green
} else {
    Write-Host "Mode: CPU-only" -ForegroundColor Yellow
}
