"""
Command snippets manager.

Stores and manages reusable command snippets that can be quickly
inserted into any terminal. Snippets are stored in a JSON file at:
  ~/.config/ssh-client-manager/snippets.json
"""

import json
from pathlib import Path
from typing import Optional

from .config import get_config_dir


class Snippet:
    """A reusable command snippet."""

    def __init__(
        self,
        name: str = "",
        command: str = "",
        category: str = "",
        description: str = "",
    ):
        self.name = name
        self.command = command
        self.category = category
        self.description = description

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "category": self.category,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Snippet":
        return cls(
            name=data.get("name", ""),
            command=data.get("command", ""),
            category=data.get("category", ""),
            description=data.get("description", ""),
        )


class SnippetManager:
    """Manages command snippets persisted in a JSON file."""

    def __init__(self):
        self._file = get_config_dir() / "snippets.json"
        self._snippets: list[Snippet] = []
        self.load()

    def load(self):
        """Load snippets from JSON file."""
        if not self._file.exists():
            self._snippets = self._default_snippets()
            self.save()
            return
        try:
            with open(self._file, "r") as f:
                data = json.load(f)
            self._snippets = [Snippet.from_dict(s) for s in data.get("snippets", [])]
        except (json.JSONDecodeError, IOError):
            self._snippets = self._default_snippets()

    def save(self):
        """Persist snippets to JSON file."""
        data = {"snippets": [s.to_dict() for s in self._snippets]}
        try:
            with open(self._file, "w") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save snippets: {e}")

    def get_snippets(self) -> list[Snippet]:
        """Get all snippets."""
        return list(self._snippets)

    def get_categories(self) -> list[str]:
        """Get all unique categories, sorted."""
        return sorted(set(s.category for s in self._snippets if s.category))

    def add_snippet(self, snippet: Snippet):
        """Add a new snippet."""
        self._snippets.append(snippet)
        self.save()

    def update_snippet(self, index: int, snippet: Snippet):
        """Update a snippet by index."""
        if 0 <= index < len(self._snippets):
            self._snippets[index] = snippet
            self.save()

    def delete_snippet(self, index: int):
        """Delete a snippet by index."""
        if 0 <= index < len(self._snippets):
            del self._snippets[index]
            self.save()

    @staticmethod
    def _default_snippets() -> list["Snippet"]:
        """Return a set of useful default snippets."""
        return [
            Snippet("Disk Usage", "df -h", "System", "Show disk usage"),
            Snippet("Memory Info", "free -h", "System", "Show memory usage"),
            Snippet(
                "Top Processes", "top -bn1 | head -20", "System", "Top CPU processes"
            ),
            Snippet("Network Ports", "ss -tlnp", "Network", "Show listening ports"),
            Snippet("IP Addresses", "ip -br addr", "Network", "Show IP addresses"),
            Snippet("System Uptime", "uptime", "System", "Show uptime and load"),
            Snippet("Tail Syslog", "tail -f /var/log/syslog", "Logs", "Follow syslog"),
            Snippet(
                "Docker PS",
                "docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'",
                "Docker",
                "List running containers",
            ),
            Snippet("Git Status", "git status -sb", "Git", "Short git status"),
            Snippet(
                "Find Large Files",
                "find / -type f -size +100M 2>/dev/null | head -20",
                "System",
                "Find files > 100MB",
            ),
        ]
