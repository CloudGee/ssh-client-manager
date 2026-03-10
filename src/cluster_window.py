"""
Cluster mode window – send commands to selected terminals.

A standalone popup window (like gnome-connection-manager's Wcluster)
that lists all open terminals with checkboxes.  The user picks which
terminals should receive the command, types in the command entry, and
presses Enter (or the Send button).

Features:
    - All / None / Invert quick-select buttons
    - Checked terminals get a visual highlight on their tab label
    - Command history navigable with Ctrl+Up / Ctrl+Down
    - Close button or window close clears all highlights
"""

import gi
gi.require_version('Gtk', '4.0')

from gi.repository import Gtk, Gdk, GLib


class ClusterWindow(Gtk.Window):
    """Popup window for cluster-mode command sending."""

    def __init__(self, parent_window, terminal_panel):
        super().__init__(title="Cluster – Send to Terminals")
        self.set_transient_for(parent_window)
        self.set_modal(False)
        self.set_default_size(480, 420)
        self.set_resizable(True)

        self._terminal_panel = terminal_panel
        # [(CheckButton, TerminalWidget)]
        self._checks: list[tuple[Gtk.CheckButton, object]] = []
        self._history: list[str] = []
        self._history_index: int = -1

        self._build_ui()
        self.connect("close-request", self._on_close_request)

        # History navigation: Ctrl+Up / Ctrl+Down
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_entry_key_pressed)
        self._entry.add_controller(key_ctrl)

    # -----------------------------------------------------------------
    # UI construction
    # -----------------------------------------------------------------

    def _build_ui(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.set_margin_start(10)
        box.set_margin_end(10)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        # --- Quick-select buttons ---
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for label_text, handler in [
            ("All", self._on_select_all),
            ("None", self._on_select_none),
            ("Invert", self._on_select_invert),
        ]:
            btn = Gtk.Button(label=label_text)
            btn.connect("clicked", handler)
            btn_row.append(btn)
        box.append(btn_row)

        # --- Terminal checklist (scrollable, resizable) ---
        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        sw.set_vexpand(True)

        self._list_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        self._list_box.set_margin_start(4)
        self._list_box.set_margin_end(4)
        self._list_box.set_margin_top(4)
        self._list_box.set_margin_bottom(4)
        sw.set_child(self._list_box)
        box.append(sw)

        self._populate_terminal_list()

        # --- Separator ---
        box.append(Gtk.Separator())

        # --- Command entry row ---
        entry_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._entry = Gtk.Entry()
        self._entry.set_placeholder_text("Type command and press Enter…")
        self._entry.set_hexpand(True)
        self._entry.connect("activate", self._on_send)
        entry_row.append(self._entry)

        btn_send = Gtk.Button(label="Send")
        btn_send.add_css_class("suggested-action")
        btn_send.connect("clicked", self._on_send)
        entry_row.append(btn_send)

        box.append(entry_row)

        # --- Bottom button row ---
        bottom_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bottom_row.set_halign(Gtk.Align.END)

        btn_close = Gtk.Button(label="Close")
        btn_close.connect("clicked", lambda _: self.close())
        bottom_row.append(btn_close)

        box.append(bottom_row)

        self.set_child(box)

        # Focus the command entry (return False so idle fires only once)
        GLib.idle_add(lambda: self._entry.grab_focus() and False)

    # -----------------------------------------------------------------
    # Terminal list
    # -----------------------------------------------------------------

    def _populate_terminal_list(self):
        """Fill the checklist with all open terminals."""
        self._checks.clear()
        terminals = self._terminal_panel.get_terminal_info()
        if not terminals:
            lbl = Gtk.Label(label="No open terminals")
            lbl.add_css_class("dim-label")
            self._list_box.append(lbl)
            return

        for title, terminal in terminals:
            cb = Gtk.CheckButton(label=title)
            cb.connect("toggled", self._on_check_toggled, terminal)
            self._list_box.append(cb)
            self._checks.append((cb, terminal))

    def refresh(self):
        """Rebuild the terminal list (call when tabs change)."""
        # Remember which terminals were selected
        selected = {t for cb, t in self._checks if cb.get_active()}

        # Clear old list
        child = self._list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._list_box.remove(child)
            child = nxt
        self._checks.clear()

        # Rebuild
        terminals = self._terminal_panel.get_terminal_info()
        if not terminals:
            lbl = Gtk.Label(label="No open terminals")
            lbl.add_css_class("dim-label")
            self._list_box.append(lbl)
            return

        for title, terminal in terminals:
            cb = Gtk.CheckButton(label=title)
            # Restore previous selection
            if terminal in selected:
                cb.set_active(True)
            cb.connect("toggled", self._on_check_toggled, terminal)
            self._list_box.append(cb)
            self._checks.append((cb, terminal))

    # -----------------------------------------------------------------
    # Callbacks
    # -----------------------------------------------------------------

    def _on_check_toggled(self, check_button, terminal):
        self._terminal_panel.set_cluster_highlight(
            terminal, check_button.get_active()
        )

    def _on_select_all(self, *_):
        for cb, _ in self._checks:
            cb.set_active(True)

    def _on_select_none(self, *_):
        for cb, _ in self._checks:
            cb.set_active(False)

    def _on_select_invert(self, *_):
        for cb, _ in self._checks:
            cb.set_active(not cb.get_active())

    def _on_send(self, *_):
        """Send the command to all checked terminals."""
        text = self._entry.get_text()
        if not text:
            return

        selected = [t for cb, t in self._checks if cb.get_active()]
        if selected:
            self._terminal_panel.send_to_selected(text + "\n", selected)

        # Save to history
        if not self._history or self._history[-1] != text:
            self._history.append(text)
        self._history_index = -1

        self._entry.set_text("")
        self._entry.grab_focus()

    def _on_entry_key_pressed(self, controller, keyval, keycode, state):
        """Handle Ctrl+Up / Ctrl+Down for command history."""
        if not (state & Gdk.ModifierType.CONTROL_MASK):
            return False
        if not self._history:
            return False

        key = Gdk.keyval_name(keyval)
        if key and key.upper() == "UP":
            self._history_index -= 1
            if self._history_index < -1:
                self._history_index = len(self._history) - 1
        elif key and key.upper() == "DOWN":
            self._history_index += 1
            if self._history_index >= len(self._history):
                self._history_index = -1
        else:
            return False

        if self._history_index >= 0:
            self._entry.set_text(self._history[self._history_index])
        else:
            self._entry.set_text("")
        # Move cursor to end
        self._entry.set_position(-1)
        return True

    def _on_close_request(self, *_):
        """Clear all highlights when the window is closed."""
        self._on_select_none()
        return False  # allow default close

    def get_selected_terminals(self) -> list:
        """Return the list of currently checked terminals."""
        return [t for cb, t in self._checks if cb.get_active()]
