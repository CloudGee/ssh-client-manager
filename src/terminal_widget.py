"""
VTE Terminal widget wrapper for GTK4.

Provides a configured VTE terminal with scrollbar, search, and connection
lifecycle management.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Vte", "3.91")

from gi.repository import Gtk, Vte, GLib, Gdk, Pango, GObject
from typing import Optional

from .connection import Connection
from .config import Config


class TerminalWidget(Gtk.Box):
    """
    A VTE terminal with scrollbar and connection management.

    Signals:
        title-changed: Terminal title changed
        child-exited: The shell/SSH process exited
        connection-established: SSH connection appears alive
    """

    __gsignals__ = {
        "title-changed": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "child-exited": (GObject.SignalFlags.RUN_LAST, None, (int,)),
    }

    def __init__(self, config: Config, connection: Optional[Connection] = None):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)

        self.config = config
        self.connection = connection
        self._process_pid = -1
        self._log_file = None
        self._log_handler_id = None
        self._font_scale = 1.0
        self._session_recorder = None
        self._recording_handler_id = None
        # Set when SSH -f flag is used (backgrounds itself); prevents false
        # disconnect detection when the parent SSH process exits immediately.
        self._background_mode = False

        # Create VTE terminal
        self.vte = Vte.Terminal()
        self._configure_terminal()

        # Create scrollbar
        vadjustment = self.vte.get_vadjustment()
        self.scrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL, adjustment=vadjustment
        )

        # Pack terminal + scrollbar
        self.vte.set_hexpand(True)
        self.vte.set_vexpand(True)
        self.append(self.vte)
        self.append(self.scrollbar)

        # Connect signals
        self.vte.connect("child-exited", self._on_child_exited)
        self.vte.connect("window-title-changed", self._on_title_changed)

        # Focus handling
        self.vte.set_focusable(True)
        self.vte.set_can_focus(True)

        # Key event controller for shortcuts (CAPTURE phase to intercept before VTE)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.vte.add_controller(key_ctrl)

        # Middle-click to paste from system clipboard (not PRIMARY selection)
        mid_click = Gtk.GestureClick(button=2)
        mid_click.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        mid_click.connect("pressed", self._on_middle_click)
        self.vte.add_controller(mid_click)

        # Right-click menu
        self._setup_context_menu()

        # Ctrl/Cmd + scroll wheel for font zoom
        # Use CAPTURE phase on the parent box so we intercept before VTE
        # processes the scroll (prevents content from scrolling during zoom)
        scroll_ctrl = Gtk.EventControllerScroll()
        scroll_ctrl.set_flags(
            Gtk.EventControllerScrollFlags.VERTICAL
            | Gtk.EventControllerScrollFlags.DISCRETE
        )
        scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_ctrl.connect("scroll", self._on_scroll_zoom)
        self.add_controller(scroll_ctrl)

    def _configure_terminal(self):
        """Apply terminal configuration."""
        cfg = self.config

        # Font
        font_desc = Pango.FontDescription.from_string(
            (self.connection and self.connection.font) or cfg["terminal_font"]
        )
        self.vte.set_font(font_desc)

        # Scrollback
        self.vte.set_scrollback_lines(cfg["terminal_scrollback_lines"])

        # Colors – use connection overrides only when they are set AND distinct
        conn_bg = (self.connection and self.connection.bg_color) or ""
        conn_fg = (self.connection and self.connection.fg_color) or ""
        if conn_bg and conn_fg and conn_bg == conn_fg:
            # Same fg/bg would make text invisible → ignore both
            conn_bg = conn_fg = ""
        bg_str = conn_bg or cfg["terminal_bg_color"]
        fg_str = conn_fg or cfg["terminal_fg_color"]

        bg = Gdk.RGBA()
        bg.parse(bg_str)
        fg = Gdk.RGBA()
        fg.parse(fg_str)

        # Parse palette
        palette = []
        for color_str in cfg["terminal_palette"]:
            c = Gdk.RGBA()
            c.parse(color_str)
            palette.append(c)

        self.vte.set_colors(fg, bg, palette)

        # Cursor
        cursor_map = {
            "block": Vte.CursorShape.BLOCK,
            "ibeam": Vte.CursorShape.IBEAM,
            "underline": Vte.CursorShape.UNDERLINE,
        }
        shape = cursor_map.get(cfg["terminal_cursor_shape"], Vte.CursorShape.BLOCK)
        self.vte.set_cursor_shape(shape)

        # Misc
        self.vte.set_allow_bold(cfg["terminal_allow_bold"])
        self.vte.set_audible_bell(cfg["terminal_audible_bell"])

        # Allow hyperlinks
        try:
            self.vte.set_allow_hyperlink(True)
        except AttributeError:
            pass  # Older VTE

        # Word char exceptions for double-click selection
        try:
            self.vte.set_word_char_exceptions(cfg["word_separators"])
        except AttributeError:
            pass

    def spawn_command(
        self, argv: list[str], env: list[str] = None, working_dir: str = None
    ):
        """
        Spawn a process inside the terminal.

        Args:
            argv: Command and arguments, e.g. ["ssh", "user@host"]
            env: Environment variables as "KEY=VALUE" strings
            working_dir: Working directory (default: home)
        """
        import os
        import shutil

        if working_dir is None:
            working_dir = str(GLib.get_home_dir())

        # Build environment
        if env is None:
            env = [f"{k}={v}" for k, v in os.environ.items()]

        # Resolve argv[0] to absolute path (GLib.SpawnFlags.DEFAULT
        # requires an absolute path; this avoids needing SEARCH_PATH)
        if argv and not os.path.isabs(argv[0]):
            resolved = shutil.which(argv[0])
            if resolved:
                argv = [resolved] + argv[1:]
            else:
                print(f"Warning: command not found in PATH: {argv[0]}")
                return

        # Detect SSH -f flag (background mode): SSH will fork to background
        # and the terminal child process exits immediately with code 0.
        # We mark the terminal so child-exited doesn't trigger reconnect logic.
        self._background_mode = bool(
            argv and os.path.basename(argv[0]) in ("ssh", "ssh.real") and "-f" in argv
        )

        try:
            self.vte.spawn_async(
                Vte.PtyFlags.DEFAULT,  # pty_flags
                working_dir,  # working_directory
                argv,  # argv
                env,  # envv
                GLib.SpawnFlags.DEFAULT,  # spawn_flags
                None,  # child_setup
                None,  # child_setup_data
                -1,  # timeout (-1 = default)
                None,  # cancellable
                self._on_spawn_complete,  # callback
                None,  # callback_data
            )
        except Exception as e:
            print(f"Error spawning process: {e}")
            import traceback

            traceback.print_exc()

    def _on_spawn_complete(self, terminal, pid, error, *user_data):
        """Callback after spawn_async completes."""
        if error:
            print(f"Spawn error: {error}")
            return
        self._process_pid = pid

    def feed_child(self, text: str):
        """Send text to the terminal as if typed."""
        try:
            self.vte.feed_child(text.encode("utf-8"))
        except TypeError:
            # Some VTE versions need different args
            data = text.encode("utf-8")
            self.vte.feed_child(data, len(data))

    def copy_clipboard(self):
        """Copy selected text to clipboard."""
        self.vte.copy_clipboard_format(Vte.Format.TEXT)

    def paste_clipboard(self):
        """Paste from clipboard."""
        self.vte.paste_clipboard()

    def select_all(self):
        """Select all terminal content."""
        self.vte.select_all()

    def reset_terminal(self, clear: bool = False):
        """Reset the terminal. If clear=True, also clear scrollback."""
        self.vte.reset(True, clear)

    def get_text(self) -> str:
        """Get all visible terminal text."""
        try:
            text = self.vte.get_text_format(Vte.Format.TEXT)
            if isinstance(text, tuple):
                text = text[0]
            return text if text else ""
        except Exception:
            return ""

    def search_text(self, pattern: str, backward: bool = False):
        """Search for text in the terminal.

        On incremental (search-changed) calls the default direction is
        backward because the user most often wants to find something that
        already scrolled past.  If the first direction fails we try the
        other direction so wrap-around works naturally.
        """
        if not pattern:
            self.vte.search_set_regex(None, 0)
            return
        try:
            import re

            escaped = re.escape(pattern)
            regex = Vte.Regex.new_for_search(escaped, len(escaped.encode()), 0)
            self.vte.search_set_regex(regex, 0)
            self.vte.search_set_wrap_around(True)
            if backward:
                if not self.vte.search_find_previous():
                    self.vte.search_find_next()
            else:
                # Try backward first (most content is above cursor),
                # fall back to forward.
                if not self.vte.search_find_previous():
                    self.vte.search_find_next()
        except Exception as e:
            print(f"Search error: {e}")

    def search_next(self, pattern: str):
        """Search forward (downward) explicitly — used by the Next button."""
        if not pattern:
            return
        try:
            import re

            escaped = re.escape(pattern)
            regex = Vte.Regex.new_for_search(escaped, len(escaped.encode()), 0)
            self.vte.search_set_regex(regex, 0)
            self.vte.search_set_wrap_around(True)
            if not self.vte.search_find_next():
                self.vte.search_find_previous()
        except Exception as e:
            print(f"Search error: {e}")

    def set_font_scale(self, scale: float):
        """Set the terminal font scale."""
        self.vte.set_font_scale(scale)

    def get_title(self) -> str:
        """Get the terminal window title."""
        return self.vte.get_window_title() or ""

    def grab_focus(self):
        """Focus the VTE terminal."""
        self.vte.grab_focus()

    # --- Font Zoom ---

    def zoom_in(self):
        """Increase font size."""
        self._font_scale = min(self._font_scale + 0.1, 5.0)
        self.vte.set_font_scale(self._font_scale)

    def zoom_out(self):
        """Decrease font size."""
        self._font_scale = max(self._font_scale - 0.1, 0.3)
        self.vte.set_font_scale(self._font_scale)

    def zoom_reset(self):
        """Reset font size to default."""
        self._font_scale = 1.0
        self.vte.set_font_scale(self._font_scale)

    def _on_scroll_zoom(self, controller, dx, dy):
        """Handle Ctrl/Cmd + scroll wheel for font zoom."""
        import sys

        seat = controller.get_current_event().get_device().get_seat()
        # Check modifier state from the current event
        event = controller.get_current_event()
        state = event.get_modifier_state() if event else 0

        if sys.platform == "darwin":
            modifier = state & Gdk.ModifierType.META_MASK
        else:
            modifier = state & Gdk.ModifierType.CONTROL_MASK

        if not modifier:
            return False  # Let normal scrolling happen

        if dy < 0:
            self.zoom_in()
        elif dy > 0:
            self.zoom_out()
        return True  # Consume the event

    # --- Signal handlers ---

    def _on_child_exited(self, terminal, status):
        self.stop_logging()
        self.emit("child-exited", status)

    def _on_title_changed(self, terminal):
        title = self.get_title()
        self.emit("title-changed", title)

    # --- Logging ---

    def start_logging(self, log_path: str):
        """Start logging terminal output to a file."""
        import os

        try:
            os.makedirs(os.path.dirname(log_path), exist_ok=True)
            self._log_file = open(log_path, "ab")
            self._log_handler_id = self.vte.connect(
                "contents-changed", self._on_contents_for_log
            )
            self._log_last_text = ""
        except IOError as e:
            print(f"Could not start logging: {e}")

    def stop_logging(self):
        """Stop logging terminal output."""
        if self._log_handler_id is not None:
            try:
                self.vte.disconnect(self._log_handler_id)
            except Exception:
                pass
            self._log_handler_id = None
        if self._log_file is not None:
            try:
                self._log_file.close()
            except Exception:
                pass
            self._log_file = None

    def _on_contents_for_log(self, vte):
        """Capture new terminal content for logging."""
        if not self._log_file:
            return
        try:
            text = vte.get_text_format(Vte.Format.TEXT)
            if isinstance(text, tuple):
                text = text[0]
            if text and text != self._log_last_text:
                # Write only the new portion
                if self._log_last_text and text.startswith(self._log_last_text):
                    new_data = text[len(self._log_last_text) :]
                else:
                    new_data = text
                if new_data:
                    self._log_file.write(new_data.encode("utf-8", errors="replace"))
                    self._log_file.flush()
                self._log_last_text = text
        except Exception:
            pass

    @property
    def is_logging(self) -> bool:
        """Whether terminal output is being logged."""
        return self._log_file is not None

    def start_recording(self, path: str):
        """Start recording terminal session to asciicast file."""
        from .session_recorder import SessionRecorder

        if self._session_recorder and self._session_recorder.is_recording:
            self.stop_recording()

        cols = self.vte.get_column_count()
        rows = self.vte.get_row_count()
        self._session_recorder = SessionRecorder(cols, rows)
        self._session_recorder.start(path, cols, rows)

        # State for line-by-line diff recording
        self._rec_prev_lines: list[str] = []
        self._rec_pending = False

        # Hook into VTE contents-changed signal to capture output
        self._recording_handler_id = self.vte.connect(
            "contents-changed", self._on_contents_for_recording
        )

    def stop_recording(self):
        """Stop recording the terminal session."""
        if self._recording_handler_id is not None:
            try:
                self.vte.disconnect(self._recording_handler_id)
            except Exception:
                pass
            self._recording_handler_id = None
        if self._session_recorder:
            self._session_recorder.stop()
            self._session_recorder = None
        self._rec_prev_lines = []
        self._rec_pending = False

    def _on_contents_for_recording(self, vte):
        """Debounce: schedule a capture after a short delay."""
        if not self._session_recorder or not self._session_recorder.is_recording:
            return
        if not self._rec_pending:
            self._rec_pending = True
            GLib.timeout_add(50, self._do_record_capture)

    def _do_record_capture(self) -> bool:
        """Capture the visible viewport and record changed lines."""
        self._rec_pending = False
        if not self._session_recorder or not self._session_recorder.is_recording:
            return False
        try:
            vte = self.vte
            cols = vte.get_column_count()
            rows = vte.get_row_count()

            # Determine the visible viewport row range.
            # get_cursor_position() returns (col, row) in absolute coords
            # (including scrollback).  The visible viewport spans the last
            # ``rows`` lines of the buffer.
            vadj = vte.get_vadjustment()
            # upper = total lines (scrollback + visible), page_size = visible rows
            first_visible = int(vadj.get_value())
            last_visible = first_visible + rows - 1

            # Capture only the visible area using get_text_range_format
            result = vte.get_text_range_format(
                Vte.Format.TEXT, first_visible, 0, last_visible, cols - 1
            )
            text = result[0] if isinstance(result, tuple) else result
            if text is None:
                return False

            # Split into individual lines, strip trailing spaces per line
            lines = text.split("\n")
            lines = [l.rstrip() for l in lines]

            # Remove excess trailing empty lines (keep at most ``rows`` lines)
            while len(lines) > rows:
                if not lines[-1]:
                    lines.pop()
                else:
                    break
            while lines and not lines[-1]:
                lines.pop()

            prev = self._rec_prev_lines

            if lines == prev:
                return False  # No change

            # Build output with ANSI cursor positioning for changed lines
            parts: list[str] = []
            for i, line in enumerate(lines):
                old_line = prev[i] if i < len(prev) else None
                if line != old_line:
                    # ESC[row;1H = move cursor to row,col1  ESC[2K = erase line
                    parts.append(f"\x1b[{i + 1};1H\x1b[2K{line}")

            # Clear any extra lines that were in prev but not in current
            for i in range(len(lines), len(prev)):
                parts.append(f"\x1b[{i + 1};1H\x1b[2K")

            # Position cursor where VTE's cursor actually is
            cursor_col, cursor_row = vte.get_cursor_position()
            rel_row = cursor_row - first_visible + 1
            if 1 <= rel_row <= rows:
                parts.append(f"\x1b[{rel_row};{cursor_col + 1}H")

            if parts:
                self._session_recorder.feed("".join(parts))

            self._rec_prev_lines = lines[:]

        except Exception:
            pass
        return False

    @property
    def is_recording(self) -> bool:
        """Whether terminal session is being recorded."""
        return (
            self._session_recorder is not None and self._session_recorder.is_recording
        )

    def _on_middle_click(self, gesture, n_press, x, y):
        """Middle-click: if text is selected, copy+paste it; else paste clipboard."""
        if self.vte.get_has_selection():
            # Copy selection to clipboard, then paste it
            self.copy_clipboard()
            GLib.timeout_add(50, self.paste_clipboard)
        else:
            self.paste_clipboard()
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        """Handle keyboard shortcuts including macOS Cmd+C/V."""
        import sys

        ctrl = state & Gdk.ModifierType.CONTROL_MASK
        shift = state & Gdk.ModifierType.SHIFT_MASK
        meta = state & Gdk.ModifierType.META_MASK

        # macOS: Command+C/V for copy/paste, Command+=/- for zoom
        if sys.platform == "darwin" and meta:
            if keyval in (Gdk.KEY_c, Gdk.KEY_C):
                self.copy_clipboard()
                return True
            elif keyval in (Gdk.KEY_v, Gdk.KEY_V):
                self.paste_clipboard()
                return True
            elif keyval in (Gdk.KEY_equal, Gdk.KEY_plus):
                self.zoom_in()
                return True
            elif keyval in (Gdk.KEY_minus, Gdk.KEY_underscore):
                self.zoom_out()
                return True
            elif keyval in (Gdk.KEY_0, Gdk.KEY_parenright):
                self.zoom_reset()
                return True

        # Linux/other: Ctrl+Shift+C/V
        if ctrl and shift:
            if keyval == Gdk.KEY_C:
                self.copy_clipboard()
                return True
            elif keyval == Gdk.KEY_V:
                self.paste_clipboard()
                return True

        # Ctrl+=/- for font zoom (Linux/all)
        if ctrl and not shift:
            if keyval in (Gdk.KEY_equal, Gdk.KEY_plus):
                self.zoom_in()
                return True
            elif keyval in (Gdk.KEY_minus, Gdk.KEY_underscore):
                self.zoom_out()
                return True
            elif keyval == Gdk.KEY_0:
                self.zoom_reset()
                return True

        return False

    def _setup_context_menu(self):
        """Set up right-click context menu."""
        click = Gtk.GestureClick(button=3)  # Right click
        click.connect("pressed", self._show_context_menu)
        self.vte.add_controller(click)

    def _show_context_menu(self, gesture, n_press, x, y):
        """Show the terminal context menu."""
        menu_model = self._build_context_menu()
        popover = Gtk.PopoverMenu(menu_model=menu_model)
        popover.set_parent(self.vte)
        popover.set_pointing_to(Gdk.Rectangle(int(x), int(y), 1, 1))

        # We need to connect action handlers on the widget
        popover.popup()

    def _build_context_menu(self):
        """Build the context menu model."""
        from gi.repository import Gio

        menu = Gio.Menu()

        section1 = Gio.Menu()
        section1.append("Copy", "term.copy")
        section1.append("Paste", "term.paste")
        section1.append("Select All", "term.select-all")
        menu.append_section(None, section1)

        section2 = Gio.Menu()
        section2.append("Reset Terminal", "term.reset")
        section2.append("Clear Scrollback", "term.clear")
        menu.append_section(None, section2)

        section3 = Gio.Menu()
        section3.append("Split Horizontally", "panel.split-h")
        section3.append("Split Vertically", "panel.split-v")
        menu.append_section(None, section3)

        section4 = Gio.Menu()
        section4.append("Command Snippets", "win.snippets")
        section4.append("Start / Stop Recording", "term.toggle-record")
        menu.append_section(None, section4)

        return menu
