# Ubuntu / Debian Packaging — SSH Client Manager

Two installation methods are provided:

| Method | Best for | Output |
|--------|----------|--------|
| **`install.sh`** | Quick local install, development | Files placed directly into `/usr/lib/` |
| **`build-deb.sh`** | Distribution, sharing, `apt` management | `.deb` binary package |

---

## Files

```
packaging/ubuntu/
├── install.sh                              # Direct install from source (recommended for quick setup)
├── build-deb.sh                            # Build a .deb package
├── io.github.ssh-client-manager.desktop   # Desktop entry (app menu integration)
├── io.github.ssh-client-manager.metainfo.xml  # AppStream metadata
└── debian/                                 # Debian source package structure
    ├── control                             # Package name, dependencies, description
    ├── rules                               # Build rules (debhelper)
    ├── changelog                           # Version history
    ├── compat                              # Debhelper compatibility level
    ├── install                             # File installation manifest
    └── source/
        └── format                          # Source package format
```

---

## Method 1 — Quick Install from Source (`install.sh`)

Installs directly from the cloned repository without building a `.deb`.
Ideal for development machines or personal use.

### 1. Install system dependencies

```bash
sudo apt update
sudo apt install \
    python3 \
    python3-gi \
    python3-gi-cairo \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    gir1.2-vte-3.91 \
    libgtk-4-1 \
    libadwaita-1-0 \
    libvte-2.91-gtk4-0 \
    python3-cryptography \
    python3-paramiko \
    python3-psutil
```

> **Ubuntu version note:**
> - Ubuntu 22.04 (Jammy): `gir1.2-adw-1` and `libvte-2.91-gtk4-0` may need the `universe` repository: `sudo add-apt-repository universe`
> - Ubuntu 24.04 (Noble) and later: all packages are in `main` / `universe` by default.

### 2. Run the installer

From the **project root**:

```bash
chmod +x packaging/ubuntu/install.sh
sudo packaging/ubuntu/install.sh
```

### 3. Launch

```bash
ssh-client-manager
# or find "SSH Client Manager" in your desktop app menu
```

### Uninstall

```bash
sudo packaging/ubuntu/install.sh --uninstall
```

---

## Method 2 — Build a `.deb` Package (`build-deb.sh`)

Produces a proper Debian binary package that can be installed with `apt` and
tracked / removed like any system package.

### 1. Install build tools

```bash
sudo apt install \
    dpkg-dev \
    debhelper \
    dh-python \
    python3-all \
    python3-setuptools \
    desktop-file-utils
```

### 2. Build the package

From the **project root**:

```bash
chmod +x packaging/ubuntu/build-deb.sh
./packaging/ubuntu/build-deb.sh
```

Output: `../ssh-client-manager_1.0.0-1_all.deb` (one directory above the project root, standard `dpkg-buildpackage` behaviour).

### 3. Install the `.deb`

```bash
sudo apt install ./ssh-client-manager_1.0.0-1_all.deb
# apt resolves any missing dependencies automatically

# Alternative if apt install is not available:
sudo dpkg -i ssh-client-manager_1.0.0-1_all.deb
sudo apt-get install -f   # fix missing deps
```

### Uninstall

```bash
sudo apt remove ssh-client-manager
```

---

## Updating the Version

1. Edit `packaging/ubuntu/debian/changelog` — add a new entry at the top following the Debian changelog format:

```
ssh-client-manager (1.1.0-1) unstable; urgency=medium

  * Brief description of what changed.

 -- Your Name <you@example.com>  Thu, 01 Jan 2026 00:00:00 +0000
```

2. Rebuild with `./packaging/ubuntu/build-deb.sh`.

---

## System Dependency Reference

| Package | Provides |
|---------|---------|
| `python3-gi` | PyGObject — Python GObject/GTK bindings |
| `python3-gi-cairo` | Cairo integration for PyGObject |
| `gir1.2-gtk-4.0` | GTK 4 GObject introspection typelib |
| `gir1.2-adw-1` | libadwaita GObject introspection typelib |
| `gir1.2-vte-3.91` | VTE terminal widget typelib |
| `libgtk-4-1` | GTK 4 shared library |
| `libadwaita-1-0` | libadwaita shared library (GNOME HIG widgets) |
| `libvte-2.91-gtk4-0` | VTE terminal widget (GTK4 variant) |
| `python3-cryptography` | AES encryption for credential storage |
| `python3-paramiko` | SSH protocol library |
| `python3-psutil` | Process monitoring utilities |

No `sshpass`, no `expect`, no `libsecret` / keyring required.

---

## Troubleshooting

### `gir1.2-vte-3.91` not found (Ubuntu 22.04)

```bash
sudo add-apt-repository universe
sudo apt update
sudo apt install gir1.2-vte-3.91 libvte-2.91-gtk4-0
```

### `gir1.2-adw-1` not found (Ubuntu 22.04)

libadwaita 1.x landed in Ubuntu 22.10+. On 22.04 you can use the official GNOME PPA:

```bash
sudo add-apt-repository ppa:gnome-team/gnome-backports
sudo apt update
sudo apt install libadwaita-1-0 gir1.2-adw-1
```

### Application starts but window is blank / unstyled

GTK theme data is missing. Ensure `libadwaita-1-0` is installed and your session runs under GNOME or a compatible DE with GTK4 theming support.

### SSH connections fail with "No such file or directory"

The app uses the system `ssh` binary. Verify it is installed:

```bash
which ssh || sudo apt install openssh-client
```
