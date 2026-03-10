#!/bin/bash

# ╔══════════════════════════════════════════════════════════════════╗
# ║  SSH Client Manager — macOS Build & Packaging Script            ║
# ║                                                                  ║
# ║  Checks all dependencies, installs missing ones via Homebrew,   ║
# ║  builds a self-contained .app bundle, and optionally creates    ║
# ║  a .dmg installer.                                               ║
# ╚══════════════════════════════════════════════════════════════════╝

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
info()    { echo -e "${BLUE}ℹ️  ${NC}$1"; }
success() { echo -e "${GREEN}✅ ${NC}$1"; }
warn()    { echo -e "${YELLOW}⚠️  ${NC}$1"; }
error()   { echo -e "${RED}❌ ${NC}$1"; exit 1; }
ask()     { echo -en "${YELLOW}❓ ${NC}$1 [Y/n] "; read -r ans; [[ "$ans" =~ ^[Nn] ]] && return 1 || return 0; }

echo ""
echo -e "${BOLD}🚀 SSH Client Manager — macOS Build Script${NC}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ──────────────────────────────────────────────
# Step 0: Check we're in the right directory
# ──────────────────────────────────────────────
if [ ! -f "ssh-client-manager.spec" ]; then
    error "ssh-client-manager.spec not found.\n   Please run this script from the project root directory."
fi

