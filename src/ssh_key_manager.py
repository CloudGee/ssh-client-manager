"""
SSH Key Management dialog.

Provides UI for:
- Listing existing SSH keys (~/.ssh/)
- Generating new SSH key pairs (ssh-keygen)
- Deploying public keys to remote hosts (ssh-copy-id)
- Viewing public key content
"""

import gi
import os
import subprocess
from pathlib import Path

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, GObject


class SSHKeyManagerDialog(Adw.Window):
    """Dialog for managing SSH keys."""

    def __init__(self, parent: Gtk.Window):
        super().__init__(
            transient_for=parent,
            modal=True,
            title="SSH Key Manager",
            default_width=600,
            default_height=520,
        )

        self._ssh_dir = Path.home() / ".ssh"

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        header = Adw.HeaderBar()
        btn_generate = Gtk.Button(icon_name="list-add-symbolic")
        btn_generate.set_tooltip_text("Generate New Key Pair")
        btn_generate.connect("clicked", self._on_generate_key)
        header.pack_start(btn_generate)

        btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        btn_refresh.set_tooltip_text("Refresh")
        btn_refresh.connect("clicked", lambda _: self._load_keys())
        header.pack_end(btn_refresh)
        main_box.append(header)

        # Key list
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

        placeholder = Gtk.Label(label="No SSH keys found in ~/.ssh/")
        placeholder.add_css_class("dim-label")
        placeholder.set_margin_top(24)
        placeholder.set_margin_bottom(24)
        self._list_box.set_placeholder(placeholder)

        scrolled.set_child(self._list_box)
        main_box.append(scrolled)

        self.set_content(main_box)
        self._load_keys()

    def _load_keys(self):
        """Scan ~/.ssh/ directory for key files."""
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        if not self._ssh_dir.exists():
            return

        # Find private keys (files without .pub that have a matching .pub)
        key_files = []
        for f in sorted(self._ssh_dir.iterdir()):
            if f.is_file() and not f.name.startswith("."):
                pub = f.with_suffix(f.suffix + ".pub") if f.suffix != ".pub" else None
                if f.suffix == ".pub":
                    continue
                if pub and pub.exists():
                    key_files.append(f)
                elif f.name in (
                    "id_rsa",
                    "id_ed25519",
                    "id_ecdsa",
                    "id_dsa",
                    "id_ed25519_sk",
                    "id_ecdsa_sk",
                ):
                    key_files.append(f)

        for key_path in key_files:
            self._list_box.append(self._build_key_row(key_path))

    def _build_key_row(self, key_path: Path) -> Gtk.ListBoxRow:
        """Build a row for a key file."""
        pub_path = Path(str(key_path) + ".pub")

        # Read key type and comment from public key
        key_type = "Unknown"
        comment = ""
        if pub_path.exists():
            try:
                content = pub_path.read_text().strip()
                parts = content.split(None, 2)
                if len(parts) >= 1:
                    key_type = parts[0].replace("ssh-", "").upper()
                if len(parts) >= 3:
                    comment = parts[2]
            except IOError:
                pass

        row = Adw.ActionRow()
        row.set_title(key_path.name)
        subtitle = key_type
        if comment:
            subtitle += f"  •  {comment}"
        row.set_subtitle(subtitle)

        # Copy public key button
        btn_copy = Gtk.Button(icon_name="edit-copy-symbolic")
        btn_copy.set_tooltip_text("Copy Public Key")
        btn_copy.add_css_class("flat")
        btn_copy.set_valign(Gtk.Align.CENTER)
        btn_copy.connect("clicked", lambda _, p=pub_path: self._copy_pub_key(p))
        row.add_suffix(btn_copy)

        # Deploy button
        btn_deploy = Gtk.Button(icon_name="mail-send-symbolic")
        btn_deploy.set_tooltip_text("Deploy to Remote Host")
        btn_deploy.add_css_class("flat")
        btn_deploy.set_valign(Gtk.Align.CENTER)
        btn_deploy.connect("clicked", lambda _, p=key_path: self._on_deploy_key(p))
        row.add_suffix(btn_deploy)

        # View public key
        btn_view = Gtk.Button(icon_name="document-open-symbolic")
        btn_view.set_tooltip_text("View Public Key")
        btn_view.add_css_class("flat")
        btn_view.set_valign(Gtk.Align.CENTER)
        btn_view.connect("clicked", lambda _, p=pub_path: self._view_pub_key(p))
        row.add_suffix(btn_view)

        return row

    def _copy_pub_key(self, pub_path: Path):
        """Copy public key to clipboard."""
        if not pub_path.exists():
            return
        try:
            content = pub_path.read_text().strip()
            clipboard = self.get_clipboard()
            from gi.repository import Gdk

            clipboard.set(content)
            # Brief visual feedback
            self.set_title("Public key copied!")
            GLib.timeout_add(2000, lambda: self.set_title("SSH Key Manager") or False)
        except IOError:
            pass

    def _view_pub_key(self, pub_path: Path):
        """Show public key content in a dialog."""
        if not pub_path.exists():
            return
        try:
            content = pub_path.read_text().strip()
        except IOError:
            content = "(Could not read file)"

        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            title=f"Public Key: {pub_path.name}",
            default_width=500,
            default_height=200,
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        box.append(header)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_wrap_mode(Gtk.WrapMode.CHAR)
        text_view.set_monospace(True)
        text_view.get_buffer().set_text(content)
        text_view.set_margin_start(12)
        text_view.set_margin_end(12)
        text_view.set_margin_top(8)
        text_view.set_margin_bottom(12)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_child(text_view)
        box.append(scrolled)

        dialog.set_content(box)
        dialog.present()

    def _on_generate_key(self, _btn):
        """Show key generation dialog."""
        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            title="Generate SSH Key",
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

        btn_gen = Gtk.Button(label="Generate")
        btn_gen.add_css_class("suggested-action")
        header.pack_end(btn_gen)
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(16)

        # Key type
        combo_type = Gtk.ComboBoxText()
        combo_type.append("ed25519", "Ed25519 (recommended)")
        combo_type.append("rsa", "RSA (4096-bit)")
        combo_type.append("ecdsa", "ECDSA")
        combo_type.set_active_id("ed25519")
        content.append(self._field_row("Type:", combo_type))

        # Filename
        entry_name = Gtk.Entry()
        entry_name.set_placeholder_text("id_ed25519")
        entry_name.set_text("id_ed25519")
        content.append(self._field_row("Filename:", entry_name))

        # Update default filename when type changes
        def on_type_changed(combo):
            key_type = combo.get_active_id() or "ed25519"
            default_names = {
                "ed25519": "id_ed25519",
                "rsa": "id_rsa",
                "ecdsa": "id_ecdsa",
            }
            entry_name.set_text(default_names.get(key_type, f"id_{key_type}"))

        combo_type.connect("changed", on_type_changed)

        # Comment
        entry_comment = Gtk.Entry()
        entry_comment.set_placeholder_text("your_email@example.com")
        content.append(self._field_row("Comment:", entry_comment))

        # Passphrase
        entry_pass = Gtk.PasswordEntry()
        entry_pass.set_show_peek_icon(True)
        content.append(self._field_row("Passphrase:", entry_pass))

        # Status
        status_label = Gtk.Label()
        status_label.set_xalign(0)
        status_label.add_css_class("dim-label")
        status_label.set_margin_start(8)
        content.append(status_label)

        box.append(content)
        dialog.set_content(box)

        def do_generate(_btn):
            key_type = combo_type.get_active_id() or "ed25519"
            filename = entry_name.get_text().strip() or f"id_{key_type}"
            comment = entry_comment.get_text().strip()
            passphrase = entry_pass.get_text()

            key_path = self._ssh_dir / filename
            if key_path.exists():
                status_label.set_text(f"Error: {filename} already exists!")
                status_label.remove_css_class("dim-label")
                status_label.add_css_class("error")
                return

            # Ensure .ssh directory exists
            self._ssh_dir.mkdir(mode=0o700, exist_ok=True)

            cmd = ["ssh-keygen", "-t", key_type, "-f", str(key_path)]
            if key_type == "rsa":
                cmd.extend(["-b", "4096"])
            if comment:
                cmd.extend(["-C", comment])
            cmd.extend(["-N", passphrase])

            try:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if result.returncode == 0:
                    dialog.close()
                    self._load_keys()
                else:
                    status_label.set_text(f"Error: {result.stderr.strip()}")
            except Exception as e:
                status_label.set_text(f"Error: {e}")

        btn_gen.connect("clicked", do_generate)
        dialog.present()

    def _on_deploy_key(self, key_path: Path):
        """Show deploy key dialog."""
        pub_path = Path(str(key_path) + ".pub")
        if not pub_path.exists():
            return

        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            title=f"Deploy {key_path.name}",
            default_width=400,
            default_height=-1,
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _: dialog.close())
        header.pack_start(btn_cancel)

        btn_deploy = Gtk.Button(label="Deploy")
        btn_deploy.add_css_class("suggested-action")
        header.pack_end(btn_deploy)
        box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        content.set_margin_start(16)
        content.set_margin_end(16)
        content.set_margin_top(12)
        content.set_margin_bottom(16)

        hint = Gtk.Label(
            label="Deploy public key to a remote server using ssh-copy-id.\n"
            "You may be prompted for the remote password."
        )
        hint.set_xalign(0)
        hint.add_css_class("dim-label")
        hint.set_wrap(True)
        content.append(hint)

        entry_host = Gtk.Entry()
        entry_host.set_placeholder_text("user@hostname")
        content.append(self._field_row("Remote:", entry_host))

        spin_port = Gtk.SpinButton()
        spin_port.set_range(1, 65535)
        spin_port.set_value(22)
        spin_port.set_increments(1, 10)
        content.append(self._field_row("Port:", spin_port))

        status_label = Gtk.Label()
        status_label.set_xalign(0)
        status_label.add_css_class("dim-label")
        content.append(status_label)

        box.append(content)
        dialog.set_content(box)

        def do_deploy(_btn):
            remote = entry_host.get_text().strip()
            port = int(spin_port.get_value())
            if not remote:
                status_label.set_text("Please enter remote host")
                return

            status_label.set_text("Deploying...")
            btn_deploy.set_sensitive(False)

            cmd = ["ssh-copy-id", "-i", str(pub_path), "-p", str(port), remote]

            def run_deploy():
                try:
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=30
                    )
                    if result.returncode == 0:
                        GLib.idle_add(
                            lambda: status_label.set_text("Key deployed successfully!")
                        )
                    else:
                        err = result.stderr.strip()[:200]
                        GLib.idle_add(lambda: status_label.set_text(f"Error: {err}"))
                except subprocess.TimeoutExpired:
                    GLib.idle_add(
                        lambda: status_label.set_text(
                            "Timed out. Try deploying manually."
                        )
                    )
                except Exception as e:
                    GLib.idle_add(lambda: status_label.set_text(f"Error: {e}"))
                finally:
                    GLib.idle_add(lambda: btn_deploy.set_sensitive(True))

            import threading

            threading.Thread(target=run_deploy, daemon=True).start()

        btn_deploy.connect("clicked", do_deploy)
        entry_host.connect("activate", do_deploy)
        dialog.present()

    def _field_row(self, label_text: str, widget: Gtk.Widget) -> Gtk.Box:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label=label_text)
        label.set_xalign(1)
        label.set_size_request(100, -1)
        label.add_css_class("dim-label")
        row.append(label)
        widget.set_hexpand(True)
        row.append(widget)
        return row
