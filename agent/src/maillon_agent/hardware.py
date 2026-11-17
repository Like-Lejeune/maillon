"""Mesure du matériel de la machine : GPU, processeur, mémoire, disque.

Fonctionne sous Linux et Windows. Le GPU est lu avec `nvidia-smi`, installé
avec le pilote NVIDIA ; sans lui, la machine est déclarée sans GPU.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

import psutil

MIB = 1024 * 1024
GIB = 1024 * 1024 * 1024

# Emplacements habituels de nvidia-smi sous Windows quand il n'est pas dans le PATH.
_WINDOWS_NVIDIA_SMI = (
    Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe",
    Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    / "NVIDIA Corporation"
    / "NVSMI"
    / "nvidia-smi.exe",
)


@dataclass
class Gpu:
    model: str
    vram_mb: int


@dataclass
class Hardware:
    os: str
    gpu_model: str | None
    vram_mb: int
    cpu_cores: int
    ram_mb: int
    disk_free_gb: int

    def as_payload(self) -> dict:
        return asdict(self)


def find_nvidia_smi() -> str | None:
    found = shutil.which("nvidia-smi")
    if found:
        return found
    if platform.system() == "Windows":
        for candidate in _WINDOWS_NVIDIA_SMI:
            if candidate.is_file():
                return str(candidate)
    return None


def parse_nvidia_smi(output: str) -> list[Gpu]:
    """Lit la sortie de `nvidia-smi --query-gpu=name,memory.total --format=csv,noheader,nounits`."""
    gpus = []
    for line in output.splitlines():
        if not line.strip():
            continue
        name, _, memory = line.rpartition(",")
        try:
            vram_mb = int(float(memory.strip()))
        except ValueError:
            continue
        if name.strip():
            gpus.append(Gpu(model=name.strip(), vram_mb=vram_mb))
    return gpus


def detect_gpus() -> list[Gpu]:
    executable = find_nvidia_smi()
    if executable is None:
        return []
    try:
        result = subprocess.run(
            [executable, "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return parse_nvidia_smi(result.stdout)


def describe_os() -> str:
    system = platform.system()
    if system == "Linux":
        try:
            info = platform.freedesktop_os_release()
            return info.get("PRETTY_NAME") or f"Linux {platform.release()}"
        except OSError:
            return f"Linux {platform.release()}"
    if system == "Windows":
        return f"Windows {platform.release()} ({platform.version()})"
    return f"{system} {platform.release()}".strip()


def detect(data_dir: Path) -> Hardware:
    """Mesure la machine. `data_dir` est le dossier où seront stockés les modèles :
    c'est l'espace libre de ce disque-là qui compte."""
    gpus = detect_gpus()
    # Une tâche à la fois et un modèle par GPU : on retient le plus gros.
    best = max(gpus, key=lambda gpu: gpu.vram_mb, default=None)
    data_dir.mkdir(parents=True, exist_ok=True)
    return Hardware(
        os=describe_os()[:120],
        gpu_model=best.model[:200] if best else None,
        vram_mb=best.vram_mb if best else 0,
        cpu_cores=psutil.cpu_count(logical=True) or os.cpu_count() or 1,
        ram_mb=psutil.virtual_memory().total // MIB,
        disk_free_gb=shutil.disk_usage(data_dir).free // GIB,
    )
