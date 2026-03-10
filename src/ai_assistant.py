"""
AI Assistant side panel for SSH Client Manager.

Provides an AI-powered chat panel that can analyse terminal output,
explain commands, interpret error messages, and answer Linux/SSH questions.

Supports OpenAI and Anthropic Claude (selectable via provider config).
Also works with any OpenAI-compatible endpoint (Ollama, LM Studio,
Groq, Mistral, local models, etc.).

Features:
- Markdown rendering in responses (headers, bold, code blocks, inline code, lists)
- One-click paste of shell commands from code blocks to the active terminal
- Quote terminal selection into the chat input
- Edit any historical user message to rewind and re-send from that point
- Configurable system prompt via Preferences

The panel is embedded in the main window as a collapsible right-side
panel (similar to VS Code's Copilot Chat).

Version: 3.2.0
"""

import gi
import threading
import json
import re
import urllib.request
import urllib.error

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Adw, GLib, GObject, Pango, Gdk

from .config import Config


def _is_claude_endpoint(base_url: str) -> bool:
    """Return True when *base_url* points to the Anthropic / Claude API."""
    url = base_url.lower()
    return "anthropic.com" in url or "claude" in url


class AIChatPanel(Gtk.Box):
    """
    Collapsible AI chat panel designed to be embedded in a Gtk.Paned on
    the right side of the main window.
    """

    __gsignals__ = {
        "close-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "paste-to-terminal": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, config: Config):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)

        self.config = config
        self.context_text: str = ""
        self._messages: list[dict] = []  # {"role":..., "content":...}
        self._bubble_widgets: list[dict] = (
            []
        )  # {"role":..., "widget": outer_box, "msg_index": int}
        self._typing_indicator: Gtk.Widget | None = None

        self.set_size_request(320, -1)
        self._build_ui()

    # ──────────────────────────────────────────────
    # UI
    # ──────────────────────────────────────────────

    def _build_ui(self):
        # ── Header row ──
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.set_margin_top(6)
        header.set_margin_bottom(4)
        header.set_margin_start(10)
        header.set_margin_end(6)

        title = Gtk.Label(label="AI Assistant")
        title.add_css_class("heading")
        title.set_hexpand(True)
        title.set_xalign(0)
        header.append(title)

        btn_clear = Gtk.Button(icon_name="edit-clear-all-symbolic")
        btn_clear.set_tooltip_text("Clear conversation")
        btn_clear.add_css_class("flat")
        btn_clear.add_css_class("circular")
        btn_clear.connect("clicked", self._on_clear)
        header.append(btn_clear)

        btn_close = Gtk.Button(icon_name="window-close-symbolic")
        btn_close.set_tooltip_text("Close panel")
        btn_close.add_css_class("flat")
        btn_close.add_css_class("circular")
        btn_close.connect("clicked", lambda _: self.emit("close-requested"))
        header.append(btn_close)

        self.append(header)
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Chat history ──
        self._scroll = Gtk.ScrolledWindow()
        self._scroll.set_vexpand(True)
        self._scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._chat_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self._chat_box.set_margin_top(8)
        self._chat_box.set_margin_bottom(8)
        self._chat_box.set_margin_start(10)
        self._chat_box.set_margin_end(10)

        self._scroll.set_child(self._chat_box)
        self.append(self._scroll)

        # ── Separator ──
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # ── Quote reference area (hidden by default) ──
        self._quote_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._quote_box.set_margin_start(10)
        self._quote_box.set_margin_end(10)
        self._quote_box.set_margin_top(6)
        self._quote_box.set_visible(False)

        quote_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        quote_icon = Gtk.Label(label="📎 Terminal Reference")
        quote_icon.add_css_class("dim-label")
        quote_icon.set_hexpand(True)
        quote_icon.set_xalign(0)
        quote_header.append(quote_icon)

        btn_clear_quote = Gtk.Button(icon_name="window-close-symbolic")
        btn_clear_quote.add_css_class("flat")
        btn_clear_quote.add_css_class("circular")
        btn_clear_quote.set_tooltip_text("Remove reference")
        btn_clear_quote.connect("clicked", self._on_clear_quote)
        quote_header.append(btn_clear_quote)
        self._quote_box.append(quote_header)

        self._quote_label = Gtk.Label()
        self._quote_label.set_xalign(0)
        self._quote_label.set_wrap(True)
        self._quote_label.set_max_width_chars(40)
        self._quote_label.set_ellipsize(Pango.EllipsizeMode.END)
        self._quote_label.set_lines(4)
        self._quote_label.add_css_class("monospace")
        self._quote_label.add_css_class("dim-label")
        quote_scroll = Gtk.ScrolledWindow()
        quote_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        quote_scroll.set_max_content_height(80)
        quote_scroll.set_child(self._quote_label)
        self._quote_box.append(quote_scroll)

        self.append(self._quote_box)
        self._pending_quote: str = ""
        self._selection_provider = None  # callable(callback) -> callback(text)

        # ── Input row ──
        input_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        input_box.set_margin_top(6)
        input_box.set_margin_bottom(8)
        input_box.set_margin_start(10)
        input_box.set_margin_end(10)

        self._entry = Gtk.Entry()
        self._entry.set_hexpand(True)
        self._entry.set_placeholder_text(
            "Select terminal text and Ask about terminal AI"
        )
        self._entry.connect("activate", self._on_send)
        input_box.append(self._entry)

        self._btn_send = Gtk.Button(label="Send")
        self._btn_send.add_css_class("suggested-action")
        self._btn_send.connect("clicked", self._on_send)
        input_box.append(self._btn_send)

        self.append(input_box)

    # ──────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────

    def set_context(self, text: str):
        """Replace the current terminal context."""
        self.context_text = (text or "").strip()
        first = self._chat_box.get_first_child()
        if first is not None and isinstance(first, Gtk.Expander):
            self._chat_box.remove(first)
        if self.context_text:
            self._add_context_bubble(self.context_text)

    def focus_input(self):
        """Give keyboard focus to the input entry."""
        self._entry.grab_focus()

    def set_selection_provider(self, provider):
        """Set a callable that provides terminal selection asynchronously.

        ``provider(callback)`` should call ``callback(text)`` with the
        currently selected terminal text (or empty string).
        """
        self._selection_provider = provider

    def quote_terminal_selection(self, text: str):
        """Set the quoted terminal selection as a reference block above the input."""
        if not text or not text.strip():
            return
        self._pending_quote = text.strip()
        display = self._pending_quote
        if len(display) > 500:
            display = display[:500] + "…"
        self._quote_label.set_text(display)
        self._quote_box.set_visible(True)
        self._entry.grab_focus()

    def _on_clear_quote(self, *_):
        """Clear the pending quote reference."""
        self._pending_quote = ""
        self._quote_label.set_text("")
        self._quote_box.set_visible(False)

    # ──────────────────────────────────────────────
    # Chat bubble helpers
    # ──────────────────────────────────────────────

    def _add_context_bubble(self, text: str):
        display = text if len(text) <= 3000 else text[:3000] + "\n…[truncated]"
        expander = Gtk.Expander(label="📋 Terminal context")
        expander.set_margin_bottom(4)
        ctx_label = Gtk.Label(label=display)
        ctx_label.set_selectable(True)
        ctx_label.set_xalign(0)
        ctx_label.set_wrap(True)
        ctx_label.set_max_width_chars(60)
        ctx_label.add_css_class("monospace")
        ctx_label.set_margin_top(4)
        expander.set_child(ctx_label)
        first = self._chat_box.get_first_child()
        if first:
            self._chat_box.insert_child_after(expander, None)
        else:
            self._chat_box.append(expander)

    def _add_system_message(self, text: str):
        label = Gtk.Label(label=text)
        label.set_selectable(True)
        label.set_xalign(0)
        label.set_wrap(True)
        label.set_max_width_chars(60)
        label.add_css_class("dim-label")
        label.set_margin_top(4)
        label.set_margin_bottom(4)
        self._chat_box.append(label)
        self._scroll_to_bottom()

    def _add_bubble(self, role: str, text: str, msg_index: int = -1):
        """Add a chat bubble. For assistant role, render markdown."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        outer.set_margin_top(2)
        outer.set_margin_bottom(2)

        # Horizontal alignment container
        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        inner.set_margin_top(8)
        inner.set_margin_bottom(8)
        inner.set_margin_start(10)
        inner.set_margin_end(10)

        if role == "assistant":
            self._render_markdown_into(inner, text)
        else:
            label = Gtk.Label(label=text)
            label.set_selectable(True)
            label.set_xalign(0)
            label.set_yalign(0)
            label.set_wrap(True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            label.set_max_width_chars(55)
            inner.append(label)

        frame = Gtk.Frame()
        frame.set_child(inner)

        if role == "user":
            hbox.set_halign(Gtk.Align.END)
            frame.add_css_class("card")
        else:
            hbox.set_halign(Gtk.Align.START)

        hbox.append(frame)
        outer.append(hbox)

        # Edit button for user messages
        if role == "user" and msg_index >= 0:
            btn_edit = Gtk.Button(label="✏️ Edit")
            btn_edit.add_css_class("flat")
            btn_edit.add_css_class("caption")
            btn_edit.set_halign(Gtk.Align.END)
            btn_edit.set_margin_top(2)
            btn_edit.connect(
                "clicked",
                lambda _, idx=msg_index, txt=text: self._on_edit_message(idx, txt),
            )
            outer.append(btn_edit)

        self._chat_box.append(outer)

        # Track the widget
        self._bubble_widgets.append(
            {
                "role": role,
                "widget": outer,
                "msg_index": msg_index,
            }
        )

        self._scroll_to_bottom()

    # ──────────────────────────────────────────────
    # Message editing / rewind
    # ──────────────────────────────────────────────

    def _on_edit_message(self, msg_index: int, original_text: str):
        """
        When user clicks Edit on a historical message, populate the input
        with the original text and remove everything from that message
        onwards — rewinding the conversation to that point.
        """
        # Truncate _messages to just before this message
        self._messages = self._messages[:msg_index]

        # Remove all bubble widgets from that point onwards
        to_remove = [bw for bw in self._bubble_widgets if bw["msg_index"] >= msg_index]
        for bw in to_remove:
            w = bw["widget"]
            if w.get_parent() is not None:
                self._chat_box.remove(w)
        self._bubble_widgets = [
            bw for bw in self._bubble_widgets if bw["msg_index"] < msg_index
        ]

        # Put the text in the input for editing
        self._entry.set_text(original_text)
        self._entry.grab_focus()
        self._entry.set_position(-1)

    # ──────────────────────────────────────────────
    # Markdown rendering
    # ──────────────────────────────────────────────

    def _render_markdown_into(self, container: Gtk.Box, text: str):
        """
        Parse markdown and append GTK widgets into container.
        Handles: code blocks, inline code, headers, bold, italic, lists.
        """
        parts = re.split(r"(```[\s\S]*?```)", text)

        for part in parts:
            code_match = re.match(r"```(\w*)\n?([\s\S]*?)```", part)
            if code_match:
                lang = code_match.group(1) or ""
                code = code_match.group(2).rstrip("\n")
                self._add_code_block(container, code, lang)
                continue

            if not part.strip():
                continue

            lines = part.split("\n")
            paragraph_lines: list[str] = []

            def _flush_paragraph():
                if paragraph_lines:
                    ptext = "\n".join(paragraph_lines).strip()
                    if ptext:
                        lbl = self._make_markup_label(ptext)
                        container.append(lbl)
                    paragraph_lines.clear()

            for line in lines:
                stripped = line.strip()

                if stripped.startswith("#### "):
                    _flush_paragraph()
                    lbl = Gtk.Label(label=stripped[5:])
                    lbl.set_xalign(0)
                    lbl.set_wrap(True)
                    lbl.set_max_width_chars(55)
                    lbl.add_css_class("heading")
                    lbl.set_margin_top(4)
                    container.append(lbl)
                elif stripped.startswith("### "):
                    _flush_paragraph()
                    lbl = Gtk.Label(label=stripped[4:])
                    lbl.set_xalign(0)
                    lbl.set_wrap(True)
                    lbl.set_max_width_chars(55)
                    lbl.add_css_class("heading")
                    lbl.set_margin_top(4)
                    container.append(lbl)
                elif stripped.startswith("## "):
                    _flush_paragraph()
                    lbl = Gtk.Label(label=stripped[3:])
                    lbl.set_xalign(0)
                    lbl.set_wrap(True)
                    lbl.set_max_width_chars(55)
                    lbl.add_css_class("heading")
                    lbl.set_margin_top(4)
                    container.append(lbl)
                elif stripped.startswith("# "):
                    _flush_paragraph()
                    lbl = Gtk.Label(label=stripped[2:])
                    lbl.set_xalign(0)
                    lbl.set_wrap(True)
                    lbl.set_max_width_chars(55)
                    lbl.add_css_class("title-3")
                    lbl.set_margin_top(6)
                    container.append(lbl)
                elif re.match(r"^[-*]\s+", stripped):
                    _flush_paragraph()
                    item_text = re.sub(r"^[-*]\s+", "", stripped)
                    lbl = self._make_markup_label("  •  " + item_text)
                    container.append(lbl)
                elif re.match(r"^\d+\.\s+", stripped):
                    _flush_paragraph()
                    lbl = self._make_markup_label("  " + stripped)
                    container.append(lbl)
                elif stripped == "---" or stripped == "***" or stripped == "___":
                    _flush_paragraph()
                    sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
                    sep.set_margin_top(4)
                    sep.set_margin_bottom(4)
                    container.append(sep)
                else:
                    paragraph_lines.append(line)

            _flush_paragraph()

    def _make_markup_label(self, text: str) -> Gtk.Label:
        """Create a label with inline markdown converted to Pango markup."""
        markup = GLib.markup_escape_text(text)
        markup = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", markup)
        markup = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<i>\1</i>", markup)
        markup = re.sub(r"`([^`]+)`", r"<tt>\1</tt>", markup)
        lbl = Gtk.Label()
        lbl.set_markup(markup)
        lbl.set_selectable(True)
        lbl.set_xalign(0)
        lbl.set_yalign(0)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_max_width_chars(55)
        return lbl

    def _add_code_block(self, container: Gtk.Box, code: str, lang: str = ""):
        """Add a styled code block with Copy and Paste buttons."""
        block_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        block_box.add_css_class("card")
        block_box.set_margin_top(4)
        block_box.set_margin_bottom(4)

        hdr = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        hdr.set_margin_start(8)
        hdr.set_margin_end(4)
        hdr.set_margin_top(4)
        hdr.set_margin_bottom(2)

        lang_lbl = Gtk.Label(label=lang or "code")
        lang_lbl.set_xalign(0)
        lang_lbl.add_css_class("dim-label")
        lang_lbl.add_css_class("caption")
        lang_lbl.set_hexpand(True)
        hdr.append(lang_lbl)

        btn_copy = Gtk.Button(icon_name="edit-copy-symbolic")
        btn_copy.set_tooltip_text("Copy to clipboard")
        btn_copy.add_css_class("flat")
        btn_copy.add_css_class("circular")
        btn_copy.connect("clicked", lambda _, c=code: self._copy_to_clipboard(c))
        hdr.append(btn_copy)

        btn_paste = Gtk.Button(label="▶ Paste")
        btn_paste.set_tooltip_text("Paste command into active terminal")
        btn_paste.add_css_class("flat")
        btn_paste.connect(
            "clicked", lambda _, c=code: self.emit("paste-to-terminal", c)
        )
        hdr.append(btn_paste)

        block_box.append(hdr)

        code_label = Gtk.Label(label=code)
        code_label.set_selectable(True)
        code_label.set_xalign(0)
        code_label.set_wrap(True)
        code_label.set_wrap_mode(Pango.WrapMode.CHAR)
        code_label.set_max_width_chars(55)
        code_label.add_css_class("monospace")
        code_label.set_margin_start(8)
        code_label.set_margin_end(8)
        code_label.set_margin_bottom(8)
        block_box.append(code_label)

        container.append(block_box)

    def _copy_to_clipboard(self, text: str):
        display = Gdk.Display.get_default()
        if display:
            clipboard = display.get_clipboard()
            clipboard.set(text)

    def _add_typing_indicator(self):
        lbl = Gtk.Label(label="🤖  Thinking…")
        lbl.set_halign(Gtk.Align.START)
        lbl.add_css_class("dim-label")
        lbl.set_margin_top(2)
        lbl.set_margin_bottom(2)
        self._chat_box.append(lbl)
        self._typing_indicator = lbl
        self._scroll_to_bottom()

    def _remove_typing_indicator(self):
        if self._typing_indicator is not None:
            self._chat_box.remove(self._typing_indicator)
            self._typing_indicator = None

    def _scroll_to_bottom(self):
        def _do():
            adj = self._scroll.get_vadjustment()
            adj.set_value(adj.get_upper() - adj.get_page_size())
            return False

        GLib.timeout_add(60, _do)

    # ──────────────────────────────────────────────
    # Actions
    # ──────────────────────────────────────────────

    def _on_clear(self, *_):
        self._messages.clear()
        self._bubble_widgets.clear()
        child = self._chat_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self._chat_box.remove(child)
            child = nxt
        if self.context_text:
            self._add_context_bubble(self.context_text)

    def _on_send(self, *_):
        question = self._entry.get_text().strip()
        if not question:
            return

        # If there's already a manually-set pending quote, send immediately
        if self._pending_quote:
            self._do_send(question)
            return

        # Otherwise, auto-capture terminal selection before sending
        if self._selection_provider:
            self._btn_send.set_sensitive(False)

            def _on_selection(text):
                if text and text.strip():
                    self._pending_quote = text.strip()
                    display = self._pending_quote
                    if len(display) > 500:
                        display = display[:500] + "…"
                    self._quote_label.set_text(display)
                    self._quote_box.set_visible(True)
                self._do_send(question)

            self._selection_provider(_on_selection)
        else:
            self._do_send(question)

    def _do_send(self, question: str):
        """Actually send the message to the AI backend."""
        api_key = self.config.get("ai_api_key", "").strip()
        if not api_key:
            self._add_system_message(
                "⚠  No API key configured.\n"
                "Open Preferences → AI Assistant and set your API key."
            )
            self._btn_send.set_sensitive(True)
            return

        # Build the full message: include quoted reference if present
        quoted = self._pending_quote
        if quoted:
            full_content = f"```\n{quoted}\n```\n\n{question}"
            display_text = f"📎 ```\n{quoted[:300]}{'…' if len(quoted) > 300 else ''}\n```\n{question}"
        else:
            full_content = question
            display_text = question

        # Clear input and quote
        self._entry.set_text("")
        self._pending_quote = ""
        self._quote_label.set_text("")
        self._quote_box.set_visible(False)
        self._btn_send.set_sensitive(False)

        msg_index = len(self._messages)
        self._add_bubble("user", display_text, msg_index=msg_index)
        self._add_typing_indicator()

        self._messages.append({"role": "user", "content": full_content})

        system_prompt = self._build_system_prompt()
        api_messages = [{"role": "system", "content": system_prompt}]
        api_messages.extend(self._messages)

        threading.Thread(
            target=self._call_api_thread, args=(api_messages,), daemon=True
        ).start()

    def _build_system_prompt(self) -> str:
        custom = self.config.get("ai_system_prompt", "").strip()
        if custom:
            prompt = custom
        else:
            prompt = (
                "You are an expert Linux/SSH/DevOps assistant embedded in an SSH client manager. "
                "Rules:\n"
                "1. Keep answers concise, straight to the point, no unnecessary wording.\n"
                "2. Use emojis appropriately to make replies clearer and more intuitive.\n"
                "3. Maintain logical structure: explain the cause first, then provide the solution.\n"
                "4. Use Markdown: headers, bold text, lists, and bash code blocks for commands.\n"
                "5. If multiple solutions exist, recommend the best one first."
            )
        return prompt

    # ──────────────────────────────────────────────
    # API call (worker thread)
    # ──────────────────────────────────────────────

    def _call_api_thread(self, messages: list):
        api_key = self.config.get("ai_api_key", "").strip()
        base_url = self.config.get("ai_base_url", "https://api.openai.com/v1").rstrip(
            "/"
        )
        model = self.config.get("ai_model", "gpt-4o-mini").strip() or "gpt-4o-mini"
        provider = self.config.get("ai_provider", "openai")

        is_claude = provider == "claude" or _is_claude_endpoint(base_url)

        if is_claude:
            text = self._call_claude(api_key, base_url, model, messages)
        else:
            text = self._call_openai(api_key, base_url, model, messages)

        GLib.idle_add(self._on_api_response, text)

    @staticmethod
    def _call_openai(api_key, base_url, model, messages):
        payload = json.dumps(
            {
                "model": model,
                "messages": messages,
                "max_tokens": 2048,
                "temperature": 0.5,
            }
        ).encode("utf-8")

        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                return data["choices"][0]["message"]["content"]
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                err_msg = json.loads(body).get("error", {}).get("message", body)
            except Exception:
                err_msg = body
            return f"API Error {e.code}: {err_msg}"
        except Exception as exc:
            return f"Error: {exc}"

    @staticmethod
    def _call_claude(api_key, base_url, model, messages):
        system_text = ""
        turns = []
        for m in messages:
            if m["role"] == "system":
                system_text += m["content"] + "\n"
            else:
                turns.append({"role": m["role"], "content": m["content"]})

        merged = []
        for t in turns:
            if merged and merged[-1]["role"] == t["role"]:
                merged[-1]["content"] += "\n" + t["content"]
            else:
                merged.append(dict(t))
        if not merged or merged[0]["role"] != "user":
            merged.insert(0, {"role": "user", "content": "(context above)"})

        if model in ("gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"):
            model = "claude-sonnet-4-20250514"

        payload = json.dumps(
            {
                "model": model,
                "max_tokens": 2048,
                "system": system_text.strip(),
                "messages": merged,
            }
        ).encode("utf-8")

        url = base_url.rstrip("/")
        if not url.endswith("/messages"):
            url = url.rstrip("/") + "/v1/messages"

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
        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                blocks = data.get("content", [])
                parts = [b["text"] for b in blocks if b.get("type") == "text"]
                return "\n".join(parts) if parts else str(data)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                err_msg = json.loads(body).get("error", {}).get("message", body)
            except Exception:
                err_msg = body
            return f"Claude API Error {e.code}: {err_msg}"
        except Exception as exc:
            return f"Error: {exc}"

    def _on_api_response(self, response_text):
        self._remove_typing_indicator()
        msg_index = len(self._messages)
        self._messages.append({"role": "assistant", "content": response_text})
        self._add_bubble("assistant", response_text, msg_index=msg_index)
        self._btn_send.set_sensitive(True)
        self._entry.grab_focus()
        return False
