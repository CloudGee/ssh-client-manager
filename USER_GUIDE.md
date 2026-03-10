# SSH Client Manager — User Guide

---

## Quick Start

1. Add a new connection via **New Connection** in the menu or sidebar right-click
2. Fill in the SSH command (e.g. `ssh user@hostname`) or use structured fields for RDP/VNC
3. **Double-click** a connection in the sidebar to connect
4. Use **Quick Connect** in the header bar for instant `ssh user@host` connections

---

## Connection Management

- SSH, SFTP, RDP, and VNC protocols supported
- Organize connections into **groups and subgroups** — use `/` in the group field for nesting (e.g. `Work/Dev`)
- **Drag-and-drop** connections or entire groups onto other groups to reorganize them; drag to empty space to move to root level
- **Right-click** the sidebar for context menu: connect, edit, duplicate, delete, open SFTP
- **Favorites** — mark connections for quick access
- **Tags** — add comma-separated tags for categorization and filtering
- **Open After** — set a prerequisite connection that auto-connects first
- **Credentials** are AES-256 encrypted and stored locally
- **Delete a group**: choose to keep its connections (move to Ungrouped) or delete all

---

## Terminal Features

### Split Terminals

- Use the **Split menu button** in the header bar to split in any direction
- **Split Left / Right / Up / Down** — places new pane on the chosen side
- **Right-click a tab** for context menu with split options
- Click **Unsplit All** to restore single-pane layout
- Tabs can be reordered by dragging within a notebook

### Font Zoom

| Action | Shortcut |
|---|---|
| Zoom in | `Ctrl/Cmd` + `=` |
| Zoom out | `Ctrl/Cmd` + `-` |
| Reset zoom | `Ctrl/Cmd` + `0` |
| Zoom via scroll | `Ctrl/Cmd` + Scroll wheel |

### Copy & Paste

- **macOS:** `Cmd+C` / `Cmd+V`
- **Linux:** `Ctrl+Shift+C` / `Ctrl+Shift+V`
- Middle-click to paste selection

---

## Keyboard Shortcuts

| Shortcut | Action |
|---|---|
| `Ctrl+Shift+T` / `Cmd+T` | New local terminal |
| `Ctrl+W` / `Cmd+W` | Close current tab |
| `Ctrl+Shift+N` / `Cmd+N` | New connection dialog |
| `Ctrl+F` / `Cmd+F` | Search in terminal |
| `Ctrl+,` / `Cmd+,` | Open Preferences |
| `Ctrl+Tab` | Next tab |
| `Ctrl+Shift+Tab` | Previous tab |
| `Alt+1`–`Alt+9` | Switch to tab by number |
| `Ctrl+Shift+Left` | Split Left |
| `Ctrl+Shift+Right` | Split Right |
| `Ctrl+Shift+Up` | Split Up |
| `Ctrl+Shift+Down` | Split Down |
| `Ctrl+Shift+D` | Clone / duplicate current tab |
| `Ctrl+Shift+S` / `Cmd+Shift+S` | Command Snippets |
| `F9` | Toggle sidebar |
| `Ctrl+Q` / `Cmd+Q` | Quit |

---

## SFTP File Browser

- **Right-click** an SSH/SFTP connection in the sidebar → **Open SFTP** to browse remote files
- Toolbar navigation: **Back**, **Up**, **Home**, **Refresh**
- **Double-click a folder** to enter it; **double-click a file** to download it to `~/Downloads/`
- **Upload**: click the **Upload** button in the toolbar, or right-click → Upload
- **Download**: double-click a file, or right-click → Download
- **Right-click** remote files/folders for the full context menu: Download, Upload, Rename, Delete, New Folder
- Sort columns by clicking column headers (Name, Size, Modified, Permissions)
- Upload and download progress is shown in the status bar

---

## Cluster Mode

- Click the **Cluster** button in the header bar to enter cluster mode
- Select which open terminals to broadcast to using the checkboxes
- Type once → keystroke is sent to **all selected terminals simultaneously**
- Useful for running the same command on multiple servers at once

---

## Command Snippets

Access via the **menu** or `Ctrl+Shift+S` / `Cmd+Shift+S`.

- Save frequently used commands with a **name**, **category**, and optional **description**
- **Send** — send the snippet directly to the active terminal
- **Broadcast** — send to **all** open terminals at once
- **Edit** — modify an existing snippet
- **Copy** — copy command to clipboard
- **Export / Import** snippets using the buttons in the snippet window

### Variable Support

