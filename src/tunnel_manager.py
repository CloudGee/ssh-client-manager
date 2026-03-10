"""
SSH Tunnel Management Panel.

Provides a UI for viewing, adding, and managing SSH port forwards
(local, remote, dynamic) as a standalone panel/dialog.
Automatically detects -L/-R/-D port forwards defined in connection commands.
"""

import gi
import subprocess
import os
import signal
import shlex

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, GObject


class TunnelEntry:
    """Represents a single SSH tunnel."""

    def __init__(
        self,
        tunnel_type: str = "L",
        local_port: str = "",
        remote_host: str = "localhost",
        remote_port: str = "",
        ssh_host: str = "",
        ssh_user: str = "",
        ssh_port: int = 22,
        name: str = "",
    ):
        self.tunnel_type = tunnel_type  # L, R, D
        self.local_port = local_port
        self.remote_host = remote_host
        self.remote_port = remote_port
        self.ssh_host = ssh_host
        self.ssh_user = ssh_user
        self.ssh_port = ssh_port
        self.name = name
        self.process: subprocess.Popen | None = None
        self.active = False

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        if self.tunnel_type == "D":
            return f"SOCKS :{self.local_port} via {self.ssh_host}"
        direction = "Local" if self.tunnel_type == "L" else "Remote"
        return f"{direction} :{self.local_port} → {self.remote_host}:{self.remote_port}"

    @property
    def forward_spec(self) -> str:
        if self.tunnel_type == "D":
            return self.local_port
        return f"{self.local_port}:{self.remote_host}:{self.remote_port}"

    def build_command(self) -> list[str]:
        """Build the SSH tunnel command."""
        cmd = ["ssh", "-N", "-o", "ExitOnForwardFailure=yes"]
        if self.ssh_port and self.ssh_port != 22:
            cmd.extend(["-p", str(self.ssh_port)])
        cmd.extend([f"-{self.tunnel_type}", self.forward_spec])
        dest = f"{self.ssh_user}@{self.ssh_host}" if self.ssh_user else self.ssh_host
        cmd.append(dest)
        return cmd


