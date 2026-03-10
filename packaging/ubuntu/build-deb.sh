#!/bin/bash
# build-deb.sh — Build a .deb package for SSH Client Manager
#
# Run from the PROJECT ROOT directory:
#   chmod +x packaging/ubuntu/build-deb.sh
#   ./packaging/ubuntu/build-deb.sh
#
# Produces: ../ssh-client-manager_1.0.0-1_all.deb

set -e

echo "📦 Building SSH Client Manager .deb package..."

# ── Verify we are in the project root ────────────────────────────────────────
if [ ! -f "run.py" ] || [ ! -d "src" ]; then
    echo "❌ Error: run this script from the project root directory."
    echo "   cd /path/to/ssh-client-manager && ./packaging/ubuntu/build-deb.sh"
    exit 1
fi

# ── Check required build tools ───────────────────────────────────────────────
for tool in dpkg-buildpackage debhelper dh-python python3; do
    if ! command -v "$tool" &>/dev/null; then
        echo "❌ Missing build tool: $tool"
        echo "   Install with:"
        echo "     sudo apt install dpkg-dev debhelper dh-python python3-all python3-setuptools"
        exit 1
    fi
done

# ── Read version from changelog ───────────────────────────────────────────────
VERSION=$(head -1 packaging/ubuntu/debian/changelog | grep -oP '\(\K[^)]+' | cut -d- -f1)
echo "📐 Version: $VERSION"

# ── Stage a temporary build directory ────────────────────────────────────────
BUILD_DIR=$(mktemp -d)
STAGE_DIR="$BUILD_DIR/ssh-client-manager-$VERSION"
echo "🏗️  Staging build in: $STAGE_DIR"
trap 'rm -rf "$BUILD_DIR"' EXIT

mkdir -p "$STAGE_DIR"

# Copy project files
cp -a run.py src requirements.txt README.md "$STAGE_DIR/"
[ -d resources ] && cp -a resources "$STAGE_DIR/"

# Copy debian packaging files into the staged source
cp -a packaging/ubuntu/debian "$STAGE_DIR/"
# Copy desktop entry and metainfo so debian/rules can find them
cp packaging/ubuntu/io.github.ssh-client-manager.desktop "$STAGE_DIR/packaging/ubuntu/" 2>/dev/null || \
    mkdir -p "$STAGE_DIR/packaging/ubuntu" && \
    cp packaging/ubuntu/io.github.ssh-client-manager.desktop \
       packaging/ubuntu/io.github.ssh-client-manager.metainfo.xml \
       "$STAGE_DIR/packaging/ubuntu/"

# ── Install wrapper script that points at /usr/lib location ──────────────────
mkdir -p "$STAGE_DIR/bin"
cat > "$STAGE_DIR/bin/ssh-client-manager" << 'LAUNCHER'
#!/bin/bash
exec python3 /usr/lib/ssh-client-manager/run.py "$@"
LAUNCHER
chmod +x "$STAGE_DIR/bin/ssh-client-manager"

# ── Build the .deb ────────────────────────────────────────────────────────────
echo "🔨 Running dpkg-buildpackage..."
cd "$STAGE_DIR"
dpkg-buildpackage -us -uc -b

# ── Collect output ────────────────────────────────────────────────────────────
DEB_FILE=$(find "$BUILD_DIR" -maxdepth 1 -name "*.deb" | head -1)
if [ -z "$DEB_FILE" ]; then
    echo "❌ Build failed — no .deb produced."
    exit 1
fi

DEST="$(pwd | sed "s|/ssh-client-manager-$VERSION||")"
cp "$DEB_FILE" "$DEST/"
FINAL_DEB="$DEST/$(basename "$DEB_FILE")"

echo ""
echo "✅ Build complete!"
echo "📍 Package : $FINAL_DEB"
echo ""
echo "🚀 Install with:"
echo "   sudo apt install '$FINAL_DEB'"
echo "   # or"
echo "   sudo dpkg -i '$FINAL_DEB' && sudo apt-get install -f"
