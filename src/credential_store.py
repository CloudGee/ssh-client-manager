"""
Encrypted credential storage using Fernet (AES-128-CBC + HMAC-SHA256).

Credentials are stored in an encrypted file at:
  ~/.config/ssh-client-manager/credentials.dat

The encryption key is auto-generated and stored at:
  ~/.config/ssh-client-manager/.store.key

No expect commands are used. Passwords are injected via SSH_ASKPASS.
"""

import json
import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .config import get_config_dir


class CredentialStore:
    """
    Secure credential storage with AES encryption.

    Internal format (encrypted JSON):
    {
        "credentials": {
            "<connection_id>": {
                "passphrase1": "...",
                "passphrase2": "...",
                "password": "..."
            }
        }
    }
    """

    def __init__(self):
        self._config_dir = get_config_dir()
        self._key_file = self._config_dir / ".store.key"
        self._cred_file = self._config_dir / "credentials.dat"
        self._key = self._load_or_create_key()
        try:
            self._fernet = Fernet(self._key)
        except (ValueError, Exception) as e:
            print(f"Warning: Invalid encryption key, regenerating: {e}")
            # Key file is corrupted — regenerate
            # Back up old credentials file before regenerating key
            if self._cred_file.exists():
                backup_path = self._cred_file.with_suffix(".dat.bak")
                try:
                    import shutil

                    shutil.copy2(str(self._cred_file), str(backup_path))
                    print(f"Warning: Old credentials backed up to {backup_path}")
                    print(
                        "Warning: Stored passwords will be lost due to key regeneration."
                    )
                except IOError:
                    pass
            self._key = Fernet.generate_key()
            fd = os.open(
                str(self._key_file),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat.S_IRUSR | stat.S_IWUSR,
            )
            with os.fdopen(fd, "wb") as f:
                f.write(self._key)
            self._fernet = Fernet(self._key)
        self._data = self._load()

    def _load_or_create_key(self) -> bytes:
        """Load existing key or create a new one."""
        if self._key_file.exists():
            with open(self._key_file, "rb") as f:
                return f.read().strip()
        else:
            key = Fernet.generate_key()
            fd = os.open(
                str(self._key_file),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat.S_IRUSR | stat.S_IWUSR,  # 0600
            )
            with os.fdopen(fd, "wb") as f:
                f.write(key)
            return key

    def _load(self) -> dict:
        """Load and decrypt the credential file."""
        if not self._cred_file.exists():
            return {"credentials": {}}
        try:
            with open(self._cred_file, "rb") as f:
                encrypted = f.read()
            decrypted = self._fernet.decrypt(encrypted)
            return json.loads(decrypted.decode("utf-8"))
        except (InvalidToken, json.JSONDecodeError, IOError):
            print("Warning: Could not decrypt credentials file. Starting fresh.")
            return {"credentials": {}}

    def _save(self):
        """Encrypt and save the credential file."""
        try:
            plaintext = json.dumps(self._data).encode("utf-8")
            encrypted = self._fernet.encrypt(plaintext)
            fd = os.open(
                str(self._cred_file),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat.S_IRUSR | stat.S_IWUSR,  # 0600
            )
            with os.fdopen(fd, "wb") as f:
                f.write(encrypted)
        except IOError as e:
            print(f"Warning: Could not save credentials: {e}")

    def store_password(self, connection_id: str, password: str):
        """Store a password for a connection."""
        creds = self._data.setdefault("credentials", {})
        entry = creds.setdefault(connection_id, {})
        entry["password"] = password
        self._save()

    def get_password(self, connection_id: str) -> str | None:
        """Retrieve a password for a connection."""
        return self._data.get("credentials", {}).get(connection_id, {}).get("password")

    def store_passphrase1(self, connection_id: str, passphrase: str):
        """Store the first passphrase for a connection."""
        creds = self._data.setdefault("credentials", {})
        entry = creds.setdefault(connection_id, {})
        entry["passphrase1"] = passphrase
        self._save()

    def get_passphrase1(self, connection_id: str) -> str | None:
        """Retrieve the first passphrase for a connection."""
        return (
            self._data.get("credentials", {}).get(connection_id, {}).get("passphrase1")
        )

    def store_passphrase2(self, connection_id: str, passphrase: str):
        """Store the second passphrase for a connection."""
        creds = self._data.setdefault("credentials", {})
        entry = creds.setdefault(connection_id, {})
        entry["passphrase2"] = passphrase
        self._save()

    def get_passphrase2(self, connection_id: str) -> str | None:
        """Retrieve the second passphrase for a connection."""
        return (
            self._data.get("credentials", {}).get(connection_id, {}).get("passphrase2")
        )

    def store_passphrases(self, connection_id: str, passphrases: list[str]):
        """Store a list of passphrases (replaces legacy passphrase1/passphrase2)."""
        creds = self._data.setdefault("credentials", {})
        entry = creds.setdefault(connection_id, {})
        entry["passphrases"] = [p for p in passphrases if p]
        entry.pop("passphrase1", None)
        entry.pop("passphrase2", None)
        self._save()

    def get_passphrases(self, connection_id: str) -> list[str]:
        """Retrieve all passphrases (backward-compatible with passphrase1/2)."""
        entry = self._data.get("credentials", {}).get(connection_id, {})
        if "passphrases" in entry:
            return [p for p in entry["passphrases"] if p]
        result = []
        if entry.get("passphrase1"):
            result.append(entry["passphrase1"])
        if entry.get("passphrase2"):
            result.append(entry["passphrase2"])
        return result

    def delete_credentials(self, connection_id: str):
        """Remove all credentials for a connection."""
        creds = self._data.get("credentials", {})
        if connection_id in creds:
            del creds[connection_id]
            self._save()

    # --- Global Passphrases ---

    GLOBAL_KEY = "__global_passphrases__"

    def store_global_passphrases(self, passphrases: list[str]):
        """Store global passphrases that are tried for all connections."""
        self.store_passphrases(self.GLOBAL_KEY, passphrases)

    def get_global_passphrases(self) -> list[str]:
        """Retrieve global passphrases."""
        return self.get_passphrases(self.GLOBAL_KEY)

    def has_credentials(self, connection_id: str) -> bool:
        """Check if any credentials exist for a connection."""
        entry = self._data.get("credentials", {}).get(connection_id, {})
        return bool(
            entry.get("password")
            or entry.get("passphrase1")
            or entry.get("passphrase2")
            or any(entry.get("passphrases", []))
        )

    def list_connection_ids(self) -> list[str]:
        """List all connection IDs that have stored credentials."""
        return list(self._data.get("credentials", {}).keys())

    def export_credentials(self, export_password: str) -> bytes:
        """Export all credentials encrypted with a user-provided password."""
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        import base64

        salt = os.urandom(16)
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=600000,
        )
        key = base64.urlsafe_b64encode(kdf.derive(export_password.encode()))
        f = Fernet(key)
        plaintext = json.dumps(self._data).encode("utf-8")
        encrypted = f.encrypt(plaintext)
        return salt + encrypted

    def import_credentials(self, data: bytes, import_password: str) -> bool:
        """Import credentials encrypted with a user-provided password."""
        from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
        from cryptography.hazmat.primitives import hashes
        import base64

        try:
            salt = data[:16]
            encrypted = data[16:]
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=600000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(import_password.encode()))
            f = Fernet(key)
            decrypted = f.decrypt(encrypted)
            imported = json.loads(decrypted.decode("utf-8"))
            # Merge imported credentials
            for conn_id, creds in imported.get("credentials", {}).items():
                self._data.setdefault("credentials", {})[conn_id] = creds
            self._save()
            return True
        except (InvalidToken, json.JSONDecodeError):
            return False
