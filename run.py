#!/usr/bin/env python3
"""
SSH Client Manager - Entry point.

A modern GTK4/libadwaita SSH connection manager with:
- Split terminals (horizontal/vertical)
- Tabbed interface with drag-and-drop between panes
- Encrypted credential storage (AES)
- SSH_ASKPASS based authentication (no expect)
- Cluster mode for sending commands to all terminals
- Hierarchical connection grouping

Usage:
    python3 run.py [--verbose]
"""

import sys
import os
import argparse

# Add project root to path
project_dir = os.path.dirname(os.path.abspath(__file__))
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)


def main():
    parser = argparse.ArgumentParser(description="SSH Client Manager")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Enable verbose debug output")
    args = parser.parse_args()

    if args.verbose:
        os.environ["G_MESSAGES_DEBUG"] = "all"
        import logging
        logging.basicConfig(level=logging.DEBUG)
        print("Verbose mode enabled")

    from src import __version__

    # Print startup info
    print(f"SSH Client Manager v{__version__}")
    print(f"Python {sys.version}")

    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        gi.require_version('Vte', '3.91')
        from gi.repository import Gtk, Adw, Vte
        print(f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()}")
        print(f"Adwaita available")
        print(f"VTE available")
    except (ValueError, ImportError) as e:
        print(f"\nError: Required GTK4 libraries not found: {e}")
        print("\nPlease install the following packages:")
        print("  Debian/Ubuntu:")
        print("    sudo apt install python3-gi python3-gi-cairo \\")
        print("      libgtk-4-1 gir1.2-gtk-4.0 \\")
        print("      libadwaita-1-0 gir1.2-adw-1 \\")
        print("      libvte-2.91-gtk4-0 gir1.2-vte-3.91")
        print("  Fedora:")
        print("    sudo dnf install python3-gobject gtk4 libadwaita vte291-gtk4")
        sys.exit(1)

    try:
        from cryptography.fernet import Fernet
        print("cryptography available")
    except ImportError:
        print("\nWarning: 'cryptography' package not installed.")
        print("  pip install cryptography")
        print("Credential storage will not work without it.")

    print("---")

    from src.app import main as app_main
    sys.exit(app_main())


if __name__ == "__main__":
    main()
