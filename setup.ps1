#Requires -Version 5.1
<#
.SYNOPSIS
    EmberOS-Windows Master Setup Script.
    Run this once to set up the entire environment: embedded Python, dependencies,
    BitNet build, model download, and configuration.
#>

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ROOT = Split-Path -Parent $MyInvocation.MyCommand.Definition

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  EmberOS-Windows Setup" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""

# ── Helper: invoke a .bat and import its env vars ────────────────
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

# ══════════════════════════════════════════════════════════════════
# STEP 1: CPU and GPU Detection
# ══════════════════════════════════════════════════════════════════
Write-Host "[1/7] Detecting hardware..." -ForegroundColor Yellow

$cpuArch = if ([System.Environment]::Is64BitOperatingSystem) {
    $arch = $env:PROCESSOR_ARCHITECTURE
    if ($arch -eq "ARM64") { "arm64" } else { "x86_64" }
} else { "x86" }

$cpuCount = (Get-CimInstance Win32_Processor | Measure-Object -Property NumberOfLogicalProcessors -Sum).Sum
if (-not $cpuCount) { $cpuCount = $env:NUMBER_OF_PROCESSORS }
$quantType = if ($cpuArch -eq "arm64") { "tl1" } else { "i2_s" }

Write-Host "  CPU Architecture: $cpuArch"
Write-Host "  Logical cores: $cpuCount"
Write-Host "  Quantization type: $quantType"

# GPU Detection
$gpuMode = "cpu"
$gpuName = ""
$gpuVram = 0
$cudaVersion = ""

try {
    $nvsmi = & nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv,noheader,nounits 2>$null
    if ($LASTEXITCODE -eq 0 -and $nvsmi) {
        $parts = $nvsmi.Split(",")
        $gpuName = $parts[0].Trim()
        $gpuVram = [int]($parts[1].Trim())
        Write-Host "  NVIDIA GPU: $gpuName ($gpuVram MB)"

        # Get CUDA version
        $nvsmiAll = & nvidia-smi 2>$null
        $cudaLine = $nvsmiAll | Where-Object { $_ -match "CUDA Version" }
        if ($cudaLine) {
            if ($cudaLine -match "CUDA Version:\s*([\d.]+)") {
                $cudaVersion = $Matches[1]
                $cudaMajorMinor = [double]($cudaVersion.Split(".")[0] + "." + $cudaVersion.Split(".")[1])
                if ($cudaMajorMinor -ge 11.8) {
                    $gpuMode = "cuda"
                }
            }
        }
        Write-Host "  CUDA Version: $cudaVersion"
        Write-Host "  GPU Mode: $gpuMode"
    }
} catch {
    Write-Host "  No NVIDIA GPU found — using CPU mode"
}

# Save hardware config
$hardwareConfig = @{
    cpu_arch     = $cpuArch
    cpu_cores    = [int]($cpuCount / 2)
    cpu_threads  = [int]$cpuCount
    ram_gb       = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)
    gpu_available = ($gpuMode -eq "cuda")
    gpu_name     = $gpuName
    gpu_vram_mb  = $gpuVram
    gpu_mode     = $gpuMode
    cuda_version = $cudaVersion
    quant_type   = $quantType
    gpu_layers   = if ($gpuMode -eq "cuda") { 99 } else { 0 }
} | ConvertTo-Json -Depth 5

New-Item -ItemType Directory -Force -Path "$ROOT\config" | Out-Null
Set-Content -Path "$ROOT\config\hardware.json" -Value $hardwareConfig -Encoding UTF8
Write-Host "  Hardware config saved to config\hardware.json" -ForegroundColor Green

# ══════════════════════════════════════════════════════════════════
# STEP 2: Prerequisites Check
# ══════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[2/7] Checking prerequisites..." -ForegroundColor Yellow

# Git
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if (-not $gitCmd) {
    Write-Host "  ERROR: Git is not installed or not in PATH." -ForegroundColor Red
    Write-Host "  Install Git from https://git-scm.com/download/win and re-run this script."
    exit 1
}
Write-Host "  Git: OK ($((& git --version) -replace 'git version ',''))"

