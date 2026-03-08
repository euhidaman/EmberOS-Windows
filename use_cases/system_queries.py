"""System query functions for EmberOS-Windows."""

import logging
import subprocess
from datetime import datetime, timezone

import psutil

logger = logging.getLogger("emberos.use_cases.system_queries")


def get_disk_usage() -> str:
    lines = ["Drive    Size   Used   Free   Use%"]
    lines.append("-" * 42)
    main_free = ""
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except PermissionError:
            continue
        total = usage.total / (1024 ** 3)
        used = usage.used / (1024 ** 3)
        free = usage.free / (1024 ** 3)

        def _fmt(gb):
            if gb >= 1000:
                return f"{gb / 1024:.1f}T"
            return f"{gb:.0f}G"

        drive = part.mountpoint
        lines.append(f"{drive:<9}{_fmt(total):>5}  {_fmt(used):>5}  {_fmt(free):>5}   {usage.percent:.0f}%")
        if drive.upper().startswith("C"):
            main_free = f"\nYour main drive (C:) has {free:.0f}GB available."
    return "\n".join(lines) + main_free


def get_ram_status() -> str:
    vm = psutil.virtual_memory()
    total = vm.total / (1024 ** 3)
    used = vm.used / (1024 ** 3)
    avail = vm.available / (1024 ** 3)
    return (f"RAM: {used:.1f}GB used of {total:.1f}GB total ({vm.percent}% used). "
            f"{avail:.1f}GB available.")


def get_running_processes(filter_name: str = None) -> str:
    procs = []
    for p in psutil.process_iter(["name", "pid", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            if filter_name and filter_name.lower() not in (info["name"] or "").lower():
                continue
            procs.append(info)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    procs.sort(key=lambda x: x.get("cpu_percent") or 0, reverse=True)
    procs = procs[:15]

    lines = [f"{'PID':<8} {'Name':<30} {'CPU%':<8} {'RAM%':<8}"]
    lines.append("-" * 54)
    for p in procs:
        lines.append(
            f"{p['pid']:<8} {(p['name'] or '?'):<30} "
            f"{(p['cpu_percent'] or 0):<8.1f} {(p['memory_percent'] or 0):<8.1f}"
        )
    return "\n".join(lines)


def get_system_uptime() -> str:
    boot = datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc)
    delta = datetime.now(timezone.utc) - boot
    days = delta.days
    hours = delta.seconds // 3600
    minutes = (delta.seconds % 3600) // 60
    return f"System uptime: {days}d {hours}h {minutes}m"


def get_cpu_info() -> str:
    cpu_name = "Unknown"
    try:
        result = subprocess.run(
            ["wmic", "cpu", "get", "name"],
            capture_output=True, text=True, timeout=10,
        )
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line and line.lower() != "name":
                cpu_name = line
                break
    except Exception:
        pass

    freq = psutil.cpu_freq()
    per_cpu = psutil.cpu_percent(interval=1, percpu=True)
    cores = psutil.cpu_count(logical=False) or "?"
    threads = psutil.cpu_count(logical=True) or "?"
    current_freq = f"{freq.current:.0f}MHz" if freq else "N/A"

    lines = [
        f"CPU: {cpu_name}",
        f"Cores: {cores} physical, {threads} logical",
        f"Frequency: {current_freq}",
        f"Per-core usage: {', '.join(f'{p:.0f}%' for p in per_cpu)}",
        f"Average: {sum(per_cpu) / len(per_cpu):.1f}%",
    ]
    return "\n".join(lines)


def check_cpu_temperature() -> str:
    try:
        temps = psutil.sensors_temperatures()
        if temps:
            lines = []
            for name, entries in temps.items():
                for entry in entries:
                    label = entry.label or name
                    lines.append(f"{label}: {entry.current:.1f}°C")
            if lines:
                return "\n".join(lines)
    except (AttributeError, Exception):
        pass
    return ("CPU temperature monitoring is not available on this system. "
            "Use HWMonitor or HWiNFO for temperature readings.")
