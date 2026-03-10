"""
Connection data model and manager.

Connections are stored in a JSON file at:
  ~/.config/ssh-client-manager/connections.json
"""

import dataclasses
import json
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

from .config import get_config_dir


@dataclass
class Connection:
    """
    Represents an SSH connection configuration.

    Matches the gnome-connection-manager model: the 'command' field holds
    the full SSH command string with all arguments, e.g.:
        ssh -o "ServerAliveInterval=240" user@host.example.com
    """

    id: str = ""
    name: str = ""
    group: str = ""  # Hierarchical: "Production/WebServers"
    description: str = ""

    # The full SSH command (as typed into a shell)
    command: str = ""

    # Commands to run after login (newline-separated)
    commands: str = ""

    # Terminal appearance overrides (empty = use defaults)
    font: str = ""
    bg_color: str = ""
    fg_color: str = ""

    # TERM env var override
    term_type: str = ""

    # Protocol type (ssh, sftp, rdp, vnc)
    protocol: str = "ssh"

    # Structured connection fields (RDP/VNC primarily; optional for SSH/SFTP)
    host: str = ""
    port: int = 0  # 0 = use protocol default
    username: str = ""
    domain: str = ""  # RDP domain/workgroup

    # RDP specific
    rdp_resolution: str = ""
    rdp_fullscreen: bool = False

    # VNC specific
    vnc_quality: str = ""

    # Extra protocol-specific command-line options
    extra_options: str = ""

    # Connection dependency: ID of connection to open first
    depends_on: str = ""

    # Tags for filtering (comma-separated)
    tags: str = ""

    # Favorite flag
    favorite: bool = False

    # Port forwards: JSON list of {"type":"L"|"R"|"D", "local":"port", "remote":"host:port"}
    port_forwards: str = ""

    # Jump host / ProxyJump (e.g. "user@jumphost" or connection ID)
    jump_host: str = ""

    # Auto-reconnect settings
    auto_reconnect: bool = False
    auto_reconnect_delay: int = 5  # seconds
    auto_reconnect_max: int = 3  # max attempts (0 = unlimited)

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())

    def clone(self) -> "Connection":
        """Create a deep copy with a new ID."""
        data = asdict(self)
        data["id"] = str(uuid.uuid4())
        data["name"] = f"{self.name} (copy)"
        return Connection(**data)

    def display_name(self) -> str:
        """Formatted display string."""
        if self.protocol == "rdp":
            host = self.host or "unknown"
            port = self.port or 3389
            prefix = f"{self.username}@" if self.username else ""
            return f"{prefix}{host}:{port}" if port != 3389 else f"{prefix}{host}"
        elif self.protocol == "vnc":
            host = self.host or "unknown"
            port = self.port or 5900
            return f"{host}:{port}" if port != 5900 else host
        # ssh / sftp
        if self.command:
            import shlex

            try:
                parts = shlex.split(self.command)
                for part in reversed(parts):
                    if not part.startswith("-") and part not in ("ssh", "sftp"):
                        return part
            except ValueError:
                pass
            return self.command[:60]
        if self.host:
            prefix = f"{self.username}@" if self.username else ""
            return f"{prefix}{self.host}"
        return self.name or "(no command)"


