"""
secret_manager.py — Secret Manager for FRIDAY.

Safely stores and retrieves credentials, tokens, and API keys.
Prevents hardcoding secrets in .env or config files.

Architecture:
    Client → SecretManager.get("openai_key") → Decrypts from DB
"""

from __future__ import annotations

import base64
import json
import os
import threading
from typing import Dict, Optional
from cryptography.fernet import Fernet
from app.core.logger import logger


class SecretManager:
    """Manages encrypted secrets.
    
    Requires a master key. In production, this master key is injected
    via environment variable (FRIDAY_MASTER_KEY).
    """

    _instance: Optional[SecretManager] = None
    _lock = threading.Lock()

    def __init__(self, master_key: Optional[str] = None):
        self._master_key = master_key or os.environ.get("FRIDAY_MASTER_KEY")
        if not self._master_key:
            # For local dev, generate a transient key if none provided
            # Note: Secrets won't survive restarts without a stable master key
            self._master_key = Fernet.generate_key().decode()
            logger.warning("SecretManager: No FRIDAY_MASTER_KEY found, using transient key!")
            
        self._fernet = Fernet(self._master_key.encode())
        self._secrets: Dict[str, str] = {}  # In-memory cache of decrypted secrets
        self._sm_lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> SecretManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def set_secret(self, key: str, value: str) -> bool:
        """Encrypt and store a secret."""
        with self._sm_lock:
            try:
                encrypted = self._fernet.encrypt(value.encode()).decode()
                
                # In a full implementation, this saves to an encrypted SQLite table
                # For now, we simulate the database persistence via a local JSON file (simulated DB)
                self._persist_to_db(key, encrypted)
                self._secrets[key] = value
                logger.debug(f"SecretManager: securely stored '{key}'")
                return True
            except Exception as e:
                logger.error(f"SecretManager: failed to store '{key}': {e}")
                return False

    def get_secret(self, key: str) -> Optional[str]:
        """Retrieve and decrypt a secret."""
        with self._sm_lock:
            # Check cache
            if key in self._secrets:
                return self._secrets[key]
                
            # Fetch from DB
            encrypted = self._fetch_from_db(key)
            if not encrypted:
                return None
                
            try:
                decrypted = self._fernet.decrypt(encrypted.encode()).decode()
                self._secrets[key] = decrypted
                return decrypted
            except Exception as e:
                logger.error(f"SecretManager: failed to decrypt '{key}': {e}")
                return None

    # --- Simulated DB Operations (Replace with SQLAlchemy later) ---
    def _persist_to_db(self, key: str, encrypted_value: str) -> None:
        """Simulate saving to DB."""
        db_path = "database/secrets_store.json"
        data = {}
        if os.path.exists(db_path):
            with open(db_path, "r") as f:
                try: data = json.load(f)
                except: pass
        data[key] = encrypted_value
        with open(db_path, "w") as f:
            json.dump(data, f)

    def _fetch_from_db(self, key: str) -> Optional[str]:
        """Simulate fetching from DB."""
        db_path = "database/secrets_store.json"
        if not os.path.exists(db_path):
            return None
        with open(db_path, "r") as f:
            try: 
                data = json.load(f)
                return data.get(key)
            except: 
                return None
