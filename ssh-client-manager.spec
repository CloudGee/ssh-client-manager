# ssh-client-manager.spec — build with: pyinstaller --clean ssh-client-manager.spec
#
# Bundles SSH Client Manager as a self-contained macOS .app.
# All GTK4 / libadwaita / VTE dylibs, GI typelibs, GLib schemas, and
# Adwaita icon assets are copied into the bundle so no Homebrew installation
# is required on the end-user system.
#
# Adapted from the sshpilot project packaging approach.

import os
import glob
import platform
import sysconfig
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

# ── App metadata ────────────────────────────────────────────────────────────
app_name        = "SSHClientManager"
entry_py        = "run.py"
bundle_id       = "io.github.ssh-client-manager"
icon_file       = "packaging/macos/sshclientmanager.icns"
min_macos       = "12.0"

# ── Resolve site-packages (for Cairo Python binding) ────────────────────────
site_packages_dir = Path(sysconfig.get_path("platlib"))

# ── Detect architecture → Homebrew prefix ───────────────────────────────────
arch = platform.machine()
if arch == "arm64":
    homebrew = "/opt/homebrew"
    print(f"🍎 Apple Silicon (ARM64) — Homebrew at: {homebrew}")
else:
    homebrew = "/usr/local"
    print(f"💻 Intel Mac (x86_64) — Homebrew at: {homebrew}")

hb_lib   = f"{homebrew}/lib"
hb_share = f"{homebrew}/share"
hb_gir   = f"{hb_lib}/girepository-1.0"

# ── GTK dylibs to bundle ────────────────────────────────────────────────────
# ssh-client-manager requires: GTK4, libadwaita, VTE 3.91
# No gtksourceview-5, no WebKit (those are sshpilot-specific).
gtk_libs_patterns = [
    "libadwaita-1.*.dylib",
    "libgtk-4.*.dylib",
    "libgdk-4.*.dylib",
    "libgdk_pixbuf-2.0.*.dylib",
    "libvte-2.91.*.dylib",
    "libvte-2.91-gtk4.*.dylib",
    "libgraphene-1.0.*.dylib",
    "libpango-1.*.dylib",
    "libpangocairo-1.*.dylib",
    "libharfbuzz.*.dylib",
    "libfribidi.*.dylib",
    "libcairo.*.dylib",
    "libcairo-gobject.*.dylib",
    "libgobject-2.0.*.dylib",
    "libglib-2.0.*.dylib",
    "libgio-2.0.*.dylib",
    "libgmodule-2.0.*.dylib",
    "libintl.*.dylib",
    "libffi.*.dylib",
    "libicu*.dylib",
]

binaries = []
for pat in gtk_libs_patterns:
    for src in glob.glob(os.path.join(hb_lib, pat)):
        # VTE and Adwaita libs go to Frameworks root to avoid nested paths
        if "vte" in pat.lower() or "adwaita" in pat.lower():
            binaries.append((src, "."))
        else:
            binaries.append((src, "Frameworks"))

# Cairo Python binding (_gi_cairo.so)
gi_site_packages = site_packages_dir / "gi"
if gi_site_packages.exists():
    cairo_binding = next((p for p in gi_site_packages.glob("_gi_cairo.*")), None)
    if cairo_binding:
        binaries.append((str(cairo_binding), "gi"))

# ── GI typelibs ─────────────────────────────────────────────────────────────
datas = []
for typelib in glob.glob(os.path.join(hb_gir, "*.typelib")):
    datas.append((typelib, "girepository-1.0"))

# ── Shared data: schemas, icons, gtk assets ─────────────────────────────────
datas += [
    (os.path.join(hb_share, "glib-2.0", "schemas"), "Resources/share/glib-2.0/schemas"),
    (os.path.join(hb_share, "icons", "Adwaita"),    "Resources/share/icons/Adwaita"),
    (os.path.join(hb_share, "gtk-4.0"),              "Resources/share/gtk-4.0"),
    # Application source package
    ("src", "src"),
]

# ── libadwaita share data (standard path or Cellar fallback) ────────────────
libadwaita_share = os.path.join(hb_share, "libadwaita-1")
if os.path.exists(libadwaita_share):
    datas.append((libadwaita_share, "Resources/share/libadwaita-1"))
    print(f"Found libadwaita share: {libadwaita_share}")
else:
    cellar = f"{homebrew}/Cellar/libadwaita"
    if os.path.exists(cellar):
        for v in os.listdir(cellar):
            p = os.path.join(cellar, v, "share", "libadwaita-1")
            if os.path.exists(p):
                datas.append((p, "Resources/share/libadwaita-1"))
                print(f"Found libadwaita share in Cellar: {p}")
                break
    else:
        print("WARNING: libadwaita share data not found — theming may be broken")

# libadwaita locale files (Cellar)
cellar_adw = f"{homebrew}/Cellar/libadwaita"
if os.path.exists(cellar_adw):
    for v in os.listdir(cellar_adw):
        locale_p = os.path.join(cellar_adw, v, "share", "locale")
        if os.path.exists(locale_p):
            datas.append((locale_p, "Resources/share/locale"))
            print(f"Added libadwaita locale: {locale_p}")
            break

# ── GDK-Pixbuf loaders ──────────────────────────────────────────────────────
gdkpixbuf_loaders = f"{homebrew}/lib/gdk-pixbuf-2.0/2.10.0"
if os.path.exists(gdkpixbuf_loaders):
    datas.append((gdkpixbuf_loaders, "Resources/lib/gdk-pixbuf-2.0/2.10.0"))
    print(f"Added GDK-Pixbuf loaders: {gdkpixbuf_loaders}")
else:
    print("WARNING: GDK-Pixbuf loaders not found — image display may be impaired")

# ── Hidden imports ───────────────────────────────────────────────────────────
hiddenimports = collect_submodules("gi")
hiddenimports += collect_submodules("paramiko")
hiddenimports += [
    "gi._gi_cairo",
    "gi.repository.cairo",
    "cairo",
    # ssh-client-manager runtime
    "paramiko",
    "cryptography",
    "cryptography.fernet",
    "cryptography.hazmat.primitives",
    "psutil",
]

# ── PyInstaller analysis ─────────────────────────────────────────────────────
block_cipher = None

a = Analysis(
    [entry_py],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=["."],
    runtime_hooks=["hook-gtk_runtime.py"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=app_name,
    icon=icon_file if os.path.exists(icon_file) else None,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name=app_name,
)

app = BUNDLE(
    coll,
    name=f"{app_name}.app",
    icon=icon_file if os.path.exists(icon_file) else None,
    bundle_identifier=bundle_id,
    info_plist={
        "NSHighResolutionCapable":        True,
        "LSMinimumSystemVersion":         min_macos,
        "CFBundleDisplayName":            "SSH Client Manager",
        "CFBundleShortVersionString":     "1.1.0",
        "CFBundleVersion":                "1.1.0",
        "NSHumanReadableCopyright":       "© 2026 SSH Client Manager Contributors",
        "LSUIElement":                    False,
    },
)