class ConnectionManager:
    """
    Manages a collection of Connection objects.

    Stores connections in a JSON file grouped hierarchically.
    """

    def __init__(self):
        self._file = get_config_dir() / "connections.json"
        self._connections: list[Connection] = []
        self._groups: list[str] = []
        self.load()

    def load(self):
        """Load connections from JSON file.

        Handles backward compatibility: old-format connections with
        host/port/username fields are migrated to the new command-based format.
        """
        if not self._file.exists():
            self._connections = []
            self._groups = []
            return

        try:
            with open(self._file, "r") as f:
                data = json.load(f)

            self._connections = []
            migrated = False
            for item in data.get("connections", []):
                try:
                    # Migrate old format: if 'host' exists but 'command' doesn't
                    if "host" in item and not item.get("command"):
                        item["command"] = self._migrate_to_command(item)
                        migrated = True

                    # Strip unknown keys before constructing
                    valid_keys = {f.name for f in dataclasses.fields(Connection)}
                    filtered = {k: v for k, v in item.items() if k in valid_keys}

                    conn = Connection(**filtered)
                    self._connections.append(conn)
                except (TypeError, KeyError):
                    continue

            self._groups = data.get("groups", [])
            for conn in self._connections:
                if conn.group and conn.group not in self._groups:
                    self._groups.append(conn.group)

            # Persist migration
            if migrated:
                self.save()

        except (json.JSONDecodeError, IOError):
            self._connections = []
            self._groups = []

    @staticmethod
    def _migrate_to_command(item: dict) -> str:
        """Build a command string from old-format fields (host, port, username, etc.)."""
        parts = ["ssh"]
        host = item.get("host", "")
        port = item.get("port", 22)
        username = item.get("username", "")
        extra = item.get("extra_params", "")
        keepalive = item.get("keepalive_interval", 0)

        if port and port != 22:
            parts.extend(["-p", str(port)])
        if item.get("auth_method") == "key" and item.get("key_file"):
            parts.extend(["-i", item["key_file"]])
        if item.get("x11_forwarding"):
            parts.append("-X")
        if item.get("compression"):
            parts.append("-C")
        if keepalive and keepalive > 0:
            parts.extend(["-o", f"ServerAliveInterval={keepalive}"])
        for fwd in item.get("local_forwards", []):
            parts.extend(["-L", fwd])
        for fwd in item.get("remote_forwards", []):
            parts.extend(["-R", fwd])
        for fwd in item.get("dynamic_forwards", []):
            parts.extend(["-D", fwd])
        if item.get("proxy_jump"):
            parts.extend(["-J", item["proxy_jump"]])
        elif item.get("proxy_command"):
            parts.extend(["-o", f"ProxyCommand={item['proxy_command']}"])
        if extra:
            parts.append(extra)
        # Destination
        dest = f"{username}@{host}" if username else host
        parts.append(dest)
        return " ".join(parts)

    def save(self):
        """Persist connections to JSON file."""
        data = {
            "connections": [asdict(c) for c in self._connections],
            "groups": sorted(set(self._groups)),
        }
        try:
            with open(self._file, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save connections: {e}")

    # --- CRUD operations ---

    def add_connection(self, conn: Connection):
        """Add a new connection."""
        self._connections.append(conn)
        if conn.group and conn.group not in self._groups:
            self._groups.append(conn.group)
        self.save()

    def update_connection(self, conn: Connection):
        """Update an existing connection by ID."""
        for i, existing in enumerate(self._connections):
            if existing.id == conn.id:
                self._connections[i] = conn
                if conn.group and conn.group not in self._groups:
                    self._groups.append(conn.group)
                self.save()
                return
        # Not found, add as new
        self.add_connection(conn)

    def delete_connection(self, connection_id: str):
        """Delete a connection by ID."""
        self._connections = [c for c in self._connections if c.id != connection_id]
        self.save()

    def get_connection(self, connection_id: str) -> Optional[Connection]:
        """Get a connection by ID."""
        for conn in self._connections:
            if conn.id == connection_id:
                return conn
        return None

    def get_connections(self) -> list[Connection]:
        """Get all connections."""
        return list(self._connections)

    def get_connections_in_group(self, group: str) -> list[Connection]:
        """Get all connections in a specific group."""
        return [c for c in self._connections if c.group == group]

    # --- Group operations ---

    def add_group(self, group: str):
        """Add a group (path like 'Production/WebServers')."""
        if group not in self._groups:
            self._groups.append(group)
            self.save()

    def delete_group(self, group: str, delete_connections: bool = False):
        """Delete a group. Optionally delete its connections."""
        # Remove the group and its subgroups
        self._groups = [
            g for g in self._groups if g != group and not g.startswith(group + "/")
        ]
        if delete_connections:
            self._connections = [
                c
                for c in self._connections
                if c.group != group and not c.group.startswith(group + "/")
            ]
        else:
            # Move connections to root
            for c in self._connections:
                if c.group == group or c.group.startswith(group + "/"):
                    c.group = ""
        self.save()

    def rename_group(self, old_name: str, new_name: str):
        """Rename a group, updating all connections."""
        self._groups = [
            new_name if g == old_name else g.replace(old_name + "/", new_name + "/", 1)
            for g in self._groups
        ]
        for conn in self._connections:
            if conn.group == old_name:
                conn.group = new_name
            elif conn.group.startswith(old_name + "/"):
                conn.group = conn.group.replace(old_name + "/", new_name + "/", 1)
        self.save()

    def get_groups(self) -> list[str]:
        """Get all groups, sorted."""
        return sorted(set(self._groups))

    def get_group_tree(self) -> dict:
        """
        Build a hierarchical group tree.

        Returns a nested dict where keys are group names and values are
        either nested dicts (subgroups) or lists of connections.

        Example: {"Production": {"Web": [...], "DB": [...]}, "Development": [...]}
        """
        tree = {}
        all_groups = self.get_groups()

        # Build group hierarchy
        for group_path in all_groups:
            parts = group_path.split("/")
            current = tree
            for part in parts:
                if part not in current:
                    current[part] = {}
                current = current[part]

        return tree

    # --- Import/Export ---

    def export_connections(self, credential_store=None) -> str:
        """Export all connections as JSON string, optionally including credentials."""
        connections_data = []
        for c in self._connections:
            entry = asdict(c)
            if credential_store and credential_store.has_credentials(c.id):
                creds = {}
                pw = credential_store.get_password(c.id)
                if pw:
                    creds["password"] = pw
                passphrases = credential_store.get_passphrases(c.id)
                if passphrases:
                    creds["passphrases"] = passphrases
                entry["_credentials"] = creds
            connections_data.append(entry)
        data = {
            "connections": connections_data,
            "groups": self.get_groups(),
        }
        return json.dumps(data, indent=2)

    def import_connections(
        self, json_str: str, replace: bool = False, credential_store=None
    ) -> dict:
        """Import connections from JSON string.

        Args:
            json_str: JSON data
            replace: If True, replace all existing connections
            credential_store: If provided, import embedded credentials

        Returns:
            dict with 'imported' count and 'conflicts' list of renamed connections
        """
        try:
            data = json.loads(json_str)
            imported = []
            conflicts = []
            existing_names = (
                {c.name for c in self._connections} if not replace else set()
            )

            # Build a mapping from old IDs to new IDs so we can fix
            # cross-references like depends_on after all connections are created.
            id_map: dict[str, str] = {}

            for item in data.get("connections", []):
                # Extract embedded credentials before constructing Connection
                embedded_creds = item.pop("_credentials", None)

                # Strip unknown keys
                valid_keys = {f.name for f in dataclasses.fields(Connection)}
                filtered = {k: v for k, v in item.items() if k in valid_keys}

                conn = Connection(**filtered)
                old_id = conn.id
                conn.id = str(uuid.uuid4())  # Always generate new IDs
                id_map[old_id] = conn.id

                # Handle name conflicts in append mode
                if not replace and conn.name in existing_names:
                    original_name = conn.name
                    suffix = 2
                    while f"{original_name} ({suffix})" in existing_names:
                        suffix += 1
                    conn.name = f"{original_name} ({suffix})"
                    conflicts.append({"original": original_name, "renamed": conn.name})

                existing_names.add(conn.name)
                imported.append(conn)

                # Import credentials if available
                if credential_store and embedded_creds:
                    if embedded_creds.get("password"):
                        credential_store.store_password(
                            conn.id, embedded_creds["password"]
                        )
                    if embedded_creds.get("passphrases"):
                        credential_store.store_passphrases(
                            conn.id, embedded_creds["passphrases"]
                        )

            # Remap cross-references (depends_on) to use new IDs
            for conn in imported:
                if conn.depends_on and conn.depends_on in id_map:
                    conn.depends_on = id_map[conn.depends_on]

            if replace:
                self._connections = imported
            else:
                self._connections.extend(imported)

            for group in data.get("groups", []):
                if group not in self._groups:
                    self._groups.append(group)

            self.save()
            return {"imported": len(imported), "conflicts": conflicts}
        except (json.JSONDecodeError, TypeError) as e:
            raise ValueError(f"Invalid import data: {e}")
