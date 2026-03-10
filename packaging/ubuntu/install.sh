#!/bin/bash
# install.sh — Quick install SSH Client Manager directly from source on Ubuntu/Debian
#
# This script does NOT build a .deb. It installs the app files directly
# into /usr/local/ using the system Python and system GTK packages.
#
# Run as root or with sudo:
#   sudo ./packaging/ubuntu/install.sh
#
# To uninstall:
#   sudo ./packaging/ubuntu/install.sh --uninstall

set -e

APP_NAME="ssh-client-manager"
INSTALL_DIR="/usr/lib/$APP_NAME"
BIN_LINK="/usr/local/bin/$APP_NAME"
DESKTOP_DIR="/usr/share/applications"
METAINFO_DIR="/usr/share/metainfo"
ICONS_DIR="/usr/share/icons/hicolor/scalable/apps"

# ── Uninstall mode ────────────────────────────────────────────────────────────
if [ "${1:-}" = "--uninstall" ]; then
    echo "🗑️  Uninstalling SSH Client Manager..."
    rm -f  "$BIN_LINK"
    rm -rf "$INSTALL_DIR"
    rm -f  "$DESKTOP_DIR/io.github.ssh-client-manager.desktop"
    rm -f  "$METAINFO_DIR/io.github.ssh-client-manager.metainfo.xml"
    rm -f  "$ICONS_DIR/io.github.ssh-client-manager.svg"
    echo "✅ Uninstalled."
    exit 0
fi

# ── Must be root ──────────────────────────────────────────────────────────────
if [ "$(id -u)" -ne 0 ]; then
    echo "❌ This script must be run as root. Use: sudo $0"
    exit 1
fi

# ── Verify project root ───────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

if [ ! -f "$PROJECT_ROOT/run.py" ] || [ ! -d "$PROJECT_ROOT/src" ]; then
    echo "❌ Cannot find project root. Expected run.py and src/ at: $PROJECT_ROOT"
    exit 1
fi

echo "📦 Installing SSH Client Manager from source..."
echo "   Project root  : $PROJECT_ROOT"
echo "   Install prefix: $INSTALL_DIR"

# ── System dependency check ───────────────────────────────────────────────────
echo ""
echo "🔍 Checking system dependencies..."

MISSING=()

check_pkg() {
    dpkg -l "$1" &>/dev/null || MISSING+=("$1")
}

check_pkg python3
check_pkg python3-gi
check_pkg python3-gi-cairo
check_pkg gir1.2-gtk-4.0
check_pkg gir1.2-adw-1
check_pkg gir1.2-vte-3.91
check_pkg libgtk-4-1
check_pkg libadwaita-1-0
check_pkg libvte-2.91-gtk4-0
check_pkg python3-cryptography
check_pkg python3-paramiko
check_pkg python3-psutil

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "❌ Missing system packages: ${MISSING[*]}"
    echo ""
    echo "Install them with:"
    echo "  sudo apt install ${MISSING[*]}"
    exit 1
fi

echo "✅ All system dependencies satisfied."

# ── Install app files ─────────────────────────────────────────────────────────
echo ""
echo "📁 Installing application files..."

install -d "$INSTALL_DIR"
cp -a "$PROJECT_ROOT/run.py"  "$INSTALL_DIR/"
cp -a "$PROJECT_ROOT/src/"    "$INSTALL_DIR/src/"
[ -d "$PROJECT_ROOT/resources" ] && cp -a "$PROJECT_ROOT/resources/" "$INSTALL_DIR/resources/"

chmod -R a+rX "$INSTALL_DIR"

# ── Launcher script ───────────────────────────────────────────────────────────
cat > "$BIN_LINK" << LAUNCHER
#!/bin/bash
exec python3 $INSTALL_DIR/run.py "\$@"
LAUNCHER
chmod 755 "$BIN_LINK"
echo "✅ Launcher installed at $BIN_LINK"

# ── Desktop integration ───────────────────────────────────────────────────────
install -d "$DESKTOP_DIR" "$METAINFO_DIR" "$ICONS_DIR"

install -m 644 \
    "$SCRIPT_DIR/io.github.ssh-client-manager.desktop" \
    "$DESKTOP_DIR/"

install -m 644 \
    "$SCRIPT_DIR/io.github.ssh-client-manager.metainfo.xml" \
    "$METAINFO_DIR/"

if [ -f "$PROJECT_ROOT/resources/ssh-client-manager.svg" ]; then
    install -m 644 \
        "$PROJECT_ROOT/resources/ssh-client-manager.svg" \
        "$ICONS_DIR/io.github.ssh-client-manager.svg"
fi

# Refresh icon and MIME caches (best-effort)
command -v gtk-update-icon-cache &>/dev/null && \
    gtk-update-icon-cache -qtf /usr/share/icons/hicolor || true
command -v update-desktop-database &>/dev/null && \
    update-desktop-database -q "$DESKTOP_DIR" || true

echo ""
echo "🎉 SSH Client Manager installed successfully!"
echo ""
echo "▶  Launch from terminal : ssh-client-manager"
echo "▶  Launch from app menu : look for 'SSH Client Manager'"
echo ""
echo "To uninstall:"
echo "  sudo $0 --uninstall"