rm -rf dist/*

# ──────────────────────────────────────────────
# Step 1: Check for Homebrew
# ──────────────────────────────────────────────
echo -e "${BOLD}📋 Step 1: Checking Homebrew...${NC}"

if ! command -v brew &> /dev/null; then
    warn "Homebrew is not installed."
    echo "   Homebrew is required for installing GTK4, libadwaita, VTE and other dependencies."
    echo ""
    if ask "Install Homebrew now?"; then
        echo "Installing Homebrew..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        # Add brew to PATH for this session
        if [ -f "/opt/homebrew/bin/brew" ]; then
            eval "$(/opt/homebrew/bin/brew shellenv)"
        elif [ -f "/usr/local/bin/brew" ]; then
            eval "$(/usr/local/bin/brew shellenv)"
        fi
        success "Homebrew installed"
    else
        error "Homebrew is required. Please install it from https://brew.sh"
    fi
else
    success "Homebrew found: $(brew --prefix)"
fi

# Detect architecture and Homebrew prefix
ARCH=$(uname -m)
HOMEBREW_PREFIX=$(brew --prefix)
if [ "$ARCH" = "arm64" ]; then
    info "🍎 Apple Silicon (ARM64) — Homebrew at: $HOMEBREW_PREFIX"
else
    info "💻 Intel Mac (x86_64) — Homebrew at: $HOMEBREW_PREFIX"
fi
echo ""

# ──────────────────────────────────────────────
# Step 2: Check Homebrew dependencies
# ──────────────────────────────────────────────
echo -e "${BOLD}📋 Step 2: Checking system dependencies...${NC}"

# Required brew packages
REQUIRED_BREW_PACKAGES=(
    "python@3.13"
    "gtk4"
    "libadwaita"
    "vte3"
    "pygobject3"
    "pango"
    "glib"
    "cairo"
    "gdk-pixbuf"
    "graphene"
    "harfbuzz"
)

# Optional packages
OPTIONAL_BREW_PACKAGES=(
    "create-dmg"
)

MISSING_REQUIRED=()
MISSING_OPTIONAL=()

for pkg in "${REQUIRED_BREW_PACKAGES[@]}"; do
    if brew list --formula "$pkg" &>/dev/null; then
        success "$pkg"
    else
        warn "$pkg — NOT INSTALLED"
        MISSING_REQUIRED+=("$pkg")
    fi
done

echo ""
info "Checking optional packages..."
for pkg in "${OPTIONAL_BREW_PACKAGES[@]}"; do
    if brew list --formula "$pkg" &>/dev/null || command -v "$pkg" &>/dev/null; then
        success "$pkg (optional)"
    else
        warn "$pkg — NOT INSTALLED (optional, for DMG creation)"
        MISSING_OPTIONAL+=("$pkg")
    fi
done

echo ""

# ──────────────────────────────────────────────
# Step 3: Install missing dependencies
# ──────────────────────────────────────────────
if [ ${#MISSING_REQUIRED[@]} -gt 0 ]; then
    echo -e "${BOLD}📋 Step 3: Installing missing required packages...${NC}"
    echo ""
    echo "   The following packages need to be installed:"
    for pkg in "${MISSING_REQUIRED[@]}"; do
        echo "     • $pkg"
    done
    echo ""

    if ask "Install these packages via Homebrew?"; then
        for pkg in "${MISSING_REQUIRED[@]}"; do
            info "Installing $pkg..."
            brew install "$pkg" || warn "Failed to install $pkg — continuing anyway"
        done
        success "Required packages installed"
    else
        error "Required dependencies are missing. Cannot continue."
    fi
else
    echo -e "${BOLD}📋 Step 3: All required packages present${NC}"
    success "All required packages are installed"
fi

echo ""

if [ ${#MISSING_OPTIONAL[@]} -gt 0 ]; then
    echo "   Optional packages not installed: ${MISSING_OPTIONAL[*]}"
    if ask "Install optional packages too?"; then
        for pkg in "${MISSING_OPTIONAL[@]}"; do
            info "Installing $pkg..."
            brew install "$pkg" || warn "Failed to install $pkg"
        done
    fi
    echo ""
fi

# ──────────────────────────────────────────────
# Step 4: Python virtual environment
# ──────────────────────────────────────────────
echo -e "${BOLD}📋 Step 4: Python virtual environment...${NC}"

PYTHON_PATH="$HOMEBREW_PREFIX/opt/python@3.13/bin/python3.13"
if [ ! -f "$PYTHON_PATH" ]; then
    # Try fallback
    PYTHON_PATH=$(command -v python3.13 || command -v python3 || echo "")
    if [ -z "$PYTHON_PATH" ]; then
        error "Python 3.13 not found. Please install it:\n   brew install python@3.13"
    fi
fi
info "Using Python: $PYTHON_PATH ($($PYTHON_PATH --version 2>&1))"

VENV_DIR=".venv-homebrew"
if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    "$PYTHON_PATH" -m venv --system-site-packages "$VENV_DIR"
    success "Virtual environment created at $VENV_DIR"

    source "$VENV_DIR/bin/activate"

    info "Upgrading pip..."
    pip install --upgrade pip > /dev/null 2>&1

    info "Installing Python dependencies..."
    pip install PyInstaller
    pip install -r requirements.txt
    success "Python dependencies installed"
else
    info "Using existing virtual environment at $VENV_DIR"
    source "$VENV_DIR/bin/activate"
fi

echo ""

# ──────────────────────────────────────────────
# Step 5: Pre-build validation
# ──────────────────────────────────────────────
echo -e "${BOLD}📋 Step 5: Pre-build validation...${NC}"

# Check that key libraries are loadable
python -c "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk" 2>/dev/null \
    && success "GTK4 Python bindings OK" \
    || error "Cannot import GTK4 — check pygobject3 installation"

python -c "import gi; gi.require_version('Adw', '1'); from gi.repository import Adw" 2>/dev/null \
    && success "libadwaita Python bindings OK" \
    || error "Cannot import libadwaita — check libadwaita installation"

python -c "import gi; gi.require_version('Vte', '3.91'); from gi.repository import Vte" 2>/dev/null \
    && success "VTE Python bindings OK" \
    || error "Cannot import VTE — check vte3 installation"

python -c "import paramiko" 2>/dev/null \
    && success "Paramiko OK" \
    || { warn "Paramiko not found, installing..."; pip install paramiko; }

python -c "import PyInstaller" 2>/dev/null \
    && success "PyInstaller OK" \
    || { warn "PyInstaller not found, installing..."; pip install PyInstaller; }

echo ""

# ──────────────────────────────────────────────
# Step 6: Build with PyInstaller
# ──────────────────────────────────────────────
echo -e "${BOLD}📋 Step 6: Building macOS app bundle...${NC}"
info "Running PyInstaller (this may take a minute)..."

python -m PyInstaller --clean --noconfirm ssh-client-manager.spec

echo ""

if [ ! -d "dist/SSHClientManager.app" ]; then
    error "Build failed! dist/SSHClientManager.app not found."
fi

success "App bundle created: dist/SSHClientManager.app"

# Quick sanity check — app structure
BUNDLE="dist/SSHClientManager.app"
for check in "Contents/MacOS" "Contents/Resources" "Contents/Frameworks"; do
    if [ -d "$BUNDLE/$check" ]; then
        success "  $check ✓"
    else
        warn "  $check missing (may be OK depending on PyInstaller mode)"
    fi
done

echo ""

# ──────────────────────────────────────────────
# Step 7: Create DMG (optional)
# ──────────────────────────────────────────────
echo -e "${BOLD}📋 Step 7: Creating DMG installer...${NC}"

if ! command -v create-dmg &> /dev/null; then
    warn "create-dmg not installed — skipping DMG creation."
    echo "   Install it with: brew install create-dmg"
    echo ""
else
    # Determine version
    VERSION=$(python -c "from src import __version__; print(__version__)" 2>/dev/null || echo "$(date +%Y%m%d)")
    DMG_NAME="SSHClientManager-macOS-${VERSION}-${ARCH}.dmg"
    DMG_PATH="dist/${DMG_NAME}"

    info "Version: $VERSION | Arch: $ARCH"

    # Remove stale DMG
    [ -f "$DMG_PATH" ] && rm "$DMG_PATH"

    ICON_ARG=""
    if [ -f "packaging/macos/sshclientmanager.icns" ]; then
        ICON_ARG="--volicon packaging/macos/sshclientmanager.icns"
    fi

    if create-dmg \
        --volname "SSH Client Manager" \
        $ICON_ARG \
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
            success "DMG created: $DMG_PATH"
        fi
    else
        warn "DMG creation failed (non-fatal). App bundle is still usable."
    fi
    echo ""
fi

# ──────────────────────────────────────────────
# Summary
# ──────────────────────────────────────────────
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo -e "${BOLD}${GREEN}🎉 Build Complete!${NC}"
echo ""
echo "  📍 App bundle: $(pwd)/dist/SSHClientManager.app"
if [ -f "$DMG_PATH" ]; then
    echo "  📍 DMG:        $(pwd)/$DMG_PATH"
fi
echo ""
echo "  🚀 To launch:  open dist/SSHClientManager.app"
echo "  🗑️  To clean:   rm -rf dist/ build/"
echo ""
