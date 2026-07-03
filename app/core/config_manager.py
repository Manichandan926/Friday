"""
config_manager.py — Configuration Manager for FRIDAY.

Provides a unified interface for configuration settings. 
Reads from environment variables and a local JSON/YAML config file.
Values can be overridden at runtime and persisted.

Architecture:
    ConfigManager → Environment + Local File + Runtime Overrides
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from app.core.logger import logger


class ConfigManager:
    """Manages system configurations with persistence.
    
    Order of precedence:
        1. Runtime overrides (in-memory)
        2. Config file (config.json)
        3. Environment variables
        4. Default values provided at get()
        
    Usage::
    
        config = ConfigManager(config_path="config.json")
        port = config.get("server.port", default=8000)
        config.set("server.port", 8080, persist=True)
    """

    _instance: Optional[ConfigManager] = None
    _lock = threading.Lock()

    def __init__(self, config_path: str = "config.json"):
        self._config_path = Path(config_path)
        self._config: Dict[str, Any] = {}
        self._overrides: Dict[str, Any] = {}
        self._cfg_lock = threading.Lock()
        
        self.reload()

    @classmethod
    def get_instance(cls, config_path: str = "config.json") -> ConfigManager:
        """Thread-safe singleton accessor."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(config_path)
        return cls._instance

    def reload(self) -> None:
        """Reload configuration from disk."""
        with self._cfg_lock:
            self._config.clear()
            if self._config_path.exists():
                try:
                    with open(self._config_path, "r", encoding="utf-8") as f:
                        self._config = json.load(f)
                    logger.debug(f"ConfigManager: Loaded config from {self._config_path}")
                except Exception as e:
                    logger.error(f"ConfigManager: Failed to load {self._config_path}: {e}")
            else:
                logger.debug(f"ConfigManager: Config file {self._config_path} not found")

    def _save(self) -> bool:
        """Save current _config dict to disk."""
        try:
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=4)
            return True
        except Exception as e:
            logger.error(f"ConfigManager: Failed to save {self._config_path}: {e}")
            return False

    def get(self, key: str, default: Any = None) -> Any:
        """Get a configuration value."""
        with self._cfg_lock:
            # 1. Runtime override
            if key in self._overrides:
                return self._overrides[key]
                
            # 2. Config file
            # Support dot-notation for nested dicts
            parts = key.split(".")
            current = self._config
            found = True
            for part in parts:
                if isinstance(current, dict) and part in current:
                    current = current[part]
                else:
                    found = False
                    break
                    
            if found:
                return current
                
            # 3. Environment variables (transform dot to underscore, uppercase)
            env_key = key.replace(".", "_").upper()
            if env_key in os.environ:
                val = os.environ[env_key]
                # Type cast if default is provided
                if default is not None:
                    if isinstance(default, bool):
                        return val.lower() in ("true", "1", "yes")
                    elif isinstance(default, int):
                        try: return int(val)
                        except ValueError: pass
                    elif isinstance(default, float):
                        try: return float(val)
                        except ValueError: pass
                return val

        # 4. Default
        return default

    def set(self, key: str, value: Any, persist: bool = False) -> None:
        """Set a configuration value.
        
        Args:
            key: Config key (supports dot notation, e.g. 'server.port')
            value: The value to set
            persist: If True, writes to the config file. Otherwise, stores as override.
        """
        with self._cfg_lock:
            if not persist:
                self._overrides[key] = value
                logger.debug(f"ConfigManager: Set override '{key}'")
                return
                
            # Persist mode
            parts = key.split(".")
            current = self._config
            for part in parts[:-1]:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}
                current = current[part]
                
            current[parts[-1]] = value
            self._save()
            
            # Remove from overrides if it existed
            self._overrides.pop(key, None)
            logger.debug(f"ConfigManager: Persisted '{key}'")

    def all(self) -> Dict[str, Any]:
        """Return a copy of the fully merged configuration."""
        with self._cfg_lock:
            # Note: This is a simplified merge, doesn't deeply merge overrides
            # but is sufficient for debugging/introspection.
            merged = dict(self._config)
            merged.update(self._overrides)
            return merged
