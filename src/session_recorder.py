"""
Session recording and playback.

Records terminal I/O in asciicast v2 format for later playback
using asciinema or the built-in player.
"""

import json
import os
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, GObject, Gio


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

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        header = Adw.HeaderBar()
        btn_open = Gtk.Button(icon_name="document-open-symbolic")
        btn_open.set_tooltip_text("Open Recording File")
        btn_open.connect("clicked", self._on_open_file)
        header.pack_start(btn_open)
        main_box.append(header)

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
        while True:
            row = self._list_box.get_row_at_index(0)
            if row is None:
                break
            self._list_box.remove(row)

        rec_dir = get_recordings_dir(self._config)
        files = sorted(
            rec_dir.glob("*.cast"), key=lambda f: f.stat().st_mtime, reverse=True
        )

        for f in files:
            row = Adw.ActionRow()
            row.set_title(f.stem)

            stat = f.stat()
            size_kb = stat.st_size / 1024
            mod_time = time.strftime("%Y-%m-%d %H:%M", time.localtime(stat.st_mtime))
            row.set_subtitle(f"{mod_time}  •  {size_kb:.1f} KB")

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
        try:
            path.unlink()
        except IOError:
            pass
        self._load_recordings()

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
