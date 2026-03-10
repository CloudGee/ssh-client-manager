#!/usr/bin/env python3
"""Minimal test to debug the Entry-can-only-type-1-char issue."""
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Gtk, Adw, GLib

class TestApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="test.entry.debug")
        self.connect("activate", self._on_activate)

    def _on_activate(self, app):
        # Main window (Adw.ApplicationWindow, like our real app)
        self.main_win = Adw.ApplicationWindow(application=app)
        self.main_win.set_default_size(600, 400)
        btn = Gtk.Button(label="Open Cluster Window")
        btn.connect("clicked", self._open_cluster)
        self.main_win.set_content(btn)

        # Add an EventControllerKey on the main window (like our real app)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._main_key_pressed)
        self.main_win.add_controller(key_ctrl)

        self.main_win.present()

    def _main_key_pressed(self, controller, keyval, keycode, state):
        """Simulates the main window's shortcut handler."""
        return False

    def _open_cluster(self, btn):
        # Test 1: Gtk.Window (what we currently use)
        win = Gtk.Window(title="Test Gtk.Window Entry")
        win.set_transient_for(self.main_win)
        win.set_modal(False)
        win.set_default_size(400, 200)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box.set_margin_start(10); box.set_margin_end(10)
        box.set_margin_top(10); box.set_margin_bottom(10)

        lbl1 = Gtk.Label(label="Gtk.Window + Entry:")
        box.append(lbl1)
        entry1 = Gtk.Entry()
        entry1.set_placeholder_text("Type here (Gtk.Window)...")
        entry1.set_hexpand(True)
        box.append(entry1)

        win.set_child(box)
        GLib.idle_add(entry1.grab_focus)
        win.present()

        # Test 2: Adw.Window
        win2 = Adw.Window(title="Test Adw.Window Entry")
        win2.set_transient_for(self.main_win)
        win2.set_modal(False)
        win2.set_default_size(400, 200)
        box2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        box2.set_margin_start(10); box2.set_margin_end(10)
        box2.set_margin_top(10); box2.set_margin_bottom(10)

        lbl2 = Gtk.Label(label="Adw.Window + Entry:")
        box2.append(lbl2)
        entry2 = Gtk.Entry()
        entry2.set_placeholder_text("Type here (Adw.Window)...")
        entry2.set_hexpand(True)
        box2.append(entry2)

        # Adw.Window uses set_content instead of set_child
        win2.set_content(box2)
        GLib.idle_add(entry2.grab_focus)
        win2.present()

app = TestApp()
app.run([])
