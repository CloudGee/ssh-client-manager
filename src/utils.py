"""Shared UI utilities for SSH Client Manager."""

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk


def block_scroll(widget: Gtk.Widget) -> None:
    """Prevent mouse scroll-wheel from accidentally changing widget value.

    Attaches an EventControllerScroll at CAPTURE phase that consumes
    all scroll events before they reach the widget's built-in handlers.
    Useful for SpinButton, ComboBoxText, etc.
    """
    sc = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
    sc.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
    sc.connect("scroll", lambda *_args: True)
    widget.add_controller(sc)
