"""Load and merge configuration: defaults, game profile, user overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.path.abspath(__file__)).parents[2]
DEFAULT_CONFIG = ROOT / "config" / "default.yaml"
PROFILES_DIR = ROOT / "profiles"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _read_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def resolve_profile(profile: str) -> Path:
    """Accept a profile name ('bf2042') or a path to a YAML file."""
    path = Path(profile)
    if path.suffix in (".yaml", ".yml") and path.exists():
        return path
    candidate = PROFILES_DIR / f"{profile}.yaml"
    if not candidate.exists():
        available = ", ".join(p.stem for p in PROFILES_DIR.glob("*.yaml"))
        raise FileNotFoundError(f"Unknown profile '{profile}'. Available: {available}")
    return candidate


def load_config(user_config: Path | None = None, profile: str | None = None) -> dict[str, Any]:
    cfg = _read_yaml(DEFAULT_CONFIG)
    if user_config:
        cfg = _deep_merge(cfg, _read_yaml(user_config))
    cfg["profile"] = _read_yaml(resolve_profile(profile)) if profile else {"name": "generic", "keywords": {}, "vision_hints": ""}
    return cfg
