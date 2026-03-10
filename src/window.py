"""
Main application window.

Combines the sidebar (connection tree), terminal panel (split tabs),
toolbar, and menu into the main GTK4/libadwaita window.
"""

import os
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Vte", "3.91")

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, GObject, Vte, Pango

from .config import Config
from .connection import Connection, ConnectionManager
from .credential_store import CredentialStore
from .ssh_handler import SSHHandler
from .terminal_widget import TerminalWidget
from .terminal_panel import TerminalPanel
from .sidebar import Sidebar
from .cluster_window import ClusterWindow
from .connection_dialog import ConnectionDialog
from .preferences_dialog import PreferencesDialog
from .ssh_config_editor import SSHConfigEditor
from .snippet_manager import SnippetManager
from .ssh_key_manager import SSHKeyManagerDialog
from .session_recorder import (
    SessionRecorder,
    RecordingListDialog,
    get_recordings_dir,
)


class MainWindow(Adw.ApplicationWindow):
    """
    The main application window.

    Layout:
    ┌──────────────────────────────────────────────┐
    │ HeaderBar  [≡] [+Local] [SplitH] [SplitV]   │
    │            [Unsplit] [Cluster] [Prefs] [☰]   │
    ├──────────┬───────────────────────────────────┤
    │ Sidebar  │ TerminalPanel                     │
    │ ┌──────┐ │ ┌─────────────┬─────────────────┐ │
    │ │Search│ │ │ Tab1  Tab2  │ Tab3  Tab4       │ │
    │ ├──────┤ │ ├─────────────┼─────────────────┤ │
    │ │Group1│ │ │             │                 │ │
    │ │ Host1│ │ │  Terminal   │  Terminal        │ │
    │ │ Host2│ │ │             │                 │ │
    │ │Group2│ │ │             │                 │ │
    │ │ Host3│ │ └─────────────┴─────────────────┘ │
    └──────────┴───────────────────────────────────┘
    """

    def __init__(self, application: Adw.Application, config: Config):
        super().__init__(
            application=application,
            title="SSH Client Manager",
            default_width=config["window_width"],
            default_height=config["window_height"],
        )

        self.config = config
        self.connection_manager = ConnectionManager()
        self.credential_store = CredentialStore()
        self.ssh_handler = SSHHandler(self.credential_store)
        self.snippet_manager = SnippetManager()

        # Cluster mode state
        self._cluster_mode = False
        self._cluster_window: ClusterWindow | None = None

        # Track SFTP terminals (terminal widget → connection)
        self._sftp_terminals: dict = {}

        # Track connected connection IDs for status indicators
        self._connected_ids: set = set()

        # Auto-reconnect attempt tracking: terminal id → attempt count
        self._reconnect_attempts: dict = {}
        self._reconnect_timers: dict = {}

        self._setup_actions()
        self._build_ui()
        self._connect_signals()
        self._setup_keyboard_shortcuts()

        # Add a welcome local terminal on start if no connections open
        GLib.idle_add(self._open_initial_terminal)

    def _setup_actions(self):
        """Register window-level actions."""
        actions = {
            "new-connection": self._on_new_connection,
            "new-local": self._on_new_local_terminal,
            "connect-selected": self._on_connect_selected,
            "preferences": self._on_preferences,
            "split-h": self._on_split_horizontal,
            "split-v": self._on_split_vertical,
            "split-left": lambda *_: self.terminal_panel.split_directional("left"),
            "split-right": lambda *_: self.terminal_panel.split_directional("right"),
            "split-up": lambda *_: self.terminal_panel.split_directional("up"),
            "split-down": lambda *_: self.terminal_panel.split_directional("down"),
            "unsplit": self._on_unsplit,
            "cluster-toggle": self._on_cluster_toggle,
            "close-tab": self._on_close_tab,
            "next-tab": self._on_next_tab,
            "prev-tab": self._on_prev_tab,
            "toggle-sidebar": self._on_toggle_sidebar,
            "search-terminal": self._on_search_terminal,
            "import-connections": self._on_import_connections,
            "export-connections": self._on_export_connections,
            "ssh-config-editor": self._on_ssh_config_editor,
            "snippets": self._on_show_snippets,
            "backup-all": lambda *_: self._backup_all(self),
            "restore-all": lambda *_: self._restore_all(self),
            "help": self._on_show_help,
            "about": self._on_about,
            "quit": lambda *_: self.close(),
            "ssh-key-manager": self._on_ssh_key_manager,
            "session-recordings": self._on_session_recordings,
        }

        action_group = Gio.SimpleActionGroup()
        for name, callback in actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            action_group.add_action(action)

        self.insert_action_group("win", action_group)

        # Sidebar actions
        sidebar_group = Gio.SimpleActionGroup()
        sidebar_actions = {
            "connect": self._on_sidebar_connect,
            "edit": self._on_sidebar_edit,
            "delete": self._on_sidebar_delete,
            "duplicate": self._on_sidebar_duplicate,
            "add-connection": lambda *_: self._on_new_connection(None, None),
            "add-group": lambda *_: self._on_add_group(
                self.sidebar.get_selected_group_path() or ""
            ),
            "add-subgroup": lambda *_: self._on_add_group(
                self.sidebar.get_selected_group_path() or ""
            ),
            "delete-group": self._on_delete_group,
        }
        for name, callback in sidebar_actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            sidebar_group.add_action(action)

        self.insert_action_group("sidebar", sidebar_group)

        # Terminal context menu actions
        term_group = Gio.SimpleActionGroup()
        term_actions = {
            "copy": lambda *_: self._active_terminal_action("copy_clipboard"),
            "paste": lambda *_: self._active_terminal_action("paste_clipboard"),
            "select-all": lambda *_: self._active_terminal_action("select_all"),
            "reset": lambda *_: self._active_terminal_action("reset_terminal"),
            "clear": lambda *_: self._active_terminal_action("reset_terminal", True),
            "toggle-log": self._on_toggle_log,
            "toggle-record": self._on_toggle_record,
        }
        for name, callback in term_actions.items():
            action = Gio.SimpleAction(name=name)
            action.connect("activate", callback)
            term_group.add_action(action)

        self.insert_action_group("term", term_group)

    def _build_ui(self):
        """Build the complete window UI."""
        # Main vertical layout
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # --- Header Bar ---
        self.header_bar = Adw.HeaderBar()
        self._build_header_bar()
        main_box.append(self.header_bar)

        # --- Main content: Sidebar + Terminal Panel ---
        self.paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL)
        self.paned.set_vexpand(True)
        self.paned.set_wide_handle(True)
        self.paned.set_position(self.config["sidebar_width"])

        # Sidebar
        self.sidebar = Sidebar(self.connection_manager, self.credential_store)

        # Terminal panel
        self.terminal_panel = TerminalPanel(self.config)

        self.paned.set_start_child(self.sidebar)
        self.paned.set_end_child(self.terminal_panel)

        # Keep the sidebar from being resizable to collapse
        self.paned.set_shrink_start_child(False)
        self.paned.set_shrink_end_child(False)

        main_box.append(self.paned)

        # --- Status bar ---
        self.status_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.status_bar.set_margin_start(8)
        self.status_bar.set_margin_end(8)
        self.status_bar.set_margin_top(2)
        self.status_bar.set_margin_bottom(2)
        self.status_label = Gtk.Label(label="Ready")
        self.status_label.set_xalign(0)
        self.status_label.add_css_class("dim-label")
        self.status_label.set_hexpand(True)
        self.status_bar.append(self.status_label)

        self.tab_count_label = Gtk.Label(label="0 tabs")
        self.tab_count_label.add_css_class("dim-label")
        self.status_bar.append(self.tab_count_label)

        main_box.append(self.status_bar)

        self.set_content(main_box)

    def _build_header_bar(self):
        """Build the header bar with buttons and menus."""
        header = self.header_bar

        # Left side: Sidebar toggle + New Local
        btn_sidebar = Gtk.Button(icon_name="sidebar-show-symbolic")
        btn_sidebar.set_tooltip_text("Toggle Sidebar (F9)")
        btn_sidebar.set_action_name("win.toggle-sidebar")
        header.pack_start(btn_sidebar)

        # Split menu button (dropdown with directional splits)
        split_menu = Gio.Menu()
        split_menu.append("Split Left", "win.split-left")
        split_menu.append("Split Right", "win.split-right")
        split_menu.append("Split Up", "win.split-up")
        split_menu.append("Split Down", "win.split-down")
        split_menu.append("Unsplit All", "win.unsplit")

        btn_split = Gtk.MenuButton()
        btn_split.set_icon_name("object-flip-horizontal-symbolic")
        btn_split.set_tooltip_text("Split Terminal")
        btn_split.set_menu_model(split_menu)
        header.pack_start(btn_split)

        # Right side: Record, Cluster, Help (info), Menu
        # Recording toggle button — highly visible in header bar
        self._record_button = Gtk.ToggleButton()
        self._record_button.set_icon_name("media-record-symbolic")
        self._record_button.set_tooltip_text(
            "Start / Stop Recording (current terminal)"
        )
        self._record_button.connect("toggled", self._on_record_button_toggled)
        header.pack_end(self._record_button)

        btn_cluster = Gtk.ToggleButton()
        btn_cluster.set_icon_name("network-workgroup-symbolic")
        btn_cluster.set_tooltip_text("Cluster Mode — Send to All Terminals")
        btn_cluster.connect("toggled", self._on_cluster_button_toggled)
        self._cluster_button = btn_cluster
        header.pack_end(btn_cluster)

        # Help / Usage Guide button — right next to Cluster so it's always visible
        btn_help = Gtk.Button(icon_name="help-about-symbolic")
        btn_help.set_tooltip_text("Usage Guide")
        btn_help.set_action_name("win.help")
        header.pack_end(btn_help)

        # App menu
        menu_button = Gtk.MenuButton()
        menu_button.set_icon_name("open-menu-symbolic")
        menu_button.set_tooltip_text("Menu")
        menu_button.set_menu_model(self._build_app_menu())
        header.pack_end(menu_button)

        # Quick Connect entry
        self._quick_connect_entry = Gtk.Entry()
        self._quick_connect_entry.set_placeholder_text(
            "Quick: ssh user@host or host:port"
        )
        self._quick_connect_entry.set_width_chars(28)
        self._quick_connect_entry.set_tooltip_text(
            "Quick Connect: Enter an SSH command or user@host to connect immediately"
        )
        self._quick_connect_entry.connect("activate", self._on_quick_connect)
        header.set_title_widget(self._quick_connect_entry)

    def _build_app_menu(self) -> Gio.Menu:
        """Build the application menu."""
        menu = Gio.Menu()

        section1 = Gio.Menu()
        section1.append("New Connection", "win.new-connection")
        menu.append_section(None, section1)

        section2 = Gio.Menu()
        section2.append("Import Connections", "win.import-connections")
        section2.append("Export Connections", "win.export-connections")
        section2.append("Backup All", "win.backup-all")
        section2.append("Restore All", "win.restore-all")
        menu.append_section(None, section2)

        section3 = Gio.Menu()
        section3.append("Command Snippets", "win.snippets")
        section3.append("SSH Config Editor", "win.ssh-config-editor")
        section3.append("SSH Key Manager", "win.ssh-key-manager")
        section3.append("Session Recordings", "win.session-recordings")
        section3.append("Preferences", "win.preferences")
        section3.append("About", "win.about")
        menu.append_section(None, section3)

        section4 = Gio.Menu()
        section4.append("Quit", "win.quit")
        menu.append_section(None, section4)

        return menu

    def _connect_signals(self):
        """Connect signals from child widgets."""
        # Sidebar signals
        self.sidebar.connect("connect-requested", self._on_sidebar_connect_by_id)
        self.sidebar.connect(
            "edit-requested", lambda _, cid: self._edit_connection(cid)
        )
        self.sidebar.connect(
            "add-requested", lambda _: self._on_new_connection(None, None)
        )
        self.sidebar.connect(
            "add-group-requested", lambda _, prefix: self._on_add_group(prefix)
        )

        # Terminal panel signals
        self.terminal_panel.connect("tab-added", self._on_tab_added)
        self.terminal_panel.connect("tab-removed", self._on_tab_removed)
        self.terminal_panel.connect(
            "active-terminal-changed", self._on_active_terminal_changed
        )
        self.terminal_panel.connect(
            "terminal-title-changed", self._on_terminal_title_changed
        )
        self.terminal_panel.connect("reconnect-requested", self._on_reconnect_requested)
        self.terminal_panel.connect("clone-requested", self._on_clone_requested)
        self.terminal_panel.connect(
            "new-terminal-requested", lambda _: self.open_local_terminal()
        )
        self.terminal_panel.connect("child-exited", self._on_child_exited)

        # Sidebar open-sftp signal
        self.sidebar.connect(
            "open-sftp-requested", lambda _, cid: self._open_sftp_for_ssh(cid)
        )
        self.sidebar.connect(
            "delete-requested", lambda _, cid: self._on_sidebar_delete_by_id(cid)
        )

        # Handle window close
        self.connect("close-request", self._on_close_request)

    def _setup_keyboard_shortcuts(self):
        """Set up keyboard shortcuts."""
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    # =====================================================================
    # Connection Operations
    # =====================================================================

    def open_connection(self, connection_id: str, _dep_chain: set = None):
        """Open a new terminal tab for the given connection."""
        conn = self.connection_manager.get_connection(connection_id)
        if not conn:
            self._set_status(f"Connection not found: {connection_id}")
            return

        # Handle connection dependency
        dep_id = getattr(conn, "depends_on", "") or ""
        if dep_id:
            if _dep_chain is None:
                _dep_chain = set()
            # Circular dependency detection
            if dep_id in _dep_chain:
                self._set_status(f"Circular dependency detected: {conn.name}")
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="Circular Dependency",
                    body=f'Connection "{conn.name}" has a circular dependency chain.\n'
                    f"The dependency will be skipped.",
                )
                dialog.add_response("ok", "OK")
                dialog.present()
                # Continue without the dependency
            elif connection_id not in _dep_chain:
                _dep_chain.add(connection_id)
                # Check if dependency is already open
                dep_open = any(
                    info[0] and info[0].id == dep_id
                    for info in self.terminal_panel._terminals.values()
                )
                if not dep_open:
                    self.open_connection(dep_id, _dep_chain)
                    # Delay opening dependent connection
                    GLib.timeout_add(
                        2000, lambda: self._dispatch_connection(conn) or False
                    )
                    return

        self._dispatch_connection(conn)

    def _dispatch_connection(self, conn):
        """Route a connection to the appropriate protocol handler."""
        proto = getattr(conn, "protocol", "ssh") or "ssh"

        if proto == "rdp":
            self._open_rdp_connection(conn)
        elif proto == "vnc":
            self._open_vnc_connection(conn)
        elif proto == "sftp":
            self._open_sftp_connection(conn)
        else:
            self._open_ssh_connection(conn)

    def _open_ssh_connection(self, conn):
        """Open an SSH connection in a terminal tab."""
        terminal = TerminalWidget(self.config, conn)
        ssh_cmd = self.ssh_handler.build_ssh_command(conn)
        env, session_id = self.ssh_handler.build_environment(conn)
        terminal._askpass_session_id = session_id
        title = conn.name or conn.display_name()
        self.terminal_panel.add_tab(terminal, conn, title)
        terminal.spawn_command(ssh_cmd, env)

        commands = self.ssh_handler.get_post_login_commands(conn)
        if commands:
            self._schedule_post_login_commands(terminal, commands)

        if session_id:
            GLib.timeout_add(
                15000,
                lambda sid=session_id: self.ssh_handler.cleanup_askpass(sid) or False,
            )

        # Auto-start logging if enabled
        if self.config.get("terminal_logging_enabled", False):
            log_dir = self.config.get("terminal_log_directory", "")
            if log_dir:
                import datetime

                os.makedirs(log_dir, exist_ok=True)
                ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_name = conn.name.replace("/", "_").replace(" ", "_")
                log_path = os.path.join(log_dir, f"{safe_name}_{ts}.log")
                terminal.start_logging(log_path)

        # Mark connected in sidebar
        self._connected_ids.add(conn.id)
        self.sidebar.mark_connected(conn.id)
        self._set_status("Connected: {}".format(conn.name))

    def _open_sftp_connection(self, conn):
        """Open an SFTP session in a terminal tab and show file browser."""
        terminal = TerminalWidget(self.config, conn)
        sftp_cmd = self.ssh_handler.build_sftp_command(conn)
        env, session_id = self.ssh_handler.build_environment(conn)
        terminal._askpass_session_id = session_id
        title = f"SFTP: {conn.name or conn.display_name()}"
        self.terminal_panel.add_tab(terminal, conn, title)
        terminal.spawn_command(sftp_cmd, env)

        if session_id:
            GLib.timeout_add(
                15000,
                lambda sid=session_id: self.ssh_handler.cleanup_askpass(sid) or False,
            )
        self._set_status(f"SFTP: {conn.name}")

        # Track as SFTP terminal and show browser
        self._sftp_terminals[id(terminal)] = conn
        self.sidebar.show_sftp_browser(conn, self.credential_store)

    def _open_sftp_for_ssh(self, connection_id: str):
        """Open an SFTP session derived from an existing SSH connection."""
        conn = self.connection_manager.get_connection(connection_id)
        if not conn:
            return
        terminal = TerminalWidget(self.config, conn)
        sftp_cmd = self.ssh_handler.build_sftp_from_ssh(conn)
        env, session_id = self.ssh_handler.build_environment(conn)
        terminal._askpass_session_id = session_id
        title = f"SFTP: {conn.name or conn.display_name()}"
        self.terminal_panel.add_tab(terminal, conn, title)
        terminal.spawn_command(sftp_cmd, env)

        if session_id:
            GLib.timeout_add(
                15000,
                lambda sid=session_id: self.ssh_handler.cleanup_askpass(sid) or False,
            )
        self._set_status(f"SFTP: {conn.name}")

        # Track as SFTP terminal and show browser
        self._sftp_terminals[id(terminal)] = conn
        self.sidebar.show_sftp_browser(conn, self.credential_store)

    def _open_rdp_connection(self, conn):
        """Open an RDP connection.

        On macOS: directly launch Windows App (Microsoft Remote Desktop)
        with credentials stored in Keychain. No terminal tab needed.
        On Linux: open in a terminal tab using xfreerdp.
        """
        import sys

        if sys.platform == "darwin":
            self._open_rdp_macos(conn)
        else:
            self._launch_rdp_terminal(conn)

    def _open_rdp_macos(self, conn):
        """Open RDP on macOS using Windows App."""
        import subprocess

        # Check if Windows App is installed
        has_windows_app = os.path.isdir(
            "/Applications/Windows App.app"
        ) or os.path.isdir("/Applications/Microsoft Remote Desktop.app")

        if not has_windows_app:
            # Check if we can install via brew
            import shutil

            has_brew = shutil.which("brew")

            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Windows App Not Found",
                body="Windows App (Microsoft Remote Desktop) is not installed.\n\n"
                "You can install it from:\n"
                "• Mac App Store (search 'Windows App')\n"
                + (
                    "• Homebrew: brew install --cask windows-app\n\n"
                    "Would you like to install via Homebrew?"
                    if has_brew
                    else "\nPlease install it from the Mac App Store."
                ),
            )
            dialog.add_response("cancel", "Cancel")
            if has_brew:
                dialog.add_response("install", "Install via Homebrew")
                dialog.set_response_appearance(
                    "install", Adw.ResponseAppearance.SUGGESTED
                )

            def on_response(d, response):
                if response == "install":
                    terminal = TerminalWidget(self.config)
                    self.terminal_panel.add_tab(
                        terminal, None, "Installing Windows App..."
                    )
                    shell_cmd = SSHHandler.get_local_shell_command()
                    terminal.spawn_command(shell_cmd)
                    GLib.timeout_add(
                        500,
                        lambda: (
                            terminal.feed_child(
                                "brew install --cask windows-app && "
                                "echo '\\n✅ Installation complete. Please try the RDP connection again.'\n"
                            ),
                            False,
                        )[1],
                    )
                    self._set_status("Installing Windows App...")

            dialog.connect("response", on_response)
            dialog.present()
            return

        # Windows App is installed — launch directly
        success = self.ssh_handler.launch_rdp_macos(conn)
        if success:
            self._set_status(
                f"RDP: {conn.name or conn.display_name()} — opened in Windows App"
            )
        else:
            self._set_status(f"RDP: Failed to launch connection")

    def _launch_rdp_terminal(self, conn):
        """Launch RDP in a terminal tab (Linux / fallback)."""
        terminal = TerminalWidget(self.config, conn)
        rdp_cmd = self.ssh_handler.build_rdp_command(conn)
        title = f"RDP: {conn.name or conn.display_name()}"
        self.terminal_panel.add_tab(terminal, conn, title)
        terminal.spawn_command(rdp_cmd)
        self._set_status(f"RDP: {conn.name}")

    def _open_vnc_connection(self, conn):
        """Open a VNC connection (launches external viewer in terminal tab)."""
        import sys, shutil

        if sys.platform == "darwin":
            has_vnc = shutil.which("vncviewer") or shutil.which("xtigervncviewer")
            if not has_vnc:
                has_brew = shutil.which("brew")
                if has_brew:
                    self._ask_install_dependency(
                        "VNC Viewer Not Found",
                        "No CLI VNC viewer found. On macOS, the connection will use\n"
                        "the built-in Screen Sharing app.\n\n"
                        "Would you like to install TigerVNC via Homebrew?",
                        "brew install tiger-vnc",
                        lambda: self._launch_vnc_terminal(conn),
                    )
                    return
        self._launch_vnc_terminal(conn)

    def _launch_vnc_terminal(self, conn):
        """Actually launch the VNC terminal tab."""
        terminal = TerminalWidget(self.config, conn)
        vnc_cmd = self.ssh_handler.build_vnc_command(conn)
        title = f"VNC: {conn.name or conn.display_name()}"
        self.terminal_panel.add_tab(terminal, conn, title)
        terminal.spawn_command(vnc_cmd)
        self._set_status(f"VNC: {conn.name}")

    def _ask_install_dependency(
        self, heading: str, body: str, install_cmd: str, then_callback
    ):
        """Show a GTK dialog asking to install a missing dependency."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading=heading,
            body=body,
        )
        dialog.add_response("skip", "Skip, Continue Anyway")
        dialog.add_response("install", "Install")
        dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "install":
                # Open a terminal tab to run the install command
                terminal = TerminalWidget(self.config)
                self.terminal_panel.add_tab(terminal, None, "Installing...")
                shell = SSHHandler.get_local_shell_command()
                terminal.spawn_command(shell)
                GLib.timeout_add(
                    500, lambda: (terminal.feed_child(install_cmd + "\n"), False)[1]
                )
                self._set_status(f"Installing: {install_cmd}")
            else:
                then_callback()

        dialog.connect("response", on_response)
        dialog.present()

    def open_local_terminal(self):
        """Open a new local shell terminal tab."""
        terminal = TerminalWidget(self.config)
        shell_cmd = SSHHandler.get_local_shell_command()
        self.terminal_panel.add_tab(terminal, None, "Local")
        terminal.spawn_command(shell_cmd)
        self._set_status("Local terminal opened")

    def _schedule_post_login_commands(
        self, terminal: TerminalWidget, commands: list[str]
    ):
        """Send post-login commands after detecting a shell prompt.

        Polls the terminal text every 500 ms looking for a recognisable
        shell prompt (``$ ``, ``# ``, ``% ``, ``> ``) on the line where
        the cursor sits.  Polling starts after 1 s to skip the initial
        SSH handshake / banner noise.

        A safety ceiling of 30 s ensures we don't poll forever if the
        prompt pattern is unusual.
        """
        import re

        # Matches typical Bash/Zsh/Fish/Ksh prompts:
        #   [user@host ~]$   user@host:~$   host%   root#   >
        prompt_re = re.compile(r"[\$#%>]\s*$")

        state = {"fired": False, "polls": 0}
        max_polls = 58  # 1 s initial + up to 58 × 500 ms ≈ 30 s

        def _send_commands():
            """Send the commands with optional inter-command delays."""
            if state["fired"]:
                return
            state["fired"] = True

            delay_ms = 200  # tiny gap after prompt detected
            for cmd in commands:
                if cmd.startswith("##D="):
                    try:
                        delay_ms += int(cmd[4:])
                    except ValueError:
                        pass
                    continue
                GLib.timeout_add(
                    delay_ms,
                    lambda t=terminal, c=cmd: (t.feed_child(c + "\n"), False)[1],
                )
                delay_ms += 200

        def _poll_for_prompt():
            """Check whether the terminal is showing a shell prompt."""
            if state["fired"]:
                return False  # stop polling

            state["polls"] += 1

            # Safety ceiling – send anyway
            if state["polls"] > max_polls:
                _send_commands()
                return False

            try:
                vte = terminal.vte
                col, row = vte.get_cursor_position()
                # Read the cursor line and the one above (prompt may span)
                text = vte.get_text_range_format(
                    Vte.Format.TEXT, max(0, row - 1), 0, row, 300
                )
                if isinstance(text, tuple):
                    text = text[0]
                if text and prompt_re.search(text.rstrip("\n")):
                    _send_commands()
                    return False
            except Exception:
                pass

            # Fallback: scan the last non-empty line of all visible text
            try:
                text = terminal.vte.get_text_format(Vte.Format.TEXT)
                if isinstance(text, tuple):
                    text = text[0]
                if text:
                    lines = text.rstrip().split("\n")
                    if lines and prompt_re.search(lines[-1]):
                        _send_commands()
                        return False
            except Exception:
                pass

            return True  # keep polling

        # Start polling after 1 s, then every 500 ms
        GLib.timeout_add(
            1000, lambda: (GLib.timeout_add(500, _poll_for_prompt), False)[1]
        )

    # =====================================================================
    # Action Handlers
    # =====================================================================

    def _on_new_connection(self, action, param):
        """Open the new connection dialog."""
        try:
            # Determine default group from sidebar selection
            default_group = self.sidebar.get_selected_group_path()
            if not default_group:
                conn_id = self.sidebar.get_selected_connection_id()
                if conn_id:
                    sel_conn = self.connection_manager.get_connection(conn_id)
                    if sel_conn and sel_conn.group:
                        default_group = sel_conn.group

            dialog = ConnectionDialog(
                self,
                self.connection_manager,
                self.credential_store,
                default_group=default_group,
            )
            dialog.connect("connection-saved", self._on_connection_saved)
            dialog.present()
        except Exception as e:
            import traceback

            traceback.print_exc()
            self._set_status(f"Error opening dialog: {e}")

    def _on_new_local_terminal(self, action, param):
        """Open a new local terminal."""
        self.open_local_terminal()

    def _on_connect_selected(self, action, param):
        """Connect to the selected connection in sidebar."""
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self.open_connection(conn_id)

    def _on_preferences(self, action, param):
        """Open preferences dialog."""
        dialog = PreferencesDialog(self, self.config)
        dialog.init_global_passphrases(self.credential_store)
        dialog.connect("preferences-applied", lambda d: self._on_preferences_applied(d))
        dialog.present()

    def _on_split_horizontal(self, action, param):
        """Split the terminal area horizontally."""
        self.terminal_panel.split(Gtk.Orientation.HORIZONTAL)

    def _on_split_vertical(self, action, param):
        """Split the terminal area vertically."""
        self.terminal_panel.split(Gtk.Orientation.VERTICAL)

    def _on_unsplit(self, action, param):
        """Remove all splits."""
        self.terminal_panel.unsplit()

    def _on_cluster_toggle(self, action, param):
        """Toggle cluster mode (keyboard shortcut)."""
        new_state = not self._cluster_mode
        self._cluster_button.set_active(new_state)

    def _on_cluster_button_toggled(self, button):
        """Open or close the cluster window."""
        self._cluster_mode = button.get_active()
        if self._cluster_mode:
            self._open_cluster_window()
        else:
            self._close_cluster_window()

    def _open_cluster_window(self):
        """Open the cluster popup window."""
        if self._cluster_window is not None:
            self._cluster_window.present()
            return
        self._cluster_window = ClusterWindow(self, self.terminal_panel)
        self._cluster_window.connect("close-request", self._on_cluster_window_closed)
        self._cluster_window.present()

    def _close_cluster_window(self):
        """Close the cluster popup window and clear highlights."""
        if self._cluster_window is not None:
            self._cluster_window.close()
            self._cluster_window = None
        self.terminal_panel.clear_cluster_highlights()

    def _on_cluster_window_closed(self, *_):
        """Called when the user closes the cluster window directly."""
        self._cluster_window = None
        self._cluster_mode = False
        self._cluster_button.set_active(False)
        return False  # allow default close

    def _on_close_tab(self, action, param):
        """Close the current tab with optional confirmation."""
        if self.config.get("confirm_close_tab", True):
            terminal = self.terminal_panel.focused_terminal
            if terminal and terminal._process_pid > 0:
                import os

                # Check if the child process is still running
                try:
                    os.kill(terminal._process_pid, 0)
                    # Process is alive — confirm
                    dialog = Adw.MessageDialog(
                        transient_for=self,
                        heading="Close Tab?",
                        body="A process is still running in this terminal.\n"
                        "Are you sure you want to close it?",
                    )
                    dialog.add_response("cancel", "Cancel")
                    dialog.add_response("close", "Close")
                    dialog.set_response_appearance(
                        "close", Adw.ResponseAppearance.DESTRUCTIVE
                    )
                    dialog.connect(
                        "response",
                        lambda d, r: (
                            self.terminal_panel.close_current_tab()
                            if r == "close"
                            else None
                        ),
                    )
                    dialog.present()
                    return
                except (OSError, ProcessLookupError):
                    pass  # Process already exited
        self.terminal_panel.close_current_tab()

    def _on_next_tab(self, action, param):
        """Switch to next tab."""
        self.terminal_panel.next_tab()

    def _on_prev_tab(self, action, param):
        """Switch to previous tab."""
        self.terminal_panel.prev_tab()

    def _on_toggle_sidebar(self, action, param):
        """Toggle sidebar visibility."""
        visible = self.sidebar.get_visible()
        self.sidebar.set_visible(not visible)

    def _on_search_terminal(self, action, param):
        """Open search in current terminal."""
        terminal = self.terminal_panel.focused_terminal
        if terminal:
            # Create inline search bar
            self._show_search_bar(terminal)

    def _on_ssh_config_editor(self, action, param):
        """Open the SSH config editor window."""
        editor = SSHConfigEditor(parent=self)
        editor.present()

    def _on_import_connections(self, action, param):
        """Import connections from file."""
        try:
            dialog = Gtk.FileDialog()
            dialog.set_title("Import Connections")
            dialog.open(self, None, self._on_import_file_chosen)
        except Exception:
            chooser = Gtk.FileChooserNative(
                title="Import Connections",
                transient_for=self,
                action=Gtk.FileChooserAction.OPEN,
            )
            chooser.connect("response", self._on_import_chooser_response)
            chooser.show()

    def _on_export_connections(self, action, param):
        """Export connections to file with security warning."""
        # Show warning about credential export
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Export Connections",
            body="Exported files may contain encrypted credentials.\n\n"
            "⚠ Store the export file securely and delete it\n"
            "after importing on the target machine.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("export", "Export")
        dialog.set_response_appearance("export", Adw.ResponseAppearance.SUGGESTED)

        def on_warning_response(d, response):
            if response == "export":
                self._do_export()

        dialog.connect("response", on_warning_response)
        dialog.present()

    def _do_export(self):
        """Actually perform the export after warning."""
        try:
            dialog = Gtk.FileDialog()
            dialog.set_title("Export Connections")
            dialog.save(self, None, self._on_export_file_chosen)
        except Exception:
            chooser = Gtk.FileChooserNative(
                title="Export Connections",
                transient_for=self,
                action=Gtk.FileChooserAction.SAVE,
            )
            chooser.connect("response", self._on_export_chooser_response)
            chooser.show()

    def _on_import_file_chosen(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if file:
                path = file.get_path()
                with open(path, "r") as f:
                    json_data = f.read()
                self._show_import_mode_dialog(json_data)
        except Exception as e:
            self._set_status(f"Import failed: {e}")

    def _show_import_mode_dialog(self, json_data: str):
        """Show dialog asking user to overwrite or append connections."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Import Connections",
            body="How would you like to import the connections?\n\n"
            "• Overwrite: Replace all existing connections\n"
            "• Append: Add to existing connections\n"
            "  (conflicts will be auto-renamed)",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("overwrite", "Overwrite")
        dialog.add_response("append", "Append")
        dialog.set_response_appearance("overwrite", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_response_appearance("append", Adw.ResponseAppearance.SUGGESTED)

        def on_response(d, response):
            if response == "cancel":
                return
            replace = response == "overwrite"
            try:
                result = self.connection_manager.import_connections(
                    json_data,
                    replace=replace,
                    credential_store=self.credential_store,
                )
                self.sidebar.refresh()
                count = result["imported"]
                conflicts = result["conflicts"]
                msg = f"Imported {count} connection(s)"
                if conflicts:
                    renamed = ", ".join(
                        f'"{c["original"]}" → "{c["renamed"]}"' for c in conflicts
                    )
                    msg += f"\nRenamed: {renamed}"
                self._set_status(msg)
                # Show conflicts info if any
                if conflicts:
                    info = Adw.MessageDialog(
                        transient_for=self,
                        heading="Import Complete",
                        body=f"Imported {count} connection(s).\n\n"
                        f"The following were renamed due to conflicts:\n"
                        + "\n".join(
                            f'  • "{c["original"]}" → "{c["renamed"]}"'
                            for c in conflicts
                        ),
                    )
                    info.add_response("ok", "OK")
                    info.present()
            except Exception as e:
                self._set_status(f"Import failed: {e}")

        dialog.connect("response", on_response)
        dialog.present()

    def _on_export_file_chosen(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if file:
                data = self.connection_manager.export_connections(
                    credential_store=self.credential_store
                )
                with open(file.get_path(), "w") as f:
                    f.write(data)
                self._set_status("Connections exported (with credentials)")
        except Exception as e:
            self._set_status(f"Export failed: {e}")

    def _on_import_chooser_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = chooser.get_file()
            if file:
                try:
                    with open(file.get_path(), "r") as f:
                        json_data = f.read()
                    self._show_import_mode_dialog(json_data)
                except Exception as e:
                    self._set_status(f"Import failed: {e}")

    def _on_export_chooser_response(self, chooser, response):
        if response == Gtk.ResponseType.ACCEPT:
            file = chooser.get_file()
            if file:
                data = self.connection_manager.export_connections(
                    credential_store=self.credential_store
                )
                try:
                    with open(file.get_path(), "w") as f:
                        f.write(data)
                    self._set_status("Connections exported (with credentials)")
                except Exception as e:
                    self._set_status(f"Export failed: {e}")

    def _on_about(self, action, param):
        """Show about dialog."""
        from src import __version__

        try:
            about = Adw.AboutWindow(
                transient_for=self,
                application_name="SSH Client Manager",
                application_icon="utilities-terminal",
                version=__version__,
                developer_name="SSH Client Manager Contributors",
                license_type=Gtk.License.GPL_3_0,
                comments="A modern SSH connection manager with GTK4.\n\n"
                "Features:\n"
                "- Split terminals (horizontal/vertical)\n"
                "- Encrypted credential storage\n"
                "- SFTP, RDP, VNC support\n"
                "- Group management\n"
                "- Cluster mode\n"
                "- Quick Connect\n"
                "- SSH tunnel management\n"
                "- SSH key management\n"
                "- Session recording/playback\n"
                "- Connection testing\n"
                "- Auto-reconnect",
                website="https://github.com/ssh-client-manager",
            )
            about.present()
        except TypeError:
            # Older Adw fallback
            dialog = Gtk.AboutDialog(
                transient_for=self,
                modal=True,
                program_name="SSH Client Manager",
                version=__version__,
                license_type=Gtk.License.GPL_3_0,
            )
            dialog.present()

    # =====================================================================
    # Sidebar Action Handlers
    # =====================================================================

    def _on_sidebar_connect(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self.open_connection(conn_id)

    def _on_sidebar_connect_by_id(self, sidebar, conn_id):
        self.open_connection(conn_id)

    def _on_sidebar_edit(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self._edit_connection(conn_id)

    def _on_sidebar_delete(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            self._on_sidebar_delete_by_id(conn_id)

    def _on_sidebar_delete_by_id(self, conn_id: str):
        """Delete a connection with confirmation dialog."""
        conn = self.connection_manager.get_connection(conn_id)
        if not conn:
            return
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Delete Connection?",
            body=f'Are you sure you want to delete "{conn.name}"?\n'
            f"This will also remove any stored credentials.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(d, response):
            if response == "delete":
                self.connection_manager.delete_connection(conn_id)
                self.credential_store.delete_credentials(conn_id)
                self.sidebar.refresh()
                self._set_status("Connection deleted")

        dialog.connect("response", on_response)
        dialog.present()

    def _on_sidebar_duplicate(self, action, param):
        conn_id = self.sidebar.get_selected_connection_id()
        if conn_id:
            conn = self.connection_manager.get_connection(conn_id)
            if conn:
                new_conn = conn.clone()
                self.connection_manager.add_connection(new_conn)
                # Copy credentials using the unified passphrases API
                password = self.credential_store.get_password(conn_id)
                if password:
                    self.credential_store.store_password(new_conn.id, password)
                passphrases = self.credential_store.get_passphrases(conn_id)
                if passphrases:
                    self.credential_store.store_passphrases(new_conn.id, passphrases)
                self.sidebar.refresh()
                self._set_status(f"Connection duplicated: {new_conn.name}")

    def _on_add_group(self, prefix: str = ""):
        """Show a simple dialog to add a new group."""
        try:
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="New Group",
                body="Enter group name (use / for subgroups, e.g. Production/Web):",
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("add", "Add")
            dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)

            # Add entry — pre-fill with parent group prefix if available
            entry = Gtk.Entry()
            entry.set_placeholder_text("Group Name")
            entry.set_margin_start(12)
            entry.set_margin_end(12)
            if prefix:
                entry.set_text(prefix + "/")
                entry.set_position(-1)  # cursor at end
            dialog.set_extra_child(entry)

            dialog.connect(
                "response", lambda d, r: self._on_add_group_response(d, r, entry)
            )
            dialog.present()
        except Exception as e:
            import traceback

            traceback.print_exc()
            self._set_status(f"Error opening group dialog: {e}")

    def _on_add_group_response(self, dialog, response, entry):
        if response == "add":
            group_name = entry.get_text().strip()
            if group_name:
                self.connection_manager.add_group(group_name)
                self.sidebar.refresh()
                self._set_status(f"Group added: {group_name}")

    def _on_delete_group(self, action, param):
        group_path = self.sidebar.get_selected_group_path()
        if group_path:
            self.sidebar._delete_group_confirm(group_path)

    def _edit_connection(self, connection_id: str):
        """Open the edit dialog for a connection."""
        try:
            conn = self.connection_manager.get_connection(connection_id)
            if not conn:
                return

            dialog = ConnectionDialog(
                self, self.connection_manager, self.credential_store, conn
            )
            dialog.connect("connection-saved", self._on_connection_saved)
            dialog.present()
        except Exception as e:
            import traceback

            traceback.print_exc()
            self._set_status(f"Error opening edit dialog: {e}")

    def _on_connection_saved(self, dialog, connection):
        """Handle a connection being saved."""
        self.sidebar.refresh()
        self._set_status(f"Connection saved: {connection.name}")

    # =====================================================================
    # Terminal Panel Signal Handlers
    # =====================================================================

    def _on_tab_added(self, panel, terminal):
        self._update_tab_count()
        if self._cluster_window is not None:
            self._cluster_window.refresh()

    def _on_tab_removed(self, panel, terminal):
        self._update_tab_count()
        if self._cluster_window is not None:
            self._cluster_window.refresh()
        # Clean up SFTP tracking
        self._sftp_terminals.pop(id(terminal), None)
        # Kill the child process (e.g. ssh) if still running
        pid = getattr(terminal, "_process_pid", -1)
        if pid > 0:
            import signal

            try:
                os.kill(pid, signal.SIGHUP)
            except OSError:
                pass
        # Use terminal.connection directly (the terminal is already removed
        # from panel._terminals by the time this signal fires)
        conn = getattr(terminal, "connection", None)
        # Clean up askpass for this specific session
        session_id = getattr(terminal, "_askpass_session_id", None)
        if session_id:
            self.ssh_handler.cleanup_askpass(session_id)
        if conn:
            # Only mark disconnected if no other terminal uses this connection
            still_open = any(
                info[0] and info[0].id == conn.id for info in panel._terminals.values()
            )
            if not still_open:
                self._connected_ids.discard(conn.id)
                self.sidebar.mark_disconnected(conn.id)
        # Stop logging if active
        if getattr(terminal, "is_logging", False):
            terminal.stop_logging()
        # Send desktop notification if enabled and window not focused
        if self.config.get("notify_on_completion", True):
            if not self.is_active():
                tab_title = conn.name if conn else "Local"
                self._send_notification(f"Terminal closed: {tab_title}")

    def _on_child_exited(self, panel, terminal, conn):
        """Handle SSH child process exiting (terminal tab stays open)."""
        if conn:
            # Clean up askpass script and state files now that SSH has exited
            session_id = getattr(terminal, "_askpass_session_id", None)
            if session_id:
                self.ssh_handler.cleanup_askpass(session_id)

            # SSH -f: process daemonized itself intentionally — not a real
            # disconnect.  Skip sidebar update and auto-reconnect entirely.
            if getattr(terminal, "_background_mode", False):
                self._set_status(f"{conn.name}: running in background (SSH -f)")
                return

            # Only mark disconnected if no OTHER terminal uses this connection
            other_alive = any(
                t is not terminal and info[0] and info[0].id == conn.id
                for t, info in panel._terminals.items()
            )
            if not other_alive:
                self._connected_ids.discard(conn.id)
                self.sidebar.mark_disconnected(conn.id)

            # Auto-reconnect if enabled for this connection
            if getattr(conn, "auto_reconnect", False):
                tid = id(terminal)
                attempts = self._reconnect_attempts.get(tid, 0)
                max_attempts = getattr(conn, "auto_reconnect_max", 3) or 0
                delay = max(1, getattr(conn, "auto_reconnect_delay", 5))

                if max_attempts == 0 or attempts < max_attempts:
                    self._reconnect_attempts[tid] = attempts + 1
                    self._set_status(
                        f"Auto-reconnecting {conn.name} in {delay}s "
                        f"(attempt {attempts + 1}"
                        f"{'/' + str(max_attempts) if max_attempts else ''})"
                    )
                    timer_id = GLib.timeout_add_seconds(
                        delay,
                        self._auto_reconnect,
                        panel,
                        terminal,
                        conn,
                    )
                    self._reconnect_timers[tid] = timer_id
                else:
                    self._set_status(
                        f"Auto-reconnect exhausted for {conn.name} "
                        f"after {max_attempts} attempts"
                    )
                    self._reconnect_attempts.pop(tid, None)

    def _auto_reconnect(self, panel, terminal, conn):
        """Perform auto-reconnect by respawning the SSH command."""
        tid = id(terminal)
        self._reconnect_timers.pop(tid, None)

        # Check terminal is still alive (not closed)
        if terminal not in panel._terminals:
            self._reconnect_attempts.pop(tid, None)
            return False

        self._set_status(f"Reconnecting {conn.name}...")
        self._on_reconnect_requested(panel, terminal, conn)
        return False  # Don't repeat the timeout

    def _on_active_terminal_changed(self, panel, terminal):
        conn = panel.get_terminal_connection(terminal)
        if conn:
            self._set_status(f"{conn.name or conn.display_name()}")

            # Reset auto-reconnect counter on successful connection
            tid = id(terminal)
            self._reconnect_attempts.pop(tid, None)
            timer_id = self._reconnect_timers.pop(tid, None)
            if timer_id:
                GLib.source_remove(timer_id)
            # Switch sidebar to/from SFTP browser based on whether this
            # terminal is an SFTP session
            is_sftp = id(terminal) in self._sftp_terminals
            if is_sftp:
                if not self.sidebar.is_sftp_mode:
                    sftp_conn = self._sftp_terminals[id(terminal)]
                    self.sidebar.show_sftp_browser(sftp_conn, self.credential_store)
            else:
                if self.sidebar.is_sftp_mode:
                    self.sidebar.show_connections()
        else:
            self._set_status("Local terminal")
            if self.sidebar.is_sftp_mode:
                self.sidebar.show_connections()

        # Sync header bar record button with the newly focused terminal
        self._update_record_button()

    def _on_terminal_title_changed(self, panel, terminal, title):
        pass  # Tab label updates are handled in terminal_panel

    def _on_reconnect_requested(self, panel, terminal, conn):
        """Handle reconnect request — spawn the connection process."""
        import sys

        proto = getattr(conn, "protocol", "ssh") or "ssh"
        if proto == "rdp":
            if sys.platform == "darwin":
                # On macOS, launch Windows App directly (no terminal)
                self.ssh_handler.launch_rdp_macos(conn)
                self._set_status(
                    f"RDP: {conn.name or conn.display_name()} — opened in Windows App"
                )
            else:
                rdp_cmd = self.ssh_handler.build_rdp_command(conn)
                terminal.spawn_command(rdp_cmd)
        elif proto == "vnc":
            vnc_cmd = self.ssh_handler.build_vnc_command(conn)
            terminal.spawn_command(vnc_cmd)
        elif proto == "sftp":
            sftp_cmd = self.ssh_handler.build_sftp_command(conn)
            env, session_id = self.ssh_handler.build_environment(conn)
            terminal._askpass_session_id = session_id
            terminal.spawn_command(sftp_cmd, env)
            if session_id:
                GLib.timeout_add(
                    15000,
                    lambda sid=session_id: self.ssh_handler.cleanup_askpass(sid)
                    or False,
                )
        else:
            ssh_cmd = self.ssh_handler.build_ssh_command(conn)
            env, session_id = self.ssh_handler.build_environment(conn)
            terminal._askpass_session_id = session_id
            terminal.spawn_command(ssh_cmd, env)
            commands = self.ssh_handler.get_post_login_commands(conn)
            if commands:
                self._schedule_post_login_commands(terminal, commands)
            if session_id:
                GLib.timeout_add(
                    15000,
                    lambda sid=session_id: self.ssh_handler.cleanup_askpass(sid)
                    or False,
                )
        # Clear disconnected visual state on the tab
        self.terminal_panel.mark_tab_active(terminal)
        # Mark connected in sidebar
        self._connected_ids.add(conn.id)
        self.sidebar.mark_connected(conn.id)
        self._set_status(f"Reconnected: {conn.name}")

    def _on_clone_requested(self, panel, terminal, conn):
        """Handle clone request — spawn the connection process in the new terminal."""
        self._on_reconnect_requested(panel, terminal, conn)

    # =====================================================================
    # Keyboard Shortcuts
    # =====================================================================

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle global keyboard shortcuts."""
        import sys

        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        alt = state & Gdk.ModifierType.ALT_MASK
        meta = state & Gdk.ModifierType.META_MASK

        # macOS Command key shortcuts (window-level: Cmd+W/T/Q/F)
        if sys.platform == "darwin" and meta:
            if keyval in (Gdk.KEY_w, Gdk.KEY_W):
                self.terminal_panel.close_current_tab()
                return True
            elif keyval in (Gdk.KEY_t, Gdk.KEY_T) and not shift:
                self.open_local_terminal()
                return True
            elif keyval in (Gdk.KEY_q, Gdk.KEY_Q):
                self.close()
                return True
            elif keyval in (Gdk.KEY_f, Gdk.KEY_F):
                self._on_search_terminal(None, None)
                return True
            elif keyval in (Gdk.KEY_n, Gdk.KEY_N):
                self._on_new_connection(None, None)
                return True
            elif keyval == Gdk.KEY_comma:
                self._on_preferences(None, None)
                return True

        # macOS Cmd+Shift+S or Ctrl+Shift+S: Command Snippets
        if sys.platform == "darwin" and meta and shift:
            if keyval in (Gdk.KEY_s, Gdk.KEY_S):
                self._on_show_snippets()
                return True

        # F9: Toggle sidebar
        if keyval == Gdk.KEY_F9:
            self._on_toggle_sidebar(None, None)
            return True

        # Ctrl+Shift shortcuts
        if ctrl and shift:
            if keyval == Gdk.KEY_T:
                self.open_local_terminal()
                return True
            elif keyval == Gdk.KEY_N:
                self._on_new_connection(None, None)
                return True
            elif keyval == Gdk.KEY_D:
                self.terminal_panel.clone_current_tab()
                return True
            elif keyval == Gdk.KEY_Left:
                self.terminal_panel.split_directional("left")
                return True
            elif keyval == Gdk.KEY_Right:
                self.terminal_panel.split_directional("right")
                return True
            elif keyval == Gdk.KEY_Up:
                self.terminal_panel.split_directional("up")
                return True
            elif keyval == Gdk.KEY_Down:
                self.terminal_panel.split_directional("down")
                return True
            elif keyval == Gdk.KEY_S:
                self._on_show_snippets()
                return True
            elif keyval == Gdk.KEY_V:
                # Note: Ctrl+Shift+V is paste in the terminal.
                pass

        # Ctrl+W: Close tab
        if ctrl and keyval == Gdk.KEY_w:
            self.terminal_panel.close_current_tab()
            return True

        # Ctrl+Tab / Ctrl+Shift+Tab: Next/Prev tab
        if ctrl and keyval == Gdk.KEY_Tab:
            if shift:
                self.terminal_panel.prev_tab()
            else:
                self.terminal_panel.next_tab()
            return True

        # Ctrl+F: Search
        if ctrl and keyval == Gdk.KEY_f:
            self._on_search_terminal(None, None)
            return True

        # Alt+1-9: Switch to tab by number
        if alt:
            num = keyval - Gdk.KEY_1
            if 0 <= num <= 8:
                self.terminal_panel.switch_to_tab(num)
                return True

        return False

    # =====================================================================
    # Search
    # =====================================================================

    def _show_search_bar(self, terminal: TerminalWidget):
        """Show an inline search bar for the terminal."""
        # Check if we already have a search bar
        search_bar = getattr(self, "_search_revealer", None)
        if search_bar and search_bar.get_reveal_child():
            search_bar.set_reveal_child(False)
            terminal.grab_focus()
            return

        # Create search bar
        revealer = Gtk.Revealer()
        revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hbox.set_margin_start(8)
        hbox.set_margin_end(8)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)

        search_entry = Gtk.SearchEntry()
        search_entry.set_hexpand(True)
        search_entry.set_placeholder_text("Search terminal...")
        hbox.append(search_entry)

        btn_prev = Gtk.Button(icon_name="go-up-symbolic")
        btn_prev.add_css_class("flat")
        hbox.append(btn_prev)

        btn_next = Gtk.Button(icon_name="go-down-symbolic")
        btn_next.add_css_class("flat")
        hbox.append(btn_next)

        btn_close = Gtk.Button(icon_name="window-close-symbolic")
        btn_close.add_css_class("flat")
        hbox.append(btn_close)

        def _get_target():
            """Always search in the currently focused terminal."""
            return self.terminal_panel.focused_terminal or terminal

        def close_search(*_):
            revealer.set_reveal_child(False)
            _get_target().grab_focus()

        btn_close.connect("clicked", close_search)

        # Esc key to close search
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect(
            "key-pressed",
            lambda c, kv, kc, s: close_search() if kv == Gdk.KEY_Escape else None,
        )
        search_entry.add_controller(key_ctrl)

        # Connect search — always targets the focused terminal
        search_entry.connect(
            "search-changed", lambda e: _get_target().search_text(e.get_text())
        )
        btn_next.connect(
            "clicked",
            lambda _: _get_target().search_next(search_entry.get_text()),
        )
        btn_prev.connect(
            "clicked",
            lambda _: _get_target().search_text(search_entry.get_text(), backward=True),
        )

        revealer.set_child(hbox)

        # Insert at top of terminal panel
        if hasattr(self, "_search_revealer") and self._search_revealer.get_parent():
            self._search_revealer.get_parent().remove(self._search_revealer)

        self.terminal_panel.prepend(revealer)
        self._search_revealer = revealer
        revealer.set_reveal_child(True)
        search_entry.grab_focus()

    # =====================================================================
    # Snippets, Logging, Notifications
    # =====================================================================

    def _on_show_snippets(self, *_):
        """Show the command snippets window."""
        import re

        terminal = self.terminal_panel.focused_terminal
        if not terminal:
            self._set_status("No active terminal for snippets")
            return

        var_pattern = re.compile(r"\{\{(\w+)\}\}")

        def _resolve_variables(command: str, parent_win, callback):
            """If command contains ${var} placeholders, prompt for values then call callback(resolved)."""
            var_names = var_pattern.findall(command)
            # De-dup while preserving order
            seen = set()
            unique_vars = []
            for v in var_names:
                if v not in seen:
                    seen.add(v)
                    unique_vars.append(v)

            if not unique_vars:
                callback(command)
                return

            # Show a dialog to fill in variable values
            var_dialog = Adw.Window(
                transient_for=parent_win,
                modal=True,
                title="Fill Variables",
                default_width=400,
                default_height=min(200 + len(unique_vars) * 60, 500),
            )
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            vh = Adw.HeaderBar()
            vbox.append(vh)

            form_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            form_box.set_margin_start(16)
            form_box.set_margin_end(16)
            form_box.set_margin_top(12)
            form_box.set_margin_bottom(12)

            preview_label = Gtk.Label(label=command)
            preview_label.set_xalign(0)
            preview_label.add_css_class("dim-label")
            preview_label.set_wrap(True)
            preview_label.set_selectable(True)
            form_box.append(preview_label)

            entries = {}
            for vname in unique_vars:
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                lbl = Gtk.Label(label=f"{{{{{vname}}}}}:")
                lbl.set_xalign(0)
                lbl.set_width_chars(12)
                row.append(lbl)
                entry = Gtk.Entry()
                entry.set_placeholder_text(vname)
                entry.set_hexpand(True)
                row.append(entry)
                form_box.append(row)
                entries[vname] = entry

            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            btn_row.set_halign(Gtk.Align.END)
            btn_row.set_margin_top(8)

            btn_cancel = Gtk.Button(label="Cancel")
            btn_cancel.connect("clicked", lambda _: var_dialog.close())
            btn_row.append(btn_cancel)

            btn_ok = Gtk.Button(label="OK")
            btn_ok.add_css_class("suggested-action")

            def _apply(*_):
                resolved = command
                for vname, entry in entries.items():
                    val = entry.get_text()
                    resolved = resolved.replace(f"{{{{{vname}}}}}", val)
                var_dialog.close()
                callback(resolved)

            btn_ok.connect("clicked", _apply)
            btn_row.append(btn_ok)
            form_box.append(btn_row)

            # Enter key in any entry triggers OK
            for entry in entries.values():
                entry.connect("activate", _apply)

            vbox.append(form_box)
            var_dialog.set_content(vbox)
            var_dialog.present()

        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            title="Command Snippets",
            default_width=600,
            default_height=560,
        )

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        main_box.append(header)

        # Search entry
        search_entry = Gtk.SearchEntry()
        search_entry.set_placeholder_text(
            "🔍 Filter snippets (name, command, description)..."
        )
        search_entry.set_margin_start(12)
        search_entry.set_margin_end(12)
        search_entry.set_margin_top(6)
        main_box.append(search_entry)

        # Snippet list
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        snippets_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        snippets_box.set_margin_start(12)
        snippets_box.set_margin_end(12)
        snippets_box.set_margin_top(6)
        snippets_box.set_margin_bottom(12)

        all_snippets = self.snippet_manager.get_snippets()

        def _build_rows(filter_text=""):
            # Clear
            child = snippets_box.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                snippets_box.remove(child)
                child = next_child

            ft = filter_text.lower()
            for idx, snippet in enumerate(all_snippets):
                if (
                    ft
                    and ft not in snippet.name.lower()
                    and ft not in snippet.command.lower()
                    and ft not in (snippet.category or "").lower()
                    and ft not in (snippet.description or "").lower()
                ):
                    continue
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                row.set_margin_top(4)
                row.set_margin_bottom(4)
                row.add_css_class("snippet-row")

                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                info_box.set_hexpand(True)
                name_label = Gtk.Label(label=snippet.name)
                name_label.set_xalign(0)
                name_label.add_css_class("heading")
                info_box.append(name_label)

                cmd_label = Gtk.Label(label=snippet.command)
                cmd_label.set_xalign(0)
                cmd_label.add_css_class("dim-label")
                cmd_label.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
                info_box.append(cmd_label)

                if snippet.description:
                    desc_label = Gtk.Label(label=snippet.description)
                    desc_label.set_xalign(0)
                    desc_label.add_css_class("dim-label")
                    desc_label.set_wrap(True)
                    info_box.append(desc_label)

                row.append(info_box)

                # Check if command has variables
                has_vars = bool(var_pattern.search(snippet.command))

                # Send to terminal button
                btn_send = Gtk.Button(label="▶")
                btn_send.set_tooltip_text(
                    "Send to terminal (will prompt for variables)"
                    if has_vars
                    else "Send to terminal"
                )
                btn_send.add_css_class("flat")
                _cmd = snippet.command

                def _on_send(b, cmd=_cmd):
                    def _do_send(resolved):
                        t = self.terminal_panel.focused_terminal
                        if t:
                            t.feed_child(resolved + "\n")
                        dialog.close()

                    _resolve_variables(cmd, dialog, _do_send)

                btn_send.connect("clicked", _on_send)
                row.append(btn_send)

                # Batch send to ALL terminals button
                btn_batch = Gtk.Button(icon_name="network-workgroup-symbolic")
                btn_batch.set_tooltip_text("Send to ALL open terminals")
                btn_batch.add_css_class("flat")

                def _on_batch(b, cmd=_cmd):
                    def _do_batch(resolved):
                        for t in self.terminal_panel.get_all_terminals():
                            t.feed_child(resolved + "\n")
                        dialog.close()
                        self._set_status(
                            f"Sent to {len(self.terminal_panel.get_all_terminals())} terminals"
                        )

                    _resolve_variables(cmd, dialog, _do_batch)

                btn_batch.connect("clicked", _on_batch)
                row.append(btn_batch)

                # Copy to clipboard button
                btn_copy = Gtk.Button(icon_name="edit-copy-symbolic")
                btn_copy.set_tooltip_text(
                    "Copy to clipboard (will prompt for variables)"
                    if has_vars
                    else "Copy to clipboard"
                )
                btn_copy.add_css_class("flat")

                def _on_copy(b, cmd=_cmd):
                    def _do_copy(resolved):
                        clipboard = Gdk.Display.get_default().get_clipboard()
                        clipboard.set(resolved)
                        self._set_status("Snippet copied to clipboard")

                    _resolve_variables(cmd, dialog, _do_copy)

                btn_copy.connect("clicked", _on_copy)
                row.append(btn_copy)

                # Edit button
                btn_edit = Gtk.Button(icon_name="document-edit-symbolic")
                btn_edit.set_tooltip_text("Edit snippet")
                btn_edit.add_css_class("flat")

                def _on_edit(b, i=idx):
                    self._edit_snippet_dialog(
                        dialog, i, all_snippets, _build_rows, search_entry
                    )

                btn_edit.connect("clicked", _on_edit)
                row.append(btn_edit)

                # Delete button
                btn_del = Gtk.Button(icon_name="user-trash-symbolic")
                btn_del.set_tooltip_text("Delete snippet")
                btn_del.add_css_class("flat")
                _idx = idx

                def _on_del(b, i=_idx):
                    self.snippet_manager.delete_snippet(i)
                    all_snippets.clear()
                    all_snippets.extend(self.snippet_manager.get_snippets())
                    _build_rows(search_entry.get_text())

                btn_del.connect("clicked", _on_del)
                row.append(btn_del)

                snippets_box.append(row)

        _build_rows()
        search_entry.connect("search-changed", lambda e: _build_rows(e.get_text()))

        scrolled.set_child(snippets_box)
        main_box.append(scrolled)

        # Bottom button bar
        btn_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_bar.set_margin_start(12)
        btn_bar.set_margin_end(12)
        btn_bar.set_margin_bottom(8)
        btn_bar.set_margin_top(4)

        btn_export = Gtk.Button(label="📤 Export")
        btn_export.set_tooltip_text("Export snippets to file")
        btn_export.add_css_class("flat")
        btn_export.connect("clicked", lambda _: self._export_snippets(dialog))
        btn_bar.append(btn_export)

        btn_import = Gtk.Button(label="📥 Import")
        btn_import.set_tooltip_text("Import snippets from file")
        btn_import.add_css_class("flat")
        btn_import.connect(
            "clicked",
            lambda _: self._import_snippets(
                dialog, all_snippets, _build_rows, search_entry
            ),
        )
        btn_bar.append(btn_import)

        main_box.append(btn_bar)

        # Add snippet button
        add_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        add_box.set_margin_start(12)
        add_box.set_margin_end(12)
        add_box.set_margin_bottom(8)
        add_box.set_halign(Gtk.Align.END)
        btn_add = Gtk.Button(label="➕ Add Snippet")
        btn_add.add_css_class("suggested-action")
        btn_add.connect(
            "clicked",
            lambda _: self._add_snippet_dialog(
                dialog, all_snippets, _build_rows, search_entry
            ),
        )
        add_box.append(btn_add)
        main_box.append(add_box)

        dialog.set_content(main_box)
        dialog.present()

    def _add_snippet_dialog(
        self, parent_dialog, all_snippets=None, rebuild_fn=None, search_entry=None
    ):
        """Show dialog to add a new snippet."""
        dialog = Adw.Window(
            transient_for=parent_dialog,
            modal=True,
            title="Add Command Snippet",
            default_width=400,
            default_height=320,
        )
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        main_box.append(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Snippet name")
        box.append(name_entry)

        cmd_entry = Gtk.Entry()
        cmd_entry.set_placeholder_text("Command (use {{var}} for variables)")
        box.append(cmd_entry)

        cat_entry = Gtk.Entry()
        cat_entry.set_placeholder_text("Category (optional)")
        box.append(cat_entry)

        desc_entry = Gtk.Entry()
        desc_entry.set_placeholder_text("Description (optional)")
        box.append(desc_entry)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        btn_row.set_margin_top(8)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _: dialog.close())
        btn_row.append(btn_cancel)

        btn_add = Gtk.Button(label="Add")
        btn_add.add_css_class("suggested-action")

        def _on_add(_):
            name = name_entry.get_text().strip()
            cmd = cmd_entry.get_text().strip()
            if name and cmd:
                from .snippet_manager import Snippet

                snippet = Snippet(
                    name=name,
                    command=cmd,
                    category=cat_entry.get_text().strip() or "Custom",
                    description=desc_entry.get_text().strip(),
                )
                self.snippet_manager.add_snippet(snippet)
                if all_snippets is not None:
                    all_snippets.clear()
                    all_snippets.extend(self.snippet_manager.get_snippets())
                if rebuild_fn:
                    rebuild_fn(search_entry.get_text() if search_entry else "")
                dialog.close()

        btn_add.connect("clicked", _on_add)
        btn_row.append(btn_add)
        box.append(btn_row)

        main_box.append(box)
        dialog.set_content(main_box)
        dialog.present()

    def _edit_snippet_dialog(
        self,
        parent_dialog,
        snippet_idx,
        all_snippets=None,
        rebuild_fn=None,
        search_entry=None,
    ):
        """Show dialog to edit an existing snippet."""
        snippet = (
            all_snippets[snippet_idx]
            if all_snippets
            else self.snippet_manager.get_snippets()[snippet_idx]
        )

        dialog = Adw.Window(
            transient_for=parent_dialog,
            modal=True,
            title="Edit Command Snippet",
            default_width=400,
            default_height=320,
        )
        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        main_box.append(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("Snippet name")
        name_entry.set_text(snippet.name)
        box.append(name_entry)

        cmd_entry = Gtk.Entry()
        cmd_entry.set_placeholder_text("Command (use {{var}} for variables)")
        cmd_entry.set_text(snippet.command)
        box.append(cmd_entry)

        cat_entry = Gtk.Entry()
        cat_entry.set_placeholder_text("Category (optional)")
        cat_entry.set_text(snippet.category or "")
        box.append(cat_entry)

        desc_entry = Gtk.Entry()
        desc_entry.set_placeholder_text("Description (optional)")
        desc_entry.set_text(snippet.description or "")
        box.append(desc_entry)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        btn_row.set_margin_top(8)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _: dialog.close())
        btn_row.append(btn_cancel)

        btn_save = Gtk.Button(label="Save")
        btn_save.add_css_class("suggested-action")

        def _on_save(_):
            name = name_entry.get_text().strip()
            cmd = cmd_entry.get_text().strip()
            if name and cmd:
                from .snippet_manager import Snippet

                updated = Snippet(
                    name=name,
                    command=cmd,
                    category=cat_entry.get_text().strip() or "Custom",
                    description=desc_entry.get_text().strip(),
                )
                self.snippet_manager.update_snippet(snippet_idx, updated)
                if all_snippets is not None:
                    all_snippets.clear()
                    all_snippets.extend(self.snippet_manager.get_snippets())
                if rebuild_fn:
                    rebuild_fn(search_entry.get_text() if search_entry else "")
                dialog.close()

        btn_save.connect("clicked", _on_save)
        btn_row.append(btn_save)
        box.append(btn_row)

        main_box.append(box)
        dialog.set_content(main_box)
        dialog.present()

    def _export_snippets(self, parent_dialog):
        """Export snippets to a JSON file."""
        fc = Gtk.FileDialog()
        fc.set_initial_name("snippets.json")

        def _on_save(dialog, result):
            try:
                gfile = dialog.save_finish(result)
                if gfile:
                    import json

                    data = {
                        "snippets": [
                            s.to_dict() for s in self.snippet_manager.get_snippets()
                        ]
                    }
                    with open(gfile.get_path(), "w") as f:
                        json.dump(data, f, indent=2)
                    self._set_status("📤 Snippets exported successfully")
            except Exception as e:
                if "dismiss" not in str(e).lower():
                    self._set_status(f"Export failed: {e}")

        fc.save(parent_dialog, None, _on_save)

    def _import_snippets(self, parent_dialog, all_snippets, rebuild_fn, search_entry):
        """Import snippets from a JSON file with append/overwrite option."""
        # First ask append or overwrite
        ask_dialog = Adw.MessageDialog(
            transient_for=parent_dialog,
            heading="Import Snippets",
            body="Choose import mode:",
        )
        ask_dialog.add_response("cancel", "Cancel")
        ask_dialog.add_response("append", "Append")
        ask_dialog.add_response("overwrite", "Overwrite")
        ask_dialog.set_response_appearance(
            "overwrite", Adw.ResponseAppearance.DESTRUCTIVE
        )
        ask_dialog.set_response_appearance("append", Adw.ResponseAppearance.SUGGESTED)

        def _on_mode(d, response):
            if response in ("append", "overwrite"):
                self._do_import_snippets(
                    parent_dialog, response, all_snippets, rebuild_fn, search_entry
                )

        ask_dialog.connect("response", _on_mode)
        ask_dialog.present()

    def _do_import_snippets(
        self, parent_dialog, mode, all_snippets, rebuild_fn, search_entry
    ):
        """Actually import snippets from file."""
        fc = Gtk.FileDialog()

        def _on_open(dialog, result):
            try:
                gfile = dialog.open_finish(result)
                if gfile:
                    import json
                    from .snippet_manager import Snippet

                    with open(gfile.get_path(), "r") as f:
                        data = json.load(f)
                    imported = [Snippet.from_dict(s) for s in data.get("snippets", [])]
                    if mode == "overwrite":
                        self.snippet_manager._snippets = imported
                    else:
                        self.snippet_manager._snippets.extend(imported)
                    self.snippet_manager.save()
                    all_snippets.clear()
                    all_snippets.extend(self.snippet_manager.get_snippets())
                    rebuild_fn(search_entry.get_text() if search_entry else "")
                    self._set_status(f"📥 Imported {len(imported)} snippets ({mode})")
            except Exception as e:
                if "dismiss" not in str(e).lower():
                    self._set_status(f"Import failed: {e}")

        fc.open(parent_dialog, None, _on_open)

    def _backup_all(self, parent_dialog):
        """Backup all settings to a ZIP file."""
        import zipfile
        from .config import get_config_dir

        fc = Gtk.FileDialog()
        fc.set_initial_name("ssh-client-manager-backup.zip")

        def _on_save(dialog, result):
            try:
                gfile = dialog.save_finish(result)
                if not gfile:
                    return
                backup_path = gfile.get_path()
                config_dir = get_config_dir()

                with zipfile.ZipFile(backup_path, "w", zipfile.ZIP_DEFLATED) as zf:
                    for fname in (
                        "config.json",
                        "connections.json",
                        "snippets.json",
                        "credentials.dat",
                    ):
                        fpath = config_dir / fname
                        if fpath.exists():
                            zf.write(fpath, fname)

                self._set_status(f"💾 Backup saved: {os.path.basename(backup_path)}")
            except Exception as e:
                if "dismiss" not in str(e).lower():
                    self._set_status(f"Backup failed: {e}")
            finally:
                GLib.idle_add(self._force_redraw)

        fc.save(parent_dialog, None, _on_save)

    def _restore_all(self, parent_dialog):
        """Restore all settings from a backup ZIP file."""
        confirm = Adw.MessageDialog(
            transient_for=parent_dialog,
            heading="Restore All Settings",
            body="This will overwrite ALL current settings, connections, snippets, and credentials.\n\nAre you sure?",
        )
        confirm.add_response("cancel", "Cancel")
        confirm.add_response("restore", "Restore")
        confirm.set_response_appearance("restore", Adw.ResponseAppearance.DESTRUCTIVE)

        def _on_confirm(d, response):
            if response == "restore":
                self._do_restore_all(parent_dialog)

        confirm.connect("response", _on_confirm)
        confirm.present()

    def _do_restore_all(self, parent_dialog):
        """Actually restore from ZIP."""
        import zipfile
        from .config import get_config_dir

        fc = Gtk.FileDialog()

        def _on_open(dialog, result):
            try:
                gfile = dialog.open_finish(result)
                if not gfile:
                    return
                backup_path = gfile.get_path()
                config_dir = get_config_dir()

                with zipfile.ZipFile(backup_path, "r") as zf:
                    for fname in zf.namelist():
                        if fname in (
                            "config.json",
                            "connections.json",
                            "snippets.json",
                            "credentials.dat",
                        ):
                            zf.extract(fname, config_dir)

                # Reload everything
                self.config.load()
                self.connection_manager.load()
                self.snippet_manager.load()
                self.credential_store._data = self.credential_store._load()
                self.sidebar.refresh()

                self._set_status(
                    "📂 All settings restored from backup — restart recommended"
                )
            except Exception as e:
                if "dismiss" not in str(e).lower():
                    self._set_status(f"Restore failed: {e}")
            finally:
                # Force redraw to clear any FileDialog rendering artifacts
                GLib.idle_add(self._force_redraw)

        fc.open(parent_dialog, None, _on_open)

    def _force_redraw(self):
        """Force a full window redraw to clear rendering artifacts."""
        self.queue_draw()
        # Toggle a harmless property to force the compositor to refresh
        for child in [self.paned, self.terminal_panel]:
            child.queue_draw()
        return False

    def _on_toggle_log(self, *_):
        """Toggle terminal logging for the active terminal."""
        terminal = self.terminal_panel.focused_terminal
        if not terminal:
            return
        if getattr(terminal, "is_logging", False):
            terminal.stop_logging()
            self._set_status("Terminal logging stopped")
        else:
            log_dir = self.config.get("terminal_log_directory", "")
            if not log_dir:
                log_dir = os.path.join(os.path.expanduser("~"), "ssh-logs")
            os.makedirs(log_dir, exist_ok=True)
            import datetime

            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            conn = self.terminal_panel.get_terminal_connection(terminal)
            name = conn.name if conn else "local"
            safe_name = name.replace("/", "_").replace(" ", "_")
            log_path = os.path.join(log_dir, f"{safe_name}_{ts}.log")
            terminal.start_logging(log_path)
            self._set_status(f"Logging to {log_path}")

    def _on_toggle_record(self, *_):
        """Toggle terminal session recording for the active terminal."""
        terminal = self.terminal_panel.focused_terminal
        if not terminal:
            return
        if getattr(terminal, "is_recording", False):
            rec_path = getattr(terminal, "_recording_path", "")
            terminal.stop_recording()
            self._set_status(f"Recording saved: {rec_path}")
            # Remove [REC] from tab title
            label = self.terminal_panel.get_tab_title(terminal)
            if label and label.endswith(" [REC]"):
                self.terminal_panel.set_tab_title(terminal, label[:-6])
        else:
            import datetime

            recordings_dir = get_recordings_dir(self.config)
            ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            conn = self.terminal_panel.get_terminal_connection(terminal)
            name = conn.name if conn else "local"
            safe_name = name.replace("/", "_").replace(" ", "_")
            rec_path = str(recordings_dir / f"{safe_name}_{ts}.cast")
            terminal._recording_path = rec_path
            terminal.start_recording(rec_path)
            self._set_status(f"[REC] Recording started: {rec_path}")
            # Add [REC] to tab title
            label = self.terminal_panel.get_tab_title(terminal)
            if label and not label.endswith(" [REC]"):
                self.terminal_panel.set_tab_title(terminal, label + " [REC]")
        self._update_record_button()

    def _on_record_button_toggled(self, button):
        """Handle the header bar record button being toggled."""
        terminal = self.terminal_panel.focused_terminal
        if not terminal:
            # No terminal — reset button without re-triggering
            button.handler_block_by_func(self._on_record_button_toggled)
            button.set_active(False)
            button.handler_unblock_by_func(self._on_record_button_toggled)
            return
        is_recording = getattr(terminal, "is_recording", False)
        # Only act if the button state differs from the actual recording state
        if button.get_active() != is_recording:
            self._on_toggle_record()

    def _update_record_button(self):
        """Sync the header bar record button with the active terminal's recording state."""
        terminal = self.terminal_panel.focused_terminal
        is_recording = getattr(terminal, "is_recording", False) if terminal else False
        btn = self._record_button
        btn.handler_block_by_func(self._on_record_button_toggled)
        btn.set_active(is_recording)
        btn.handler_unblock_by_func(self._on_record_button_toggled)
        if is_recording:
            btn.add_css_class("destructive-action")
            btn.set_tooltip_text("Stop Recording (current terminal)")
        else:
            btn.remove_css_class("destructive-action")
            btn.set_tooltip_text("Start Recording (current terminal)")

    def _on_show_help(self, *_):
        """Show the usage guide window."""
        dialog = Adw.Window(
            transient_for=self,
            modal=True,
            title="SSH Client Manager — Usage Guide",
            default_width=700,
            default_height=600,
        )

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        main_box.append(header)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)

        text_view = Gtk.TextView()
        text_view.set_editable(False)
        text_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        text_view.set_left_margin(24)
        text_view.set_right_margin(24)
        text_view.set_top_margin(16)
        text_view.set_bottom_margin(16)
        text_view.set_monospace(False)

        buf = text_view.get_buffer()

        # Create text tags for formatting
        buf.create_tag(
            "h1",
            weight=Pango.Weight.BOLD,
            scale=1.6,
            pixels_below_lines=8,
            pixels_above_lines=12,
        )
        buf.create_tag(
            "h2",
            weight=Pango.Weight.BOLD,
            scale=1.3,
            pixels_below_lines=6,
            pixels_above_lines=10,
        )
        buf.create_tag(
            "h3",
            weight=Pango.Weight.BOLD,
            scale=1.1,
            pixels_below_lines=4,
            pixels_above_lines=8,
        )
        buf.create_tag("bold", weight=Pango.Weight.BOLD)
        buf.create_tag("mono", family="monospace", background="rgba(128,128,128,0.15)")
        buf.create_tag("body", pixels_below_lines=2)

        def insert_h1(text):
            buf.insert_with_tags_by_name(buf.get_end_iter(), text + "\n", "h1")

        def insert_h2(text):
            buf.insert_with_tags_by_name(buf.get_end_iter(), text + "\n", "h2")

        def insert_h3(text):
            buf.insert_with_tags_by_name(buf.get_end_iter(), text + "\n", "h3")

        def insert_body(text):
            buf.insert_with_tags_by_name(buf.get_end_iter(), text + "\n", "body")

        def insert_mono(text):
            buf.insert_with_tags_by_name(buf.get_end_iter(), text + "\n", "mono")

        # Build the guide content
        insert_h1("SSH Client Manager — Usage Guide")

        insert_h2("Quick Start")
        insert_body(
            "1. Add a new connection via New Connection in the menu or sidebar right-click"
        )
        insert_body(
            "2. Fill in the SSH command (e.g. ssh user@hostname) or use structured fields for RDP/VNC"
        )
        insert_body("3. Double-click a connection in the sidebar to connect")
        insert_body(
            "4. Use Quick Connect in the header bar for instant ssh user@host connections"
        )
        insert_body("")

        insert_h2("Connection Management")
        insert_body("• SSH, SFTP, RDP, VNC protocols supported")
        insert_body(
            "• Organize connections into groups and subgroups (use / for nesting)"
        )
        insert_body(
            "• Right-click sidebar for context menu: connect, edit, duplicate, delete"
        )
        insert_body("• Mark connections as favorites for quick access")
        insert_body("• Add tags (comma-separated) for categorization")
        insert_body(
            "• Open After: set connection dependencies (auto-connect prerequisite first)"
        )
        insert_body("• Credentials are encrypted and stored securely")
        insert_body(
            "• Deleting a group: choose to keep connections (become ungrouped) or delete all"
        )
        insert_body("")

        insert_h2("Terminal Features")
        insert_h3("Split Terminals")
        insert_body(
            "• Use the Split menu button in the header bar for directional splits"
        )
        insert_body(
            "• Split Left / Right / Up / Down — places new pane on the chosen side"
        )
        insert_body("• Right-click a tab for the context menu with split options")
        insert_body("• Click Unsplit All to restore single-pane layout")
        insert_body("• Tab dragging reorders tabs within a notebook (no drag-to-split)")
        insert_body("")
        insert_h3("Font Zoom")
        insert_body("• Ctrl/⌘ + =  →  Zoom in")
        insert_body("• Ctrl/⌘ + -  →  Zoom out")
        insert_body("• Ctrl/⌘ + 0  →  Reset zoom")
        insert_body("• Ctrl/⌘ + Scroll wheel  →  Zoom in/out")
        insert_body("")
        insert_h3("Copy & Paste")
        insert_body("• macOS: ⌘+C / ⌘+V")
        insert_body("• Linux: Ctrl+Shift+C / Ctrl+Shift+V")
        insert_body("• Middle-click: paste clipboard (or copy selection then paste)")
        insert_body("")

        insert_h2("Keyboard Shortcuts")
        insert_mono("  Ctrl+Shift+T / ⌘+T       New local terminal")
        insert_mono("  Ctrl+W / ⌘+W             Close current tab")
        insert_mono("  Ctrl+Shift+N / ⌘+N       New connection dialog")
        insert_mono("  Ctrl+F / ⌘+F             Search in terminal")
        insert_mono("  Ctrl+, / ⌘+,             Open Preferences")
        insert_mono("  Ctrl+Tab                 Next tab")
        insert_mono("  Ctrl+Shift+Tab           Previous tab")
        insert_mono("  Alt+1–9                  Switch to tab by number")
        insert_mono("  Ctrl+Shift+←             Split Left")
        insert_mono("  Ctrl+Shift+→             Split Right")
        insert_mono("  Ctrl+Shift+↑             Split Up")
        insert_mono("  Ctrl+Shift+↓             Split Down")
        insert_mono("  Ctrl+Shift+D             Clone/duplicate tab")
        insert_mono("  Ctrl+Shift+S / ⌘+Shift+S Command Snippets")
        insert_mono("  F9                       Toggle sidebar")
        insert_mono("  Ctrl+Q / ⌘+Q             Quit")
        insert_body("")

        insert_h2("SFTP File Browser")
        insert_body(
            "• Right-click an SSH connection → Open SFTP to browse remote files"
        )
        insert_body("• Navigate with toolbar: Back, Up, Home, Refresh")
        insert_body("• Double-click a file to download, double-click a folder to enter")
        insert_body(
            "• Upload: click Upload button in toolbar or drag files from your file manager"
        )
        insert_body(
            "• Right-click for context menu: download, rename, delete, upload, new folder"
        )
        insert_body("• Download by dragging files out from the SFTP browser")
        insert_body("")

        insert_h2("Cluster Mode")
        insert_body("• Click the Cluster button in the header bar")
        insert_body("• Select which terminals to broadcast to")
        insert_body("• Type once, send to all selected terminals simultaneously")
        insert_body("• Great for running the same command on multiple servers")
        insert_body("")

        insert_h2("Command Snippets")
        insert_body("• Access via menu or Ctrl+Shift+S / ⌘+Shift+S")
        insert_body(
            "• Save frequently used commands with name, category, and description"
        )
        insert_body("• Click Send to send directly to the active terminal")
        insert_body("• Click Broadcast to send to ALL open terminals (batch send)")
        insert_body("• Click Edit to modify an existing snippet")
        insert_body("• Click Copy to copy to clipboard")
        insert_body("")
        insert_h3("Variable Support")
        insert_body("• Use {{variable_name}} syntax in commands for dynamic values")
        insert_body("• Example: ssh {{user}}@{{host}} -p {{port}}")
        insert_body(
            "• When sending or copying, you'll be prompted to fill in each variable"
        )
        insert_body("• Export / Import snippets via the snippet window buttons")
        insert_body("")

        insert_h2("Advanced Features")
        insert_h3("Port Forwarding")
        insert_body(
            "• Configure in connection properties: Local (-L), Remote (-R), Dynamic (-D)"
        )
        insert_body("• Example: Local 8080 → remote-host:80")
        insert_body("")
        insert_h3("Jump Hosts (ProxyJump)")
        insert_body("• Set in connection properties: e.g. user@jumphost")
        insert_body("• Multiple hops: user@host1,user@host2")
        insert_body("• ProxyCommand: paste full command like ssh user@jump -W %h:%p")
        insert_body("")
        insert_h3("Global Passphrases")
        insert_body(
            "• Set up to 5 global passphrases in Preferences → Global Passphrases"
        )
        insert_body(
            "• These are tried automatically after connection-specific passphrases"
        )
        insert_body(
            "• For multi-hop (jump host), each hop retries all passphrases from first to last"
        )
        insert_body(
            "• When no stored password exists, passphrases are also tried for password prompts"
        )
        insert_body("")
        insert_h3("Post-Login Commands")
        insert_body("• Add commands in the Commands tab of connection properties")
        insert_body("• Use ##D=1000 for delays (milliseconds) between commands")
        insert_body("")
        insert_h3("Terminal Logging")
        insert_body(
            "• Auto-logging can be configured in Preferences → Terminal Logging"
        )
        insert_body(
            "• Logs saved to ~/ssh-logs/ by default (configurable in Preferences)"
        )
        insert_body("• Log files are plain text captures of terminal output (.log)")
        insert_body("")
        insert_h3("Session Recording")
        insert_body(
            "• Right-click a terminal → Start / Stop Recording to record a session"
        )
        insert_body(
            "• Recordings are saved in asciicast v2 format (.cast) for playback"
        )
        insert_body(
            "• The tab shows [REC] while recording is active; status bar shows the file path"
        )
        insert_body(
            "• Default directory: ~/Documents/SSHClientManager-Recordings (configurable in Preferences)"
        )
        insert_body("• View and replay past recordings via Menu → Session Recordings")
        insert_body("• Playback supports speed control (0.5x–8x) and a progress bar")
        insert_body("")
        insert_h3("SSH Key Manager")
        insert_body("• Open via Menu → SSH Key Manager to view and manage SSH keys")
        insert_body("• Lists all keys in ~/.ssh/ with type, fingerprint, and comment")
        insert_body("• Generate new RSA/Ed25519/ECDSA keys directly from the dialog")
        insert_body("")
        insert_h3("Auto-Reconnect")
        insert_body(
            "• When an SSH session disconnects unexpectedly, a reconnect prompt appears"
        )
        insert_body(
            "• Click Reconnect to re-establish the same connection in the same tab"
        )
        insert_body("")
        insert_h3("SSH Config Editor")
        insert_body("• Edit ~/.ssh/config directly from the app")
        insert_body("• Access via SSH Config Editor in the menu")
        insert_body("")

        insert_h2("Import / Export / Backup")
        insert_body(
            "• Export all connections (with encrypted credentials) to a JSON file"
        )
        insert_body("• Import with overwrite or append mode")
        insert_body(
            "• All fields are preserved: name, group, protocol, command, credentials,"
        )
        insert_body(
            "  port forwards, jump hosts, tags, favorites, appearance, dependencies"
        )
        insert_body("")
        insert_h3("Full Backup & Restore")
        insert_body(
            "• Backup All: creates a ZIP with config, connections, snippets, credentials"
        )
        insert_body("• Restore All: restores everything from a backup ZIP")
        insert_body("• Access via Menu → Backup All / Restore All")
        insert_body("")

        insert_h2("Appearance")
        insert_body(
            "• Global settings in Preferences: font, colors, cursor shape, scrollback"
        )
        insert_body(
            "• Per-connection overrides: font, background/foreground color in connection properties"
        )
        insert_body("")

        scrolled.set_child(text_view)
        main_box.append(scrolled)
        dialog.set_content(main_box)
        dialog.present()

    # =====================================================================
    # New Feature Handlers (v2.1.0)
    # =====================================================================

    def _on_ssh_key_manager(self, *_):
        """Open the SSH key manager dialog."""
        dialog = SSHKeyManagerDialog(self)
        dialog.present()

    def _on_session_recordings(self, *_):
        """Open the session recordings dialog."""
        dialog = RecordingListDialog(self, config=self.config)
        dialog.present()

    def _send_notification(self, body: str):
        """Send a desktop notification."""
        try:
            notification = Gio.Notification.new("SSH Client Manager")
            notification.set_body(body)
            self.get_application().send_notification(None, notification)
        except Exception:
            pass  # Notifications may not be available

    # =====================================================================
    # Utilities
    # =====================================================================

    def _open_initial_terminal(self):
        """Open an initial local terminal."""
        self.open_local_terminal()
        return False

    @staticmethod
    def _parse_host_port(host_str: str, default_port: int = 22) -> tuple:
        """Parse host:port string, with IPv6 bracket support.

        Examples:
            "host"          → ("host", default_port)
            "host:3389"     → ("host", 3389)
            "[::1]:3389"    → ("::1", 3389)
            "[::1]"         → ("::1", default_port)
        """
        host_str = host_str.strip()
        if host_str.startswith("["):
            # IPv6 in brackets
            bracket_end = host_str.find("]")
            if bracket_end == -1:
                return (host_str[1:], default_port)
            host = host_str[1:bracket_end]
            rest = host_str[bracket_end + 1 :]
            if rest.startswith(":"):
                try:
                    return (host, int(rest[1:]))
                except ValueError:
                    return (host, default_port)
            return (host, default_port)
        elif ":" in host_str:
            # Could be IPv6 without brackets or host:port
            parts = host_str.rsplit(":", 1)
            try:
                port = int(parts[1])
                return (parts[0], port)
            except ValueError:
                # Probably IPv6 address without brackets
                return (host_str, default_port)
        else:
            return (host_str, default_port)

    def _on_quick_connect(self, entry):
        """Handle Quick Connect entry activation."""
        text = entry.get_text().strip()
        if not text:
            return

        from .connection import Connection

        # Build a Connection object and save it so reconnect works
        conn = Connection()
        conn.name = text
        conn.group = "Quick Connect"

        # Detect protocol from prefix
        if text.lower().startswith("rdp://"):
            conn.protocol = "rdp"
            rest = text[6:]
            if "@" in rest:
                conn.username, rest = rest.split("@", 1)
            conn.host, conn.port = self._parse_host_port(rest, 3389)
            conn.name = f"RDP: {conn.host}"
        elif text.lower().startswith("vnc://"):
            conn.protocol = "vnc"
            rest = text[6:]
            conn.host, conn.port = self._parse_host_port(rest, 5900)
            conn.name = f"VNC: {conn.host}"
        elif text.lower().startswith("sftp://") or text.lower().startswith("sftp "):
            conn.protocol = "sftp"
            if text.lower().startswith("sftp://"):
                rest = text[7:]
                if "@" in rest:
                    conn.username, rest = rest.split("@", 1)
                if ":" in rest:
                    conn.host, port_str = rest.rsplit(":", 1)
                    try:
                        conn.port = int(port_str)
                    except ValueError:
                        conn.host = rest
                else:
                    conn.host = rest
            else:
                conn.command = text
            conn.name = f"SFTP: {text}"
        else:
            # Default SSH
            conn.protocol = "ssh"
            if not text.lower().startswith("ssh"):
                conn.command = f"ssh {text}"
            else:
                conn.command = text
            conn.name = text

        # Save connection so reconnect and sidebar status work
        self.connection_manager.add_group("Quick Connect")
        self.connection_manager.update_connection(conn)

        # Open the connection (pass conn directly rather than by ID)
        import sys

        proto = conn.protocol

        if proto == "rdp":
            if sys.platform == "darwin":
                # On macOS, launch Windows App directly (no terminal)
                self.ssh_handler.launch_rdp_macos(conn)
            else:
                terminal = TerminalWidget(self.config, conn)
                rdp_cmd = self.ssh_handler.build_rdp_command(conn)
                self.terminal_panel.add_tab(terminal, conn, conn.name)
                terminal.spawn_command(rdp_cmd)
        elif proto == "vnc":
            terminal = TerminalWidget(self.config, conn)
            vnc_cmd = self.ssh_handler.build_vnc_command(conn)
            self.terminal_panel.add_tab(terminal, conn, conn.name)
            terminal.spawn_command(vnc_cmd)
        elif proto == "sftp":
            terminal = TerminalWidget(self.config, conn)
            sftp_cmd = self.ssh_handler.build_sftp_command(conn)
            env, session_id = self.ssh_handler.build_environment(conn)
            terminal._askpass_session_id = session_id
            self.terminal_panel.add_tab(terminal, conn, conn.name)
            terminal.spawn_command(sftp_cmd, env)
            # Track as SFTP terminal and show browser
            self._sftp_terminals[id(terminal)] = conn
            self.sidebar.show_sftp_browser(conn, self.credential_store)
        else:
            terminal = TerminalWidget(self.config, conn)
            ssh_cmd = self.ssh_handler.build_ssh_command(conn)
            env, session_id = self.ssh_handler.build_environment(conn)
            terminal._askpass_session_id = session_id
            self.terminal_panel.add_tab(terminal, conn, conn.name)
            terminal.spawn_command(ssh_cmd, env)

        entry.set_text("")
        self._set_status(f"Quick Connect: {conn.name}")

    def _on_preferences_applied(self, dialog):
        """Handle preferences being applied: hot-reload all terminals."""
        self.config.load()  # Re-read from disk
        for terminal in self.terminal_panel.get_all_terminals():
            terminal._configure_terminal()

    def _set_status(self, text: str):
        """Update the status bar text."""
        self.status_label.set_text(text)

    def _update_tab_count(self):
        """Update the tab count display."""
        count = self.terminal_panel.get_tab_count()
        self.tab_count_label.set_text(f"{count} tab{'s' if count != 1 else ''}")

    def _active_terminal_action(self, method_name: str, *args):
        """Call a method on the active terminal."""
        terminal = self.terminal_panel.focused_terminal
        if terminal:
            method = getattr(terminal, method_name, None)
            if method:
                method(*args)

    def _on_close_request(self, window):
        """Handle window close request."""
        # Save window state
        self.config.batch_update(
            {
                "sidebar_width": self.paned.get_position(),
            }
        )
        alloc = self.get_allocation()
        updates = {}
        if alloc.width > 0:
            updates["window_width"] = alloc.width
        if alloc.height > 0:
            updates["window_height"] = alloc.height
        if updates:
            self.config.batch_update(updates)

        # Check if any terminals are still running
        if self.config.get("confirm_close_window", True):
            tab_count = self.terminal_panel.get_tab_count()
            if tab_count > 0:
                dialog = Adw.MessageDialog(
                    transient_for=self,
                    heading="Quit SSH Client Manager?",
                    body=f"{tab_count} terminal(s) are still open.\n"
                    f"All connections will be closed.",
                )
                dialog.add_response("cancel", "Cancel")
                dialog.add_response("quit", "Quit")
                dialog.set_response_appearance(
                    "quit", Adw.ResponseAppearance.DESTRUCTIVE
                )

                def on_response(d, response):
                    if response == "quit":
                        self.ssh_handler.cleanup_all()
                        self.destroy()

                dialog.connect("response", on_response)
                dialog.present()
                return True  # Block close; dialog handles it

        # Clean up SSH handler
        self.ssh_handler.cleanup_all()

        return False  # Allow close
