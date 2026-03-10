#!/bin/bash

# Build script for SSH Client Manager - PyInstaller macOS bundle
# Produces a self-contained SSHClientManager.app and a .dmg installer

set -e  # Exit on any error

echo "🚀 Building SSH Client Manager PyInstaller bundle..."

# Check if we're in the right directory
if [ ! -f "ssh-client-manager.spec" ]; then
    echo "❌ Error: ssh-client-manager.spec not found. Please run this script from the project root directory."
    exit 1
fi

# ──────────────────────────────────────────────
# Detect architecture and Homebrew prefix
# ──────────────────────────────────────────────
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    HOMEBREW_PREFIX="/opt/homebrew"
    echo "🍎 Detected Apple Silicon Mac (ARM64)"
else
    HOMEBREW_PREFIX="/usr/local"
    echo "💻 Detected Intel Mac (x86_64)"
fi

# ──────────────────────────────────────────────
# Ensure Homebrew Python 3.13 exists
# ──────────────────────────────────────────────
PYTHON_PATH="$HOMEBREW_PREFIX/opt/python@3.13/bin/python3.13"
if [ ! -f "$PYTHON_PATH" ]; then
    echo "❌ Homebrew Python 3.13 not found at $PYTHON_PATH"
    echo "Please install it with:"
    echo "   brew install python@3.13"
    exit 1
fi
echo "🐍 Using Python from: $PYTHON_PATH"

# ──────────────────────────────────────────────
# Create / activate isolated venv
# ──────────────────────────────────────────────
if [ ! -d ".venv-homebrew" ]; then
    echo "📦 Creating Homebrew virtual environment..."
    "$PYTHON_PATH" -m venv .venv-homebrew
    echo "✅ Virtual environment created"

    echo "📦 Installing build dependencies..."
    source .venv-homebrew/bin/activate
    pip install --upgrade pip
    pip install PyInstaller
    pip install -r requirements.txt
    echo "✅ Dependencies installed"
else
    echo "📦 Activating existing Homebrew virtual environment..."
    source .venv-homebrew/bin/activate
fi

# ──────────────────────────────────────────────
# Run PyInstaller
# ──────────────────────────────────────────────
echo "🔨 Running PyInstaller..."
python -m PyInstaller --clean --noconfirm ssh-client-manager.spec

# ──────────────────────────────────────────────
# Post-build checks
# ──────────────────────────────────────────────
if [ ! -d "dist/SSHClientManager.app" ]; then
    echo "❌ Build failed! Bundle not found at dist/SSHClientManager.app"
    exit 1
fi

echo "✅ Build successful! Bundle created at: dist/SSHClientManager.app"

# ──────────────────────────────────────────────
# Create DMG (optional — requires create-dmg)
# ──────────────────────────────────────────────
echo "📦 Attempting to create DMG..."

if ! command -v create-dmg &> /dev/null; then
    echo "⚠️  create-dmg is not installed. Skipping DMG creation."
    echo "   Install it with: brew install create-dmg"
    echo ""
    echo "🎉 SSHClientManager.app is ready!"
    echo "📍 Location: $(pwd)/dist/SSHClientManager.app"
    echo "🚀 To launch:  open dist/SSHClientManager.app"
    exit 0
fi

# Read version from src/app.py or fall back to date
VERSION=$(grep -o 'version = "[^"]*"' pyproject.toml 2>/dev/null | cut -d'"' -f2)
if [ -z "$VERSION" ]; then
    VERSION=$(date +%Y%m%d)
fi

ARCH_NAME="$ARCH"
DMG_NAME="SSHClientManager-macOS-${VERSION}-${ARCH_NAME}.dmg"
DMG_PATH="dist/${DMG_NAME}"

echo "📐 Version   : $VERSION"
echo "📐 Arch      : $ARCH_NAME"
echo "📐 DMG name  : $DMG_NAME"

# Remove stale DMG
[ -f "$DMG_PATH" ] && rm "$DMG_PATH"

if create-dmg \
    --volname "SSH Client Manager" \
    --volicon "packaging/macos/sshclientmanager.icns" \
    --window-pos 200 120 \
    --window-size 800 400 \
    --icon-size 100 \
    --icon "SSHClientManager.app" 200 190 \
    --hide-extension "SSHClientManager.app" \
    --app-drop-link 600 185 \
    --skip-jenkins \
    "$DMG_PATH" \
    "dist/SSHClientManager.app" 2>/dev/null; then

    if [ -f "$DMG_PATH" ]; then
        echo ""
        echo "🎉 All done!"
        echo "📍 App bundle : $(pwd)/dist/SSHClientManager.app"
        echo "📍 DMG        : $(pwd)/$DMG_PATH"
        echo "🚀 Quick test : open dist/SSHClientManager.app"
    else
        echo "⚠️  create-dmg exited 0 but DMG not found – bundle still usable."
        echo "📍 App bundle : $(pwd)/dist/SSHClientManager.app"
    fi
else
    echo "⚠️  DMG creation failed (non-fatal). Bundle is still usable."
    echo "📍 App bundle : $(pwd)/dist/SSHClientManager.app"
    echo "🚀 Quick test : open dist/SSHClientManager.app"
fi
