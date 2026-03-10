"""
SSH process handling with SSH_ASKPASS-based credential injection.

No expect commands are used. Instead, credentials are passed via a
temporary SSH_ASKPASS script that outputs the appropriate credential
when SSH requests it.
"""

import os
import stat
import tempfile
import shutil
import atexit
import uuid
from pathlib import Path
from typing import Optional

from .connection import Connection
from .credential_store import CredentialStore


class SSHHandler:
    """
    Builds SSH commands and manages credential injection via SSH_ASKPASS.

    The askpass mechanism works by:
    1. Creating a temporary shell script that echoes the stored password/passphrase
    2. Setting SSH_ASKPASS to point to this script
    3. Setting SSH_ASKPASS_REQUIRE=force so SSH uses it even with a TTY
    4. Cleaning up the script after a delay
    """

    def __init__(self, credential_store: CredentialStore):
        self._cred_store = credential_store
        self._askpass_scripts: dict[str, str] = {}  # connection_id -> script path
        self._askpass_dir = Path(tempfile.mkdtemp(prefix="ssh-cm-askpass-"))
        os.chmod(str(self._askpass_dir), stat.S_IRWXU)  # 0700

        # Register cleanup on process exit
        atexit.register(self.cleanup_all)

    def build_ssh_command(self, connection: Connection) -> list[str]:
        """
        Build the SSH command line from the connection's command field.

        The command field contains the full SSH command as typed in a shell,
        potentially spanning multiple lines.  We join lines and split with
        shlex so that quoted arguments are preserved.

        Port forwards and jump host from structured fields are appended.

        Returns: list of command arguments, e.g. ["ssh", "-p", "22", "user@host"]
        """
        import shlex

        if not connection.command:
            argv = ["ssh"]
        else:
            # Join multi-line commands and split properly
            cmd_str = " ".join(connection.command.strip().splitlines())
            try:
                argv = shlex.split(cmd_str)
            except ValueError:
                # Fallback: naive split
                argv = cmd_str.split()

        # Append port forwards from structured field
        argv = self._append_port_forwards(argv, connection)

        # Append jump host from structured field
        argv = self._append_jump_host(argv, connection)

        return argv

    def _append_port_forwards(
        self, argv: list[str], connection: Connection
    ) -> list[str]:
        """Append -L/-R/-D port forward flags from connection.port_forwards JSON."""
        import json

        if not connection.port_forwards:
            return argv
        try:
            forwards = json.loads(connection.port_forwards)
        except (json.JSONDecodeError, TypeError):
            return argv
        for fwd in forwards:
            fwd_type = fwd.get("type", "L").upper()
            local = fwd.get("local", "")
            remote = fwd.get("remote", "")
            if fwd_type == "D":
                if local:
                    argv.extend(["-D", local])
            elif fwd_type == "R":
                if local and remote:
                    argv.extend(["-R", f"{local}:{remote}"])
            else:
                if local and remote:
                    argv.extend(["-L", f"{local}:{remote}"])
        return argv

    def _append_jump_host(self, argv: list[str], connection: Connection) -> list[str]:
        """Append jump host or proxy command from connection.jump_host.

        If the value looks like a ProxyCommand (starts with 'ssh ' or contains
        '-W %h:%p' or starts with '/' or contains 'ProxyCommand'), use
        -o ProxyCommand=...  Otherwise treat it as a ProxyJump (-J).
        """
        if not connection.jump_host:
            return argv

        jh = connection.jump_host.strip()

        # Detect ProxyCommand patterns
        is_proxy_cmd = (
            jh.startswith("ssh ")
            or jh.startswith("/")
            or "-W " in jh
            or "-W%" in jh
            or "ProxyCommand" in jh
        )

        if is_proxy_cmd:
            # Strip leading "ProxyCommand=" or "ProxyCommand " if user pasted it
            for prefix in ("ProxyCommand=", "ProxyCommand "):
                if jh.startswith(prefix):
                    jh = jh[len(prefix) :].strip()
                    break
            if "-o" not in argv or not any("ProxyCommand" in a for a in argv):
                argv.extend(["-o", f"ProxyCommand={jh}"])
        else:
            # Standard ProxyJump
            if "-J" not in argv:
                argv.extend(["-J", jh])

        return argv

    def build_environment(
        self, connection: Connection
    ) -> tuple[list[str], Optional[str]]:
        """
        Build environment variables for the SSH process.

        Returns: (list of "KEY=VALUE" strings suitable for VTE spawn,
                  session_id for later askpass cleanup or None)
        """
        env = os.environ.copy()
        session_id = None

        # Set TERM
        if connection.term_type:
            env["TERM"] = connection.term_type
        elif "TERM" not in env:
            env["TERM"] = "xterm-256color"

        # Set up SSH_ASKPASS for credential injection
        askpass_result = self._create_askpass_script(connection)
        if askpass_result:
            askpass_script, session_id = askpass_result
            env["SSH_ASKPASS"] = askpass_script
            env["SSH_ASKPASS_REQUIRE"] = "force"
            # DISPLAY must be set for SSH_ASKPASS to work
            if "DISPLAY" not in env:
                env["DISPLAY"] = ":0"

            # If a password is stored, disable SSH agent so SSH_ASKPASS is used
            password = self._cred_store.get_password(connection.id)
            if password:
                env.pop("SSH_AUTH_SOCK", None)

        return [f"{k}={v}" for k, v in env.items()], session_id

    def _create_askpass_script(
        self, connection: Connection
    ) -> Optional[tuple[str, str]]:
        """
        Create a temporary SSH_ASKPASS script for the connection.

        Each call creates a script with a unique session ID so that
        multiple concurrent sessions for the same connection do not
        interfere with each other.

        SSH invokes this script once per prompt, passing the prompt text
        as $1.  The script inspects the prompt to decide what to return:

        * Prompt contains "passphrase" → key passphrase is needed.
          - Uses a counter to return the Nth passphrase from the stored list.
          - When the prompt text changes (different key / hop), the counter
            resets so each hop tries all passphrases from the beginning.
          - After exhausting all stored passphrases, exit 1 so SSH falls
            back to interactive input.
        * Prompt contains "password"  → login password is needed.
          - Return Password if stored, otherwise try stored passphrases
            as fallback (with separate counter / prompt tracking).
        * Any other prompt           → exit 1  (unknown prompt, don't guess).

        Because SSH spawns a **new process** for each prompt, we persist
        state in temp files keyed to the session ID so that successive
        prompts see incrementing counts and detect prompt changes.

        Returns: (script_path, session_id) or None if no credentials.
        """
        password = self._cred_store.get_password(connection.id)
        passphrases = self._cred_store.get_passphrases(connection.id)

        # Merge global passphrases (tried after connection-specific ones)
        global_pps = self._cred_store.get_global_passphrases()
        all_passphrases = list(passphrases)
        for gp in global_pps:
            if gp not in all_passphrases:
                all_passphrases.append(gp)
        passphrases = all_passphrases

        if not password and not passphrases:
            return None

        # Each session gets a unique ID so concurrent connections to the
        # same host each have their own script and state files.
        session_id = uuid.uuid4().hex[:12]

        # State files use session-id prefix so they survive across
        # the separate askpass process invocations for one SSH session.
        # Store in the secure askpass directory (0700) instead of world-readable /tmp
        counter_file = f"{self._askpass_dir}/.askpass-counter-{session_id}"
        prompt_file = f"{self._askpass_dir}/.askpass-prompt-{session_id}"

        # Build the askpass script — always branch on prompt type
        script_lines = [
            "#!/bin/bash",
            'PROMPT="$1"',
            f'COUNTER_FILE="{counter_file}"',
            f'PROMPT_FILE="{prompt_file}"',
            "",
            "# --- passphrase prompt ---",
            'if echo "$PROMPT" | grep -qi "passphrase"; then',
        ]

        if passphrases:
            # If the prompt text changed (different key / hop), reset counter
            script_lines.extend(
                [
                    '    LAST_PROMPT=$(cat "$PROMPT_FILE" 2>/dev/null || echo "")',
                    '    if [ "$PROMPT" != "$LAST_PROMPT" ]; then',
                    '        echo 0 > "$COUNTER_FILE"',
                    "    fi",
                    '    echo "$PROMPT" > "$PROMPT_FILE"',
                    "",
                    '    COUNT=$(cat "$COUNTER_FILE" 2>/dev/null || echo 0)',
                    "    COUNT=$((COUNT + 1))",
                    '    echo "$COUNT" > "$COUNTER_FILE"',
                    "    case $COUNT in",
                ]
            )
            for i, pp in enumerate(passphrases, 1):
                script_lines.append(
                    f'        {i}) echo "{self._escape_for_shell(pp)}" ;;'
                )
            # Beyond stored count, exit 1 so SSH falls back to interactive
            script_lines.append("        *) exit 1 ;;")
            script_lines.append("    esac")
        else:
            # No passphrase stored → fail so SSH falls back to interactive
            script_lines.append("    exit 1")

        # --- password prompt ---
        script_lines.append('elif echo "$PROMPT" | grep -qi "password"; then')
        if password:
            script_lines.append(f'    echo "{self._escape_for_shell(password)}"')
        elif passphrases:
            # No stored password — try passphrases as fallback for password prompts
            # Use a separate counter file so passphrase and password prompt
            # counters don't interfere with each other.
            pw_counter = f"{self._askpass_dir}/.askpass-pw-counter-{session_id}"
            pw_prompt = f"{self._askpass_dir}/.askpass-pw-prompt-{session_id}"
            script_lines.extend(
                [
                    f'    PW_COUNTER_FILE="{pw_counter}"',
                    f'    PW_PROMPT_FILE="{pw_prompt}"',
                    '    PW_LAST=$(cat "$PW_PROMPT_FILE" 2>/dev/null || echo "")',
                    '    if [ "$PROMPT" != "$PW_LAST" ]; then',
                    '        echo 0 > "$PW_COUNTER_FILE"',
                    "    fi",
                    '    echo "$PROMPT" > "$PW_PROMPT_FILE"',
                    '    PW_COUNT=$(cat "$PW_COUNTER_FILE" 2>/dev/null || echo 0)',
                    "    PW_COUNT=$((PW_COUNT + 1))",
                    '    echo "$PW_COUNT" > "$PW_COUNTER_FILE"',
                    "    case $PW_COUNT in",
                ]
            )
            for i, pp in enumerate(passphrases, 1):
                script_lines.append(
                    f'        {i}) echo "{self._escape_for_shell(pp)}" ;;'
                )
            script_lines.append("        *) exit 1 ;;")
            script_lines.append("    esac")
        else:
            script_lines.append("    exit 1")

        # --- anything else → don't guess ---
        script_lines.extend(
            [
                "else",
                "    exit 1",
                "fi",
            ]
        )

        script_content = "\n".join(script_lines) + "\n"

        # Write to temp file with restricted permissions
        script_path = os.path.join(str(self._askpass_dir), f"askpass-{session_id}")

        fd = os.open(
            script_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRWXU
        )  # 0700
        with os.fdopen(fd, "w") as f:
            f.write(script_content)

        self._askpass_scripts[session_id] = script_path
        return script_path, session_id

    def cleanup_askpass(self, session_id: str):
        """Remove the askpass script and state files for a session.

        Args:
            session_id: The unique session ID returned by build_environment().
        """
        if not session_id:
            return
        if session_id in self._askpass_scripts:
            script_path = self._askpass_scripts.pop(session_id)
            try:
                os.unlink(script_path)
            except OSError:
                pass
        # Remove state files from the secure askpass directory
        for suffix in ("counter", "prompt", "pw-counter", "pw-prompt"):
            path = os.path.join(
                str(self._askpass_dir), f".askpass-{suffix}-{session_id}"
            )
            try:
                os.unlink(path)
            except OSError:
                pass

    def cleanup_all(self):
        """Remove all askpass scripts and the temp directory."""
        for script_path in self._askpass_scripts.values():
            try:
                os.unlink(script_path)
            except OSError:
                pass
        self._askpass_scripts.clear()
        try:
            shutil.rmtree(str(self._askpass_dir), ignore_errors=True)
        except OSError:
            pass

    def get_post_login_commands(self, connection: Connection) -> list[str]:
        """
        Parse post-login commands from the connection config.

        Supports delay syntax: ##D=1000 (delay 1000ms before next command)
        Returns list of (command_or_delay) strings.
        """
        if not connection.commands:
            return []

        commands = []
        for line in connection.commands.strip().split("\n"):
            line = line.strip()
            if line:
                commands.append(line)
        return commands

    @staticmethod
    def _escape_for_shell(s: str) -> str:
        """Escape a string for safe inclusion in a shell double-quoted string."""
        return (
            s.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("$", "\\$")
            .replace("`", "\\`")
        )

    @staticmethod
    def get_local_shell_command() -> list[str]:
        """Get the command for opening a local shell."""
        shell = os.environ.get("SHELL", "/bin/bash")
        return [shell]

    # =================================================================
    # SFTP support
    # =================================================================

    def build_sftp_command(self, connection: "Connection") -> list[str]:
        """
        Build an SFTP command from the connection.

        If the connection already has an sftp command, use it directly.
        If it has an SSH command, derive an sftp command from it.
        Otherwise build from structured host/port/username fields.
        """
        import shlex

        # Direct sftp command
        if connection.protocol == "sftp" and connection.command:
            cmd_str = " ".join(connection.command.strip().splitlines())
            try:
                return shlex.split(cmd_str)
            except ValueError:
                return cmd_str.split()

        # Derive from SSH command
        if connection.command:
            try:
                ssh_parts = shlex.split(connection.command)
            except ValueError:
                ssh_parts = connection.command.split()

            # Separate options and positional args (destination).
            # sftp requires all options BEFORE the destination.
            sftp_options = ["sftp"]
            sftp_dest = []

            i = 1  # skip 'ssh'/'sftp'
            while i < len(ssh_parts):
                arg = ssh_parts[i]
                # Skip SSH-only flags (no value)
                if arg in (
                    "-t",
                    "-T",
                    "-N",
                    "-f",
                    "-g",
                    "-n",
                    "-X",
                    "-x",
                    "-Y",
                    "-A",
                    "-a",
                    "-K",
                    "-k",
                ):
                    i += 1
                    continue
                # Skip SSH-only flags (with value)
                if arg in ("-L", "-R", "-D", "-W", "-w", "-e", "-m", "-b"):
                    i += 2
                    continue
                # Convert -p to -P for sftp
                if arg == "-p" and i + 1 < len(ssh_parts):
                    sftp_options.extend(["-P", ssh_parts[i + 1]])
                    i += 2
                    continue
                # Common options with value (valid for both ssh & sftp)
                if arg in ("-i", "-F", "-o", "-S", "-J", "-c") and i + 1 < len(
                    ssh_parts
                ):
                    sftp_options.extend([arg, ssh_parts[i + 1]])
                    i += 2
                    continue
                # Pass through other single flags (-4, -6, -C, -q, -v, etc.)
                if arg.startswith("-"):
                    sftp_options.append(arg)
                    i += 1
                    continue
                # Positional arg = destination
                sftp_dest.append(arg)
                i += 1

            # Options first, then destination(s)
            sftp_options.extend(sftp_dest)
            return sftp_options

        # Build from structured fields
        parts = ["sftp"]
        port = connection.port or 22
        if port != 22:
            parts.extend(["-P", str(port)])
        if connection.extra_options:
            parts.extend(connection.extra_options.split())
        dest = (
            f"{connection.username}@{connection.host}"
            if connection.username
            else connection.host
        )
        parts.append(dest)
        return parts

    def build_sftp_from_ssh(self, ssh_connection: "Connection") -> list[str]:
        """Derive an SFTP command from an existing SSH connection."""
        return self.build_sftp_command(ssh_connection)

    # =================================================================
    # RDP support
    # =================================================================

    def build_rdp_command(self, connection: "Connection") -> list[str]:
        """Build an RDP client command (via launcher script).

        On Linux, creates a launcher script that uses xfreerdp.
        On macOS, this is only used as a fallback (Linux-style).
        """
        host = connection.host or "localhost"
        port = connection.port or 3389
        script = self._create_rdp_script(connection, host, port)
        return ["/bin/bash", script]

    def launch_rdp_macos(self, connection: "Connection") -> bool:
        """Launch RDP on macOS using Windows App (formerly Microsoft Remote Desktop).

        1. Store credentials in macOS Keychain so Windows App reads them
        2. Generate a .rdp file with connection settings
        3. Open the .rdp file with Windows App

        Returns True if launched successfully, False otherwise.
        """
        import subprocess

        host = connection.host or "localhost"
        port = connection.port or 3389
        username = connection.username or ""
        domain = connection.domain or ""
        password = self._cred_store.get_password(connection.id) or ""
        resolution = connection.rdp_resolution or "1920x1080"
        fullscreen = connection.rdp_fullscreen

        res_w = resolution.split("x")[0] if "x" in resolution else "1920"
        res_h = resolution.split("x")[1] if "x" in resolution else "1080"

        # Store credentials in macOS Keychain for Windows App
        if password and username:
            self._store_rdp_keychain_credential(host, port, username, password, domain)

        # Generate .rdp file
        rdp_file = f"/tmp/ssh-cm-rdp-{connection.id[:8]}.rdp"
        rdp_content = self._build_rdp_file_content(
            host, port, username, domain, res_w, res_h, fullscreen
        )

        try:
            with open(rdp_file, "w") as f:
                f.write(rdp_content)
            os.chmod(rdp_file, 0o600)
        except IOError:
            return False

        # Open with Windows App / Microsoft Remote Desktop
        try:
            subprocess.Popen(["open", rdp_file])
            # Schedule cleanup of .rdp file after a delay
            import threading

            def _cleanup():
                import time

                time.sleep(10)
                try:
                    os.unlink(rdp_file)
                except OSError:
                    pass

            threading.Thread(target=_cleanup, daemon=True).start()
            return True
        except (FileNotFoundError, OSError):
            return False

    def _store_rdp_keychain_credential(
        self, host: str, port: int, username: str, password: str, domain: str = ""
    ):
        """Store RDP credentials in macOS Keychain for Windows App.

        Windows App (Microsoft Remote Desktop) reads credentials from
        the macOS Keychain using a specific service name format.
        We use the `security` tool to add/update an internet password.
        """
        import subprocess

        # Full address as used in .rdp file
        server = f"{host}:{port}" if port != 3389 else host
        account = f"{domain}\\{username}" if domain else username

        # Delete any existing entry first (ignore errors)
        subprocess.run(
            [
                "security",
                "delete-internet-password",
                "-s",
                server,
                "-a",
                account,
                "-r",
                "rdp ",
            ],
            capture_output=True,
        )

        # Add the new credential
        # -s: server, -a: account, -w: password, -r: protocol (rdp), -D: kind
        subprocess.run(
            [
                "security",
                "add-internet-password",
                "-s",
                server,
                "-a",
                account,
                "-w",
                password,
                "-r",
                "rdp ",
                "-D",
                "RDP password",
                "-T",
                "",  # Allow access from any app
                "-U",
            ],  # Update if already exists
            capture_output=True,
        )

    def _build_rdp_file_content(
        self,
        host: str,
        port: int,
        username: str,
        domain: str,
        res_w: str,
        res_h: str,
        fullscreen: bool,
    ) -> str:
        """Build the contents of a .rdp file for Windows App."""
        lines = [
            f"full address:s:{host}:{port}",
            f"username:s:{username}" if username else "",
            f"domain:s:{domain}" if domain else "",
            f"desktopwidth:i:{res_w}",
            f"desktopheight:i:{res_h}",
            f"screen mode id:i:{'2' if fullscreen else '1'}",
            "use multimon:i:0",
            "session bpp:i:32",
            "redirectclipboard:i:1",
            "autoreconnection enabled:i:1",
            "authentication level:i:0",
            "prompt for credentials:i:0",
            "negotiate security layer:i:1",
        ]
        return "\n".join(line for line in lines if line) + "\n"

    def _create_rdp_script(self, connection, host, port) -> str:
        """Create a launcher script for RDP connections (Linux only)."""
        password = self._cred_store.get_password(connection.id) or ""
        username = connection.username or ""
        domain = connection.domain or ""
        resolution = connection.rdp_resolution or "1920x1080"
        fullscreen = connection.rdp_fullscreen
        extra = connection.extra_options or ""

        # Build xfreerdp argument line
        xfree_args = [f"/v:{host}:{port}"]
        if username:
            xfree_args.append(f'/u:"{self._escape_for_shell(username)}"')
        if domain:
            xfree_args.append(f'/d:"{self._escape_for_shell(domain)}"')
        if resolution:
            xfree_args.append(f"/size:{resolution}")
        if fullscreen:
            xfree_args.append("/f")
        xfree_args.append("/cert:ignore")
        xfree_args.append("+clipboard")
        if extra:
            xfree_args.append(extra)
        xfree_line = " ".join(xfree_args)

        pw_arg = f'/p:"{self._escape_for_shell(password)}"' if password else ""

        lines = [
            "#!/bin/bash",
            f'echo "=== RDP Connection ==="',
            f'echo "Host: {host}:{port}"',
            f'echo "User: {username or "(interactive)"}"',
            'echo "---"',
            "",
            "# Try FreeRDP",
            "if command -v xfreerdp3 &>/dev/null; then",
            '    echo "Using xfreerdp3 (FreeRDP 3.x)"',
        ]
        if password:
            lines.append(f"    xfreerdp3 {xfree_line} {pw_arg}")
        else:
            lines.append(f"    xfreerdp3 {xfree_line}")
        lines.extend(
            [
                "    EXIT_CODE=$?",
                "elif command -v xfreerdp &>/dev/null; then",
                '    echo "Using xfreerdp (FreeRDP 2.x)"',
            ]
        )
        if password:
            lines.append(f"    xfreerdp {xfree_line} {pw_arg}")
        else:
            lines.append(f"    xfreerdp {xfree_line}")
        lines.extend(
            [
                "    EXIT_CODE=$?",
                "else",
                '    echo "Error: No RDP client found."',
                '    echo "Install FreeRDP:"',
                '    echo "  Ubuntu: sudo apt install freerdp2-x11"',
                '    echo "  Fedora: sudo dnf install freerdp"',
                "    exit 1",
                "fi",
                "",
                'echo "---"',
                'if [ "${EXIT_CODE:-0}" -eq 0 ]; then',
                '    echo "RDP session ended normally."',
                "else",
                '    echo "RDP session ended with exit code $EXIT_CODE"',
                "fi",
            ]
        )

        script_content = "\n".join(lines) + "\n"
        script_path = os.path.join(
            str(self._askpass_dir), f"rdp-launch-{connection.id[:8]}"
        )
        fd = os.open(script_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRWXU)
        with os.fdopen(fd, "w") as f:
            f.write(script_content)
        return script_path

    # =================================================================
    # VNC support
    # =================================================================

    def build_vnc_command(self, connection: "Connection") -> list[str]:
        """Build a VNC viewer command (via launcher script)."""
        host = connection.host or "localhost"
        port = connection.port or 5900
        script = self._create_vnc_script(connection, host, port)
        return ["/bin/bash", script]

    def _create_vnc_script(self, connection, host, port) -> str:
        """Create a launcher script for VNC connections."""
        password = self._cred_store.get_password(connection.id) or ""
        extra = connection.extra_options or ""

        import sys

        is_mac = sys.platform == "darwin"

        # VNC password file (for TigerVNC)
        passwd_file = f"/tmp/ssh-cm-vnc-{connection.id[:8]}.passwd"

        lines = [
            "#!/bin/bash",
            f'echo "=== VNC Connection ==="',
            f'echo "Host: {host}:{port}"',
            'echo "---"',
            "",
        ]

        if password:
            lines.extend(
                [
                    "# Prepare VNC password file for TigerVNC",
                    f'VNCP="{passwd_file}"',
                    "if command -v vncpasswd &>/dev/null; then",
                    f'    echo "{self._escape_for_shell(password)}" | vncpasswd -f > "$VNCP" 2>/dev/null',
                    '    chmod 600 "$VNCP"',
                    '    PASSWD_FLAG="-passwd $VNCP"',
                    "else",
                    '    PASSWD_FLAG=""',
                    "fi",
                    "",
                ]
            )
        else:
            lines.append('PASSWD_FLAG=""')
            lines.append("")

        lines.extend(
            [
                "if command -v vncviewer &>/dev/null; then",
                '    echo "Using vncviewer"',
                f"    vncviewer {host}:{port} $PASSWD_FLAG {extra}",
                "    EXIT_CODE=$?",
                "elif command -v xtigervncviewer &>/dev/null; then",
                '    echo "Using xtigervncviewer"',
                f"    xtigervncviewer {host}:{port} $PASSWD_FLAG {extra}",
                "    EXIT_CODE=$?",
            ]
        )

        if is_mac:
            vnc_url = f"vnc://{host}:{port}"
            lines.extend(
                [
                    "else",
                    '    echo "No CLI VNC viewer found. Opening macOS Screen Sharing..."',
                    f'    open "{vnc_url}"',
                    '    echo ""',
                    '    echo "Screen Sharing should open shortly."',
                    "    EXIT_CODE=0",
                ]
            )
        else:
            lines.extend(
                [
                    "else",
                    '    echo "Error: No VNC viewer found."',
                    '    echo "Install a VNC viewer:"',
                    '    echo "  Ubuntu: sudo apt install tigervnc-viewer"',
                    '    echo "  Fedora: sudo dnf install tigervnc"',
                    '    echo "  macOS:  brew install tiger-vnc"',
                    "    exit 1",
                ]
            )

        lines.extend(
            [
                "fi",
                "",
            ]
        )

        if password:
            lines.append(f'rm -f "{passwd_file}"')

        lines.extend(
            [
                'echo "---"',
                'if [ "${EXIT_CODE:-0}" -eq 0 ]; then',
                '    echo "VNC session ended normally."',
                "else",
                '    echo "VNC session ended with exit code $EXIT_CODE"',
                "fi",
            ]
        )

        script_content = "\n".join(lines) + "\n"
        script_path = os.path.join(
            str(self._askpass_dir), f"vnc-launch-{connection.id[:8]}"
        )
        fd = os.open(script_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRWXU)
        with os.fdopen(fd, "w") as f:
            f.write(script_content)
        return script_path
