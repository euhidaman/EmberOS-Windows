"""Configuration loader and writer for EmberOS-Windows."""

import json
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

try:
    import tomllib
except ImportError:
    tomllib = None


def _get_root() -> Path:
    """Return the EmberOS-Windows root directory."""
    return Path(__file__).resolve().parent.parent


ROOT_DIR = _get_root()


@dataclass
class Config:
    model_path: str = "models/BitNet-b1.58-2B-4T/ggml-model-i2_s.gguf"
    server_host: str = "127.0.0.1"
    server_port: int = 8765
    context_size: int = 4096
    threads: int = 0
    gpu_layers: int = 0
    temperature: float = 0.7
    max_tokens: int = 512
    system_prompt: str = (
        "You are EmberOS, an AI agent layer running on this Windows machine. "
        "You can observe system context (active window, clipboard), execute tools, "
        "manage files, run shell commands, and assist the user with any task. "
        "Be concise, practical, and proactive."
    )
    memory_db_path: str = "data/ember.db"
    vector_store_path: str = "data/vectors"
    sentence_transformer_cache: str = "data/models/sentence_transformer"
    max_context_tokens: int = 3500
    summarize_after_turns: int = 10
    turns_to_keep_verbatim: int = 6
    max_total_conversations: int = 10000
    log_file: str = "logs/emberos.log"
    service_name: str = "EmberOSAgent"
    tray_autostart: bool = True
    gpu_mode: str = "cpu"
    quant_type: str = "i2_s"
    agent_api_port: int = 8766

    # GUI settings
    theme: str = "dark"
    gui_geometry: str = "900x700+100+100"
    gui_opacity: float = 0.95
    gui_font_size: int = 11

    # Agent behaviour
    confirm_destructive: bool = True
    snapshot_retention_days: int = 7

    # Hardware fields (populated from hardware.json)
    cpu_arch: str = ""
    cpu_cores: int = 0
    cpu_threads: int = 0
    ram_gb: float = 0.0
    gpu_available: bool = False
    gpu_name: str = ""
    gpu_vram_mb: int = 0
    cuda_version: str = ""

    def resolve_path(self, rel: str) -> Path:
        """Resolve a relative path against the EmberOS root."""
        p = Path(rel)
        if p.is_absolute():
            return p
        return ROOT_DIR / rel

    @property
    def abs_model_path(self) -> Path:
        return self.resolve_path(self.model_path)

    @property
    def abs_memory_db_path(self) -> Path:
        return self.resolve_path(self.memory_db_path)

    @property
    def abs_vector_store_path(self) -> Path:
        return self.resolve_path(self.vector_store_path)

    @property
    def abs_sentence_transformer_cache(self) -> Path:
        return self.resolve_path(self.sentence_transformer_cache)

    @property
    def abs_log_file(self) -> Path:
        return self.resolve_path(self.log_file)


def load_config() -> Config:
    """Load config from default.json, emberos.toml, and hardware.json, merging them."""
    cfg = Config()

    # Auto-detect threads
    cpu_count = os.cpu_count() or 4
    cfg.threads = cpu_count
    cfg.cpu_threads = cpu_count

    default_path = ROOT_DIR / "config" / "default.json"
    if default_path.exists():
        with open(default_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _apply_dict(cfg, data)

    # Load TOML (overrides JSON defaults)
    toml_path = ROOT_DIR / "config" / "emberos.toml"
    if toml_path.exists() and tomllib is not None:
        with open(toml_path, "rb") as f:
            toml_data = tomllib.load(f)
        _apply_toml(cfg, toml_data)

    hardware_path = ROOT_DIR / "config" / "hardware.json"
    if hardware_path.exists():
        with open(hardware_path, "r", encoding="utf-8") as f:
            hw = json.load(f)
        _apply_dict(cfg, hw)

    # Re-resolve auto threads
    if cfg.threads == 0:
        cfg.threads = cpu_count

    return cfg


def save_config(cfg: Config) -> None:
    """Save config back to default.json."""
    default_path = ROOT_DIR / "config" / "default.json"
    default_path.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(cfg)
    # Remove hardware-only fields from default config
    hw_fields = {
        "cpu_arch", "cpu_cores", "cpu_threads", "ram_gb",
        "gpu_available", "gpu_name", "gpu_vram_mb", "cuda_version",
    }
    default_data = {k: v for k, v in data.items() if k not in hw_fields}
    with open(default_path, "w", encoding="utf-8") as f:
        json.dump(default_data, f, indent=2)


def save_hardware(cfg: Config) -> None:
    """Save hardware profile to hardware.json."""
    hw_path = ROOT_DIR / "config" / "hardware.json"
    hw_path.parent.mkdir(parents=True, exist_ok=True)
    hw_fields = {
        "cpu_arch", "cpu_cores", "cpu_threads", "ram_gb",
        "gpu_available", "gpu_name", "gpu_vram_mb", "cuda_version",
        "gpu_mode", "quant_type", "gpu_layers",
    }
    data = {k: v for k, v in asdict(cfg).items() if k in hw_fields}
    with open(hw_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def _apply_dict(cfg: Config, data: dict) -> None:
    """Apply dictionary values to a Config, skipping unknown keys."""
    for k, v in data.items():
        if k == "threads" and v == "auto":
            continue  # handled separately
        if hasattr(cfg, k):
            expected_type = type(getattr(cfg, k))
            try:
                if expected_type is int and isinstance(v, str):
                    v = int(v)
                elif expected_type is float and isinstance(v, str):
                    v = float(v)
                elif expected_type is bool and isinstance(v, str):
                    v = v.lower() in ("true", "1", "yes")
                setattr(cfg, k, v)
            except (ValueError, TypeError):
                pass


_TOML_FIELD_MAP = {
    ("gui", "theme"): "theme",
    ("gui", "opacity"): "gui_opacity",
    ("gui", "window_width"): None,
    ("gui", "window_height"): None,
    ("gui", "font_size"): "gui_font_size",
    ("llm", "server_url"): None,
    ("llm", "model_name"): None,
    ("agent", "confirm_destructive"): "confirm_destructive",
    ("agent", "snapshot_retention_days"): "snapshot_retention_days",
}


def _apply_toml(cfg: Config, toml_data: dict) -> None:
    """Apply TOML sections to Config. TOML values override JSON defaults."""
    for section_name, section in toml_data.items():
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            mapped = _TOML_FIELD_MAP.get((section_name, key))
            if mapped and hasattr(cfg, mapped):
                setattr(cfg, mapped, value)
            elif hasattr(cfg, key):
                setattr(cfg, key, value)
