"""
Session recording and playback.

Records terminal I/O in asciicast v2 format for later playback
using asciinema or the built-in player.
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, GObject, Gio
from .utils import block_scroll


class SessionRecorder:
    """Records VTE terminal output in asciicast v2 format."""

    def __init__(self, cols: int = 120, rows: int = 36):
        self._file = None
        self._start_time: float = 0.0
        self._recording = False
        self._path: str = ""
        self._cols = cols
        self._rows = rows

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def path(self) -> str:
        return self._path

    def start(self, path: str, cols: int = 0, rows: int = 0):
        """Start recording to file in asciicast v2 format."""
        if self._recording:
            self.stop()

        self._path = path
        if cols:
            self._cols = cols
        if rows:
            self._rows = rows

        self._file = open(path, "w", buffering=1)
        header = {
            "version": 2,
            "width": self._cols,
            "height": self._rows,
            "timestamp": int(time.time()),
            "env": {"TERM": os.environ.get("TERM", "xterm-256color")},
        }
        self._file.write(json.dumps(header) + "\n")
        self._start_time = time.monotonic()
        self._recording = True

    def feed(self, data: str):
        """Record a chunk of terminal output."""
        if not self._recording or not self._file:
            return
        elapsed = time.monotonic() - self._start_time
        event = [round(elapsed, 6), "o", data]
        try:
            self._file.write(json.dumps(event) + "\n")
        except (IOError, ValueError):
            pass

    def stop(self):
        """Stop recording and close file."""
        self._recording = False
        if self._file:
            try:
                self._file.close()
            except IOError:
                pass
            self._file = None

    def __del__(self):
        self.stop()


def get_recordings_dir(config=None) -> Path:
    """Return the recordings directory, creating it if needed.

    Uses the directory from config 'session_recordings_directory' if set,
    otherwise defaults to ~/Documents/SSHClientManager-Recordings.
    """
    custom_dir = ""
    if config is not None:
        custom_dir = config.get("session_recordings_directory", "")
    if custom_dir:
        recordings_dir = Path(os.path.expanduser(custom_dir))
    else:
        recordings_dir = Path.home() / "Documents" / "SSHClientManager-Recordings"
    recordings_dir.mkdir(parents=True, exist_ok=True)
    return recordings_dir


class SessionPlayerDialog(Adw.Window):
    """Simple asciicast v2 playback dialog using VTE."""

    def __init__(self, parent: Gtk.Window, filepath: str):
        super().__init__(
            transient_for=parent,
            modal=True,
            title=f"Playback: {Path(filepath).name}",
            default_width=800,
            default_height=500,
        )
        self._filepath = filepath
        self._events = []
        self._event_idx = 0
        self._playing = False
        self._timeout_id = 0
        self._speed = 1.0

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header
        header = Adw.HeaderBar()

        btn_play = Gtk.Button(icon_name="media-playback-start-symbolic")
        btn_play.set_tooltip_text("Play / Pause")
        btn_play.connect("clicked", self._on_play_pause)
        self._btn_play = btn_play
        header.pack_start(btn_play)

        btn_restart = Gtk.Button(icon_name="media-skip-backward-symbolic")
        btn_restart.set_tooltip_text("Restart")
        btn_restart.connect("clicked", self._on_restart)
        header.pack_start(btn_restart)

        # Speed control
        speed_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        speed_label = Gtk.Label(label="Speed:")
        speed_label.add_css_class("dim-label")
        speed_box.append(speed_label)

        speed_combo = Gtk.ComboBoxText()
        for s in ("0.5x", "1x", "2x", "4x", "8x"):
            speed_combo.append_text(s)
        speed_combo.set_active(1)
        speed_combo.connect("changed", self._on_speed_changed)
        block_scroll(speed_combo)
        speed_box.append(speed_combo)

        header.pack_end(speed_box)
        main_box.append(header)

        # VTE terminal for playback
        try:
            gi.require_version("Vte", "3.91")
            from gi.repository import Vte

            self._terminal = Vte.Terminal()
            self._terminal.set_vexpand(True)
            self._terminal.set_hexpand(True)
            self._terminal.set_input_enabled(False)
            main_box.append(self._terminal)
            self._has_vte = True
        except (ValueError, ImportError):
            # Fallback: plain text view
            self._has_vte = False
            sw = Gtk.ScrolledWindow()
            sw.set_vexpand(True)
            tv = Gtk.TextView()
            tv.set_monospace(True)
            tv.set_editable(False)
            tv.set_margin_start(8)
            tv.set_margin_end(8)
            self._text_buffer = tv.get_buffer()
            sw.set_child(tv)
            main_box.append(sw)

        # Progress
        self._progress = Gtk.ProgressBar()
        self._progress.set_margin_start(8)
        self._progress.set_margin_end(8)
        self._progress.set_margin_bottom(4)
        self._progress.set_margin_top(4)
        main_box.append(self._progress)

        self.set_content(main_box)
        self._load_events()

    def _load_events(self):
        """Load asciicast v2 events from file."""
        try:
            with open(self._filepath, "r") as f:
                lines = f.readlines()
        except IOError:
            return

        if not lines:
            return

        # Parse header (first line) to get recording dimensions
        try:
            header = json.loads(lines[0].strip())
            self._rec_width = header.get("width", 80)
            self._rec_height = header.get("height", 24)
            # Set player terminal to match recording dimensions
            if self._has_vte:
                self._terminal.set_size(self._rec_width, self._rec_height)
        except (json.JSONDecodeError, KeyError):
            pass

        # Load events
        for line in lines[1:]:
            try:
                event = json.loads(line.strip())
                if isinstance(event, list) and len(event) >= 3:
                    self._events.append(event)
            except json.JSONDecodeError:
                continue

    def _on_play_pause(self, _btn):
        if self._playing:
            self._pause()
        else:
            self._play()

    def _play(self):
        self._playing = True
        self._btn_play.set_icon_name("media-playback-pause-symbolic")
        self._schedule_next()

    def _pause(self):
        self._playing = False
        self._btn_play.set_icon_name("media-playback-start-symbolic")
        if self._timeout_id:
            GLib.source_remove(self._timeout_id)
            self._timeout_id = 0

    def _on_restart(self, _btn):
        self._pause()
        self._event_idx = 0
        self._progress.set_fraction(0)
        if self._has_vte:
            self._terminal.reset(True, True)
        else:
            self._text_buffer.set_text("")

    def _on_speed_changed(self, combo):
        text = combo.get_active_text()
        if text:
            self._speed = float(text.replace("x", ""))

    def _schedule_next(self):
        if not self._playing or self._event_idx >= len(self._events):
            self._pause()
            return

        if self._event_idx == 0:
            delay = 0
        else:
            prev_time = self._events[self._event_idx - 1][0]
            cur_time = self._events[self._event_idx][0]
            delay = max(0, (cur_time - prev_time) / self._speed)
            delay = min(delay, 2.0)  # Cap max delay at 2 seconds

        self._timeout_id = GLib.timeout_add(int(delay * 1000), self._play_event)

    def _play_event(self) -> bool:
        if self._event_idx >= len(self._events):
            self._pause()
            return False

        event = self._events[self._event_idx]
        data = event[2] if len(event) > 2 else ""

        if self._has_vte:
            self._terminal.feed(data.encode("utf-8"))
        else:
            self._text_buffer.insert(self._text_buffer.get_end_iter(), data)

        self._event_idx += 1
        total = len(self._events)
        if total > 0:
            self._progress.set_fraction(self._event_idx / total)

        self._schedule_next()
        return False


class RecordingListDialog(Adw.Window):
    """Dialog showing saved recordings with playback."""

    def __init__(self, parent: Gtk.Window, config=None):
        super().__init__(
            transient_for=parent,
            modal=True,
            title="Session Recordings",
            default_width=500,
            default_height=400,
        )
        self._parent_window = parent
        self._config = config
        self._all_records: list[dict] = []

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        btn_open = Gtk.Button(icon_name="document-open-symbolic")
        btn_open.set_tooltip_text("Open Recording File")
        btn_open.connect("clicked", self._on_open_file)
        header.pack_start(btn_open)
        main_box.append(header)

        controls = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        controls.set_margin_start(12)
        controls.set_margin_end(12)
        controls.set_margin_top(8)

        self._search_entry = Gtk.SearchEntry()
        self._search_entry.set_placeholder_text("Search by host/date/size/name...")
        self._search_entry.connect("search-changed", lambda *_: self._apply_filters())
        controls.append(self._search_entry)

        filter_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)

        self._host_filter = Gtk.ComboBoxText()
        self._host_filter.append_text("Host: All")
        self._host_filter.set_active(0)
        self._host_filter.connect("changed", lambda *_: self._apply_filters())
        block_scroll(self._host_filter)
        filter_row.append(self._host_filter)

        self._date_filter = Gtk.ComboBoxText()
        for text in ("Date: All", "Date: Today", "Date: 7 days", "Date: 30 days"):
            self._date_filter.append_text(text)
        self._date_filter.set_active(0)
        self._date_filter.connect("changed", lambda *_: self._apply_filters())
        block_scroll(self._date_filter)
        filter_row.append(self._date_filter)

        self._size_filter = Gtk.ComboBoxText()
        for text in (
            "Size: All",
            "Size: < 1 MB",
            "Size: 1 MB - 10 MB",
            "Size: > 10 MB",
        ):
            self._size_filter.append_text(text)
        self._size_filter.set_active(0)
        self._size_filter.connect("changed", lambda *_: self._apply_filters())
        block_scroll(self._size_filter)
        filter_row.append(self._size_filter)

        controls.append(filter_row)
        main_box.append(controls)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_margin_start(12)
        self._list_box.set_margin_end(12)
        self._list_box.set_margin_top(12)
        self._list_box.set_margin_bottom(12)

        placeholder = Gtk.Label(label="No recordings found")
        placeholder.add_css_class("dim-label")
        placeholder.set_margin_top(24)
        placeholder.set_margin_bottom(24)
        self._list_box.set_placeholder(placeholder)

        scrolled.set_child(self._list_box)
        main_box.append(scrolled)

        self.set_content(main_box)
        self._load_recordings()

    def _load_recordings(self):
        """List all .cast files in recordings directory."""
        rec_dir = get_recordings_dir(self._config)
        files = sorted(
            rec_dir.glob("*.cast"), key=lambda f: f.stat().st_mtime, reverse=True
        )

        self._all_records = []
        hosts = set()

        for f in files:
            stat = f.stat()
            host = self._extract_host_tag(f)
            hosts.add(host)
            self._all_records.append(
                {
                    "path": f,
                    "stem": f.stem,
                    "host": host,
                    "size_bytes": stat.st_size,
                    "size_kb": stat.st_size / 1024,
                    "mtime": stat.st_mtime,
                    "mtime_text": time.strftime(
                        "%Y-%m-%d %H:%M", time.localtime(stat.st_mtime)
                    ),
                }
            )

        self._host_filter.remove_all()
        self._host_filter.append_text("Host: All")
        for host in sorted(hosts):
            self._host_filter.append_text(f"Host: {host}")
        self._host_filter.set_active(0)

        self._apply_filters()

    def _extract_host_tag(self, path: Path) -> str:
        """Try to extract host part from recording file name pattern."""
        m = re.match(r"^(.*)_\d{8}_\d{6}$", path.stem)
        return m.group(1) if m else path.stem

    def _apply_filters(self):
        """Apply search and host/date/size filters to recordings list."""
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        query = (self._search_entry.get_text() or "").strip().lower()
        host_filter = (self._host_filter.get_active_text() or "Host: All").replace(
            "Host: ", "", 1
        )
        date_idx = self._date_filter.get_active()
        size_idx = self._size_filter.get_active()
        now = time.time()

        def _date_ok(mtime: float) -> bool:
            if date_idx <= 0:
                return True
            age = now - mtime
            if date_idx == 1:
                return age <= 86400
            if date_idx == 2:
                return age <= 86400 * 7
            if date_idx == 3:
                return age <= 86400 * 30
            return True

        def _size_ok(size_bytes: int) -> bool:
            if size_idx <= 0:
                return True
            if size_idx == 1:
                return size_bytes < 1024 * 1024
            if size_idx == 2:
                return 1024 * 1024 <= size_bytes <= 10 * 1024 * 1024
            if size_idx == 3:
                return size_bytes > 10 * 1024 * 1024
            return True

        records = []
        for rec in self._all_records:
            if host_filter != "All" and rec["host"] != host_filter:
                continue
            if not _date_ok(rec["mtime"]):
                continue
            if not _size_ok(rec["size_bytes"]):
                continue
            if query:
                hay = f"{rec['stem']} {rec['host']} {rec['mtime_text']} {rec['size_kb']:.1f}"
                if query not in hay.lower():
                    continue
            records.append(rec)

        for rec in records:
            f = rec["path"]
            row = Adw.ActionRow()
            row.set_title(rec["stem"])
            row.set_subtitle(
                f"{rec['mtime_text']}  •  {rec['size_kb']:.1f} KB  •  {rec['host']}"
            )

            btn_play = Gtk.Button(icon_name="media-playback-start-symbolic")
            btn_play.set_tooltip_text("Play")
            btn_play.add_css_class("flat")
            btn_play.set_valign(Gtk.Align.CENTER)
            btn_play.connect(
                "clicked",
                lambda _, p=str(f): self._play_recording(p),
            )
            row.add_suffix(btn_play)

            btn_copy = Gtk.Button(icon_name="edit-copy-symbolic")
            btn_copy.set_tooltip_text("Copy File")
            btn_copy.add_css_class("flat")
            btn_copy.set_valign(Gtk.Align.CENTER)
            btn_copy.connect(
                "clicked",
                lambda _, p=str(f): self._copy_recording(p),
            )
            row.add_suffix(btn_copy)

            btn_rename = Gtk.Button(icon_name="document-edit-symbolic")
            btn_rename.set_tooltip_text("Rename")
            btn_rename.add_css_class("flat")
            btn_rename.set_valign(Gtk.Align.CENTER)
            btn_rename.connect(
                "clicked",
                lambda _, p=f: self._rename_recording(p),
            )
            row.add_suffix(btn_rename)

            btn_gif = Gtk.Button(icon_name="image-x-generic-symbolic")
            btn_gif.set_tooltip_text("Export GIF")
            btn_gif.add_css_class("flat")
            btn_gif.set_valign(Gtk.Align.CENTER)
            btn_gif.connect(
                "clicked",
                lambda _, p=f: self._export_gif(p),
            )
            row.add_suffix(btn_gif)

            btn_mp4 = Gtk.Button(icon_name="video-x-generic-symbolic")
            btn_mp4.set_tooltip_text("Export MP4")
            btn_mp4.add_css_class("flat")
            btn_mp4.set_valign(Gtk.Align.CENTER)
            btn_mp4.connect(
                "clicked",
                lambda _, p=f: self._export_mp4(p),
            )
            row.add_suffix(btn_mp4)

            btn_del = Gtk.Button(icon_name="user-trash-symbolic")
            btn_del.set_tooltip_text("Delete")
            btn_del.add_css_class("flat")
            btn_del.set_valign(Gtk.Align.CENTER)
            btn_del.connect(
                "clicked",
                lambda _, p=f: self._delete_recording(p),
            )
            row.add_suffix(btn_del)

            self._list_box.append(row)

    def _play_recording(self, path: str):
        player = SessionPlayerDialog(self, path)
        player.present()

    def _copy_recording(self, path: str):
        """Copy a recording file via a Save dialog."""
        src = Path(path)
        dialog = Gtk.FileDialog()
        dialog.set_initial_name(src.name)
        dialog.save(self, None, lambda d, r, s=src: self._on_copy_save_done(d, r, s))

    def _on_copy_save_done(self, dialog, result, src: Path):
        try:
            f = dialog.save_finish(result)
            if f:
                import shutil

                shutil.copy2(str(src), f.get_path())
        except GLib.Error:
            pass

    def _delete_recording(self, path: Path):
        if self._config and self._config.get("confirm_delete_recording", True):
            dialog = Adw.MessageDialog(
                transient_for=self,
                heading="Delete Recording?",
                body=f'Delete recording "{path.name}"? This cannot be undone.',
            )
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("delete", "Delete")
            dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

            def _on_response(_d, response):
                if response == "delete":
                    self._do_delete_recording(path)

            dialog.connect("response", _on_response)
            dialog.present()
            return

        self._do_delete_recording(path)

    def _do_delete_recording(self, path: Path):
        try:
            path.unlink()
        except IOError:
            pass
        self._load_recordings()

    def _rename_recording(self, path: Path):
        """Prompt for a new name and rename the selected .cast file."""
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Rename Recording",
            body="Enter a new recording name:",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("rename", "Rename")
        dialog.set_response_appearance("rename", Adw.ResponseAppearance.SUGGESTED)

        entry = Gtk.Entry()
        entry.set_margin_start(12)
        entry.set_margin_end(12)
        entry.set_text(path.stem)
        dialog.set_extra_child(entry)

        def _on_response(_d, response):
            if response != "rename":
                return
            new_stem = entry.get_text().strip()
            if not new_stem:
                self._show_info("Name cannot be empty.")
                return
            new_path = path.with_name(f"{new_stem}{path.suffix}")
            if new_path == path:
                return
            if new_path.exists():
                self._show_info("A recording with this name already exists.")
                return
            try:
                path.rename(new_path)
                self._load_recordings()
            except OSError as exc:
                self._show_error(f"Rename failed: {exc}")

        dialog.connect("response", _on_response)
        dialog.present()

    def _export_gif(self, path: Path):
        """Open export panel for GIF output."""
        self._open_export_options(path, "gif")

    def _export_mp4(self, path: Path):
        """Open export panel for MP4 output."""
        self._open_export_options(path, "mp4")

    def _open_export_options(self, src: Path, fmt: str):
        """Show export options panel for GIF/MP4."""
        panel = Adw.Window(
            transient_for=self,
            modal=True,
            title=f"Export {fmt.upper()}",
            default_width=440,
            default_height=320,
        )

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(Adw.HeaderBar())

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_start(16)
        box.set_margin_end(16)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        quality = Gtk.ComboBoxText()
        for q in ("high", "medium", "low"):
            quality.append_text(q)
        quality.set_active(1)
        block_scroll(quality)

        fps = Gtk.SpinButton.new_with_range(1, 60, 1)
        fps.set_value(15)

        speed = Gtk.SpinButton.new_with_range(0.25, 4.0, 0.05)
        speed.set_value(1.0)

        theme = Gtk.ComboBoxText()
        for th in ("asciinema", "solarized-dark", "solarized-light", "dracula"):
            theme.append_text(th)
        theme.set_active(0)
        block_scroll(theme)

        for label, widget in (
            ("Quality", quality),
            ("FPS", fps),
            ("Speed", speed),
            ("Theme", theme),
        ):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            lbl = Gtk.Label(label=label)
            lbl.set_xalign(0)
            lbl.set_width_chars(10)
            row.append(lbl)
            widget.set_hexpand(True)
            row.append(widget)
            box.append(row)

        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_row.set_halign(Gtk.Align.END)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: panel.close())
        btn_row.append(cancel)

        export = Gtk.Button(label=f"Export {fmt.upper()}")
        export.add_css_class("suggested-action")

        def _on_export(*_):
            opts = {
                "quality": quality.get_active_text() or "medium",
                "fps": int(fps.get_value()),
                "speed": float(speed.get_value()),
                "theme": theme.get_active_text() or "asciinema",
            }
            panel.close()
            self._choose_export_destination(src, fmt, opts)

        export.connect("clicked", _on_export)
        btn_row.append(export)
        box.append(btn_row)

        root.append(box)
        panel.set_content(root)
        panel.present()

    def _choose_export_destination(self, src: Path, fmt: str, opts: dict):
        dialog = Gtk.FileDialog()
        dialog.set_title(f"Export {fmt.upper()}")
        dialog.set_initial_name(f"{src.stem}.{fmt}")
        dialog.save(
            self,
            None,
            lambda d, r: self._on_export_destination_chosen(d, r, src, fmt, opts),
        )

    def _on_export_destination_chosen(
        self, dialog, result, src: Path, fmt: str, opts: dict
    ):
        try:
            out_file = dialog.save_finish(result)
            if not out_file:
                return
            dst = Path(out_file.get_path())
            if fmt == "gif":
                self._ensure_tools_then(
                    ["agg"], lambda: self._run_gif_export(src, dst, opts)
                )
            else:
                self._ensure_tools_then(
                    ["agg", "ffmpeg"],
                    lambda: self._run_mp4_export(src, dst, opts),
                )
        except GLib.Error:
            pass

    def _ensure_tools_then(self, tools: list[str], on_ready):
        """Ensure required tools are available, offering brew install when missing."""
        missing = [t for t in tools if not shutil.which(t)]
        if not missing:
            on_ready()
            return

        has_brew = shutil.which("brew") is not None
        msg = ", ".join(missing)
        body = f"Export requires missing tool(s): {msg}.\n\n"
        body += (
            "Would you like to install them via Homebrew now?"
            if has_brew
            else "Please install them and retry export."
        )

        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Export Dependency Missing",
            body=body,
        )
        dialog.add_response("cancel", "Cancel")
        if has_brew:
            dialog.add_response("install", "Install via Homebrew")
            dialog.set_response_appearance("install", Adw.ResponseAppearance.SUGGESTED)

        def _on_response(_d, response):
            if response != "install":
                return
            self._install_tools(missing, on_ready)

        dialog.connect("response", _on_response)
        dialog.present()

    def _install_tools(self, tools: list[str], on_ready):
        """Install tools via brew in background thread."""

        def _worker():
            result = subprocess.run(
                ["brew", "install", *tools], capture_output=True, text=True
            )

            def _done():
                still_missing = [t for t in tools if not shutil.which(t)]
                if result.returncode == 0 and not still_missing:
                    self._show_info(
                        "Dependencies installed successfully. Starting export..."
                    )
                    on_ready()
                else:
                    err = (result.stderr or result.stdout or "Unknown error").strip()
                    self._show_error(f"Homebrew install failed:\n{err}")
                return False

            GLib.idle_add(_done)

        self._show_info(
            "Installing dependencies via Homebrew. This may take a moment..."
        )
        threading.Thread(target=_worker, daemon=True).start()

    def _build_agg_commands(self, src: Path, dst: Path, opts: dict) -> list[list[str]]:
        """Build agg command candidates to support different agg CLI versions."""
        fps = str(int(opts.get("fps", 15)))

        # Most compatible invocation for current agg releases
        commands = [
            ["agg", "--fps-cap", fps, str(src), str(dst)],
            ["agg", "--fps", fps, str(src), str(dst)],
            ["agg", str(src), str(dst)],
        ]

        # Keep backward-compatible output-flag forms as additional fallbacks.
        commands.extend(
            [
                ["agg", "--fps-cap", fps, "--output", str(dst), str(src)],
                ["agg", "--output", str(dst), str(src)],
            ]
        )
        return commands

    def _run_gif_export(self, src: Path, dst: Path, opts: dict):
        """Run agg conversion in a worker thread to keep UI responsive."""

        def _worker():
            commands = self._build_agg_commands(src, dst, opts)
            last_error = ""
            for cmd in commands:
                result = subprocess.run(cmd, capture_output=True, text=True)
                if result.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
                    GLib.idle_add(
                        lambda: (self._show_info(f"GIF exported to: {dst}"), False)[1]
                    )
                    return
                last_error = (result.stderr or result.stdout or "Unknown error").strip()

            GLib.idle_add(
                lambda: (
                    self._show_error(
                        "GIF export failed.\n"
                        "Please ensure your .cast file is valid and agg supports this format.\n\n"
                        f"Details: {last_error}"
                    ),
                    False,
                )[1]
            )

        self._show_info("Exporting GIF...")
        threading.Thread(target=_worker, daemon=True).start()

    def _run_mp4_export(self, src: Path, dst: Path, opts: dict):
        """Export MP4 by rendering temp GIF via agg then converting with ffmpeg."""

        quality_crf = {"high": "18", "medium": "23", "low": "28"}

        def _worker():
            tmp_gif = Path(tempfile.gettempdir()) / f"{src.stem}_{int(time.time())}.gif"
            try:
                last_err = ""
                for agg_cmd in self._build_agg_commands(src, tmp_gif, opts):
                    agg_result = subprocess.run(agg_cmd, capture_output=True, text=True)
                    if (
                        agg_result.returncode == 0
                        and tmp_gif.exists()
                        and tmp_gif.stat().st_size > 0
                    ):
                        break
                    last_err = (
                        agg_result.stderr or agg_result.stdout or "Unknown error"
                    ).strip()
                else:
                    GLib.idle_add(
                        lambda: (
                            self._show_error(
                                f"MP4 export failed at render stage:\n{last_err}"
                            ),
                            False,
                        )[1]
                    )
                    return

                speed = float(opts.get("speed", 1.0))
                fps = int(opts.get("fps", 15))
                ffmpeg_cmd = [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(tmp_gif),
                    "-vf",
                    f"fps={fps},setpts=PTS/{speed},scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-pix_fmt",
                    "yuv420p",
                    "-movflags",
                    "+faststart",
                    "-crf",
                    quality_crf.get(opts.get("quality", "medium"), "23"),
                    str(dst),
                ]
                ff_result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True)
                if (
                    ff_result.returncode == 0
                    and dst.exists()
                    and dst.stat().st_size > 0
                ):
                    GLib.idle_add(
                        lambda: (self._show_info(f"MP4 exported to: {dst}"), False)[1]
                    )
                    return

                err = (ff_result.stderr or ff_result.stdout or "Unknown error").strip()
                GLib.idle_add(
                    lambda: (self._show_error(f"MP4 export failed:\n{err}"), False)[1]
                )
            finally:
                try:
                    if tmp_gif.exists():
                        tmp_gif.unlink()
                except OSError:
                    pass

        self._show_info("Exporting MP4...")
        threading.Thread(target=_worker, daemon=True).start()

    def _show_info(self, message: str):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Session Recordings",
            body=message,
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _show_error(self, message: str):
        dialog = Adw.MessageDialog(
            transient_for=self,
            heading="Error",
            body=message,
        )
        dialog.add_response("ok", "OK")
        dialog.present()

    def _on_open_file(self, _btn):
        dialog = Gtk.FileDialog()
        filter_cast = Gtk.FileFilter()
        filter_cast.set_name("Asciicast Files (*.cast)")
        filter_cast.add_pattern("*.cast")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(filter_cast)
        dialog.set_filters(filters)
        # Open in the configured recordings directory
        rec_dir = get_recordings_dir(self._config)
        dialog.set_initial_folder(Gio.File.new_for_path(str(rec_dir)))
        dialog.open(self, None, self._on_file_chosen)

    def _on_file_chosen(self, dialog, result):
        try:
            f = dialog.open_finish(result)
            if f:
                self._play_recording(f.get_path())
        except GLib.Error:
            pass
