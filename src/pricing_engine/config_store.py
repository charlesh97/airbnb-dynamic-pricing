"""Read/write property JSON configs from config/properties/."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_DIR = Path(__file__).parent.parent.parent / "config" / "properties"


class PropertyConfigStore:
    """Load and persist per-property JSON configs."""

    def __init__(self, config_dir: Path | str = DEFAULT_CONFIG_DIR):
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

    def load(self, property_uid: str) -> dict[str, Any]:
        """Load config for a property. Returns empty dict if not found."""
        path = self.config_dir / f"{property_uid}.json"
        if not path.exists():
            return {}
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, property_uid: str, config: dict[str, Any]) -> None:
        """Save config for a property."""
        self.config_dir.mkdir(parents=True, exist_ok=True)
        path = self.config_dir / f"{property_uid}.json"
        path.write_text(json.dumps(config, indent=2, ensure_ascii=False), encoding="utf-8")

    def list_properties(self) -> list[str]:
        """List all property UIDs with configs on disk."""
        if not self.config_dir.exists():
            return []
        return [p.stem for p in self.config_dir.glob("*.json")]

    def merge_with_env_defaults(self, property_uid: str, env_config: dict[str, Any]) -> dict[str, Any]:
        """Merge property JSON config with env-based EngineConfig defaults."""
        prop_config = self.load(property_uid)
        # Env config wins for top-level keys
        merged = {**prop_config}
        # Inject env defaults where property config is missing
        for key, value in env_config.items():
            if key not in merged and value is not None:
                merged[key] = value
        return merged