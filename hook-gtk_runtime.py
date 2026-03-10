# hook-gtk_runtime.py
#
# PyInstaller runtime hook for SSH Client Manager on macOS.
# Executes inside the app bundle before any application code runs.
# Sets up all environment variables required for GTK4 / libadwaita / VTE
# to locate their bundled resources instead of looking in the host system.
#
# Adapted from the sshpilot project (https://github.com/mfat/sshpilot).

import os
import sys
from pathlib import Path


def _setup_macos_env() -> None:
    # ── Locate Contents/ inside the .app bundle ──────────────────────────────
    cur = Path(sys.executable).resolve()
    contents = None
    for _ in range(8):
        if (cur / "Contents").is_dir():
            contents = cur / "Contents"
            break
        cur = cur.parent

    if contents is None:
        # Onedir / development fallback
        bundle_root = Path(sys.executable).parent
        if (bundle_root / "_internal").is_dir():
            contents = bundle_root / "_internal"
        else:
            contents = Path(getattr(sys, "_MEIPASS", Path.cwd())) / ".."

    resources  = (contents / "Resources").resolve()
    frameworks = (contents / "Frameworks").resolve()

    # ── GI typelib path ───────────────────────────────────────────────────────
    gi_candidates = [
        contents  / "girepository-1.0",  # onedir layout
        resources / "girepository-1.0",  # app-bundle layout
    ]
    gi_paths = [str(p) for p in gi_candidates if p.is_dir()]
    if gi_paths:
        os.environ["GI_TYPELIB_PATH"] = ":".join(gi_paths)

    # ── GSettings schemas ─────────────────────────────────────────────────────
    for schemas_dir in [
        resources / "share" / "glib-2.0" / "schemas",
        contents  / "share" / "glib-2.0" / "schemas",
    ]:
        if schemas_dir.is_dir():
            os.environ["GSETTINGS_SCHEMA_DIR"] = str(schemas_dir)
            break

    # ── XDG data dirs (icons, themes, gtk-4.0 assets) ────────────────────────
    for share_dir in [resources / "share", contents / "share"]:
        if share_dir.is_dir():
            os.environ["XDG_DATA_DIRS"] = str(share_dir)
            break

    # ── GDK-Pixbuf loaders ───────────────────────────────────────────────────
    pixbuf_module_dir  = frameworks / "lib" / "gdk-pixbuf" / "loaders"
    pixbuf_module_file = resources  / "lib" / "gdk-pixbuf" / "loaders.cache"
    if pixbuf_module_dir.is_dir():
        os.environ["GDK_PIXBUF_MODULEDIR"] = str(pixbuf_module_dir)
    if pixbuf_module_file.is_file():
        os.environ["GDK_PIXBUF_MODULE_FILE"] = str(pixbuf_module_file)

    # ── PATH — ensure system ssh(1) is reachable on double-click launch ───────
    # SSH Client Manager spawns `ssh` subprocess directly; it must be on PATH.
    system_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
    current_path = os.environ.get("PATH", "")
    existing = set(current_path.split(":"))
    extra = [p for p in system_paths if p not in existing]
    os.environ["PATH"] = ":".join(extra + [current_path]) if current_path else ":".join(extra)

    # ── XDG user dirs (needed by credential store and config) ────────────────
    home = os.environ.setdefault("HOME", os.path.expanduser("~"))
    os.environ.setdefault("USER",    os.environ.get("LOGNAME", "unknown"))
    os.environ.setdefault("LOGNAME", os.environ.get("USER",    "unknown"))
    os.environ.setdefault("SHELL",   "/bin/bash")

    os.environ["XDG_CONFIG_HOME"] = os.path.join(home, ".config")
    os.environ["XDG_DATA_HOME"]   = os.path.join(home, ".local", "share")
    os.environ["XDG_CACHE_HOME"]  = os.path.join(home, ".cache")

    for env_key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME"):
        os.makedirs(os.environ[env_key], exist_ok=True)

    # ── Cairo / GI Python path ────────────────────────────────────────────────
    gi_modules_path = str(frameworks / "gi")
    if Path(gi_modules_path).is_dir():
        pythonpath = os.environ.get("PYTHONPATH", "")
        os.environ["PYTHONPATH"] = f"{gi_modules_path}:{pythonpath}" if pythonpath else gi_modules_path


# Only activate on macOS bundles
if sys.platform == "darwin":
    _setup_macos_env()
