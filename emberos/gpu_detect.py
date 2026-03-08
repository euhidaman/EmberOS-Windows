"""GPU and CPU hardware detection for EmberOS-Windows."""

import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path

from emberos.config import ROOT_DIR, load_config, save_hardware, Config


@dataclass
class HardwareProfile:
    cpu_arch: str = ""
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_gb: float = 0.0
    gpu_available: bool = False
    gpu_name: str = ""
    gpu_vram_mb: int = 0
    gpu_mode: str = "cpu"
    cuda_version: str = ""


def detect_cpu() -> dict:
    """Detect CPU architecture and core counts."""
    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64", "x64"):
        cpu_arch = "x86_64"
    elif arch in ("arm64", "aarch64"):
        cpu_arch = "arm64"
    else:
        cpu_arch = arch

    cpu_cores = os.cpu_count() or 4
    # Physical cores estimate (logical / 2 for hyper-threaded)
    physical = cpu_cores // 2 if cpu_cores > 1 else 1

    return {
        "cpu_arch": cpu_arch,
        "cpu_cores": physical,
        "cpu_threads": cpu_cores,
    }


def detect_ram() -> float:
    """Detect total RAM in GB."""
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        # Fallback via wmic
        try:
            result = subprocess.run(
                ["wmic", "computersystem", "get", "TotalPhysicalMemory"],
                capture_output=True, text=True, timeout=10
            )
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.isdigit():
                    return round(int(line) / (1024 ** 3), 1)
        except Exception:
            pass
    return 0.0


def detect_nvidia_gpu() -> dict:
    """Detect NVIDIA GPU using nvidia-smi."""
    info = {"gpu_available": False, "gpu_name": "", "gpu_vram_mb": 0, "cuda_version": ""}
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split(",")
            if len(parts) >= 2:
                info["gpu_available"] = True
                info["gpu_name"] = parts[0].strip()
                try:
                    info["gpu_vram_mb"] = int(float(parts[1].strip()))
                except ValueError:
                    pass

        # Get CUDA version
        result2 = subprocess.run(
            ["nvidia-smi"], capture_output=True, text=True, timeout=15
        )
        if result2.returncode == 0:
            for line in result2.stdout.split("\n"):
                if "CUDA Version" in line:
                    idx = line.index("CUDA Version:")
                    ver = line[idx + 14:].strip().split()[0]
                    info["cuda_version"] = ver
                    break
    except FileNotFoundError:
        pass
    except Exception:
        pass
    return info


def detect_all_gpus() -> list:
    """List all GPUs via wmic."""
    gpus = []
    try:
        result = subprocess.run(
            ["wmic", "path", "win32_VideoController", "get", "name"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            for line in result.stdout.strip().split("\n")[1:]:
                name = line.strip()
                if name:
                    gpus.append(name)
    except Exception:
        pass
    return gpus


def detect_hardware() -> HardwareProfile:
    """Run full hardware detection and return a HardwareProfile."""
    cpu = detect_cpu()
    ram = detect_ram()
    nvidia = detect_nvidia_gpu()

    gpu_mode = "cpu"
    if nvidia["gpu_available"]:
        cuda_ver = nvidia["cuda_version"]
        if cuda_ver:
            try:
                major_minor = float(cuda_ver.split(".")[0] + "." + cuda_ver.split(".")[1])
                if major_minor >= 11.8:
                    gpu_mode = "cuda"
            except (ValueError, IndexError):
                pass

    quant_type = "tl1" if cpu["cpu_arch"] == "arm64" else "i2_s"

    return HardwareProfile(
        cpu_arch=cpu["cpu_arch"],
        cpu_cores=cpu["cpu_cores"],
        cpu_threads=cpu["cpu_threads"],
        ram_gb=ram,
        gpu_available=nvidia["gpu_available"],
        gpu_name=nvidia["gpu_name"],
        gpu_vram_mb=nvidia["gpu_vram_mb"],
        gpu_mode=gpu_mode,
        cuda_version=nvidia["cuda_version"],
    )


def write_hardware_config(profile: HardwareProfile) -> None:
    """Write hardware profile into the config system."""
    cfg = load_config()
    cfg.cpu_arch = profile.cpu_arch
    cfg.cpu_cores = profile.cpu_cores
    cfg.cpu_threads = profile.cpu_threads
    cfg.ram_gb = profile.ram_gb
    cfg.gpu_available = profile.gpu_available
    cfg.gpu_name = profile.gpu_name
    cfg.gpu_vram_mb = profile.gpu_vram_mb
    cfg.gpu_mode = profile.gpu_mode
    cfg.cuda_version = profile.cuda_version

    quant_type = "tl1" if profile.cpu_arch == "arm64" else "i2_s"
    cfg.quant_type = quant_type
    cfg.gpu_layers = 99 if profile.gpu_mode == "cuda" else 0

    save_hardware(cfg)
    return cfg


if __name__ == "__main__":
    profile = detect_hardware()
    print(f"CPU: {profile.cpu_arch}, {profile.cpu_cores}C/{profile.cpu_threads}T")
    print(f"RAM: {profile.ram_gb} GB")
    print(f"GPU: {profile.gpu_name or 'None'} ({profile.gpu_vram_mb} MB)")
    print(f"GPU mode: {profile.gpu_mode}")
    print(f"CUDA: {profile.cuda_version or 'N/A'}")
    write_hardware_config(profile)
    print("Hardware config saved.")
