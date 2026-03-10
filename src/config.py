"""
Application configuration management.

Stores settings in a JSON file at ~/.config/ssh-client-manager/config.json.
"""

import json
import os
from pathlib import Path


def get_config_dir() -> Path:
    """Return the config directory, creating it if needed."""
    config_dir = Path.home() / ".config" / "ssh-client-manager"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir


def get_data_dir() -> Path:
    """Return the data directory, creating it if needed."""
    data_dir = Path.home() / ".local" / "share" / "ssh-client-manager"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


# Default configuration values
DEFAULTS = {
    # Window
    "window_width": 1200,
    "window_height": 800,
    "sidebar_width": 250,
    "sidebar_visible": True,
    # Terminal
    "terminal_font": "Monospace 11",
    "terminal_scrollback_lines": 10000,
    "terminal_bg_color": "#1e1e2e",
    "terminal_fg_color": "#cdd6f4",
    "terminal_cursor_shape": "block",  # block, ibeam, underline
    "terminal_allow_bold": True,
    "terminal_audible_bell": False,
    # Terminal color palette (Catppuccin Mocha)
    "terminal_palette": [
        "#45475a",
        "#f38ba8",
        "#a6e3a1",
        "#f9e2af",
        "#89b4fa",
        "#f5c2e7",
        "#94e2d5",
        "#bac2de",
        "#585b70",
        "#f38ba8",
        "#a6e3a1",
        "#f9e2af",
        "#89b4fa",
        "#f5c2e7",
        "#94e2d5",
        "#a6adc8",
    ],
    # SSH
    "ssh_default_port": 22,
    "ssh_keepalive_interval": 60,
    "ssh_connection_timeout": 30,
    # Behavior
    "confirm_close_tab": True,
    "confirm_close_window": True,
    "show_tab_close_button": True,
    "word_separators": "-A-Za-z0-9,./?%&#:_=+@~",
    # Terminal logging
    "terminal_logging_enabled": False,
    "terminal_log_directory": "",
    # Session recording
    "session_recordings_directory": "",
    # Desktop notifications
    "notify_on_completion": True,
    # Cluster mode
    "cluster_mode_enabled": False,
    # RDP defaults
    "rdp_default_port": 3389,
    "rdp_default_resolution": "1920x1080",
    # VNC defaults
    "vnc_default_port": 5900,
    "vnc_default_quality": "high",
}


_SENTINEL = object()


class Config:
    """Application configuration backed by a JSON file."""

    def __init__(self):
        self._config_file = get_config_dir() / "config.json"
        self._data = dict(DEFAULTS)
        self.load()

    def load(self):
        """Load configuration from file, merging with defaults."""
        if self._config_file.exists():
            try:
                with open(self._config_file, "r") as f:
                    saved = json.load(f)
                self._data.update(saved)
            except (json.JSONDecodeError, IOError):
                pass  # Use defaults on error

    def save(self):
        """Persist configuration to file."""
        try:
            with open(self._config_file, "w") as f:
                json.dump(self._data, f, indent=2)
        except IOError as e:
            print(f"Warning: Could not save config: {e}")

    def get(self, key: str, default=_SENTINEL):
        """Get a config value."""
        if default is not _SENTINEL:
            return self._data.get(key, default)
        return self._data.get(key, DEFAULTS.get(key))

    def set(self, key: str, value):
        """Set a config value and save."""
        self._data[key] = value
        self.save()

    def __getitem__(self, key):
        return self.get(key)

    def __setitem__(self, key, value):
        self.set(key, value)

    def batch_update(self, updates: dict):
        """Update multiple config values at once with a single save."""
        for key, value in updates.items():
            self._data[key] = value
        self.save()
