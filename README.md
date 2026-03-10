# SSH Client Manager

A modern, user-friendly SSH connection manager built with **Python 3**, **GTK4**, **libadwaita**, and **VTE**. This project combines the best features of [gnome-connection-manager](https://github.com/kuthulu/gnome-connection-manager) and [sshpilot](https://github.com/mfat/sshpilot) into a unified application.

## Features

- **Split Terminals**: Split the terminal area horizontally or vertically, with unlimited nesting
- **Tabbed Interface**: Multiple tabs per split pane, with drag-and-drop between panes
- **Encrypted Credential Storage**: AES-encrypted password and passphrase storage (no plaintext, no expect)
- **SSH_ASKPASS Authentication**: Secure password injection via SSH_ASKPASS mechanism (no expect scripts)
- **Hierarchical Groups**: Organize servers in nested groups (e.g., Production/WebServers) with drag-and-drop reordering of both connections and groups
- **Cluster Mode**: Send commands to all open terminals simultaneously
- **Modern UI**: GTK4 + libadwaita with dark/light theme support
- **Connection Management**: Full CRUD for connections with import/export
- **Post-Login Commands**: Configure commands to run automatically after login (with delay support)
- **Port Forwarding**: Local, remote, and dynamic port forwarding support
- **Keyboard Shortcuts**: Comprehensive keyboard navigation
- **Session Recording**: Record terminal sessions in asciicast v2 format with built-in playback
- **SSH Key Manager**: Generate and manage SSH keys (RSA/Ed25519/ECDSA) from within the app
- **Auto-Reconnect**: Automatic reconnection prompt when SSH sessions disconnect unexpectedly
- **SFTP File Browser**: Browse, upload, and download remote files with drag-and-drop support
- **Command Snippets**: Save and reuse frequently used commands with variable substitution

## Architecture

This project takes the best of both source projects:

| Feature | gnome-connection-manager | sshpilot | ssh-client-manager |
|---------|--------------------------|----------|-------------------|
| Toolkit | GTK3 | GTK4/Adw | **GTK4/Adw** |
| Terminal | VTE (GTK3) | VTE (GTK4) + PyXterm.js | **VTE (GTK4)** |
| Split Panes | ✅ HPaned/VPaned | ❌ | **✅ Gtk.Paned** |
| Tab DnD | ✅ Between split panes | N/A | **✅ Between split panes** |
| Credentials | AES + expect scripts | libsecret/keyring + ssh-agent | **AES (Fernet) + SSH_ASKPASS** |
| Auth Method | expect auto-input | SSH_ASKPASS + sshpass | **SSH_ASKPASS** |
| Config Format | INI file | SSH config + JSON | **JSON** |
| Groups | Flat slash-separated | Flat with managers | **Hierarchical tree** |

## Screenshots

```
┌──────────────────────────────────────────────────────┐
│ [≡] [+Local] [SplitH] [SplitV] [Unsplit] [☰]       │
├──────────┬─────────────────────┬─────────────────────┤
│ Search   │ Tab1 | Tab2         │ Tab3 | Tab4         │
├──────────┤─────────────────────┤─────────────────────┤
│ ▶ Prod   │                     │                     │
│   Web01  │  $ ls -la           │  $ top              │
│   Web02  │  total 42           │                     │
│ ▶ Dev    │  drwxr-xr-x ...    │  PID USER ...       │
│   Local  │                     │                     │
│          │─────────────────────│                     │
│          │ Tab5                │                     │
│          │─────────────────────│                     │
│          │  $ tail -f /var/log │                     │
└──────────┴─────────────────────┴─────────────────────┘
```

## Requirements

### System Dependencies

**Debian/Ubuntu:**
```bash
sudo apt install \
  python3 python3-gi python3-gi-cairo \
  libgtk-4-1 gir1.2-gtk-4.0 \
  libadwaita-1-0 gir1.2-adw-1 \
  libvte-2.91-gtk4-0 gir1.2-vte-3.91 \
  python3-cryptography
```

**Fedora/RHEL:**
```bash
sudo dnf install \
  python3 python3-gobject \
  gtk4 libadwaita \
  vte291-gtk4 \
  python3-cryptography
```

### Python Dependencies

```bash
pip install -r requirements.txt
```

## Run from Source

```bash
python3 run.py
```

Enable verbose debugging:
```bash
python3 run.py --verbose
```

## Project Structure

```
ssh-client-manager/
├── run.py                      # Entry point
├── requirements.txt            # Python dependencies
├── README.md                   # This file
├── USER_GUIDE.md               # User-facing documentation
├── src/
│   ├── __init__.py             # Package metadata
│   ├── app.py                  # Adw.Application + CSS + shortcuts
│   ├── window.py               # Main window with sidebar + toolbars
│   ├── terminal_widget.py      # VTE terminal wrapper
│   ├── terminal_panel.py       # Tab/split management (Paned + Notebook)
│   ├── connection.py           # Connection model + ConnectionManager
│   ├── connection_dialog.py    # Add/Edit connection dialog
│   ├── credential_store.py     # AES-encrypted credential storage
│   ├── ssh_handler.py          # SSH command builder + SSH_ASKPASS
│   ├── sidebar.py              # Connection tree sidebar
│   ├── config.py               # App configuration (JSON)
│   ├── preferences_dialog.py   # Preferences window
│   ├── sftp_browser.py         # SFTP file browser panel
│   ├── snippet_manager.py      # Command snippet manager
│   ├── cluster_window.py       # Cluster mode selector
│   ├── ssh_config_editor.py    # SSH config file editor
│   ├── ssh_key_manager.py      # SSH key management dialog
│   └── session_recorder.py     # Session recording + playback (asciicast v2)
```

## Key Design Decisions

### No `expect` for Credentials
Instead of using expect scripts to auto-type passwords (like gnome-connection-manager), this project uses the `SSH_ASKPASS` mechanism:

1. A temporary script is created that echoes the stored password
2. `SSH_ASKPASS` environment variable points to this script
3. `SSH_ASKPASS_REQUIRE=force` tells SSH to use it even with a TTY
4. The script distinguishes password vs passphrase prompts
5. The temp script is cleaned up after connection

### Encrypted Storage
Credentials are encrypted with [Fernet](https://cryptography.io/en/latest/fernet/) (AES-128-CBC + HMAC-SHA256):
- Auto-generated encryption key stored at `~/.config/ssh-client-manager/.store.key` (mode 0600)
- Encrypted credentials at `~/.config/ssh-client-manager/credentials.dat` (mode 0600)
- Export/import uses PBKDF2-derived keys from user passwords

### Split Terminal Architecture
Adapted from gnome-connection-manager's approach:
- The terminal area starts as a single `Gtk.Notebook`
- Splitting wraps notebooks in `Gtk.Paned` containers
- All notebooks share a group name enabling tab drag-and-drop between panes
- When a notebook becomes empty, it auto-removes and the pane collapses
- Unlimited nesting: `Paned → Paned → Notebook`

## Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| Ctrl+Shift+T | New local terminal |
| Ctrl+Shift+N | New connection |
| Ctrl+W | Close current tab |
| Ctrl+Tab | Next tab |
| Ctrl+Shift+Tab | Previous tab |
| Ctrl+Shift+H | Split horizontally |
| Ctrl+Shift+D | Clone current tab |
| Ctrl+F | Search in terminal |
| F9 | Toggle sidebar |
| Alt+1-9 | Switch to tab N |
| Ctrl+Q | Quit |

## Configuration

Settings are stored in `~/.config/ssh-client-manager/config.json`.
Connections are stored in `~/.config/ssh-client-manager/connections.json`.

## License

GPL-3.0