Use `{{variable_name}}` syntax for dynamic values:

```
ssh {{user}}@{{host}} -p {{port}}
```

When sending or copying, a dialog prompts to fill in each variable before the command is dispatched.

---

## Session Recording

- Click the **Record button** (circle icon) in the header bar to start/stop recording the current terminal
- Also available via **right-click a terminal** → **Start / Stop Recording**
- The button turns red while recording; the tab title shows **[REC]**
- Recordings are saved in **asciicast v2** format (`.cast` files)
- The tab title shows **[REC]** while recording; the status bar shows the output file path
- Default save directory: `~/Documents/SSHClientManager-Recordings` (configurable in Preferences → Session Recording)
- The directory is created automatically if it doesn't exist
- View and replay past recordings via **Menu → Session Recordings**
- Built-in player with **Play/Pause**, **Restart**, and **speed control** (0.5x–8x)
- Open external `.cast` files via the **Open** button in the recordings dialog

---

## SSH Key Manager

- Open via **Menu → SSH Key Manager**
- Lists all SSH keys found in `~/.ssh/` with type, bits, fingerprint, and comment
- **Generate new keys**: RSA (2048/4096), Ed25519, ECDSA (256/384/521)
- Set custom filenames, comments, and optional passphrases for generated keys
- Keys are generated using `ssh-keygen` on the local system

---

## Auto-Reconnect

- When an SSH connection drops unexpectedly, a **reconnect prompt** appears in the terminal tab
- Click **Reconnect** to re-establish the same connection in the same tab
- Works for SSH, SFTP, and other terminal-based connections

---

## Advanced Features

### Port Forwarding

Configure in connection properties under the **Forwarding** tab:

| Type | Flag | Example |
|---|---|---|
| Local | `-L` | `8080:remote-host:80` (local 8080 → remote port 80) |
| Remote | `-R` | `9090:localhost:9090` |
| Dynamic (SOCKS) | `-D` | `1080` |

### Jump Hosts (ProxyJump)

Set in connection properties → **Advanced** tab:

- Single hop: `user@jumphost`
- Multiple hops: `user@host1,user@host2`
- Custom ProxyCommand: e.g. `ssh user@jump -W %h:%p`

### Global Passphrases

Set in **Preferences → Global Passphrases**:

- Up to 5 passphrases tried automatically for every connection
- Order of attempts for key decryption and SSH authentication:
  1. Connection-specific passphrases
  2. Global passphrases
  3. Connection password (as fallback for key decryption)
- For multi-hop jump hosts, each hop retries all passphrases from the beginning
- If no stored password exists, passphrases are also tried for password prompts
- Also applies to SFTP browser connections (paramiko-based)

### Post-Login Commands

Configure in connection properties → **Commands** tab:

- Commands are sent to the terminal automatically after login
- Use `##D=1000` on its own line to insert a **1000 ms delay** between commands

### Terminal Logging

- Configure **auto-logging** in Preferences → Terminal Logging
- Logs are saved to `~/ssh-logs/` by default (configurable in Preferences)
- Log files are plain text captures of terminal output (`.log`)

### SSH Config Editor

- Edit `~/.ssh/config` directly inside the app
- Access via **Menu → SSH Config Editor**
- Supports syntax highlighting; save with `Ctrl+S` / `Cmd+S`

---

## Import / Export / Backup

### Connection Import / Export

- **Export** all connections (with encrypted credentials) to a `.json` file
- **Import** with **Overwrite** (replace all) or **Append** (merge) mode
- All fields preserved: name, group, protocol, command, credentials, port forwards, jump hosts, tags, favorites, appearance, dependencies

### Full Backup & Restore

| Action | Description |
|---|---|
| **Backup All** | Creates a `.zip` containing config, connections, snippets, and encrypted credentials |
| **Restore All** | Restores everything from a backup `.zip` (replaces current data) |

Access via **Menu → Backup All / Restore All**.

---

## Appearance

- **Global settings** in Preferences: font family & size, foreground/background color, cursor shape, scrollback lines
- **Per-connection overrides**: font and colors in connection properties → **Appearance** tab — these override the global defaults for that connection only

---

## Configuration Files

All configuration is stored in `~/.config/ssh-client-manager/`:

| File | Contents |
|---|---|
| `config.json` | App preferences and settings |
| `connections.json` | Connection definitions |
| `snippets.json` | Command snippets |
| `.store.key` | Encryption key for the credential store |
| `.credentials.enc` | Encrypted passwords and passphrases |
