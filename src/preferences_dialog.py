"""
Preferences dialog.

Provides UI for configuring terminal appearance, behavior, and shortcuts.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, Gdk, GObject, GLib
from .config import Config
from .utils import block_scroll


class PreferencesDialog(Adw.Window):
    """Application preferences window."""

    __gsignals__ = {
        "preferences-applied": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, parent: Gtk.Window, config: Config):
        super().__init__(
            transient_for=parent,
            modal=True,
            default_width=500,
            default_height=600,
            title="Preferences",
        )

        self.config = config

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Header bar
        header = Adw.HeaderBar()
        main_box.append(header)

        # Scrolled content
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(12)
        content.set_margin_end(12)

        # === Terminal Section ===
        content.append(self._section_label("Terminal"))

        # Font
        self.entry_font = Gtk.Entry(text=config["terminal_font"])
        content.append(self._pref_row("Font:", self.entry_font))

        # Scrollback lines
        self.spin_scrollback = Gtk.SpinButton()
        self.spin_scrollback.set_range(100, 1000000)
        self.spin_scrollback.set_value(config["terminal_scrollback_lines"])
        self.spin_scrollback.set_increments(100, 1000)
        block_scroll(self.spin_scrollback)
        content.append(self._pref_row("Scrollback Lines:", self.spin_scrollback))

        # Background color
        self.color_bg = Gtk.ColorButton()
        bg = Gdk.RGBA()
        bg.parse(config["terminal_bg_color"])
        self.color_bg.set_rgba(bg)
        content.append(self._pref_row("Background Color:", self.color_bg))

        # Foreground color
        self.color_fg = Gtk.ColorButton()
        fg = Gdk.RGBA()
        fg.parse(config["terminal_fg_color"])
        self.color_fg.set_rgba(fg)
        content.append(self._pref_row("Foreground Color:", self.color_fg))

        # Cursor shape
        self.combo_cursor = Gtk.ComboBoxText()
        self.combo_cursor.append("block", "Block")
        self.combo_cursor.append("ibeam", "IBeam")
        self.combo_cursor.append("underline", "Underline")
        self.combo_cursor.set_active_id(config["terminal_cursor_shape"])
        block_scroll(self.combo_cursor)
        content.append(self._pref_row("Cursor Shape:", self.combo_cursor))

        # Bold
        self.switch_bold = Gtk.Switch()
        self.switch_bold.set_active(config["terminal_allow_bold"])
        self.switch_bold.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Allow Bold:", self.switch_bold))

        # Bell
        self.switch_bell = Gtk.Switch()
        self.switch_bell.set_active(config["terminal_audible_bell"])
        self.switch_bell.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Audible Bell:", self.switch_bell))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === SSH Section ===
        content.append(self._section_label("SSH"))

        self.spin_default_port = Gtk.SpinButton()
        self.spin_default_port.set_range(1, 65535)
        self.spin_default_port.set_value(config["ssh_default_port"])
        self.spin_default_port.set_increments(1, 10)
        block_scroll(self.spin_default_port)
        content.append(self._pref_row("Default Port:", self.spin_default_port))

        self.spin_keepalive = Gtk.SpinButton()
        self.spin_keepalive.set_range(0, 3600)
        self.spin_keepalive.set_value(config["ssh_keepalive_interval"])
        self.spin_keepalive.set_increments(10, 60)
        block_scroll(self.spin_keepalive)
        content.append(self._pref_row("Keepalive Interval:", self.spin_keepalive))

        self.spin_timeout = Gtk.SpinButton()
        self.spin_timeout.set_range(5, 300)
        self.spin_timeout.set_value(config["ssh_connection_timeout"])
        self.spin_timeout.set_increments(5, 30)
        block_scroll(self.spin_timeout)
        content.append(self._pref_row("Connection Timeout:", self.spin_timeout))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === Behavior Section ===
        content.append(self._section_label("Behavior"))

        self.switch_confirm_close_tab = Gtk.Switch()
        self.switch_confirm_close_tab.set_active(config["confirm_close_tab"])
        self.switch_confirm_close_tab.set_halign(Gtk.Align.START)
        content.append(
            self._pref_row("Confirm Close Tab:", self.switch_confirm_close_tab)
        )

        self.switch_confirm_close_window = Gtk.Switch()
        self.switch_confirm_close_window.set_active(config["confirm_close_window"])
        self.switch_confirm_close_window.set_halign(Gtk.Align.START)
        content.append(
            self._pref_row("Confirm Close Window:", self.switch_confirm_close_window)
        )

        self.switch_tab_close_btn = Gtk.Switch()
        self.switch_tab_close_btn.set_active(config["show_tab_close_button"])
        self.switch_tab_close_btn.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Tab Close Button:", self.switch_tab_close_btn))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === Logging Section ===
        content.append(self._section_label("Terminal Logging"))

        self.switch_logging = Gtk.Switch()
        self.switch_logging.set_active(config.get("terminal_logging_enabled", False))
        self.switch_logging.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Auto-Log Sessions:", self.switch_logging))

        self.entry_log_dir = Gtk.Entry()
        self.entry_log_dir.set_text(config.get("terminal_log_directory", ""))
        self.entry_log_dir.set_placeholder_text("~/ssh-logs")
        content.append(self._pref_row("Log Directory:", self.entry_log_dir))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === Session Recording Section ===
        content.append(self._section_label("Session Recording"))

        self.entry_rec_dir = Gtk.Entry()
        self.entry_rec_dir.set_text(config.get("session_recordings_directory", ""))
        self.entry_rec_dir.set_placeholder_text(
            "~/Documents/SSHClientManager-Recordings"
        )
        content.append(self._pref_row("Recordings Directory:", self.entry_rec_dir))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === Notifications Section ===
        content.append(self._section_label("Notifications"))

        self.switch_notify = Gtk.Switch()
        self.switch_notify.set_active(config.get("notify_on_completion", True))
        self.switch_notify.set_halign(Gtk.Align.START)
        content.append(self._pref_row("Notify on Close:", self.switch_notify))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === AI Assistant Section ===
        content.append(self._section_label("AI Assistant"))

        ai_hint = Gtk.Label(
            label="Supports OpenAI, Anthropic Claude, and any\n"
            "OpenAI-compatible API (Ollama, LM Studio, Groq, etc.)."
        )
        ai_hint.set_xalign(0)
        ai_hint.add_css_class("dim-label")
        ai_hint.set_margin_start(8)
        content.append(ai_hint)

        # Provider selector
        self.combo_ai_provider = Gtk.ComboBoxText()
        self.combo_ai_provider.append("openai", "OpenAI / Compatible")
        self.combo_ai_provider.append("claude", "Anthropic Claude")
        self.combo_ai_provider.set_active_id(config.get("ai_provider", "openai"))
        self.combo_ai_provider.connect("changed", self._on_ai_provider_changed)
        block_scroll(self.combo_ai_provider)
        content.append(self._pref_row("Provider:", self.combo_ai_provider))

        self.entry_ai_key = Gtk.PasswordEntry()
        self.entry_ai_key.set_show_peek_icon(True)
        self.entry_ai_key.set_text(config.get("ai_api_key", ""))
        try:
            self.entry_ai_key.props.placeholder_text = "sk-… / sk-ant-…"
        except Exception:
            pass
        content.append(self._pref_row("API Key:", self.entry_ai_key))

        self.entry_ai_base_url = Gtk.Entry()
        self.entry_ai_base_url.set_text(
            config.get("ai_base_url", "https://api.openai.com/v1")
        )
        self.entry_ai_base_url.set_placeholder_text("https://api.openai.com/v1")
        content.append(self._pref_row("API Base URL:", self.entry_ai_base_url))

        # Model: ComboBoxText with entry (user can type or pick from fetched list)
        self._ai_model_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.combo_ai_model = Gtk.ComboBoxText.new_with_entry()
        self.combo_ai_model.set_hexpand(True)
        block_scroll(self.combo_ai_model)
        entry_child = self.combo_ai_model.get_child()
        if entry_child:
            entry_child.set_text(config.get("ai_model", "gpt-4o-mini"))
            entry_child.set_placeholder_text("gpt-4o-mini")
        self._ai_model_row.append(self.combo_ai_model)

        btn_fetch_models = Gtk.Button(label="Fetch")
        btn_fetch_models.set_tooltip_text("Fetch available models from API")
        btn_fetch_models.add_css_class("flat")
        btn_fetch_models.connect("clicked", self._on_fetch_models)
        self._ai_model_row.append(btn_fetch_models)
        self._btn_fetch_models = btn_fetch_models
        content.append(self._pref_row("Model:", self._ai_model_row))

        # Test Connection button
        self._test_result_label = Gtk.Label()
        self._test_result_label.set_xalign(0)
        self._test_result_label.set_wrap(True)
        self._test_result_label.set_max_width_chars(50)
        self._test_result_label.set_visible(False)

        btn_test = Gtk.Button(label="🔗 Test Connection")
        btn_test.set_tooltip_text("Send a test request to verify API connection")
        btn_test.add_css_class("flat")
        btn_test.connect("clicked", self._on_test_connection)
        self._btn_test = btn_test

        test_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        test_box.append(btn_test)
        test_box.append(self._test_result_label)
        content.append(self._pref_row("", test_box))

        # System prompt
        self.tv_ai_system_prompt = Gtk.TextView()
        self.tv_ai_system_prompt.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.tv_ai_system_prompt.set_monospace(True)
        self.tv_ai_system_prompt.get_buffer().set_text(
            config.get("ai_system_prompt", "")
        )
        prompt_scroll = Gtk.ScrolledWindow()
        prompt_scroll.set_min_content_height(80)
        prompt_scroll.set_max_content_height(150)
        prompt_scroll.set_child(self.tv_ai_system_prompt)
        prompt_scroll.set_hexpand(True)
        prompt_frame = Gtk.Frame()
        prompt_frame.set_child(prompt_scroll)
        content.append(self._pref_row("System Prompt:", prompt_frame))

        prompt_hint = Gtk.Label(
            label="Custom system prompt for AI. Leave empty to use the\n"
            "built-in default (Linux/SSH expert assistant)."
        )
        prompt_hint.set_xalign(0)
        prompt_hint.add_css_class("dim-label")
        prompt_hint.set_margin_start(160)
        content.append(prompt_hint)

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === Screenshot Section ===
        content.append(self._section_label("Screenshot"))

        self.entry_watermark = Gtk.Entry()
        self.entry_watermark.set_text(config.get("screenshot_watermark", ""))
        self.entry_watermark.set_placeholder_text("Watermark text (empty = disabled)")
        content.append(self._pref_row("Watermark:", self.entry_watermark))

        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # === Global Passphrases Section ===
        content.append(self._section_label("Global Passphrases"))

        pp_hint = Gtk.Label(
            label="These passphrases will be tried automatically for ALL\n"
            "SSH key prompts (after any connection-specific ones)."
        )
        pp_hint.set_xalign(0)
        pp_hint.add_css_class("dim-label")
        pp_hint.set_margin_start(8)
        content.append(pp_hint)

        self._global_pp_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._global_pp_box.set_margin_start(8)
        self._global_pp_box.set_margin_end(8)
        self._global_pp_entries: list[Gtk.PasswordEntry] = []
        content.append(self._global_pp_box)

        btn_add_pp = Gtk.Button(label="+ Add Passphrase")
        btn_add_pp.add_css_class("flat")
        btn_add_pp.set_halign(Gtk.Align.START)
        btn_add_pp.set_margin_start(8)
        btn_add_pp.connect("clicked", lambda _: self._add_global_pp_row())
        content.append(btn_add_pp)

        # Load existing global passphrases
        self._credential_store = None  # will be set by caller
        # We'll initialize the rows after construction via init_global_passphrases()

        # === Apply/Cancel Buttons ===
        content.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        btn_box.set_halign(Gtk.Align.END)
        btn_box.set_margin_top(8)

        btn_cancel = Gtk.Button(label="Cancel")
        btn_cancel.connect("clicked", lambda _: self.close())
        btn_box.append(btn_cancel)

        btn_apply = Gtk.Button(label="Apply")
        btn_apply.add_css_class("suggested-action")
        btn_apply.connect("clicked", self._on_apply)
        btn_box.append(btn_apply)

        content.append(btn_box)

        scrolled.set_child(content)
        main_box.append(scrolled)
        self.set_content(main_box)

    def _section_label(self, text: str) -> Gtk.Label:
        """Create a section heading label."""
        label = Gtk.Label(label=text)
        label.set_xalign(0)
        label.add_css_class("heading")
        label.set_margin_top(8)
        return label

    def _pref_row(self, label_text: str, widget: Gtk.Widget) -> Gtk.Box:
        """Create a preference row with label and widget."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        row.set_margin_start(8)
        row.set_margin_end(8)

        label = Gtk.Label(label=label_text)
        label.set_xalign(1)
        label.set_size_request(150, -1)
        label.add_css_class("dim-label")
        row.append(label)

        widget.set_hexpand(True)
        row.append(widget)
        return row

    # --- Global Passphrase Rows ---

    def _add_global_pp_row(self, value: str = ""):
        """Add a global passphrase entry row."""
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        entry = Gtk.PasswordEntry()
        entry.set_show_peek_icon(True)
        entry.set_hexpand(True)
        if value:
            entry.set_text(value)
        try:
            entry.props.placeholder_text = (
                f"Passphrase {len(self._global_pp_entries) + 1}"
            )
        except Exception:
            pass
        row.append(entry)

        btn_remove = Gtk.Button(label="✕")
        btn_remove.add_css_class("flat")
        btn_remove.add_css_class("circular")
        btn_remove.connect(
            "clicked", lambda _, r=row, e=entry: self._remove_global_pp_row(r, e)
        )
        row.append(btn_remove)

        self._global_pp_entries.append(entry)
        self._global_pp_box.append(row)

    def _remove_global_pp_row(self, row, entry):
        """Remove a global passphrase entry row."""
        if entry in self._global_pp_entries:
            self._global_pp_entries.remove(entry)
        self._global_pp_box.remove(row)

    def init_global_passphrases(self, credential_store):
        """Initialize global passphrase rows from the credential store."""
        self._credential_store = credential_store
        existing = credential_store.get_global_passphrases()
        for pp in existing:
            self._add_global_pp_row(pp)
        if not existing:
            self._add_global_pp_row()  # one empty row by default

    def _on_apply(self, button):
        """Apply and save all preferences."""
        cfg = self.config

        cfg.batch_update(
            {
                "terminal_font": self.entry_font.get_text(),
                "terminal_scrollback_lines": int(self.spin_scrollback.get_value()),
                "terminal_bg_color": self.color_bg.get_rgba().to_string(),
                "terminal_fg_color": self.color_fg.get_rgba().to_string(),
                "terminal_cursor_shape": self.combo_cursor.get_active_id() or "block",
                "terminal_allow_bold": self.switch_bold.get_active(),
                "terminal_audible_bell": self.switch_bell.get_active(),
                "ssh_default_port": int(self.spin_default_port.get_value()),
                "ssh_keepalive_interval": int(self.spin_keepalive.get_value()),
                "ssh_connection_timeout": int(self.spin_timeout.get_value()),
                "confirm_close_tab": self.switch_confirm_close_tab.get_active(),
                "confirm_close_window": self.switch_confirm_close_window.get_active(),
                "show_tab_close_button": self.switch_tab_close_btn.get_active(),
                "terminal_logging_enabled": self.switch_logging.get_active(),
                "terminal_log_directory": self.entry_log_dir.get_text().strip(),
                "session_recordings_directory": self.entry_rec_dir.get_text().strip(),
                "notify_on_completion": self.switch_notify.get_active(),
                # AI Assistant
                "ai_provider": self.combo_ai_provider.get_active_id() or "openai",
                "ai_api_key": self.entry_ai_key.get_text().strip(),
                "ai_base_url": self._normalize_url(
                    self.entry_ai_base_url.get_text().strip()
                )
                or "https://api.openai.com/v1",
                "ai_model": self._get_ai_model_text() or "gpt-4o-mini",
                "ai_system_prompt": self._get_system_prompt_text(),
                # Screenshot
                "screenshot_watermark": self.entry_watermark.get_text().strip(),
            }
        )

        # Save global passphrases
        if self._credential_store is not None:
            pps = [
                e.get_text() for e in self._global_pp_entries if e.get_text().strip()
            ]
            self._credential_store.store_global_passphrases(pps)

        self.emit("preferences-applied")
        self.close()

    # --- AI helper methods ---

    @staticmethod
    def _normalize_url(url: str) -> str:
        """Auto-prepend https:// if no scheme is present."""
        if not url:
            return url
        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
        return url.rstrip("/")

    def _get_ai_model_text(self) -> str:
        """Get the current model text from the combo entry."""
        entry = self.combo_ai_model.get_child()
        return entry.get_text().strip() if entry else ""

    def _get_system_prompt_text(self) -> str:
        """Get the system prompt text from the TextView buffer."""
        buf = self.tv_ai_system_prompt.get_buffer()
        start = buf.get_start_iter()
        end = buf.get_end_iter()
        return buf.get_text(start, end, False).strip()

    def _on_ai_provider_changed(self, combo):
        """When provider changes, update the base URL placeholder."""
        provider = combo.get_active_id()
        if provider == "claude":
            self.entry_ai_base_url.set_placeholder_text("https://api.anthropic.com")
            # If the URL is still the OpenAI default, switch it
            current = self.entry_ai_base_url.get_text().strip()
            if not current or current == "https://api.openai.com/v1":
                self.entry_ai_base_url.set_text("https://api.anthropic.com")
        else:
            self.entry_ai_base_url.set_placeholder_text("https://api.openai.com/v1")
            current = self.entry_ai_base_url.get_text().strip()
            if not current or current == "https://api.anthropic.com":
                self.entry_ai_base_url.set_text("https://api.openai.com/v1")

    def _on_fetch_models(self, button):
        """Fetch available models from the API and populate the dropdown."""
        import threading

        api_key = self.entry_ai_key.get_text().strip()
        base_url = (
            self._normalize_url(self.entry_ai_base_url.get_text().strip())
            or "https://api.openai.com/v1"
        )
        provider = self.combo_ai_provider.get_active_id() or "openai"

        if not api_key:
            self._show_fetch_error("Please enter an API key first.")
            return

        button.set_sensitive(False)
        button.set_label("…")

        def _fetch():
            models = []
            error = ""
            try:
                if provider == "claude":
                    # Anthropic has no list-models endpoint; provide known models
                    models = [
                        "claude-sonnet-4-20250514",
                        "claude-opus-4-20250514",
                        "claude-3-7-sonnet-latest",
                        "claude-3-5-haiku-latest",
                        "claude-3-5-sonnet-latest",
                        "claude-3-haiku-20240307",
                    ]
                else:
                    import json
                    import urllib.request
                    import urllib.error

                    url = base_url.rstrip("/") + "/models"
                    req = urllib.request.Request(
                        url,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                        },
                        method="GET",
                    )
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        data = json.loads(resp.read().decode("utf-8"))
                    raw = data.get("data", data if isinstance(data, list) else [])
                    for m in raw:
                        mid = m.get("id", "") if isinstance(m, dict) else str(m)
                        if mid:
                            models.append(mid)
                    models.sort()
            except Exception as exc:
                error = str(exc)

            GLib.idle_add(self._on_models_fetched, models, error)

        threading.Thread(target=_fetch, daemon=True).start()

    def _on_models_fetched(self, models, error):
        """Populate the model combo with fetched models."""
        self._btn_fetch_models.set_sensitive(True)
        self._btn_fetch_models.set_label("Fetch")
        if error and not models:
            self._show_fetch_error(f"Failed to fetch models:\n{error}")
            return
        # Preserve current text
        current = self._get_ai_model_text()
        self.combo_ai_model.remove_all()
        for m in models:
            self.combo_ai_model.append_text(m)
        # Restore / set current text
        entry = self.combo_ai_model.get_child()
        if entry:
            entry.set_text(current if current else (models[0] if models else ""))
        return False

    def _show_fetch_error(self, msg: str):
        """Show a brief error label below the model row."""
        err = Gtk.Label(label=msg)
        err.set_xalign(0)
        err.add_css_class("error")
        err.set_margin_start(160)
        parent = self._ai_model_row.get_parent()
        if parent:
            parent.insert_child_after(err, self._ai_model_row.get_parent())
        # Auto-remove after 5 seconds
        GLib.timeout_add(5000, lambda: err.unparent() if err.get_parent() else None)

    def _on_test_connection(self, button):
        """Test the AI API connection with a simple request."""
        import threading

        api_key = self.entry_ai_key.get_text().strip()
        base_url = (
            self._normalize_url(self.entry_ai_base_url.get_text().strip())
            or "https://api.openai.com/v1"
        )
        model = self._get_ai_model_text() or "gpt-4o-mini"
        provider = self.combo_ai_provider.get_active_id() or "openai"

        if not api_key:
            self._test_result_label.set_label("❌ Please enter an API key first.")
            self._test_result_label.remove_css_class("success")
            self._test_result_label.add_css_class("error")
            self._test_result_label.set_visible(True)
            return

        button.set_sensitive(False)
        button.set_label("⏳ Testing…")
        self._test_result_label.set_visible(False)

        def _test():
            import json
            import urllib.request
            import urllib.error
            import time

            start = time.time()
            ok = False
            msg = ""

            try:
                is_claude = provider == "claude" or (
                    "anthropic.com" in base_url.lower() or "claude" in base_url.lower()
                )

                if is_claude:
                    url = base_url.rstrip("/")
                    if not url.endswith("/messages"):
                        url = url.rstrip("/") + "/v1/messages"
                    payload = json.dumps(
                        {
                            "model": model,
                            "max_tokens": 32,
                            "messages": [{"role": "user", "content": "Say OK"}],
                        }
                    ).encode("utf-8")
                    req = urllib.request.Request(
                        url,
                        data=payload,
                        headers={
                            "Content-Type": "application/json",
                            "x-api-key": api_key,
                            "anthropic-version": "2023-06-01",
                        },
                        method="POST",
                    )
                else:
                    url = base_url.rstrip("/") + "/chat/completions"
                    payload = json.dumps(
                        {
                            "model": model,
                            "messages": [{"role": "user", "content": "Say OK"}],
                            "max_tokens": 32,
                        }
                    ).encode("utf-8")
                    req = urllib.request.Request(
                        url,
                        data=payload,
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {api_key}",
                        },
                        method="POST",
                    )

                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    elapsed = time.time() - start

                    if is_claude:
                        blocks = data.get("content", [])
                        reply = " ".join(
                            b["text"] for b in blocks if b.get("type") == "text"
                        )
                    else:
                        reply = data["choices"][0]["message"]["content"]

                    ok = True
                    msg = (
                        f"✅ Connection OK!  ({elapsed:.1f}s)\n"
                        f"Model: {model}\n"
                        f"Response: {reply.strip()[:80]}"
                    )

            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                try:
                    err_detail = json.loads(body).get("error", {}).get("message", body)
                except Exception:
                    err_detail = body
                msg = f"❌ HTTP {e.code}: {err_detail[:200]}"
            except Exception as exc:
                msg = f"❌ Error: {exc}"

            GLib.idle_add(self._on_test_result, ok, msg)

        threading.Thread(target=_test, daemon=True).start()

    def _on_test_result(self, ok: bool, msg: str):
        """Display the test connection result."""
        self._btn_test.set_sensitive(True)
        self._btn_test.set_label("🔗 Test Connection")
        self._test_result_label.set_label(msg)
        self._test_result_label.remove_css_class("error")
        self._test_result_label.remove_css_class("success")
        if ok:
            self._test_result_label.add_css_class("success")
        else:
            self._test_result_label.add_css_class("error")
        self._test_result_label.set_visible(True)
        return False
