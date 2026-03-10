# macOS Packaging — SSH Client Manager

This document explains how to build a self-contained **SSHClientManager.app** bundle
and distribute it as a drag-and-drop **.dmg** installer for macOS 12 (Monterey) and later.

The approach is based on **PyInstaller** with explicit GTK4 dylib bundling, adapted
from the [sshpilot](https://github.com/mfat/sshpilot) project which uses the same
GTK4 / libadwaita / VTE stack.

---

## Files

| File | Purpose |
|------|---------|
| `pyinstaller.sh` | Top-level build script — run this to produce the `.app` and `.dmg` |
| `ssh-client-manager.spec` | PyInstaller spec: declares all dylibs, typelibs, and shared data to bundle |
| `hook-gtk_runtime.py` | PyInstaller runtime hook: sets `GI_TYPELIB_PATH`, `GSETTINGS_SCHEMA_DIR`, `PATH`, etc. at launch |
| `packaging/macos/Info.plist` | Reference `Info.plist` (PyInstaller generates its own; this is for documentation / manual builds) |
| `packaging/macos/sshclientmanager.icns` | App icon — **add your own `.icns` here** (see below) |

---

## Prerequisites

All build-time dependencies must be installed via **Homebrew** on the developer's Mac.
No special setup is required on the *end-user* system — the resulting `.app` is self-contained.

### 1 — Install Homebrew (if not already installed)

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

### 2 — Install system packages

```bash
# Python runtime
brew install python@3.13

# GTK4 stack
brew install gtk4
brew install libadwaita

# VTE terminal widget (GTK4 variant)
brew install vte3

# GObject Python bindings
brew install pygobject3
brew install py3cairo

# DMG creation tool (optional but needed for .dmg output)
brew install create-dmg
```

### 3 — Verify GTK stack

```bash
python3 -c "
import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('Vte', '3.91')
from gi.repository import Gtk, Adw, Vte
print('GTK', Gtk.get_major_version(), '— OK')
print('Adw — OK')
print('VTE — OK')
"
```

---

## Building

### Quick build (one command)

Run from the **project root** directory:

```bash
chmod +x pyinstaller.sh
./pyinstaller.sh
```

The script will:
1. Detect your Mac architecture (`arm64` / `x86_64`) and set the correct Homebrew prefix
2. Create an isolated virtual environment at `.venv-homebrew/` using Homebrew Python 3.13
3. Install `PyInstaller` and project Python dependencies inside that venv
4. Run `pyinstaller --clean --noconfirm ssh-client-manager.spec`
5. Produce `dist/SSHClientManager.app`
6. Create `dist/SSHClientManager-macOS-{VERSION}-{ARCH}.dmg` via `create-dmg` (if installed)

### Manual PyInstaller invocation

```bash
source .venv-homebrew/bin/activate
python -m PyInstaller --clean --noconfirm ssh-client-manager.spec
```

---

## Output

| Path | Description |
|------|-------------|
| `dist/SSHClientManager.app` | Self-contained macOS application bundle |
| `dist/SSHClientManager-macOS-*.dmg` | Drag-and-drop installer (if `create-dmg` is installed) |

### Test the bundle

```bash
open dist/SSHClientManager.app
```

Or from a terminal (to see debug output):

```bash
dist/SSHClientManager.app/Contents/MacOS/SSHClientManager
```

---

## How the Bundle Works

### What gets bundled

The PyInstaller spec (`ssh-client-manager.spec`) copies the following into the `.app`:

| Resource | Bundle location |
|----------|----------------|
| GTK4 / libadwaita / VTE dylibs | `Contents/Frameworks/` |
| GI typelibs (`*.typelib`) | `Contents/girepository-1.0/` |
| GLib schemas | `Contents/Resources/share/glib-2.0/schemas/` |
| Adwaita icon theme | `Contents/Resources/share/icons/Adwaita/` |
| GTK-4.0 assets | `Contents/Resources/share/gtk-4.0/` |
| libadwaita share data & locale | `Contents/Resources/share/libadwaita-1/` |
| GDK-Pixbuf loaders | `Contents/Resources/lib/gdk-pixbuf-2.0/2.10.0/` |
| App source (`src/`) | `Contents/MacOS/src/` |

### Runtime hook (`hook-gtk_runtime.py`)

Before any application code runs, the runtime hook sets:

| Variable | Value inside bundle |
|----------|-------------------|
| `GI_TYPELIB_PATH` | Bundled typelibs directory |
| `GSETTINGS_SCHEMA_DIR` | Bundled GLib schemas directory |
| `XDG_DATA_DIRS` | Bundled `share/` directory |
| `GDK_PIXBUF_MODULEDIR` | Bundled pixbuf loaders |
| `PATH` | Prepends `/opt/homebrew/bin`, `/usr/local/bin`, `/usr/bin`, `/bin` |
| `XDG_CONFIG_HOME` | `~/.config` |
| `XDG_DATA_HOME` | `~/.local/share` |
| `XDG_CACHE_HOME` | `~/.cache` |

This ensures GTK, Adwaita, and VTE can find all assets without any host Homebrew installation.

---

## Adding an App Icon

PyInstaller requires a `.icns` file. Place it at:

```
packaging/macos/sshclientmanager.icns
```

### Create from a PNG

```bash
# Starting from a 1024×1024 PNG
mkdir MyIcon.iconset
sips -z 16 16     icon.png --out MyIcon.iconset/icon_16x16.png
sips -z 32 32     icon.png --out MyIcon.iconset/icon_16x16@2x.png
sips -z 32 32     icon.png --out MyIcon.iconset/icon_32x32.png
sips -z 64 64     icon.png --out MyIcon.iconset/icon_32x32@2x.png
sips -z 128 128   icon.png --out MyIcon.iconset/icon_128x128.png
sips -z 256 256   icon.png --out MyIcon.iconset/icon_128x128@2x.png
sips -z 256 256   icon.png --out MyIcon.iconset/icon_256x256.png
sips -z 512 512   icon.png --out MyIcon.iconset/icon_256x256@2x.png
sips -z 512 512   icon.png --out MyIcon.iconset/icon_512x512.png
cp icon.png            MyIcon.iconset/icon_512x512@2x.png
iconutil -c icns MyIcon.iconset -o packaging/macos/sshclientmanager.icns
```

If no `.icns` file is present, the build still succeeds but the app will use the default
macOS document icon.

---

## Troubleshooting

### App crashes immediately at launch

Run from Terminal to see the error output:

```bash
dist/SSHClientManager.app/Contents/MacOS/SSHClientManager
```

### `GI_TYPELIB_PATH` not set / typelib not found

Verify the typelibs were collected:

```bash
ls dist/SSHClientManager.app/Contents/_internal/girepository-1.0/
# Should list: Gtk-4.0.typelib, Adw-1.typelib, Vte-3.91.typelib, etc.
```

If they are absent, check that `brew install pygobject3` and `brew install vte3`
completed successfully and that the Homebrew prefix in `ssh-client-manager.spec`
matches your installed Homebrew location (`/opt/homebrew` for Apple Silicon,
`/usr/local` for Intel).

### `vte3` not available via Homebrew

On some Homebrew configurations, VTE for GTK4 may be named differently:

```bash
brew search vte
# Install whichever provides libvte-2.91-gtk4.dylib
```

### `libadwaita` theming broken (white / unstyled window)

The libadwaita share data was not bundled. Check the build output for:

```
WARNING: libadwaita share data not found
```

Reinstall libadwaita and rebuild:

```bash
brew reinstall libadwaita
./pyinstaller.sh
```

### SSH connections fail (Permission denied / no such file)

The app uses macOS's built-in `/usr/bin/ssh`. Verify it is accessible:

```bash
/usr/bin/ssh -V
```

The runtime hook adds `/usr/bin` to `PATH` automatically, so this should work
out of the box even when the app is launched via double-click from Finder.

---

## Architecture Notes

| Concern | Detail |
|---------|--------|
| Apple Silicon | Homebrew at `/opt/homebrew`; detected automatically |
| Intel Mac | Homebrew at `/usr/local`; detected automatically |
| Universal binary | Not supported by this approach; build separately on each arch and produce two DMGs |
| macOS Keychain | Not used — credentials are AES-encrypted locally by `src/credential_store.py` |
| WebKit / webkitgtk | Not required; this is a Linux-only dependency not present in ssh-client-manager |
| sshpass | Not required; authentication uses SSH_ASKPASS mechanism with temporary scripts |