# Visual Studio 2022
$vsWherePath = "C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path $vsWherePath)) {
    Write-Host "  ERROR: Visual Studio 2022 not found." -ForegroundColor Red
    Write-Host "  Install Visual Studio 2022 with the following workloads:"
    Write-Host "    - Desktop development with C++"
    Write-Host "    - C++ CMake Tools for Windows"
    Write-Host "    - C++ Clang Compiler for Windows"
    Write-Host "    - MS-Build support for LLVM toolset"
    Write-Host "  Download from: https://visualstudio.microsoft.com/downloads/"
    exit 1
}
$vsPath = & $vsWherePath -latest -property installationPath
Write-Host "  Visual Studio: OK ($vsPath)"

# CMake
$cmakeCmd = Get-Command cmake -ErrorAction SilentlyContinue
if (-not $cmakeCmd) {
    # Check VS cmake
    $vsCmake = Join-Path $vsPath "Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe"
    if (Test-Path $vsCmake) {
        $env:PATH = (Split-Path $vsCmake) + ";" + $env:PATH
        Write-Host "  CMake: OK (from VS installation)"
    } else {
        Write-Host "  ERROR: CMake >= 3.22 not found." -ForegroundColor Red
        Write-Host "  Install CMake from https://cmake.org/download/ or ensure it's in PATH."
        exit 1
    }
} else {
    Write-Host "  CMake: OK ($(& cmake --version | Select-Object -First 1))"
}

# Conda check (informational only)
$condaCmd = Get-Command conda -ErrorAction SilentlyContinue
if ($condaCmd) {
    Write-Host "  Conda detected but will NOT be used — using embedded Python venv instead."
}

# ══════════════════════════════════════════════════════════════════
# STEP 3: Embedded Python Setup
# ══════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[3/7] Setting up embedded Python environment..." -ForegroundColor Yellow

$envDir = "$ROOT\env"
$embedDir = "$envDir\python-embed"
$venvDir = "$envDir\venv"
$venvPython = "$venvDir\Scripts\python.exe"
$venvPip = "$venvDir\Scripts\pip.exe"

if (Test-Path $venvPython) {
    Write-Host "  Venv already exists at $venvDir — skipping Python setup."
} else {
    New-Item -ItemType Directory -Force -Path $embedDir | Out-Null

    # Download embeddable Python
    $pyZipUrl = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
    $pyZipFile = "$envDir\python-embed.zip"

    if (-not (Test-Path "$embedDir\python.exe")) {
        Write-Host "  Downloading Python 3.11.9 embeddable..."
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -Uri $pyZipUrl -OutFile $pyZipFile -UseBasicParsing
        Expand-Archive -Path $pyZipFile -DestinationPath $embedDir -Force
        Remove-Item $pyZipFile -Force
        Write-Host "  Python extracted to $embedDir"
    }

    # Enable import site in ._pth file
    $pthFile = Get-ChildItem "$embedDir\*.pth" | Where-Object { $_.Name -match "python\d+\._pth" } | Select-Object -First 1
    if (-not $pthFile) {
        $pthFile = Get-ChildItem "$embedDir\*._pth" | Select-Object -First 1
    }
    if ($pthFile) {
        $content = Get-Content $pthFile.FullName -Raw
        $content = $content -replace "#import site", "import site"
        Set-Content -Path $pthFile.FullName -Value $content -NoNewline
        Write-Host "  Enabled 'import site' in $($pthFile.Name)"
    }

    # Install pip
    $getPipUrl = "https://bootstrap.pypa.io/get-pip.py"
    $getPipFile = "$envDir\get-pip.py"
    Write-Host "  Downloading get-pip.py..."
    Invoke-WebRequest -Uri $getPipUrl -OutFile $getPipFile -UseBasicParsing
    & "$embedDir\python.exe" $getPipFile --no-warn-script-location 2>&1 | Out-Null
    Write-Host "  pip installed into embedded Python"

    # Create venv
    Write-Host "  Creating virtual environment..."
    & "$embedDir\python.exe" -m venv $venvDir
    if (-not (Test-Path $venvPython)) {
        Write-Host "  ERROR: Failed to create venv at $venvDir" -ForegroundColor Red
        exit 1
    }
    Write-Host "  Venv created at $venvDir" -ForegroundColor Green
}

# ══════════════════════════════════════════════════════════════════
# STEP 4: Python Dependencies
# ══════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[4/7] Installing Python dependencies..." -ForegroundColor Yellow

# Upgrade pip first
& $venvPip install --upgrade pip --quiet 2>&1 | Out-Null