class TunnelManagerDialog(Adw.Window):
    """Dialog for managing SSH tunnels."""

    def __init__(self, parent: Gtk.Window, connection_manager=None):
        super().__init__(
            transient_for=parent,
            modal=True,
            title="SSH Tunnel Manager",
            default_width=650,
            default_height=500,
        )

        self._tunnels: list[TunnelEntry] = []
        self._connection_manager = connection_manager

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        header = Adw.HeaderBar()
        btn_add = Gtk.Button(icon_name="list-add-symbolic")
        btn_add.set_tooltip_text("Add Tunnel")
        btn_add.connect("clicked", self._on_add_tunnel)
        header.pack_start(btn_add)
        main_box.append(header)

        # Tunnel list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_margin_start(12)
        self._list_box.set_margin_end(12)
        self._list_box.set_margin_top(12)
        self._list_box.set_margin_bottom(12)

        # Placeholder
        placeholder = Gtk.Label(label="No tunnels configured.\nClick + to add one.")
        placeholder.add_css_class("dim-label")
        placeholder.set_margin_top(24)
        placeholder.set_margin_bottom(24)
        self._list_box.set_placeholder(placeholder)

        scrolled.set_child(self._list_box)
        main_box.append(scrolled)

        self.set_content(main_box)

        # Parse tunnels from existing connections
        if connection_manager:
            self._load_connection_tunnels()

    def _load_connection_tunnels(self):
        """Parse -L/-R/-D flags from all connection commands and pre-populate."""
        if not self._connection_manager:
            return
        for conn in self._connection_manager.get_connections():
            if not conn.command:
                continue
            tunnels = self._parse_tunnels_from_command(conn.command, conn.name)
            self._tunnels.extend(tunnels)
        if self._tunnels:
            self._refresh_list()

    @staticmethod
    def _parse_tunnels_from_command(
        command: str, conn_name: str = ""
    ) -> list[TunnelEntry]:
        """Parse -L, -R, -D port-forward flags from an SSH command string.

        Supports formats like:
          ssh -L 3389:remote:10001 user@host
          ssh -L3389:remote:10001 user@host
          ssh -D 1080 user@host
        """
        tunnels: list[TunnelEntry] = []
        try:
            parts = shlex.split(command)
        except ValueError:
            return tunnels

        # Determine SSH destination (user@host) and port
        ssh_host = ""
        ssh_user = ""
        ssh_port = 22
        flags_with_arg = {
            "-b",
            "-c",
            "-D",
            "-E",
            "-e",
            "-F",
            "-I",
            "-i",
            "-J",
            "-L",
            "-l",
            "-m",
            "-O",
            "-o",
            "-p",
            "-Q",
            "-R",
            "-S",
            "-W",
            "-w",
        }
        skip_next = False
        for idx, part in enumerate(parts):
            if skip_next:
                skip_next = False
                continue
            if part in flags_with_arg:
                if part == "-p" and idx + 1 < len(parts):
                    try:
                        ssh_port = int(parts[idx + 1])
                    except ValueError:
                        pass
                skip_next = True
                continue
            if part.startswith("-"):
                flag_letter = part[1:2] if len(part) > 1 else ""
                if f"-{flag_letter}" in flags_with_arg and len(part) > 2:
                    continue
                continue
            if part in ("ssh", "sftp", "scp"):
                continue
            if "@" in part:
                ssh_user, ssh_host = part.split("@", 1)
            else:
                ssh_host = part
            break

        # Extract tunnel specs
        i = 0
        while i < len(parts):
            part = parts[i]
            tunnel_type = ""
            spec = ""

            if part in ("-L", "-R", "-D"):
                tunnel_type = part[1]
                if i + 1 < len(parts):
                    spec = parts[i + 1]
                    i += 2
                else:
                    i += 1
                    continue
            elif len(part) > 2 and part[0] == "-" and part[1] in "LRD":
                tunnel_type = part[1]
                spec = part[2:]
                i += 1
            else:
                i += 1
                continue

            if not spec:
                continue

            entry = TunnelEntry(
                tunnel_type=tunnel_type,
                ssh_host=ssh_host,
                ssh_user=ssh_user,
                ssh_port=ssh_port,
                name=f"[{conn_name}]" if conn_name else "",
            )

            if tunnel_type == "D":
                entry.local_port = spec
            else:
                # Format: local_port:remote_host:remote_port
                # or: bind_addr:local_port:remote_host:remote_port
                fwd_parts = spec.split(":")
                if len(fwd_parts) == 3:
                    entry.local_port = fwd_parts[0]
                    entry.remote_host = fwd_parts[1]
                    entry.remote_port = fwd_parts[2]
                elif len(fwd_parts) == 4:
                    entry.local_port = fwd_parts[1]
                    entry.remote_host = fwd_parts[2]
                    entry.remote_port = fwd_parts[3]
                else:
                    continue

            tunnels.append(entry)

        return tunnels

    def _on_add_tunnel(self, _btn):
        """Show add tunnel dialog."""
        self._show_tunnel_editor(None)

    def _show_tunnel_editor(self, tunnel: TunnelEntry | None):
        """Show tunnel create/edit dialog."""
        is_new = tunnel is None
        if is_new:
            tunnel = TunnelEntry()

        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            title="Add Tunnel" if is_new else "Edit Tunnel",
            default_width=420,
            default_height=-1,
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _: dialog.close())
        header.pack_start(btn_cancel)

        btn_save = Gtk.Button(label="Save")
        btn_save.add_css_class("suggested-action")
        header.pack_end(btn_save)
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(16)

        # Name
        entry_name = Gtk.Entry()
        entry_name.set_placeholder_text("Tunnel name (optional)")
        entry_name.set_text(tunnel.name)
        content.append(self._field_row("Name:", entry_name))

        # Type selector
        combo_type = Gtk.ComboBoxText()
        combo_type.append("L", "Local Forward (-L)")
        combo_type.append("R", "Remote Forward (-R)")
        combo_type.append("D", "Dynamic SOCKS (-D)")
        combo_type.set_active_id(tunnel.tunnel_type)
        content.append(self._field_row("Type:", combo_type))

        # Local port
        entry_local = Gtk.Entry()
        entry_local.set_placeholder_text("e.g. 8080")
        entry_local.set_text(tunnel.local_port)
        content.append(self._field_row("Local Port:", entry_local))

        # Remote host/port (hidden for SOCKS)
        entry_rhost = Gtk.Entry()
        entry_rhost.set_placeholder_text("e.g. localhost")
        entry_rhost.set_text(tunnel.remote_host)
        row_rhost = self._field_row("Remote Host:", entry_rhost)
        content.append(row_rhost)

        entry_rport = Gtk.Entry()
        entry_rport.set_placeholder_text("e.g. 3306")
        entry_rport.set_text(tunnel.remote_port)
        row_rport = self._field_row("Remote Port:", entry_rport)
        content.append(row_rport)

        def on_type_changed(combo):
            is_socks = combo.get_active_id() == "D"
            row_rhost.set_visible(not is_socks)
            row_rport.set_visible(not is_socks)

        combo_type.connect("changed", on_type_changed)
        on_type_changed(combo_type)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # SSH connection details
        entry_ssh_host = Gtk.Entry()
        entry_ssh_host.set_placeholder_text("SSH server hostname")
        entry_ssh_host.set_text(tunnel.ssh_host)
        content.append(self._field_row("SSH Host:", entry_ssh_host))

        entry_ssh_user = Gtk.Entry()
        entry_ssh_user.set_placeholder_text("username")
        entry_ssh_user.set_text(tunnel.ssh_user)
        content.append(self._field_row("SSH User:", entry_ssh_user))

        spin_ssh_port = Gtk.SpinButton()
        spin_ssh_port.set_range(1, 65535)
        spin_ssh_port.set_value(tunnel.ssh_port)
        spin_ssh_port.set_increments(1, 10)
        content.append(self._field_row("SSH Port:", spin_ssh_port))

        box.append(content)
        dialog.set_content(box)

        def do_save(_btn):
            tunnel.name = entry_name.get_text().strip()
            tunnel.tunnel_type = combo_type.get_active_id() or "L"
            tunnel.local_port = entry_local.get_text().strip()
            tunnel.remote_host = entry_rhost.get_text().strip() or "localhost"
            tunnel.remote_port = entry_rport.get_text().strip()
            tunnel.ssh_host = entry_ssh_host.get_text().strip()
            tunnel.ssh_user = entry_ssh_user.get_text().strip()
            tunnel.ssh_port = int(spin_ssh_port.get_value())

            if not tunnel.local_port or not tunnel.ssh_host:
                return

            if is_new:
                self._tunnels.append(tunnel)
            self._refresh_list()
            dialog.close()

        btn_save.connect("clicked", do_save)
        dialog.present()

    def _field_row(self, label_text: str, widget: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=label_text)
        label.set_xalign(1)
        label.set_size_request(110, -1)
        label.add_css_class("dim-label")
        row.append(label)
        widget.set_hexpand(True)
        row.append(widget)
        return row

    def _refresh_list(self):
        """Rebuild the tunnel list UI."""
        # Remove all rows
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        for tunnel in self._tunnels:
            row = self._build_tunnel_row(tunnel)
            self._list_box.append(row)

    def _build_tunnel_row(self, tunnel: TunnelEntry) -> Gtk.ListBoxRow:
        """Build a single tunnel row widget."""
        row = Adw.ActionRow()
        row.set_title(tunnel.display_name)

        type_labels = {"L": "Local", "R": "Remote", "D": "SOCKS"}
        subtitle = type_labels.get(tunnel.tunnel_type, tunnel.tunnel_type)
        if tunnel.name and tunnel.name.startswith("["):
            subtitle += f"  •  From connection {tunnel.name}"
        if tunnel.active:
            subtitle += "  •  Active"
        row.set_subtitle(subtitle)

        # Toggle button
        btn_toggle = Gtk.Button()
        if tunnel.active:
            btn_toggle.set_icon_name("media-playback-stop-symbolic")
            btn_toggle.set_tooltip_text("Stop Tunnel")
            btn_toggle.add_css_class("destructive-action")
        else:
            btn_toggle.set_icon_name("media-playback-start-symbolic")
            btn_toggle.set_tooltip_text("Start Tunnel")
            btn_toggle.add_css_class("suggested-action")
        btn_toggle.set_valign(Gtk.Align.CENTER)
        btn_toggle.connect("clicked", lambda _, t=tunnel: self._toggle_tunnel(t))
        row.add_suffix(btn_toggle)

        # Delete button
        btn_del = Gtk.Button(icon_name="edit-delete-symbolic")
        btn_del.set_tooltip_text("Remove")
        btn_del.add_css_class("flat")
        btn_del.set_valign(Gtk.Align.CENTER)
        btn_del.connect("clicked", lambda _, t=tunnel: self._delete_tunnel(t))
        row.add_suffix(btn_del)

        return row

    def _toggle_tunnel(self, tunnel: TunnelEntry):
        """Start or stop a tunnel."""
        if tunnel.active:
            self._stop_tunnel(tunnel)
        else:
            self._start_tunnel(tunnel)
        self._refresh_list()

    def _start_tunnel(self, tunnel: TunnelEntry):
        """Start an SSH tunnel subprocess."""
        cmd = tunnel.build_command()
        try:
            tunnel.process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            tunnel.active = True

            # Monitor for exit
            def check_process():
                if tunnel.process and tunnel.process.poll() is not None:
                    tunnel.active = False
                    tunnel.process = None
                    GLib.idle_add(self._refresh_list)
                    return False
                return tunnel.active

            GLib.timeout_add(2000, check_process)
        except Exception as e:
            print(f"Failed to start tunnel: {e}")

    def _stop_tunnel(self, tunnel: TunnelEntry):
        """Stop an SSH tunnel subprocess."""
        if tunnel.process:
            try:
                tunnel.process.terminate()
                tunnel.process.wait(timeout=5)
            except Exception:
                try:
                    tunnel.process.kill()
                except Exception:
                    pass
            tunnel.process = None
        tunnel.active = False

    def _delete_tunnel(self, tunnel: TunnelEntry):
        """Remove a tunnel."""
        self._stop_tunnel(tunnel)
        if tunnel in self._tunnels:
            self._tunnels.remove(tunnel)
        self._refresh_list()

    def cleanup(self):
        """Stop all tunnels on close."""
        for tunnel in self._tunnels:
            self._stop_tunnel(tunnel)
