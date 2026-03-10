"""
Connection add/edit dialog.

Matches the gnome-connection-manager layout:
  Properties tab: Group, Name, Description, Command (multiline), Passphrase1,
                  Passphrase2, Password, TERM
  Commands tab:   Post-login commands
  Appearance tab: Font, colors
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GObject
from typing import Optional

from .connection import Connection, ConnectionManager
from .credential_store import CredentialStore


class ConnectionDialog(Adw.Window):
    """
    Dialog for adding or editing a connection.

    Tabs:
    - Properties: group, name, description, command, credentials, TERM
    - Commands:   post-login commands
    - Appearance: font, bg/fg color overrides
    """

    __gsignals__ = {
        "connection-saved": (GObject.SignalFlags.RUN_LAST, None, (object,)),
    }

    def __init__(
        self,
        parent: Gtk.Window,
        connection_manager: ConnectionManager,
        credential_store: CredentialStore,
        connection: Optional[Connection] = None,
        default_group: Optional[str] = None,
    ):
        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=580,
            default_height=680,
        )

        self.connection_manager = connection_manager
        self.credential_store = credential_store
        self.connection = connection  # None for new, existing for edit

        is_edit = connection is not None
        self.set_title("Edit Connection" if is_edit else "New Connection")

        # ----- Main layout -----
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        save_btn = Gtk.Button(label="Save")
        save_btn.add_css_class("suggested-action")
        save_btn.connect("clicked", self._on_save)
        header.pack_end(save_btn)
        main_box.append(header)

        # Notebook
        notebook = Gtk.Notebook()
        notebook.set_margin_start(12)
        notebook.set_margin_end(12)
        notebook.set_margin_top(6)
        notebook.set_margin_bottom(12)
        notebook.set_vexpand(True)

        notebook.append_page(
            self._build_properties_page(), Gtk.Label(label="Properties")
        )
        notebook.append_page(self._build_commands_page(), Gtk.Label(label="Commands"))
        notebook.append_page(
            self._build_appearance_page(), Gtk.Label(label="Appearance")
        )

        main_box.append(notebook)
        self.set_content(main_box)

        # Populate if editing
        if connection:
            self._populate_fields(connection)
        elif default_group:
            # Auto-select group when adding from sidebar
            model = self.combo_group.get_model()
            for i in range(model.iter_n_children(None)):
                iter_ = model.iter_nth_child(None, i)
                if iter_ and model.get_value(iter_, 0) == default_group:
                    self.combo_group.set_active(i)
                    break

    # -----------------------------------------------------------------
    # Page builders
    # -----------------------------------------------------------------

    def _build_properties_page(self) -> Gtk.Widget:
        """Build the main connection settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_start(8)
        box.set_margin_end(8)

        # Protocol selector
        self.combo_protocol = Gtk.ComboBoxText()
        for proto in ("ssh", "sftp", "rdp", "vnc"):
            self.combo_protocol.append(proto, proto.upper())
        self.combo_protocol.set_active_id("ssh")
        self.combo_protocol.connect("changed", self._on_protocol_changed)
        box.append(self._labeled("Protocol:", self.combo_protocol))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # Group combo
        self.combo_group = Gtk.ComboBoxText()
        self.combo_group.append_text("")  # Root (no group)
        for group in self.connection_manager.get_groups():
            self.combo_group.append_text(group)
        self.combo_group.set_hexpand(True)
        self.combo_group.connect("changed", self._on_group_combo_changed)
        box.append(self._labeled("Group:", self.combo_group))

        # New group entry
        self.entry_group = Gtk.Entry()
        self.entry_group.set_placeholder_text("Group / Subgroup")
        self.entry_group.connect("changed", self._on_new_group_changed)
        box.append(self._labeled("New Group:", self.entry_group))

        # Name
        self.entry_name = Gtk.Entry()
        self.entry_name.set_placeholder_text("My Server")
        box.append(self._labeled("Name:", self.entry_name))

        # Description
        self.entry_description = Gtk.Entry()
        self.entry_description.set_placeholder_text("Optional description")
        box.append(self._labeled("Description:", self.entry_description))

        # Connection Dependency
        self.combo_depends_on = Gtk.ComboBoxText()
        self.combo_depends_on.append("", "(None)")
        for conn in self.connection_manager.get_connections():
            if not self.connection or conn.id != self.connection.id:
                label = f"[{conn.group}] {conn.name}" if conn.group else conn.name
                self.combo_depends_on.append(conn.id, label)
        self.combo_depends_on.set_active_id("")
        box.append(self._labeled("Open After:", self.combo_depends_on))

        # Jump host (ProxyJump) or ProxyCommand
        self.entry_jump_host = Gtk.Entry()
        self.entry_jump_host.set_placeholder_text(
            "user@jumphost  or  ssh user@jump -i key -W %h:%p"
        )
        box.append(self._labeled("Jump / Proxy:", self.entry_jump_host))

        # Tags
        self.entry_tags = Gtk.Entry()
        self.entry_tags.set_placeholder_text("tag1, tag2, tag3")
        box.append(self._labeled("Tags:", self.entry_tags))

        # Favorite toggle
        self.switch_favorite = Gtk.Switch()
        self.switch_favorite.set_halign(Gtk.Align.START)
        box.append(self._labeled("Favorite:", self.switch_favorite))

        # --- Auto-reconnect section ---
        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        reconnect_label = Gtk.Label(label="Auto-Reconnect")
        reconnect_label.set_xalign(0)
        reconnect_label.add_css_class("heading")
        box.append(reconnect_label)

        self.switch_auto_reconnect = Gtk.Switch()
        self.switch_auto_reconnect.set_halign(Gtk.Align.START)
        box.append(self._labeled("Enable:", self.switch_auto_reconnect))

        self.spin_reconnect_delay = Gtk.SpinButton()
        self.spin_reconnect_delay.set_range(1, 300)
        self.spin_reconnect_delay.set_value(5)
        self.spin_reconnect_delay.set_increments(1, 5)
        box.append(self._labeled("Delay (seconds):", self.spin_reconnect_delay))

        self.spin_reconnect_max = Gtk.SpinButton()
        self.spin_reconnect_max.set_range(0, 100)
        self.spin_reconnect_max.set_value(3)
        self.spin_reconnect_max.set_increments(1, 5)
        self.spin_reconnect_max.set_tooltip_text("0 = unlimited attempts")
        box.append(self._labeled("Max Attempts:", self.spin_reconnect_max))

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- SSH/SFTP command section ---
        self._ssh_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        label_cmd = Gtk.Label(
            label="SSH/SFTP Command (the full command line, e.g.\n"
            '  ssh -o "ServerAliveInterval=240" user@host )'
        )
        label_cmd.set_xalign(0)
        label_cmd.add_css_class("dim-label")
        self._ssh_section.append(label_cmd)

        self.textview_command = Gtk.TextView()
        self.textview_command.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview_command.set_monospace(True)
        self.textview_command.set_left_margin(8)
        self.textview_command.set_right_margin(8)
        self.textview_command.set_top_margin(4)
        self.textview_command.set_bottom_margin(4)

        scrolled_cmd = Gtk.ScrolledWindow()
        scrolled_cmd.set_min_content_height(72)
        scrolled_cmd.set_max_content_height(120)
        scrolled_cmd.set_child(self.textview_command)
        scrolled_cmd.add_css_class("card")
        self._ssh_section.append(scrolled_cmd)

        box.append(self._ssh_section)

        # --- Structured fields (RDP/VNC, also optional for SFTP) ---
        self._struct_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.entry_host = Gtk.Entry()
        self.entry_host.set_placeholder_text("hostname or IP")
        self._struct_section.append(self._labeled("Host:", self.entry_host))

        self.spin_port = Gtk.SpinButton()
        self.spin_port.set_range(0, 65535)
        self.spin_port.set_value(0)
        self.spin_port.set_increments(1, 100)
        port_row = self._labeled("Port:", self.spin_port)
        self._port_hint = Gtk.Label(label="(0 = protocol default)")
        self._port_hint.add_css_class("dim-label")
        port_row.append(self._port_hint)
        self._struct_section.append(port_row)

        self.entry_username = Gtk.Entry()
        self.entry_username.set_placeholder_text("username")
        self._struct_section.append(self._labeled("Username:", self.entry_username))

        # RDP-only fields
        self._rdp_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.entry_domain = Gtk.Entry()
        self.entry_domain.set_placeholder_text("WORKGROUP")
        self._rdp_section.append(self._labeled("Domain:", self.entry_domain))

        self.entry_rdp_resolution = Gtk.Entry()
        self.entry_rdp_resolution.set_placeholder_text("1920x1080")
        self._rdp_section.append(
            self._labeled("Resolution:", self.entry_rdp_resolution)
        )

        self.switch_rdp_fullscreen = Gtk.Switch()
        self.switch_rdp_fullscreen.set_halign(Gtk.Align.START)
        self._rdp_section.append(
            self._labeled("Fullscreen:", self.switch_rdp_fullscreen)
        )

        self._struct_section.append(self._rdp_section)

        # VNC-only fields
        self._vnc_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.combo_vnc_quality = Gtk.ComboBoxText()
        self.combo_vnc_quality.append("high", "High")
        self.combo_vnc_quality.append("medium", "Medium")
        self.combo_vnc_quality.append("low", "Low")
        self.combo_vnc_quality.set_active_id("high")
        self._vnc_section.append(self._labeled("Quality:", self.combo_vnc_quality))

        self._struct_section.append(self._vnc_section)

        # Extra options (all protocols)
        self.entry_extra = Gtk.Entry()
        self.entry_extra.set_placeholder_text("extra command-line options")
        self._struct_section.append(self._labeled("Extra Options:", self.entry_extra))

        box.append(self._struct_section)

        # Initially hide structured section (SSH is default)
        self._struct_section.set_visible(False)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- Port Forwarding section (SSH/SFTP only) ---
        self._portfwd_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)

        pf_label = Gtk.Label(label="Port Forwarding:")
        pf_label.set_xalign(0)
        pf_label.add_css_class("dim-label")
        self._portfwd_section.append(pf_label)

        self._portfwd_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._portfwd_rows: list = []
        self._portfwd_section.append(self._portfwd_box)

        btn_add_pf = Gtk.Button(label="+ Add Port Forward")
        btn_add_pf.add_css_class("flat")
        btn_add_pf.set_halign(Gtk.Align.START)
        btn_add_pf.connect("clicked", lambda _: self._add_port_forward_row())
        self._portfwd_section.append(btn_add_pf)

        box.append(self._portfwd_section)

        box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- Credentials section (all protocols) ---
        self._cred_section = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        # Dynamic passphrases (SSH/SFTP only)
        self._passphrase_label = Gtk.Label(label="Passphrases (one per key/hop):")
        self._passphrase_label.set_xalign(0)
        self._passphrase_label.add_css_class("dim-label")
        self._cred_section.append(self._passphrase_label)

        self._passphrase_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._passphrase_entries = []
        self._cred_section.append(self._passphrase_box)

        self._btn_add_passphrase = Gtk.Button(label="+ Add Passphrase")
        self._btn_add_passphrase.add_css_class("flat")
        self._btn_add_passphrase.set_halign(Gtk.Align.START)
        self._btn_add_passphrase.connect(
            "clicked", lambda _: self._add_passphrase_row()
        )
        self._cred_section.append(self._btn_add_passphrase)

        # Add one empty passphrase row by default
        self._add_passphrase_row()

        # Password (all protocols)
        self.entry_password = Gtk.PasswordEntry()
        self.entry_password.set_show_peek_icon(True)
        self._cred_section.append(self._labeled("Password:", self.entry_password))

        hint = Gtk.Label(
            label="Credentials are encrypted and injected automatically.\n"
            "Leave blank to be prompted interactively."
        )
        hint.set_xalign(0)
        hint.set_wrap(True)
        hint.add_css_class("dim-label")
        self._cred_section.append(hint)

        box.append(self._cred_section)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_child(box)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        return scrolled

    def _build_commands_page(self) -> Gtk.Widget:
        """Build the post-login commands page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_start(8)
        box.set_margin_end(8)

        label = Gtk.Label(
            label="Commands to execute after SSH login (one per line).\n"
            "Use ##D=1000 to insert a delay in milliseconds."
        )
        label.set_xalign(0)
        label.set_wrap(True)
        label.add_css_class("dim-label")
        box.append(label)

        self.textview_commands = Gtk.TextView()
        self.textview_commands.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.textview_commands.set_monospace(True)
        self.textview_commands.set_left_margin(8)
        self.textview_commands.set_right_margin(8)
        self.textview_commands.set_top_margin(4)
        self.textview_commands.set_bottom_margin(4)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(self.textview_commands)
        scrolled.add_css_class("card")
        box.append(scrolled)

        return box

    def _build_appearance_page(self) -> Gtk.Widget:
        """Build the appearance settings page."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(12)
        box.set_margin_start(8)
        box.set_margin_end(8)

        hint = Gtk.Label(
            label="Override default terminal appearance for this connection.\n"
            "Leave defaults to use global settings."
        )
        hint.set_wrap(True)
        hint.set_xalign(0)
        hint.add_css_class("dim-label")
        box.append(hint)

        # Font
        self.entry_font = Gtk.Entry()
        self.entry_font.set_placeholder_text("Monospace 11")
        box.append(self._labeled("Font:", self.entry_font))

        # Background color – use a check button to opt-in
        self.chk_bg_color = Gtk.CheckButton(label="Override background")
        self.color_bg = Gtk.ColorButton()
        bg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        bg_row.append(self.chk_bg_color)
        bg_row.append(self.color_bg)
        self.color_bg.set_sensitive(False)
        self.chk_bg_color.connect(
            "toggled", lambda c: self.color_bg.set_sensitive(c.get_active())
        )
        box.append(bg_row)

        # Foreground color
        self.chk_fg_color = Gtk.CheckButton(label="Override foreground")
        self.color_fg = Gtk.ColorButton()
        fg_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        fg_row.append(self.chk_fg_color)
        fg_row.append(self.color_fg)
        self.color_fg.set_sensitive(False)
        self.chk_fg_color.connect(
            "toggled", lambda c: self.color_fg.set_sensitive(c.get_active())
        )
        box.append(fg_row)

        # TERM type
        self.entry_term = Gtk.Entry()
        self.entry_term.set_placeholder_text("xterm-256color")
        box.append(self._labeled("TERM:", self.entry_term))

        return box

    # -----------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------

    @staticmethod
    def _labeled(label_text: str, widget: Gtk.Widget) -> Gtk.Box:
        """Create a horizontal box with a label and widget."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.set_margin_bottom(4)
        lbl = Gtk.Label(label=label_text)
        lbl.set_xalign(1)
        lbl.set_size_request(120, -1)
        lbl.add_css_class("dim-label")
        row.append(lbl)
        widget.set_hexpand(True)
        row.append(widget)
        return row

    def _on_group_combo_changed(self, combo):
        """When a group is selected from combo, clear the new group entry."""
        if combo.get_active() > 0:
            self.entry_group.set_text("")

    def _on_new_group_changed(self, entry):
        """When new group has text, clear the group combo to avoid confusion."""
        if entry.get_text().strip():
            self.combo_group.set_active(0)  # Reset to empty (root)

    def _add_passphrase_row(self, value: str = ""):
        """Add a passphrase entry row with a remove button."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        entry = Gtk.PasswordEntry()
        entry.set_show_peek_icon(True)
        entry.set_hexpand(True)
        if value:
            entry.set_text(value)
        try:
            entry.props.placeholder_text = (
                f"Passphrase {len(self._passphrase_entries) + 1}"
            )
        except Exception:
            pass
        row.append(entry)

        btn_remove = Gtk.Button(icon_name="list-remove-symbolic")
        btn_remove.add_css_class("flat")
        btn_remove.add_css_class("circular")
        btn_remove.connect(
            "clicked", lambda _, r=row, e=entry: self._remove_passphrase_row(r, e)
        )
        row.append(btn_remove)

        self._passphrase_entries.append(entry)
        self._passphrase_box.append(row)

    def _remove_passphrase_row(self, row, entry):
        """Remove a passphrase entry row."""
        if entry in self._passphrase_entries:
            self._passphrase_entries.remove(entry)
        self._passphrase_box.remove(row)
        # Always keep at least one row
        if not self._passphrase_entries:
            self._add_passphrase_row()

    def _add_port_forward_row(self, pf_type="L", local="", remote=""):
        """Add a port forwarding row (type combo, local, remote, remove button)."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)

        combo_type = Gtk.ComboBoxText()
        combo_type.append("L", "Local (-L)")
        combo_type.append("R", "Remote (-R)")
        combo_type.append("D", "Dynamic (-D)")
        combo_type.set_active_id(pf_type)
        combo_type.set_size_request(120, -1)
        row.append(combo_type)

        entry_local = Gtk.Entry()
        entry_local.set_placeholder_text("local port")
        entry_local.set_hexpand(True)
        entry_local.set_text(local)
        row.append(entry_local)

        entry_remote = Gtk.Entry()
        entry_remote.set_placeholder_text("host:port")
        entry_remote.set_hexpand(True)
        entry_remote.set_text(remote)
        row.append(entry_remote)

        btn_remove = Gtk.Button(icon_name="list-remove-symbolic")
        btn_remove.add_css_class("flat")
        btn_remove.add_css_class("circular")

        pf_data = {
            "combo": combo_type,
            "local": entry_local,
            "remote": entry_remote,
            "row": row,
        }
        self._portfwd_rows.append(pf_data)

        def _on_remove(_):
            if pf_data in self._portfwd_rows:
                self._portfwd_rows.remove(pf_data)
            self._portfwd_box.remove(row)

        btn_remove.connect("clicked", _on_remove)
        row.append(btn_remove)

        # Hide remote entry for Dynamic (-D) type
        def _on_type_changed(c):
            entry_remote.set_visible(c.get_active_id() != "D")

        combo_type.connect("changed", _on_type_changed)
        _on_type_changed(combo_type)

        self._portfwd_box.append(row)

    def _on_protocol_changed(self, combo):
        """Show/hide fields based on the selected protocol."""
        proto = combo.get_active_id() or "ssh"
        is_ssh = proto in ("ssh", "sftp")
        is_rdp = proto == "rdp"
        is_vnc = proto == "vnc"

        # SSH/SFTP: show command text area; RDP/VNC: show structured fields
        self._ssh_section.set_visible(is_ssh)
        self._struct_section.set_visible(not is_ssh)

        # RDP-only fields
        self._rdp_section.set_visible(is_rdp)
        # VNC-only fields
        self._vnc_section.set_visible(is_vnc)

        # Passphrase rows only for SSH/SFTP
        self._passphrase_label.set_visible(is_ssh)
        self._passphrase_box.set_visible(is_ssh)
        self._btn_add_passphrase.set_visible(is_ssh)

        # Port forwarding and jump host only for SSH/SFTP
        self._portfwd_section.set_visible(is_ssh)
        self.entry_jump_host.get_parent().set_visible(is_ssh)

    # -----------------------------------------------------------------
    # Populate (edit mode)
    # -----------------------------------------------------------------

    def _populate_fields(self, conn: Connection):
        """Fill form fields from an existing connection."""
        # Protocol
        self.combo_protocol.set_active_id(conn.protocol or "ssh")
        self._on_protocol_changed(self.combo_protocol)

        self.entry_name.set_text(conn.name or "")
        self.entry_description.set_text(conn.description or "")

        # Group
        if conn.group:
            found = False
            model = self.combo_group.get_model()
            for i in range(model.iter_n_children(None)):
                iter_ = model.iter_nth_child(None, i)
                if iter_ and model.get_value(iter_, 0) == conn.group:
                    self.combo_group.set_active(i)
                    found = True
                    break
            if not found:
                self.combo_group.set_active(0)
                self.entry_group.set_text(conn.group)

        # Command
        buf = self.textview_command.get_buffer()
        buf.set_text(conn.command or "")

        # TERM
        self.entry_term.set_text(conn.term_type or "")

        # Structured fields
        self.entry_host.set_text(conn.host or "")
        self.spin_port.set_value(conn.port or 0)
        self.entry_username.set_text(conn.username or "")
        self.entry_domain.set_text(conn.domain or "")
        self.entry_rdp_resolution.set_text(conn.rdp_resolution or "")
        self.switch_rdp_fullscreen.set_active(conn.rdp_fullscreen)
        self.combo_vnc_quality.set_active_id(conn.vnc_quality or "high")
        self.entry_extra.set_text(conn.extra_options or "")

        # Commands (post-login)
        buf_cmds = self.textview_commands.get_buffer()
        buf_cmds.set_text(conn.commands or "")

        # Font
        self.entry_font.set_text(conn.font or "")

        # Colors
        if conn.bg_color:
            rgba = Gdk.RGBA()
            if rgba.parse(conn.bg_color):
                self.color_bg.set_rgba(rgba)
                self.chk_bg_color.set_active(True)
        if conn.fg_color:
            rgba = Gdk.RGBA()
            if rgba.parse(conn.fg_color):
                self.color_fg.set_rgba(rgba)
                self.chk_fg_color.set_active(True)

        # Connection dependency
        dep_id = getattr(conn, "depends_on", "") or ""
        if dep_id:
            self.combo_depends_on.set_active_id(dep_id)

        # Jump host
        jump_host = getattr(conn, "jump_host", "") or ""
        self.entry_jump_host.set_text(jump_host)

        # Tags
        tags = getattr(conn, "tags", "") or ""
        self.entry_tags.set_text(tags)

        # Favorite
        self.switch_favorite.set_active(getattr(conn, "favorite", False))

        # Auto-reconnect
        self.switch_auto_reconnect.set_active(getattr(conn, "auto_reconnect", False))
        self.spin_reconnect_delay.set_value(getattr(conn, "auto_reconnect_delay", 5))
        self.spin_reconnect_max.set_value(getattr(conn, "auto_reconnect_max", 3))

        # Port forwards
        port_forwards = getattr(conn, "port_forwards", "") or ""
        if port_forwards:
            import json

            try:
                pf_list = json.loads(port_forwards)
                for pf in pf_list:
                    self._add_port_forward_row(
                        pf_type=pf.get("type", "L"),
                        local=pf.get("local", ""),
                        remote=pf.get("remote", ""),
                    )
            except (json.JSONDecodeError, TypeError):
                pass

        # Credentials - passphrases (dynamic list)
        passphrases = self.credential_store.get_passphrases(conn.id)
        if passphrases:
            # Remove default empty row and add one per stored passphrase
            for entry in list(self._passphrase_entries):
                row = entry.get_parent()
                if row:
                    self._passphrase_box.remove(row)
            self._passphrase_entries.clear()
            for pp in passphrases:
                self._add_passphrase_row(pp)

        pw = self.credential_store.get_password(conn.id)
        if pw:
            self.entry_password.set_text(pw)

    # -----------------------------------------------------------------
    # Save
    # -----------------------------------------------------------------

    def _on_save(self, button):
        """Validate and save the connection."""
        protocol = self.combo_protocol.get_active_id() or "ssh"

        # Get command text (SSH/SFTP)
        buf = self.textview_command.get_buffer()
        start, end = buf.get_bounds()
        command = buf.get_text(start, end, True).strip()

        name = self.entry_name.get_text().strip()
        host = self.entry_host.get_text().strip()

        # Validation
        if protocol in ("ssh", "sftp"):
            if not command and not name:
                self._show_error("Either a Name or Command is required.")
                return
            if not name:
                name = command.split()[-1] if command else "Untitled"
        else:
            if not host:
                self._show_error("Host is required for RDP/VNC connections.")
                return
            if not name:
                name = host

        # Determine group
        group = self.entry_group.get_text().strip()
        if not group:
            active_text = self.combo_group.get_active_text()
            group = active_text.strip() if active_text else ""

        # Build / update connection
        conn = self.connection if self.connection else Connection()
        conn.name = name
        conn.group = group
        conn.protocol = protocol
        conn.description = self.entry_description.get_text().strip()
        conn.command = command
        conn.font = self.entry_font.get_text().strip()
        conn.term_type = self.entry_term.get_text().strip()

        # Structured fields
        conn.host = host
        conn.port = int(self.spin_port.get_value())
        conn.username = self.entry_username.get_text().strip()
        conn.domain = self.entry_domain.get_text().strip()
        conn.rdp_resolution = self.entry_rdp_resolution.get_text().strip()
        conn.rdp_fullscreen = self.switch_rdp_fullscreen.get_active()
        conn.vnc_quality = self.combo_vnc_quality.get_active_id() or "high"
        conn.extra_options = self.entry_extra.get_text().strip()
        conn.depends_on = self.combo_depends_on.get_active_id() or ""

        # New v1.2 fields
        conn.jump_host = self.entry_jump_host.get_text().strip()
        conn.tags = self.entry_tags.get_text().strip()
        conn.favorite = self.switch_favorite.get_active()

        # Auto-reconnect
        conn.auto_reconnect = self.switch_auto_reconnect.get_active()
        conn.auto_reconnect_delay = int(self.spin_reconnect_delay.get_value())
        conn.auto_reconnect_max = int(self.spin_reconnect_max.get_value())

        # Port forwards → JSON
        import json

        pf_list = []
        for pf_data in self._portfwd_rows:
            pf_type = pf_data["combo"].get_active_id() or "L"
            local_val = pf_data["local"].get_text().strip()
            remote_val = pf_data["remote"].get_text().strip()
            if local_val or remote_val:
                pf_list.append(
                    {"type": pf_type, "local": local_val, "remote": remote_val}
                )
        conn.port_forwards = json.dumps(pf_list) if pf_list else ""

        # Colors — only save when explicitly checked
        conn.bg_color = ""
        conn.fg_color = ""
        if self.chk_bg_color.get_active():
            conn.bg_color = self.color_bg.get_rgba().to_string()
        if self.chk_fg_color.get_active():
            conn.fg_color = self.color_fg.get_rgba().to_string()

        # Post-login commands
        buf_cmds = self.textview_commands.get_buffer()
        s, e = buf_cmds.get_bounds()
        conn.commands = buf_cmds.get_text(s, e, True)

        # Persist connection
        self.connection_manager.update_connection(conn)

        # --- Credentials ---
        # Passphrases (dynamic list)
        passphrases = [e.get_text() for e in self._passphrase_entries if e.get_text()]
        self.credential_store.store_passphrases(conn.id, passphrases)

        # Password
        pw = self.entry_password.get_text()
        if pw:
            self.credential_store.store_password(conn.id, pw)
        elif self.credential_store.get_password(conn.id):
            self.credential_store.store_password(conn.id, "")

        # Ensure group is tracked
        if conn.group:
            self.connection_manager.add_group(conn.group)

        self.emit("connection-saved", conn)
        self.close()

    def _show_error(self, message: str):
        """Display a validation error."""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Validation Error",
                body=message,
            )
            dialog.add_response("ok", "OK")
            dialog.present()
        except Exception:
            print(f"Error: {message}")
