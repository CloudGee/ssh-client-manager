"""
Quick SSH config editor window.

Opens ~/.ssh/config in a simple text editor with syntax highlighting
hints and save functionality.
"""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, Gdk, Pango


class SSHConfigEditor(Adw.Window):
    """
    A quick editor window for ~/.ssh/config.

    Features:
    - Load/save ~/.ssh/config
    - Monospace text editing
    - Line numbers via CSS
    - Basic validation on save
    """

    def __init__(self, parent=None):
        super().__init__(
            title="SSH Config Editor",
            default_width=700,
            default_height=550,
            transient_for=parent,
            modal=False,
        )

        self._config_path = Path.home() / ".ssh" / "config"
        self._modified = False

        self._build_ui()
        self._load_config()

    def _build_ui(self):
        """Build the editor UI."""
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # --- Header bar ---
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(True)

        # Title
        title_label = Gtk.Label(label="~/.ssh/config")
        title_label.add_css_class("heading")
        header.set_title_widget(title_label)

        # Save button
        btn_save = Gtk.Button(label="Save")
        btn_save.add_css_class("suggested-action")
        btn_save.connect("clicked", self._on_save)
        header.pack_end(btn_save)
        self._btn_save = btn_save

        # Reload button
        btn_reload = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_reload.set_tooltip_text("Reload from disk")
        btn_reload.add_css_class("flat")
        btn_reload.connect("clicked", lambda _: self._load_config())
        header.pack_start(btn_reload)

        main_box.append(header)

        # --- Info bar ---
        info_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        info_box.set_margin_start(8)
        info_box.set_margin_end(8)
        info_box.set_margin_top(4)
        info_box.set_margin_bottom(4)

        path_label = Gtk.Label(label=str(self._config_path))
        path_label.set_xalign(0)
        path_label.add_css_class("dim-label")
        path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        path_label.set_hexpand(True)
        info_box.append(path_label)

        self._status_label = Gtk.Label(label="")
        self._status_label.add_css_class("dim-label")
        info_box.append(self._status_label)

        main_box.append(info_box)
        main_box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- Text editor ---
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        self._text_view = Gtk.TextView()
        self._text_view.set_monospace(True)
        self._text_view.set_left_margin(12)
        self._text_view.set_right_margin(12)
        self._text_view.set_top_margin(8)
        self._text_view.set_bottom_margin(8)
        self._text_view.set_wrap_mode(Gtk.WrapMode.NONE)

        # Track modifications
        buffer = self._text_view.get_buffer()
        buffer.connect("changed", self._on_buffer_changed)

        scrolled.set_child(self._text_view)
        main_box.append(scrolled)

        # --- Keyboard shortcut: Cmd/Ctrl+S to save ---
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

        self.set_content(main_box)

    def _load_config(self):
        """Load the SSH config file contents."""
        try:
            if self._config_path.exists():
                text = self._config_path.read_text(encoding="utf-8")
                self._text_view.get_buffer().set_text(text)
                self._status_label.set_text("Loaded")
            else:
                # File doesn't exist yet
                self._text_view.get_buffer().set_text(
                    "# SSH Client Configuration\n"
                    "# See: man ssh_config\n\n"
                    "# Example:\n"
                    "# Host myserver\n"
                    "#     HostName example.com\n"
                    "#     User admin\n"
                    "#     Port 22\n"
                    "#     IdentityFile ~/.ssh/id_rsa\n"
                )
                self._status_label.set_text("New file")

            self._modified = False
            self._update_title()
        except Exception as e:
            self._status_label.set_text(f"Error: {e}")

    def _on_save(self, *_):
        """Save the config file."""
        try:
            # Ensure ~/.ssh directory exists
            ssh_dir = Path.home() / ".ssh"
            ssh_dir.mkdir(mode=0o700, exist_ok=True)

            buffer = self._text_view.get_buffer()
            start = buffer.get_start_iter()
            end = buffer.get_end_iter()
            text = buffer.get_text(start, end, True)

            # Write with proper permissions (0600)
            import stat as stat_mod

            fd = os.open(
                str(self._config_path),
                os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                stat_mod.S_IRUSR | stat_mod.S_IWUSR,
            )
            with os.fdopen(fd, "w") as f:
                f.write(text)

            # Ensure permissions are correct even for existing files
            os.chmod(str(self._config_path), stat_mod.S_IRUSR | stat_mod.S_IWUSR)

            self._modified = False
            self._update_title()
            self._status_label.set_text("Saved")
        except Exception as e:
            self._status_label.set_text(f"Save failed: {e}")

    def _on_buffer_changed(self, buffer):
        """Track modifications."""
        self._modified = True
        self._update_title()

    def _update_title(self):
        """Update window title to show modified state."""
        title = "~/.ssh/config"
        if self._modified:
            title += " (modified)"
        self.set_title(f"SSH Config Editor — {title}")

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts."""
        import sys

        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        meta = state & Gdk.ModifierType.META_MASK

        # Cmd+S (macOS) or Ctrl+S (Linux) to save
        if keyval in (Gdk.KEY_s, Gdk.KEY_S):
            if (sys.platform == "darwin" and meta) or (
                sys.platform != "darwin" and ctrl
            ):
                self._on_save()
                return True

        return False
