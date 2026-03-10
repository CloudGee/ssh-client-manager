"""
SFTP file browser widget using paramiko.

Provides a graphical file tree for remote servers with:
- Directory navigation (double-click, breadcrumb, toolbar)
- Download files (drag-out, right-click, toolbar)
- Upload files (drag-in, right-click, toolbar)
- File operations (delete, rename, mkdir)
- Sorting by name/size/date
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, GLib, Gdk, GObject, Gio, Pango

import os
import stat
import threading
import time
from pathlib import Path
from typing import Optional

import paramiko


# TreeStore columns
COL_ICON = 0  # str: icon name
COL_NAME = 1  # str: filename
COL_SIZE = 2  # str: human-readable size
COL_MODIFIED = 3  # str: modified date
COL_PERMS = 4  # str: permission string
COL_FULLPATH = 5  # str: full remote path
COL_IS_DIR = 6  # bool: True if directory
COL_SIZE_RAW = 7  # int64: raw size for sorting


def _human_size(size_bytes: int) -> str:
    """Convert bytes to human-readable size."""
    if size_bytes < 0:
        return ""
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(size_bytes) < 1024:
            if unit == "B":
                return f"{size_bytes} B"
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} PB"


def _format_time(mtime: int) -> str:
    """Format modification time."""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime))
    except (OSError, ValueError):
        return ""


def _perm_string(mode: int) -> str:
    """Convert mode to rwx permission string."""
    parts = []
    for shift in (6, 3, 0):
        r = "r" if mode & (4 << shift) else "-"
        w = "w" if mode & (2 << shift) else "-"
        x = "x" if mode & (1 << shift) else "-"
        parts.append(f"{r}{w}{x}")
    prefix = "d" if stat.S_ISDIR(mode) else ("-" if stat.S_ISREG(mode) else "l")
    return prefix + "".join(parts)


class SftpBrowser(Gtk.Box):
    """
    SFTP file browser widget.

    Connects to a remote server via paramiko and displays files in a
    Gtk.TreeView. Supports navigation, download, upload, and drag-drop.

    Signals:
        status-changed: Status message changed (str)
    """

    __gsignals__ = {
        "status-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        self._sftp: Optional[paramiko.SFTPClient] = None
        self._transport: Optional[paramiko.Transport] = None
        self._jump_client: Optional[paramiko.SSHClient] = None
        self._current_path = "/"
        self._history: list[str] = []
        self._history_pos = -1
        self._connecting = False
        self._connected = False
        self._host = ""
        self._port = 22
        self._username = ""

        self._build_ui()

    def _build_ui(self):
        """Build the browser UI."""
        # --- Toolbar ---
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        toolbar.set_margin_start(4)
        toolbar.set_margin_end(4)
        toolbar.set_margin_top(4)
        toolbar.set_margin_bottom(4)

        self._btn_back = Gtk.Button(icon_name="go-previous-symbolic")
        self._btn_back.set_tooltip_text("Back")
        self._btn_back.add_css_class("flat")
        self._btn_back.set_sensitive(False)
        self._btn_back.connect("clicked", self._on_back)
        toolbar.append(self._btn_back)

        self._btn_up = Gtk.Button(icon_name="go-up-symbolic")
        self._btn_up.set_tooltip_text("Parent Directory")
        self._btn_up.add_css_class("flat")
        self._btn_up.connect("clicked", self._on_up)
        toolbar.append(self._btn_up)

        self._btn_home = Gtk.Button(icon_name="go-home-symbolic")
        self._btn_home.set_tooltip_text("Home Directory")
        self._btn_home.add_css_class("flat")
        self._btn_home.connect("clicked", self._on_home)
        toolbar.append(self._btn_home)

        self._btn_refresh = Gtk.Button(icon_name="view-refresh-symbolic")
        self._btn_refresh.set_tooltip_text("Refresh")
        self._btn_refresh.add_css_class("flat")
        self._btn_refresh.connect("clicked", self._on_refresh)
        toolbar.append(self._btn_refresh)

        # Spacer
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        toolbar.append(spacer)

        # Upload button
        self._btn_upload = Gtk.Button(icon_name="document-send-symbolic")
        self._btn_upload.set_tooltip_text("Upload Files")
        self._btn_upload.add_css_class("flat")
        self._btn_upload.connect("clicked", lambda _: self._upload_dialog())
        toolbar.append(self._btn_upload)

        # New folder button
        self._btn_mkdir = Gtk.Button(icon_name="folder-new-symbolic")
        self._btn_mkdir.set_tooltip_text("New Folder")
        self._btn_mkdir.add_css_class("flat")
        self._btn_mkdir.connect("clicked", lambda _: self._mkdir_dialog())
        toolbar.append(self._btn_mkdir)

        self.append(toolbar)

        # --- Path bar ---
        path_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        path_box.set_margin_start(4)
        path_box.set_margin_end(4)
        path_box.set_margin_bottom(4)

        self._path_entry = Gtk.Entry()
        self._path_entry.set_hexpand(True)
        self._path_entry.set_placeholder_text("/remote/path")
        self._path_entry.connect("activate", self._on_path_activate)
        path_box.append(self._path_entry)

        self.append(path_box)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- File list (TreeView) ---
        self._store = Gtk.ListStore(
            str,  # COL_ICON
            str,  # COL_NAME
            str,  # COL_SIZE
            str,  # COL_MODIFIED
            str,  # COL_PERMS
            str,  # COL_FULLPATH
            bool,  # COL_IS_DIR
            GObject.TYPE_INT64,  # COL_SIZE_RAW
        )

        # Default sort: directories first, then by name
        self._store.set_sort_func(COL_NAME, self._sort_name_func, None)
        self._store.set_sort_column_id(COL_NAME, Gtk.SortType.ASCENDING)

        self._tree = Gtk.TreeView(model=self._store)
        self._tree.set_headers_visible(True)
        self._tree.set_headers_clickable(True)
        self._tree.set_enable_search(True)
        self._tree.set_search_column(COL_NAME)
        self._tree.set_activate_on_single_click(False)
        # Disable tooltip — it can get "stuck" after list refreshes (e.g. upload)
        self._tree.set_has_tooltip(False)
        self._tree.set_enable_search(False)

        # Columns
        # Icon + Name
        col_name = Gtk.TreeViewColumn("Name")
        col_name.set_expand(True)
        col_name.set_resizable(True)
        col_name.set_sort_column_id(COL_NAME)

        icon_renderer = Gtk.CellRendererPixbuf()
        col_name.pack_start(icon_renderer, False)
        col_name.add_attribute(icon_renderer, "icon-name", COL_ICON)

        name_renderer = Gtk.CellRendererText()
        name_renderer.set_padding(4, 2)
        name_renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_name.pack_start(name_renderer, True)
        col_name.add_attribute(name_renderer, "text", COL_NAME)
        self._tree.append_column(col_name)

        # Size
        col_size = Gtk.TreeViewColumn("Size")
        col_size.set_resizable(True)
        col_size.set_min_width(70)
        col_size.set_sort_column_id(COL_SIZE_RAW)
        size_renderer = Gtk.CellRendererText()
        size_renderer.set_property("xalign", 1.0)
        col_size.pack_start(size_renderer, True)
        col_size.add_attribute(size_renderer, "text", COL_SIZE)
        self._tree.append_column(col_size)

        # Modified
        col_mod = Gtk.TreeViewColumn("Modified")
        col_mod.set_resizable(True)
        col_mod.set_min_width(120)
        col_mod.set_sort_column_id(COL_MODIFIED)
        mod_renderer = Gtk.CellRendererText()
        col_mod.pack_start(mod_renderer, True)
        col_mod.add_attribute(mod_renderer, "text", COL_MODIFIED)
        self._tree.append_column(col_mod)

        # Double-click handler
        self._tree.connect("row-activated", self._on_row_activated)

        # Right-click context menu
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self._tree.add_controller(click)

        # (Drag-and-drop disabled — use right-click menu or toolbar to upload/download)

        # Scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_child(self._tree)
        self.append(scrolled)

        # --- Status bar ---
        self._status_label = Gtk.Label(label="Not connected")
        self._status_label.set_xalign(0)
        self._status_label.set_margin_start(6)
        self._status_label.set_margin_end(6)
        self._status_label.set_margin_top(2)
        self._status_label.set_margin_bottom(2)
        self._status_label.add_css_class("dim-label")
        self._status_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self.append(self._status_label)

        # --- Progress bar (hidden by default) ---
        self._progress = Gtk.ProgressBar()
        self._progress.set_visible(False)
        self._progress.set_margin_start(6)
        self._progress.set_margin_end(6)
        self._progress.set_margin_bottom(2)
        self.append(self._progress)

    # =================================================================
    # Connection
    # =================================================================

    def connect_to_server(
        self,
        host: str,
        port: int,
        username: str,
        password: str = "",
        key_filename: str = "",
        passphrases: list[str] | None = None,
    ):
        """Connect to an SFTP server in a background thread.

        For every place that needs a password (key decryption, SSH login),
        all available credentials are tried in order:
          1. connection-specific passphrase(s)
          2. global passphrase(s)
          3. connection password
        before giving up.
        """
        if self._connecting or self._connected:
            return

        if passphrases is None:
            passphrases = []

        self._connecting = True
        self._host = host
        self._port = port
        self._username = username
        self._set_status(f"Connecting to {username}@{host}:{port}...")

        def _connect_thread():
            try:
                transport = paramiko.Transport((host, port))
                transport.set_keepalive(30)

                if key_filename and os.path.isfile(key_filename):
                    # Build ordered list of passwords to try for key decryption
                    key_passwords: list[str | None] = []
                    for pp in passphrases:
                        if pp and pp not in key_passwords:
                            key_passwords.append(pp)
                    if password and password not in key_passwords:
                        key_passwords.append(password)
                    if not key_passwords:
                        key_passwords.append(None)  # try without passphrase

                    pkey = None
                    key_classes = [
                        paramiko.RSAKey,
                        paramiko.Ed25519Key,
                        paramiko.ECDSAKey,
                    ]
                    if hasattr(paramiko, "DSSKey"):
                        key_classes.append(paramiko.DSSKey)

                    last_error = None
                    for pw in key_passwords:
                        for key_class in key_classes:
                            try:
                                pkey = key_class.from_private_key_file(
                                    key_filename, password=pw
                                )
                                break
                            except paramiko.ssh_exception.PasswordRequiredException:
                                last_error = "private key file is encrypted"
                                continue
                            except paramiko.ssh_exception.SSHException:
                                continue
                        if pkey is not None:
                            break

                    if pkey is None:
                        raise paramiko.ssh_exception.SSHException(
                            last_error or f"Unable to load key file: {key_filename}"
                        )
                    transport.connect(username=username, pkey=pkey)

                elif password:
                    transport.connect(username=username, password=password)

                elif passphrases:
                    # No key file, no password — try passphrases as password
                    connected = False
                    last_err = None
                    for pp in passphrases:
                        try:
                            transport.connect(username=username, password=pp)
                            connected = True
                            break
                        except paramiko.ssh_exception.AuthenticationException as e:
                            last_err = e
                            # Need a fresh transport for retry
                            try:
                                transport.close()
                            except Exception:
                                pass
                            transport = paramiko.Transport((host, port))
                            transport.set_keepalive(30)
                            continue
                    if not connected:
                        raise last_err or paramiko.ssh_exception.AuthenticationException(
                            "Authentication failed"
                        )
                else:
                    # Try SSH agent or default keys
                    transport.connect(username=username)

                sftp = paramiko.SFTPClient.from_transport(transport)

                GLib.idle_add(self._on_connected, transport, sftp)
            except Exception as e:
                GLib.idle_add(self._on_connect_error, str(e))

        thread = threading.Thread(target=_connect_thread, daemon=True)
        thread.start()

    def connect_from_connection(self, connection, credential_store):
        """Connect using a Connection object and CredentialStore."""
        host, port, username = self._extract_connection_info(connection)
        password = credential_store.get_password(connection.id) or ""

        # Collect all passphrases: connection-specific first, then global
        passphrases = list(credential_store.get_passphrases(connection.id))
        for gp in credential_store.get_global_passphrases():
            if gp not in passphrases:
                passphrases.append(gp)

        # Try to find a key file from the SSH command
        key_filename = ""
        if connection.command:
            import shlex

            try:
                parts = shlex.split(connection.command)
                for i, part in enumerate(parts):
                    if part == "-i" and i + 1 < len(parts):
                        key_filename = os.path.expanduser(parts[i + 1])
                        break
            except ValueError:
                pass

        # Check for jump host
        jump_host = getattr(connection, "jump_host", "") or ""
        if jump_host.strip():
            self._connect_via_jump_host(
                jump_host.strip(),
                host,
                port or 22,
                username or os.getlogin(),
                password,
                key_filename,
                passphrases=passphrases,
            )
        else:
            self.connect_to_server(
                host,
                port or 22,
                username or os.getlogin(),
                password,
                key_filename,
                passphrases=passphrases,
            )

    def _connect_via_jump_host(
        self,
        jump_host_str: str,
        target_host: str,
        target_port: int,
        target_username: str,
        target_password: str = "",
        target_key_filename: str = "",
        passphrases: list[str] | None = None,
    ):
        """Connect to SFTP via a jump host using paramiko channel forwarding."""
        if self._connecting or self._connected:
            return

        if passphrases is None:
            passphrases = []

        self._connecting = True
        self._host = target_host
        self._port = target_port
        self._username = target_username
        self._set_status(
            f"Connecting via jump host to {target_username}@{target_host}:{target_port}..."
        )

        def _connect_thread():
            try:
                # Parse jump host: user@host[:port]
                jh_user = ""
                jh_host = jump_host_str
                jh_port = 22

                if "@" in jh_host:
                    jh_user, jh_host = jh_host.rsplit("@", 1)
                if ":" in jh_host:
                    jh_host, port_str = jh_host.rsplit(":", 1)
                    try:
                        jh_port = int(port_str)
                    except ValueError:
                        pass
                if not jh_user:
                    jh_user = os.getlogin()

                # Step 1: Connect to jump host
                jump_client = paramiko.SSHClient()
                jump_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                jh_connect_kwargs: dict = {
                    "hostname": jh_host,
                    "port": jh_port,
                    "username": jh_user,
                    "timeout": 15,
                }

                # Try key-based auth for jump host (agent / default keys)
                # Also try passphrases as password if key auth fails
                connected_jump = False
                try:
                    jump_client.connect(**jh_connect_kwargs)
                    connected_jump = True
                except paramiko.ssh_exception.AuthenticationException:
                    pass

                if not connected_jump:
                    # Try passphrases as password for jump host
                    for pp in passphrases:
                        try:
                            jump_client.connect(**jh_connect_kwargs, password=pp)
                            connected_jump = True
                            break
                        except paramiko.ssh_exception.AuthenticationException:
                            continue

                if not connected_jump:
                    if target_password:
                        jump_client.connect(
                            **jh_connect_kwargs, password=target_password
                        )
                    else:
                        raise paramiko.ssh_exception.AuthenticationException(
                            f"Cannot authenticate to jump host {jh_user}@{jh_host}:{jh_port}"
                        )

                # Step 2: Open a channel from jump host to target
                jump_transport = jump_client.get_transport()
                channel = jump_transport.open_channel(
                    "direct-tcpip",
                    (target_host, target_port),
                    ("127.0.0.1", 0),
                )

                # Step 3: Create transport over the forwarded channel
                transport = paramiko.Transport(channel)
                transport.set_keepalive(30)

                # Authenticate to target host (same logic as connect_to_server)
                if target_key_filename and os.path.isfile(target_key_filename):
                    key_passwords: list[str | None] = []
                    for pp in passphrases:
                        if pp and pp not in key_passwords:
                            key_passwords.append(pp)
                    if target_password and target_password not in key_passwords:
                        key_passwords.append(target_password)
                    if not key_passwords:
                        key_passwords.append(None)

                    pkey = None
                    key_classes = [
                        paramiko.RSAKey,
                        paramiko.Ed25519Key,
                        paramiko.ECDSAKey,
                    ]
                    if hasattr(paramiko, "DSSKey"):
                        key_classes.append(paramiko.DSSKey)

                    last_error = None
                    for pw in key_passwords:
                        for key_class in key_classes:
                            try:
                                pkey = key_class.from_private_key_file(
                                    target_key_filename, password=pw
                                )
                                break
                            except paramiko.ssh_exception.PasswordRequiredException:
                                last_error = "private key file is encrypted"
                                continue
                            except paramiko.ssh_exception.SSHException:
                                continue
                        if pkey is not None:
                            break

                    if pkey is None:
                        raise paramiko.ssh_exception.SSHException(
                            last_error
                            or f"Unable to load key file: {target_key_filename}"
                        )
                    transport.connect(username=target_username, pkey=pkey)

                elif target_password:
                    transport.connect(
                        username=target_username, password=target_password
                    )

                elif passphrases:
                    connected = False
                    last_err = None
                    for pp in passphrases:
                        try:
                            transport.connect(username=target_username, password=pp)
                            connected = True
                            break
                        except paramiko.ssh_exception.AuthenticationException as e:
                            last_err = e
                            # Need fresh transport over the same channel
                            try:
                                transport.close()
                            except Exception:
                                pass
                            channel = jump_transport.open_channel(
                                "direct-tcpip",
                                (target_host, target_port),
                                ("127.0.0.1", 0),
                            )
                            transport = paramiko.Transport(channel)
                            transport.set_keepalive(30)
                            continue
                    if not connected:
                        raise last_err or paramiko.ssh_exception.AuthenticationException(
                            "Authentication failed"
                        )
                else:
                    transport.connect(username=target_username)

                sftp = paramiko.SFTPClient.from_transport(transport)

                # Store jump_client for cleanup
                self._jump_client = jump_client

                GLib.idle_add(self._on_connected, transport, sftp)
            except Exception as e:
                GLib.idle_add(self._on_connect_error, str(e))

        thread = threading.Thread(target=_connect_thread, daemon=True)
        thread.start()

    def _extract_connection_info(self, connection):
        """Extract host, port, username from a connection."""
        host = connection.host
        port = connection.port
        username = connection.username

        if not host and connection.command:
            # Parse from command string
            import shlex

            try:
                parts = shlex.split(connection.command)
            except ValueError:
                parts = connection.command.split()

            skip_next = False
            for i, part in enumerate(parts):
                if skip_next:
                    skip_next = False
                    continue
                if part in ("ssh", "sftp"):
                    continue
                if part in ("-p", "-P") and i + 1 < len(parts):
                    try:
                        port = int(parts[i + 1])
                    except ValueError:
                        pass
                    skip_next = True
                    continue
                if part.startswith("-"):
                    if part in (
                        "-i",
                        "-o",
                        "-F",
                        "-J",
                        "-l",
                        "-L",
                        "-R",
                        "-D",
                        "-W",
                        "-b",
                        "-c",
                        "-e",
                        "-m",
                        "-S",
                    ):
                        skip_next = True
                    continue
                # This should be [user@]host
                if "@" in part:
                    username, host = part.rsplit("@", 1)
                else:
                    host = part
                break

        return host or "localhost", port or 22, username or ""

    def _on_connected(self, transport, sftp):
        """Called on main thread after successful connection."""
        self._transport = transport
        self._sftp = sftp
        self._connecting = False
        self._connected = True
        self._set_status(f"Connected: {self._username}@{self._host}")

        # Navigate to home directory
        try:
            home = sftp.normalize(".")
            self._navigate_to(home)
        except Exception:
            self._navigate_to("/")

    def _on_connect_error(self, error_msg: str):
        """Called on main thread after connection failure."""
        self._connecting = False
        self._set_status(f"Connection failed: {error_msg}")

    def disconnect(self):
        """Close the SFTP connection."""
        # Close each resource independently to avoid one failure
        # preventing cleanup of the others.
        if self._sftp:
            try:
                self._sftp.close()
            except Exception:
                pass
        if self._transport:
            try:
                self._transport.close()
            except Exception:
                pass
        if self._jump_client:
            try:
                self._jump_client.close()
            except Exception:
                pass
        self._sftp = None
        self._transport = None
        self._jump_client = None
        self._connected = False
        self._connecting = False
        self._store.clear()
        self._set_status("Disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected and self._transport and self._transport.is_active()

    # =================================================================
    # Navigation
    # =================================================================

    def _navigate_to(self, path: str, add_history: bool = True):
        """Navigate to a remote directory."""
        if not self.is_connected:
            return

        if add_history:
            # Trim forward history
            if self._history_pos < len(self._history) - 1:
                self._history = self._history[: self._history_pos + 1]
            self._history.append(path)
            self._history_pos = len(self._history) - 1

        self._current_path = path
        self._path_entry.set_text(path)
        self._btn_back.set_sensitive(self._history_pos > 0)
        self._btn_up.set_sensitive(path != "/")

        self._set_status(f"Loading {path}...")

        # Detach model from tree to dismiss any lingering cell tooltips
        # (CellRendererText auto-tooltip for ellipsized text can get stuck)
        self._tree.set_model(None)
        self._store.clear()
        self._tree.set_model(self._store)

        def _list_thread():
            try:
                entries = self._sftp.listdir_attr(path)
                GLib.idle_add(self._populate_store, path, entries)
            except Exception as e:
                GLib.idle_add(self._set_status, f"Error: {e}")

        thread = threading.Thread(target=_list_thread, daemon=True)
        thread.start()

    def _populate_store(self, path: str, entries: list):
        """Populate the TreeView store with directory entries."""
        if path != self._current_path:
            return  # User navigated away

        # Detach model to kill any lingering cell-renderer tooltip
        self._tree.set_model(None)
        self._store.clear()

        for attr in entries:
            name = attr.filename
            if name in (".", ".."):
                continue

            is_dir = stat.S_ISDIR(attr.st_mode or 0)
            is_link = stat.S_ISLNK(attr.st_mode or 0)

            if is_dir:
                icon = "folder-symbolic"
            elif is_link:
                icon = "emblem-symbolic-link"
            else:
                icon = self._icon_for_file(name)

            full_path = f"{path.rstrip('/')}/{name}"
            size_str = "" if is_dir else _human_size(attr.st_size or 0)
            size_raw = 0 if is_dir else (attr.st_size or 0)
            mod_str = _format_time(attr.st_mtime or 0)
            perm_str = _perm_string(attr.st_mode or 0)

            self._store.append(
                [
                    icon,
                    name,
                    size_str,
                    mod_str,
                    perm_str,
                    full_path,
                    is_dir,
                    size_raw,
                ]
            )

        count = len(self._store)
        # Re-attach model after populating
        self._tree.set_model(self._store)
        self._set_status(f"{path}  —  {count} items")

    def _icon_for_file(self, name: str) -> str:
        """Choose an icon based on file extension."""
        ext = os.path.splitext(name)[1].lower()
        icons = {
            ".py": "text-x-python-symbolic",
            ".js": "text-x-javascript-symbolic",
            ".sh": "text-x-script-symbolic",
            ".bash": "text-x-script-symbolic",
            ".zsh": "text-x-script-symbolic",
            ".html": "text-html-symbolic",
            ".css": "text-css-symbolic",
            ".json": "text-x-generic-symbolic",
            ".xml": "text-x-generic-symbolic",
            ".yaml": "text-x-generic-symbolic",
            ".yml": "text-x-generic-symbolic",
            ".md": "text-x-generic-symbolic",
            ".txt": "text-x-generic-symbolic",
            ".log": "text-x-generic-symbolic",
            ".conf": "text-x-generic-symbolic",
            ".cfg": "text-x-generic-symbolic",
            ".ini": "text-x-generic-symbolic",
            ".tar": "package-x-generic-symbolic",
            ".gz": "package-x-generic-symbolic",
            ".zip": "package-x-generic-symbolic",
            ".bz2": "package-x-generic-symbolic",
            ".xz": "package-x-generic-symbolic",
            ".7z": "package-x-generic-symbolic",
            ".jpg": "image-x-generic-symbolic",
            ".jpeg": "image-x-generic-symbolic",
            ".png": "image-x-generic-symbolic",
            ".gif": "image-x-generic-symbolic",
            ".svg": "image-x-generic-symbolic",
            ".mp3": "audio-x-generic-symbolic",
            ".wav": "audio-x-generic-symbolic",
            ".mp4": "video-x-generic-symbolic",
            ".mkv": "video-x-generic-symbolic",
        }
        return icons.get(ext, "text-x-generic-symbolic")

    # --- Toolbar handlers ---

    def _on_back(self, _btn):
        if self._history_pos > 0:
            self._history_pos -= 1
            self._navigate_to(self._history[self._history_pos], add_history=False)

    def _on_up(self, _btn):
        parent = os.path.dirname(self._current_path.rstrip("/"))
        if not parent:
            parent = "/"
        self._navigate_to(parent)

    def _on_home(self, _btn):
        if not self.is_connected:
            return
        try:
            home = self._sftp.normalize(".")
            self._navigate_to(home)
        except Exception:
            self._navigate_to("/")

    def _on_refresh(self, _btn):
        self._navigate_to(self._current_path, add_history=False)

    def _on_path_activate(self, entry):
        path = entry.get_text().strip()
        if path:
            self._navigate_to(path)

    # --- Row handlers ---

    def _on_row_activated(self, tree_view, path, column):
        """Handle double-click: enter directory or download file."""
        iter_ = self._store.get_iter(path)
        is_dir = self._store.get_value(iter_, COL_IS_DIR)
        full_path = self._store.get_value(iter_, COL_FULLPATH)

        if is_dir:
            self._navigate_to(full_path)
        else:
            # Download file on double-click
            self._download_file(full_path)

    def _sort_name_func(self, model, a, b, _data):
        """Sort: directories first, then alphabetical by name."""
        a_dir = model.get_value(a, COL_IS_DIR)
        b_dir = model.get_value(b, COL_IS_DIR)
        if a_dir != b_dir:
            return -1 if a_dir else 1
        a_name = (model.get_value(a, COL_NAME) or "").lower()
        b_name = (model.get_value(b, COL_NAME) or "").lower()
        return (a_name > b_name) - (a_name < b_name)

    # =================================================================
    # Context Menu
    # =================================================================

    def _on_right_click(self, gesture, n_press, x, y):
        """Show context menu on right-click."""
        # GestureClick gives WIDGET coordinates (includes column headers).
        # get_path_at_pos() expects BIN-WINDOW coordinates (data area only).
        bx, by = self._tree.convert_widget_to_bin_window_coords(int(x), int(y))
        path_info = self._tree.get_path_at_pos(bx, by)

        items: list[tuple[str, callable]] = []

        iter_ = None
        if path_info:
            path, column, cell_x, cell_y = path_info
            self._tree.get_selection().select_path(path)
            iter_ = self._store.get_iter(path)

        if iter_ is not None:
            is_dir = self._store.get_value(iter_, COL_IS_DIR)
            full_path = self._store.get_value(iter_, COL_FULLPATH)
            name = self._store.get_value(iter_, COL_NAME)

            if is_dir:
                items = [
                    ("Open", lambda _, fp=full_path: self._navigate_to(fp)),
                    (
                        "Download Folder",
                        lambda _, fp=full_path: self._download_directory(fp),
                    ),
                    (None, None),
                    (
                        "Rename",
                        lambda _, fp=full_path, n=name: self._rename_item(fp, n),
                    ),
                    (
                        "Delete",
                        lambda _, fp=full_path: self._delete_item(fp, is_dir=True),
                    ),
                    (None, None),
                    ("Upload File...", lambda _: self._upload_dialog()),
                    ("New Folder", lambda _: self._mkdir_dialog()),
                    ("Refresh", lambda _: self._on_refresh(None)),
                ]
            else:
                items = [
                    ("Download", lambda _, fp=full_path: self._download_file(fp)),
                    (None, None),
                    (
                        "Rename",
                        lambda _, fp=full_path, n=name: self._rename_item(fp, n),
                    ),
                    (
                        "Delete",
                        lambda _, fp=full_path: self._delete_item(fp, is_dir=False),
                    ),
                    (None, None),
                    ("Upload File...", lambda _: self._upload_dialog()),
                    ("New Folder", lambda _: self._mkdir_dialog()),
                    ("Refresh", lambda _: self._on_refresh(None)),
                ]
        else:
            items = [
                ("Upload File...", lambda _: self._upload_dialog()),
                ("New Folder", lambda _: self._mkdir_dialog()),
                (None, None),
                ("Refresh", lambda _: self._on_refresh(None)),
            ]

        # Build popover
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        popover = Gtk.Popover()
        popover.set_autohide(True)

        for label, callback in items:
            if label is None:
                box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                continue
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.set_halign(Gtk.Align.FILL)

            def make_handler(cb, pop):
                def handler(b):
                    pop.popdown()
                    cb(b)

                return handler

            btn.connect("clicked", make_handler(callback, popover))
            box.append(btn)

        popover.set_child(box)

        # Find a proper parent for the popover
        scrolled = self._tree.get_parent()
        popover.set_parent(scrolled)

        result = self._tree.translate_coordinates(scrolled, x, y)
        if result is not None:
            sx, sy = result
        else:
            sx, sy = x, y

        rect = Gdk.Rectangle()
        rect.x = int(sx)
        rect.y = int(sy)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    # =================================================================
    # File Operations
    # =================================================================

    def _download_file(self, remote_path: str):
        """Download a single file via a save dialog."""
        name = os.path.basename(remote_path)

        try:
            dialog = Gtk.FileDialog()
            dialog.set_title(f"Save: {name}")
            dialog.set_initial_name(name)

            # Find the top-level window
            win = self.get_root()
            dialog.save(
                win, None, lambda d, r: self._on_save_response(d, r, remote_path)
            )
        except Exception:
            # Fallback for older GTK
            chooser = Gtk.FileChooserNative(
                title=f"Save: {name}",
                transient_for=self.get_root(),
                action=Gtk.FileChooserAction.SAVE,
            )
            chooser.set_current_name(name)
            chooser.connect(
                "response",
                lambda c, resp: self._on_save_chooser_response(c, resp, remote_path),
            )
            chooser.show()

    def _on_save_response(self, dialog, result, remote_path: str):
        try:
            file = dialog.save_finish(result)
            if file:
                self._do_download(remote_path, file.get_path())
        except Exception:
            pass  # User cancelled
        GLib.timeout_add(200, self._dismiss_stale_tooltips)

    def _on_save_chooser_response(self, chooser, response, remote_path: str):
        if response == Gtk.ResponseType.ACCEPT:
            file = chooser.get_file()
            if file:
                self._do_download(remote_path, file.get_path())
        GLib.timeout_add(200, self._dismiss_stale_tooltips)

    def _do_download(self, remote_path: str, local_path: str):
        """Execute the download in a background thread."""
        name = os.path.basename(remote_path)
        self._set_status(f"Downloading: {name}...")
        self._progress.set_visible(True)
        self._progress.set_fraction(0)

        def _download_thread():
            try:
                # Get file size for progress
                attr = self._sftp.stat(remote_path)
                total = attr.st_size or 1

                def _progress_cb(transferred, total_bytes):
                    frac = min(transferred / max(total_bytes, 1), 1.0)
                    GLib.idle_add(self._progress.set_fraction, frac)

                self._sftp.get(remote_path, local_path, callback=_progress_cb)
                GLib.idle_add(self._download_done, name, local_path, None)
            except Exception as e:
                GLib.idle_add(self._download_done, name, local_path, str(e))

        thread = threading.Thread(target=_download_thread, daemon=True)
        thread.start()

    def _download_done(self, name: str, local_path: str, error: Optional[str]):
        self._progress.set_visible(False)
        if error:
            self._set_status(f"Download failed: {name} — {error}")
        else:
            self._set_status(f"Downloaded: {name} → {local_path}")

    def _download_directory(self, remote_path: str):
        """Download a directory recursively via a folder chooser."""
        name = os.path.basename(remote_path.rstrip("/"))

        try:
            dialog = Gtk.FileDialog()
            dialog.set_title(f"Save folder: {name}")
            win = self.get_root()
            dialog.select_folder(
                win, None, lambda d, r: self._on_folder_save_response(d, r, remote_path)
            )
        except Exception:
            chooser = Gtk.FileChooserNative(
                title=f"Save folder: {name}",
                transient_for=self.get_root(),
                action=Gtk.FileChooserAction.SELECT_FOLDER,
            )
            chooser.connect(
                "response",
                lambda c, resp: self._on_folder_chooser_response(c, resp, remote_path),
            )
            chooser.show()

    def _on_folder_save_response(self, dialog, result, remote_path: str):
        try:
            file = dialog.select_folder_finish(result)
            if file:
                local_dir = file.get_path()
                self._do_download_directory(remote_path, local_dir)
        except Exception:
            pass
        GLib.timeout_add(200, self._dismiss_stale_tooltips)

    def _on_folder_chooser_response(self, chooser, response, remote_path: str):
        if response == Gtk.ResponseType.ACCEPT:
            file = chooser.get_file()
            if file:
                self._do_download_directory(remote_path, file.get_path())
        GLib.timeout_add(200, self._dismiss_stale_tooltips)

    def _do_download_directory(self, remote_path: str, local_base: str):
        """Recursively download a directory."""
        name = os.path.basename(remote_path.rstrip("/"))
        self._set_status(f"Downloading folder: {name}...")
        self._progress.set_visible(True)
        self._progress.pulse()

        def _download_dir_thread():
            try:
                self._recursive_download(remote_path, local_base)
                GLib.idle_add(self._download_done, name, local_base, None)
            except Exception as e:
                GLib.idle_add(self._download_done, name, local_base, str(e))

        thread = threading.Thread(target=_download_dir_thread, daemon=True)
        thread.start()

    def _recursive_download(self, remote_dir: str, local_dir: str):
        """Recursively download remote_dir into local_dir."""
        dir_name = os.path.basename(remote_dir.rstrip("/"))
        target = os.path.join(local_dir, dir_name)
        os.makedirs(target, exist_ok=True)

        for attr in self._sftp.listdir_attr(remote_dir):
            if attr.filename in (".", ".."):
                continue
            remote = f"{remote_dir.rstrip('/')}/{attr.filename}"
            local = os.path.join(target, attr.filename)

            if stat.S_ISDIR(attr.st_mode or 0):
                self._recursive_download(remote, target)
            else:
                GLib.idle_add(self._set_status, f"Downloading: {attr.filename}")
                self._sftp.get(remote, local)

    def _upload_dialog(self):
        """Show a file chooser to upload files."""
        try:
            dialog = Gtk.FileDialog()
            dialog.set_title("Upload File")
            win = self.get_root()
            dialog.open_multiple(win, None, self._on_upload_files_chosen)
        except Exception:
            chooser = Gtk.FileChooserNative(
                title="Upload File",
                transient_for=self.get_root(),
                action=Gtk.FileChooserAction.OPEN,
            )
            chooser.set_select_multiple(True)
            chooser.connect("response", self._on_upload_chooser_response)
            chooser.show()

    def _dismiss_stale_tooltips(self):
        """Force-dismiss any lingering tooltip / popover surfaces.

        On macOS, GTK4 FileDialog tooltips can survive after the dialog
        closes, leaving a floating label on screen.  Toggling has-tooltip
        on the tree and queueing a redraw clears them.
        """
        self._tree.set_has_tooltip(True)
        self._tree.set_has_tooltip(False)
        self._tree.queue_draw()
        win = self.get_root()
        if win is not None:
            win.queue_draw()

    def _on_upload_files_chosen(self, dialog, result):
        try:
            files = dialog.open_multiple_finish(result)
            if files:
                paths = [f.get_path() for f in files if f.get_path()]
                self._do_upload(paths)
        except Exception:
            pass
        # Dismiss stale tooltips left by the file dialog
        GLib.timeout_add(200, self._dismiss_stale_tooltips)

    def _on_upload_chooser_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            files = chooser.get_files()
            paths = [f.get_path() for f in files if f.get_path()]
            if paths:
                self._do_upload(paths)
        # Dismiss stale tooltips left by the file dialog
        GLib.timeout_add(200, self._dismiss_stale_tooltips)

    def _do_upload(self, local_paths: list[str]):
        """Upload files in a background thread."""
        self._set_status(f"Uploading {len(local_paths)} file(s)...")
        self._progress.set_visible(True)
        self._progress.set_fraction(0)

        dst_dir = self._current_path

        def _upload_thread():
            try:
                for i, local_path in enumerate(local_paths):
                    name = os.path.basename(local_path)
                    remote = f"{dst_dir.rstrip('/')}/{name}"
                    GLib.idle_add(
                        self._set_status,
                        f"Uploading ({i+1}/{len(local_paths)}): {name}",
                    )

                    if os.path.isdir(local_path):
                        self._recursive_upload(local_path, dst_dir)
                    else:
                        total = os.path.getsize(local_path)

                        def _progress_cb(transferred, total_bytes):
                            done = i / len(local_paths)
                            part = transferred / max(total_bytes, 1) / len(local_paths)
                            GLib.idle_add(
                                self._progress.set_fraction, min(done + part, 1.0)
                            )

                        self._sftp.put(local_path, remote, callback=_progress_cb)

                GLib.idle_add(self._upload_done, len(local_paths), None)
            except Exception as e:
                GLib.idle_add(self._upload_done, 0, str(e))

        thread = threading.Thread(target=_upload_thread, daemon=True)
        thread.start()

    def _recursive_upload(self, local_dir: str, remote_parent: str):
        """Recursively upload a local directory."""
        dir_name = os.path.basename(local_dir)
        remote_dir = f"{remote_parent.rstrip('/')}/{dir_name}"

        try:
            self._sftp.mkdir(remote_dir)
        except IOError:
            pass  # May already exist

        for item in os.listdir(local_dir):
            local_path = os.path.join(local_dir, item)
            if os.path.isdir(local_path):
                self._recursive_upload(local_path, remote_dir)
            else:
                remote_path = f"{remote_dir}/{item}"
                self._sftp.put(local_path, remote_path)

    def _upload_done(self, count: int, error: Optional[str]):
        self._progress.set_visible(False)
        if error:
            self._set_status(f"Upload failed: {error}")
        else:
            self._set_status(f"Uploaded {count} file(s)")
        # Refresh current directory
        self._navigate_to(self._current_path, add_history=False)
        # Flush any stale tooltips that may linger after the upload
        GLib.timeout_add(300, self._dismiss_stale_tooltips)

    def _delete_item(self, remote_path: str, is_dir: bool = False):
        """Delete a remote file or directory with confirmation."""
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        name = os.path.basename(remote_path)
        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading="Delete?",
            body=f"Delete {'folder' if is_dir else 'file'} \"{name}\"?\n"
            f"This cannot be undone.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "delete":
                self._do_delete(remote_path, is_dir)

        dialog.connect("response", on_response)
        dialog.present()

    def _do_delete(self, remote_path: str, is_dir: bool):
        """Delete in background thread."""
        name = os.path.basename(remote_path)
        self._set_status(f"Deleting: {name}...")

        def _delete_thread():
            try:
                if is_dir:
                    self._recursive_delete(remote_path)
                else:
                    self._sftp.remove(remote_path)
                GLib.idle_add(self._delete_done, name, None)
            except Exception as e:
                GLib.idle_add(self._delete_done, name, str(e))

        thread = threading.Thread(target=_delete_thread, daemon=True)
        thread.start()

    def _recursive_delete(self, remote_dir: str):
        """Recursively delete a remote directory."""
        for attr in self._sftp.listdir_attr(remote_dir):
            if attr.filename in (".", ".."):
                continue
            path = f"{remote_dir.rstrip('/')}/{attr.filename}"
            if stat.S_ISDIR(attr.st_mode or 0):
                self._recursive_delete(path)
            else:
                self._sftp.remove(path)
        self._sftp.rmdir(remote_dir)

    def _delete_done(self, name: str, error: Optional[str]):
        if error:
            self._set_status(f"Delete failed: {name} — {error}")
        else:
            self._set_status(f"Deleted: {name}")
        self._navigate_to(self._current_path, add_history=False)

    def _rename_item(self, remote_path: str, old_name: str):
        """Rename a remote file or directory."""
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading="Rename",
            body=f'Enter new name for "{old_name}":',
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_text(old_name)
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dialog.set_extra_child(entry)

        def on_response(d, response):
            if response == "rename":
                new_name = entry.get_text().strip()
                if new_name and new_name != old_name:
                    parent = os.path.dirname(remote_path)
                    new_path = f"{parent.rstrip('/')}/{new_name}"
                    self._do_rename(remote_path, new_path)

        dialog.connect("response", on_response)
        dialog.present()

    def _do_rename(self, old_path: str, new_path: str):
        """Rename in background thread."""

        def _rename_thread():
            try:
                self._sftp.rename(old_path, new_path)
                GLib.idle_add(self._navigate_to, self._current_path, False)
                GLib.idle_add(
                    self._set_status, f"Renamed → {os.path.basename(new_path)}"
                )
            except Exception as e:
                GLib.idle_add(self._set_status, f"Rename failed: {e}")

        thread = threading.Thread(target=_rename_thread, daemon=True)
        thread.start()

    def _mkdir_dialog(self):
        """Show a dialog to create a new directory."""
        import gi

        gi.require_version("Adw", "1")
        from gi.repository import Adw

        dialog = Adw.MessageDialog(
            transient_for=self.get_root(),
            heading="New Folder",
            body="Enter folder name:",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("create", "Create")
        dialog.set_response_appearance("create", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_placeholder_text("folder-name")
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        dialog.set_extra_child(entry)

        def on_response(d, response):
            if response == "create":
                name = entry.get_text().strip()
                if name:
                    path = f"{self._current_path.rstrip('/')}/{name}"
                    self._do_mkdir(path)

        dialog.connect("response", on_response)
        dialog.present()

    def _do_mkdir(self, path: str):
        """Create directory in background thread."""

        def _mkdir_thread():
            try:
                self._sftp.mkdir(path)
                GLib.idle_add(self._navigate_to, self._current_path, False)
                GLib.idle_add(self._set_status, f"Created: {os.path.basename(path)}")
            except Exception as e:
                GLib.idle_add(self._set_status, f"mkdir failed: {e}")

        thread = threading.Thread(target=_mkdir_thread, daemon=True)
        thread.start()

    # =================================================================
    # Utilities
    # =================================================================

    def _set_status(self, text: str):
        self._status_label.set_text(text)
        self.emit("status-changed", text)