# Core dependencies
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
    "sentence-transformers"
)

# Install torch based on GPU mode
if ($gpuMode -eq "cuda") {
    Write-Host "  Installing PyTorch with CUDA 12.1 support..."
    & $venvPip install torch --index-url https://download.pytorch.org/whl/cu121 --quiet 2>&1 | Out-Null
} else {
    Write-Host "  Installing PyTorch (CPU)..."
    & $venvPip install torch --index-url https://download.pytorch.org/whl/cpu --quiet 2>&1 | Out-Null
}

Write-Host "  Installing core packages..."
foreach ($dep in $coreDeps) {
    & $venvPip install $dep --quiet 2>&1 | Out-Null
}
Write-Host "  All Python dependencies installed" -ForegroundColor Green

# ══════════════════════════════════════════════════════════════════
# STEP 5: Model Download
# ══════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[5/7] Downloading BitNet model (no HF token needed)..." -ForegroundColor Yellow

$modelsDir = "$ROOT\models\BitNet-b1.58-2B-4T"
$ggufCheck = Get-ChildItem "$modelsDir\*.gguf" -ErrorAction SilentlyContinue

if ($ggufCheck) {
    Write-Host "  Model already downloaded — found $($ggufCheck.Count) GGUF file(s). Skipping."
} else {
    & $venvPython "$ROOT\scripts\download_model.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  WARNING: Model download may have failed. Check above output." -ForegroundColor Red
    } else {
        Write-Host "  Model download complete" -ForegroundColor Green
    }
}

# Verify model file
$modelFile = "$modelsDir\ggml-model-$quantType.gguf"
if (-not (Test-Path $modelFile)) {
    # Try to find any gguf file
    $anyGguf = Get-ChildItem "$modelsDir\*.gguf" -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($anyGguf) {
        Write-Host "  Note: Expected $modelFile but found $($anyGguf.Name)"
        Write-Host "  Updating config to use $($anyGguf.Name)"
        # Update default.json with actual model filename
        $cfgPath = "$ROOT\config\default.json"
        $cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
        $cfg.model_path = "models/BitNet-b1.58-2B-4T/$($anyGguf.Name)"
        $cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPath -Encoding UTF8
    }
}

# ══════════════════════════════════════════════════════════════════
# STEP 6: BitNet Build
# ══════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[6/7] Building BitNet inference server..." -ForegroundColor Yellow

$bitnetDir = "$ROOT\bitnet"
$bitnetSrc = "$bitnetDir\src"
$llamaServer = "$bitnetDir\llama-server.exe"

if (Test-Path $llamaServer) {
    Write-Host "  BitNet server binary already exists — skipping build."
} else {
    New-Item -ItemType Directory -Force -Path $bitnetDir | Out-Null

    # Clone BitNet
    if (-not (Test-Path "$bitnetSrc\.git")) {
        Write-Host "  Cloning BitNet repository..."
        & git clone --recursive https://github.com/microsoft/BitNet.git $bitnetSrc
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  ERROR: Failed to clone BitNet." -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  BitNet source already cloned."
    }

    # Install BitNet Python requirements
    $bitnetReqs = "$bitnetSrc\requirements.txt"
    if (Test-Path $bitnetReqs) {
        Write-Host "  Installing BitNet Python requirements..."
        & $venvPip install -r $bitnetReqs --quiet 2>&1 | Out-Null
    }

    # Load VS2022 Developer Environment
    Write-Host "  Loading VS2022 build environment..."
    $vsDevCmd = Join-Path $vsPath "Common7\Tools\VsDevCmd.bat"
    if (Test-Path $vsDevCmd) {
        Invoke-CmdScript $vsDevCmd "-arch=x64"
    }

    # Run BitNet setup_env.py to build
    Write-Host "  Building BitNet (this may take several minutes)..."

    $setupArgs = @(
        "$bitnetSrc\setup_env.py"
        "-md", "$modelsDir"
        "-q", $quantType
    )

    if ($gpuMode -eq "cuda") {
        $setupArgs += "--use-cuda"
    }

    Push-Location $bitnetSrc
    try {
        & $venvPython @setupArgs 2>&1 | ForEach-Object { Write-Host "    $_" }
    } finally {
        Pop-Location
    }

    # Find and copy the built binaries
    # On Windows with VS/CMake multi-config, binaries go to build/bin/Release/
    $searchPaths = @(
        "$bitnetSrc\build\bin\Release",
        "$bitnetSrc\build\bin",
        "$bitnetSrc\build\Release\bin"
    )
    $buildBinDir = $null
    foreach ($sp in $searchPaths) {
        if (Test-Path "$sp\llama-server.exe") {
            $buildBinDir = $sp
            break
        }
    }

    # Fallback: recursive search
    if (-not $buildBinDir) {
        $found = Get-ChildItem "$bitnetSrc\build" -Recurse -Filter "llama-server.exe" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            $buildBinDir = $found.DirectoryName
        }
    }

    if ($buildBinDir -and (Test-Path "$buildBinDir\llama-server.exe")) {
        Copy-Item "$buildBinDir\llama-server.exe" $llamaServer -Force
        Write-Host "  Copied llama-server.exe to $llamaServer" -ForegroundColor Green

        if (Test-Path "$buildBinDir\llama-cli.exe") {
            Copy-Item "$buildBinDir\llama-cli.exe" "$bitnetDir\llama-cli.exe" -Force
            Write-Host "  Copied llama-cli.exe"
        }
        if (Test-Path "$buildBinDir\llama-quantize.exe") {
            Copy-Item "$buildBinDir\llama-quantize.exe" "$bitnetDir\llama-quantize.exe" -Force
            Write-Host "  Copied llama-quantize.exe"
        }
    } else {
        Write-Host "  WARNING: Could not find llama-server.exe in build output." -ForegroundColor Red
        Write-Host "  Check $bitnetSrc\build\ for build artifacts."
        Write-Host "  Common cause: Missing VS2022 C++ Clang tools or CMake version too old."
    }
}

