"""
SSH Client Manager Application.

A modern GTK4/libadwaita SSH connection manager that combines
the best features of gnome-connection-manager and sshpilot:
- Split terminals (horizontal/vertical) from GCM
- Modern GTK4/Adw UI
- Encrypted credential storage (no expect)
- SSH_ASKPASS for password injection
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")

import sys
from gi.repository import Gtk, Adw, Gio, GLib

from .config import Config
from .window import MainWindow


APP_ID = "io.github.ssh-client-manager"
CSS_DATA = """
/* Tab styling */
.disconnected-tab {
    text-decoration: line-through;
    opacity: 0.5;
}

.cluster-selected {
    background-color: alpha(@accent_bg_color, 0.3);
    border-radius: 4px;
}

/* Sidebar styling */
.sidebar {
    background-color: mix(@window_bg_color, @view_bg_color, 0.5);
}

/* Status bar */
.status-bar {
    font-size: 0.85em;
    padding: 2px 8px;
}

/* Terminal panel split handle */
paned > separator {
    min-width: 4px;
    min-height: 4px;
}

/* Notebook tabs */
notebook > header > tabs > tab {
    padding: 2px 4px;
    min-height: 24px;
}

notebook > header > tabs > tab:checked {
    font-weight: bold;
}

/* Search bar */
.search-bar {
    background-color: @headerbar_bg_color;
    border-bottom: 1px solid @borders;
}

/* Cluster bar */
.cluster-bar {
    background-color: alpha(@accent_bg_color, 0.1);
    border-bottom: 1px solid @accent_bg_color;
}

/* Drop zone overlay */
.drop-zone {
    background-color: alpha(@accent_bg_color, 0.2);
    border: 2px dashed @accent_bg_color;
    border-radius: 6px;
}

/* Favorite star */
.favorite-icon {
    color: @warning_color;
}

/* Tag chip */
.tag-chip {
    background-color: alpha(@accent_bg_color, 0.2);
    border-radius: 10px;
    padding: 1px 6px;
    font-size: 0.8em;
}

/* Connected status indicator */
.status-connected {
    color: @success_color;
}

.status-disconnected {
    color: alpha(@window_fg_color, 0.3);
}

/* Snippet row */
.snippet-row {
    padding: 4px 8px;
}

.snippet-row:hover {
    background-color: alpha(@accent_bg_color, 0.1);
}
"""


class SSHClientApp(Adw.Application):
    """The main application class."""

    def __init__(self):
        super().__init__(
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

        self.config = Config()
        self.window = None

        # Set application name
        GLib.set_application_name("SSH Client Manager")
        GLib.set_prgname("ssh-client-manager")

    def do_startup(self):
        """Application startup: load CSS, register shortcuts."""
        Adw.Application.do_startup(self)
        self._load_css()
        self._register_shortcuts()

    def do_activate(self):
        """Application activated: show main window."""
        if not self.window:
            self.window = MainWindow(self, self.config)

        self.window.present()

    def _load_css(self):
        """Load application CSS."""
        from gi.repository import Gdk

        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS_DATA.encode())

        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display, css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def _register_shortcuts(self):
        """Register global keyboard shortcuts."""
        shortcuts = {
            "win.new-local": ["<Ctrl><Shift>t"],
            "win.new-connection": ["<Ctrl><Shift>n"],
            "win.close-tab": ["<Ctrl>w"],
            "win.next-tab": ["<Ctrl>Tab"],
            "win.prev-tab": ["<Ctrl><Shift>Tab"],
            "win.preferences": ["<Ctrl>comma"],
            "win.toggle-sidebar": ["F9"],
            "win.search-terminal": ["<Ctrl>f"],
            "win.split-h": ["<Ctrl><Shift>h"],
            "win.split-v": ["<Ctrl><Shift>j"],
            "win.snippets": ["<Ctrl><Shift>s"],
            "win.quit": ["<Ctrl>q"],
        }

        for action, accels in shortcuts.items():
            self.set_accels_for_action(action, accels)


def main():
    """Entry point for the application."""
    app = SSHClientApp()
    return app.run(sys.argv)