# Update hardware.json with binary path
$hwCfg = Get-Content "$ROOT\config\hardware.json" -Raw | ConvertFrom-Json
$hwCfg | Add-Member -NotePropertyName "bitnet_binary" -NotePropertyValue "bitnet/llama-server.exe" -Force
$hwCfg | ConvertTo-Json -Depth 5 | Set-Content "$ROOT\config\hardware.json" -Encoding UTF8

# ══════════════════════════════════════════════════════════════════
# STEP 7: Finalize Config and Register Service
# ══════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "[7/7] Finalizing configuration..." -ForegroundColor Yellow

# Update default.json with detected values
$cfgPath = "$ROOT\config\default.json"
$cfg = Get-Content $cfgPath -Raw | ConvertFrom-Json
$cfg.threads = [int]$cpuCount
$cfg.gpu_mode = $gpuMode
$cfg.quant_type = $quantType
$cfg.gpu_layers = if ($gpuMode -eq "cuda") { 99 } else { 0 }
$cfg | ConvertTo-Json -Depth 5 | Set-Content $cfgPath -Encoding UTF8
Write-Host "  Config updated"

# Create runtime directories
New-Item -ItemType Directory -Force -Path "$ROOT\logs" | Out-Null
New-Item -ItemType Directory -Force -Path "$ROOT\data" | Out-Null

# Register Windows Service
Write-Host "  Registering Windows Service..."
try {
    & $venvPython -m emberos.service install 2>&1 | ForEach-Object { Write-Host "    $_" }
    Write-Host "  Windows Service registered" -ForegroundColor Green
} catch {
    Write-Host "  Note: Service registration may require Administrator privileges." -ForegroundColor Yellow
    Write-Host "  Run as Admin later: $venvPython -m emberos.service install"
}

# ══════════════════════════════════════════════════════════════════
# Done
# ══════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  EmberOS-Windows Setup Complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "To launch EmberOS:"
Write-Host "  .\launch.ps1" -ForegroundColor Cyan
Write-Host ""
Write-Host "Or use the CLI:"
Write-Host "  .\emberos.bat chat" -ForegroundColor Cyan
Write-Host "  .\emberos.bat status" -ForegroundColor Cyan
Write-Host ""
Write-Host "Hardware: $cpuArch, $cpuCount threads, $([math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 1)) GB RAM"
if ($gpuMode -eq "cuda") {
    Write-Host "GPU: $gpuName ($gpuVram MB VRAM, CUDA $cudaVersion)" -ForegroundColor Green
} else {
    Write-Host "GPU: CPU-only mode" -ForegroundColor Yellow
}
