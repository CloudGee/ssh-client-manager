"""
Sidebar with hierarchical connection tree and SFTP file browser.

Displays connections organized in groups using Gtk.TreeView.
Supports right-click context menu, double-click to connect,
drag-and-drop reordering, connection status indicators,
tags/favorites, and batch operations.
When an SFTP tab is active, switches to a graphical file browser
with download/upload/DnD.
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk, Adw, GLib, Gdk, GObject, Pango, Gio
from typing import Optional

from .connection import Connection, ConnectionManager
from .sftp_browser import SftpBrowser


# TreeStore columns
COL_DISPLAY_NAME = 0  # str: Display text
COL_ICON_NAME = 1  # str: Icon name
COL_CONNECTION_ID = 2  # str: Connection ID (empty for groups)
COL_GROUP_PATH = 3  # str: Group path (for group rows)
COL_IS_GROUP = 4  # bool: True if this is a group row
COL_TOOLTIP = 5  # str: Tooltip text


class Sidebar(Gtk.Box):
    """
    Left panel showing connections grouped hierarchically.

    Signals:
        connect-requested: User double-clicked a connection
        edit-requested: User wants to edit a connection
        add-requested: User wants to add a new connection
    """

    __gsignals__ = {
        "connect-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "edit-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "add-requested": (GObject.SignalFlags.RUN_LAST, None, ()),
        "delete-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "add-group-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
        "open-sftp-requested": (GObject.SignalFlags.RUN_LAST, None, (str,)),
    }

    def __init__(self, connection_manager: ConnectionManager, credential_store=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.connection_manager = connection_manager
        self.credential_store = credential_store

        # Track which connections are currently open
        self._connected_ids: set[str] = set()

        # Batch selection mode
        self._batch_mode = False
        self._batch_selected: set[str] = set()

        self.set_size_request(200, -1)
        self.add_css_class("sidebar")

        # --- Search bar ---
        self.search_entry = Gtk.SearchEntry()
        self.search_entry.set_placeholder_text("Search connections...")
        self.search_entry.set_margin_start(6)
        self.search_entry.set_margin_end(6)
        self.search_entry.set_margin_top(6)
        self.search_entry.set_margin_bottom(6)
        self.search_entry.connect("search-changed", self._on_search_changed)
        self.append(self.search_entry)

        # --- Toolbar ---
        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        toolbar.set_margin_start(6)
        toolbar.set_margin_end(6)
        toolbar.set_margin_bottom(6)
        toolbar.set_halign(Gtk.Align.CENTER)

        btn_add = Gtk.Button(icon_name="list-add-symbolic")
        btn_add.set_tooltip_text("Add Connection")
        btn_add.connect("clicked", lambda _: self.emit("add-requested"))
        btn_add.add_css_class("flat")
        toolbar.append(btn_add)

        btn_add_group = Gtk.Button(icon_name="folder-new-symbolic")
        btn_add_group.set_tooltip_text("Add Group")
        btn_add_group.connect(
            "clicked",
            lambda _: self.emit(
                "add-group-requested", self.get_selected_group_path() or ""
            ),
        )
        btn_add_group.add_css_class("flat")
        toolbar.append(btn_add_group)

        self.append(toolbar)

        # Separator
        self.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # --- Tree View ---
        # TreeStore: display_name, icon_name, connection_id, group_path, is_group, tooltip
        self.store = Gtk.TreeStore(str, str, str, str, bool, str)

        # Filter model for search
        self.filter_model = self.store.filter_new()
        self.filter_model.set_visible_func(self._filter_func)
        self._search_text = ""

        self.tree_view = Gtk.TreeView(model=self.filter_model)
        self.tree_view.set_headers_visible(False)
        self.tree_view.set_enable_search(False)
        self.tree_view.set_tooltip_column(COL_TOOLTIP)
        self.tree_view.set_activate_on_single_click(False)

        # Column with icon + text
        column = Gtk.TreeViewColumn()
        column.set_expand(True)

        # Icon renderer
        icon_renderer = Gtk.CellRendererPixbuf()
        column.pack_start(icon_renderer, False)
        column.add_attribute(icon_renderer, "icon-name", COL_ICON_NAME)

        # Text renderer
        text_renderer = Gtk.CellRendererText()
        text_renderer.set_padding(4, 2)
        text_renderer.set_property("ellipsize", Pango.EllipsizeMode.END)
        column.pack_start(text_renderer, True)
        column.add_attribute(text_renderer, "text", COL_DISPLAY_NAME)

        self.tree_view.append_column(column)

        # Double-click to connect
        self.tree_view.connect("row-activated", self._on_row_activated)

        # Right-click menu
        click = Gtk.GestureClick(button=3)
        click.connect("pressed", self._on_right_click)
        self.tree_view.add_controller(click)

        # --- Drag-and-drop: move connections between groups ---
        self._setup_dnd()

        # Scrolled window for the tree
        self._scrolled = Gtk.ScrolledWindow()
        self._scrolled.set_vexpand(True)
        self._scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self._scrolled.set_child(self.tree_view)

        # --- Gtk.Stack: connections vs SFTP browser ---
        self._stack = Gtk.Stack()
        self._stack.set_vexpand(True)
        self._stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self._stack.set_transition_duration(200)

        # Page 1: Connection tree
        connections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        connections_box.append(self._scrolled)
        self._stack.add_named(connections_box, "connections")

        # Page 2: SFTP browser (created lazily)
        self._sftp_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)

        # "Back to connections" header
        sftp_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sftp_header.set_margin_start(4)
        sftp_header.set_margin_end(4)
        sftp_header.set_margin_top(4)
        sftp_header.set_margin_bottom(4)

        btn_back = Gtk.Button(icon_name="go-previous-symbolic")
        btn_back.set_tooltip_text("Back to Connections")
        btn_back.add_css_class("flat")
        btn_back.connect("clicked", lambda _: self.show_connections())
        sftp_header.append(btn_back)

        self._sftp_title = Gtk.Label(label="SFTP Browser")
        self._sftp_title.set_hexpand(True)
        self._sftp_title.set_xalign(0)
        self._sftp_title.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._sftp_title.add_css_class("heading")
        sftp_header.append(self._sftp_title)

        self._sftp_page.append(sftp_header)
        self._sftp_page.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))

        # The actual browser widget placeholder
        self._sftp_browser: Optional[SftpBrowser] = None
        self._sftp_browser_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._sftp_browser_container.set_vexpand(True)
        self._sftp_page.append(self._sftp_browser_container)

        self._stack.add_named(self._sftp_page, "sftp")

        self.append(self._stack)

        # Initial population
        self.refresh()

    def refresh(self):
        """Rebuild the tree from the connection manager."""
        self.store.clear()

        # Build group structure and connection placement
        groups = self.connection_manager.get_groups()
        connections = self.connection_manager.get_connections()

        # Track created group rows by path
        group_iters = {}

        # Create group rows
        for group_path in sorted(groups):
            parts = group_path.split("/")
            for i, part in enumerate(parts):
                current_path = "/".join(parts[: i + 1])
                if current_path not in group_iters:
                    parent_path = "/".join(parts[:i]) if i > 0 else None
                    parent_iter = group_iters.get(parent_path)
                    iter_ = self.store.append(
                        parent_iter,
                        [
                            part,  # display name
                            "folder-symbolic",  # icon
                            "",  # no connection ID
                            current_path,  # group path
                            True,  # is group
                            f"Group: {current_path}",  # tooltip
                        ],
                    )
                    group_iters[current_path] = iter_

        # Sort: favorites first, then alphabetical
        sorted_conns = sorted(
            connections,
            key=lambda c: (not getattr(c, "favorite", False), c.name.lower()),
        )

        # Add connections
        for conn in sorted_conns:
            parent_iter = group_iters.get(conn.group)

            # Protocol-specific icons
            proto = getattr(conn, "protocol", "ssh") or "ssh"
            icon_map = {
                "ssh": "network-server-symbolic",
                "sftp": "folder-remote-symbolic",
                "rdp": "computer-symbolic",
                "vnc": "preferences-desktop-remote-desktop-symbolic",
            }
            icon = icon_map.get(proto, "network-server-symbolic")

            # Display name with favorite star and tags
            display_name = conn.name or conn.display_name()

            if getattr(conn, "favorite", False):
                display_name = f"★ {display_name}"

            tags = getattr(conn, "tags", "")
            tag_suffix = ""
            if tags:
                tag_list = [t.strip() for t in tags.split(",") if t.strip()]
                if tag_list:
                    tag_suffix = f"  [{', '.join(tag_list[:2])}]"

            proto_label = f" ({proto.upper()})" if proto != "ssh" else ""
            tooltip = f"{conn.display_name()}{proto_label}"
            if conn.description:
                tooltip += f"\n{conn.description}"
            if tags:
                tooltip += f"\nTags: {tags}"

            self.store.append(
                parent_iter,
                [
                    display_name + tag_suffix,  # display name
                    icon,  # icon
                    conn.id,  # connection ID
                    conn.group,  # group path
                    False,  # not a group
                    tooltip,  # tooltip
                ],
            )

        # Expand all by default
        self.tree_view.expand_all()
        self.filter_model.refilter()

    def get_selected_connection_id(self) -> Optional[str]:
        """Get the connection ID of the selected row, or None."""
        selection = self.tree_view.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            conn_id = model.get_value(iter_, COL_CONNECTION_ID)
            is_group = model.get_value(iter_, COL_IS_GROUP)
            if not is_group and conn_id:
                return conn_id
        return None

    def get_selected_group_path(self) -> Optional[str]:
        """Get the group path of the selected row, or None."""
        selection = self.tree_view.get_selection()
        model, iter_ = selection.get_selected()
        if iter_:
            is_group = model.get_value(iter_, COL_IS_GROUP)
            if is_group:
                return model.get_value(iter_, COL_GROUP_PATH)
        return None

    # --- Signal handlers ---

    def _setup_dnd(self):
        """Set up drag-and-drop for moving connections and groups."""
        # Drag source: connection and group rows can be dragged
        drag_source = Gtk.DragSource()
        drag_source.set_actions(Gdk.DragAction.MOVE)
        drag_source.connect("prepare", self._on_dnd_prepare)
        drag_source.connect("drag-begin", self._on_dnd_drag_begin)
        self.tree_view.add_controller(drag_source)

        # Drop target: groups and empty space can receive connections or groups
        drop_target = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        drop_target.connect("drop", self._on_dnd_drop)
        drop_target.connect("motion", self._on_dnd_motion)
        self.tree_view.add_controller(drop_target)

    @staticmethod
    def _group_is_ancestor(ancestor_path: str, child_path: str) -> bool:
        """Return True if ancestor_path is equal to or an ancestor of child_path."""
        return child_path == ancestor_path or child_path.startswith(ancestor_path + "/")

    def _on_dnd_prepare(self, source, x, y):
        """Prepare drag data — allow dragging both connection and group rows.

        Drag data format:
          - Connection: "conn:<connection_id>"
          - Group:      "group:<group_path>"
        """
        path_info = self.tree_view.get_path_at_pos(int(x), int(y))
        if not path_info:
            return None

        path = path_info[0]
        iter_ = self.filter_model.get_iter(path)
        is_group = self.filter_model.get_value(iter_, COL_IS_GROUP)

        if is_group:
            group_path = self.filter_model.get_value(iter_, COL_GROUP_PATH)
            if not group_path:
                return None
            self._drag_conn_id = None
            self._drag_group_path = group_path
            return Gdk.ContentProvider.new_for_value(f"group:{group_path}")
        else:
            conn_id = self.filter_model.get_value(iter_, COL_CONNECTION_ID)
            if not conn_id:
                return None
            self._drag_conn_id = conn_id
            self._drag_group_path = None
            return Gdk.ContentProvider.new_for_value(f"conn:{conn_id}")

    def _on_dnd_drag_begin(self, source, drag):
        """Set drag icon — show only the dragged item label."""
        drag_group = getattr(self, "_drag_group_path", None)
        if drag_group:
            leaf = drag_group.split("/")[-1]
            label = Gtk.Label(label=f"  {leaf}  ")
            label.add_css_class("heading")
            icon = Gtk.DragIcon.get_for_drag(drag)
            icon.set_child(label)
            return

        conn_id = getattr(self, "_drag_conn_id", None)
        if conn_id:
            conn = self.connection_manager.get_connection(conn_id)
            if conn:
                label = Gtk.Label(label=f"  {conn.name}  ")
                label.add_css_class("heading")
                icon = Gtk.DragIcon.get_for_drag(drag)
                icon.set_child(label)
                return
        icon = Gtk.WidgetPaintable.new(self.tree_view)
        source.set_icon(icon, 0, 0)

    def _on_dnd_motion(self, target, x, y):
        """Highlight potential drop target during drag."""
        drag_group = getattr(self, "_drag_group_path", None)
        path_info = self.tree_view.get_path_at_pos(int(x), int(y))
        if path_info:
            path = path_info[0]
            iter_ = self.filter_model.get_iter(path)
            is_group = self.filter_model.get_value(iter_, COL_IS_GROUP)
            if is_group:
                if drag_group:
                    # Refuse to drop a group onto itself or a descendant
                    target_group_path = self.filter_model.get_value(
                        iter_, COL_GROUP_PATH
                    )
                    if self._group_is_ancestor(drag_group, target_group_path):
                        return None
                self.tree_view.set_cursor(path, None, False)
                return Gdk.DragAction.MOVE
        return Gdk.DragAction.MOVE  # Allow drop on empty space (root level)

    def _on_dnd_drop(self, target, value, x, y):
        """Handle drop — move connection or group to the target group."""
        if not value:
            return False

        if value.startswith("group:"):
            return self._handle_group_drop(value[6:], x, y)
        elif value.startswith("conn:"):
            return self._handle_conn_drop(value[5:], x, y)
        else:
            # Legacy plain conn_id format
            return self._handle_conn_drop(value, x, y)

    def _handle_conn_drop(self, conn_id: str, x: float, y: float) -> bool:
        """Move a connection to the target group."""
        conn = self.connection_manager.get_connection(conn_id)
        if not conn:
            return False

        target_group = ""
        path_info = self.tree_view.get_path_at_pos(int(x), int(y))
        if path_info:
            path = path_info[0]
            iter_ = self.filter_model.get_iter(path)
            is_group = self.filter_model.get_value(iter_, COL_IS_GROUP)
            if is_group:
                target_group = self.filter_model.get_value(iter_, COL_GROUP_PATH)
            else:
                # Dropped on a connection — use that connection's group
                target_group = self.filter_model.get_value(iter_, COL_GROUP_PATH) or ""

        if conn.group != target_group:
            conn.group = target_group
            self.connection_manager.update_connection(conn)
            self.refresh()

        return True

    def _handle_group_drop(self, drag_group_path: str, x: float, y: float) -> bool:
        """Move a group to be a child of the target group (or to root level)."""
        group_leaf = drag_group_path.split("/")[-1]

        # Determine the new parent path
        target_parent = ""
        path_info = self.tree_view.get_path_at_pos(int(x), int(y))
        if path_info:
            path = path_info[0]
            iter_ = self.filter_model.get_iter(path)
            is_group = self.filter_model.get_value(iter_, COL_IS_GROUP)
            if is_group:
                target_group_path = self.filter_model.get_value(iter_, COL_GROUP_PATH)
                # Guard: cannot drop a group onto itself or a descendant
                if self._group_is_ancestor(drag_group_path, target_group_path):
                    return False
                target_parent = target_group_path

        # Compute new full group path
        new_group_path = (
            f"{target_parent}/{group_leaf}" if target_parent else group_leaf
        )

        # No-op if already at the same location
        if new_group_path == drag_group_path:
            return True

        self.connection_manager.rename_group(drag_group_path, new_group_path)
        self.refresh()
        return True

    def _on_row_activated(self, tree_view, path, column):
        """Handle double-click on a row."""
        iter_ = self.filter_model.get_iter(path)
        is_group = self.filter_model.get_value(iter_, COL_IS_GROUP)

        if is_group:
            # Toggle expand/collapse
            if tree_view.row_expanded(path):
                tree_view.collapse_row(path)
            else:
                tree_view.expand_row(path, False)
        else:
            conn_id = self.filter_model.get_value(iter_, COL_CONNECTION_ID)
            if conn_id:
                self.emit("connect-requested", conn_id)

    def _on_right_click(self, gesture, n_press, x, y):
        """Show context menu on right-click using a plain Gtk.Popover."""
        path_info = self.tree_view.get_path_at_pos(int(x), int(y))

        # Build list of (label, callback) items
        items: list[tuple[str, callable]] = []

        if path_info:
            path, column, cell_x, cell_y = path_info
            self.tree_view.get_selection().select_path(path)

            iter_ = self.filter_model.get_iter(path)
            is_group = self.filter_model.get_value(iter_, COL_IS_GROUP)
            conn_id = self.filter_model.get_value(iter_, COL_CONNECTION_ID)

            if is_group:
                group_path = self.filter_model.get_value(iter_, COL_GROUP_PATH)
                items = [
                    ("Add Connection Here", lambda _: self.emit("add-requested")),
                    (
                        "Add Subgroup",
                        lambda _, gp=group_path: self.emit("add-group-requested", gp),
                    ),
                    (None, None),
                    (
                        "Rename Group",
                        lambda _, gp=group_path: self._rename_group_dialog(gp),
                    ),
                    (
                        "Delete Group",
                        lambda _, gp=group_path: self._delete_group_confirm(gp),
                    ),
                    (None, None),
                    (
                        "Connect All in Group",
                        lambda _, gp=group_path: self._connect_group(gp),
                    ),
                ]
            else:
                conn = (
                    self.connection_manager.get_connection(conn_id) if conn_id else None
                )
                proto = getattr(conn, "protocol", "ssh") if conn else "ssh"
                is_fav = getattr(conn, "favorite", False) if conn else False

                items = [
                    (
                        "Connect",
                        lambda _, cid=conn_id: self.emit("connect-requested", cid),
                    ),
                ]
                if proto == "ssh":
                    items.append(
                        (
                            "Open SFTP",
                            lambda _, cid=conn_id: self.emit(
                                "open-sftp-requested", cid
                            ),
                        )
                    )
                items.extend(
                    [
                        (None, None),
                        (
                            "Remove Favorite" if is_fav else "Add Favorite",
                            lambda _, cid=conn_id: self._toggle_favorite(cid),
                        ),
                        (
                            "Edit",
                            lambda _, cid=conn_id: self.emit("edit-requested", cid),
                        ),
                        (
                            "Duplicate",
                            lambda _, cid=conn_id: self._duplicate_connection(cid),
                        ),
                        (
                            "Delete",
                            lambda _, cid=conn_id: self.emit("delete-requested", cid),
                        ),
                    ]
                )
        else:
            items = [
                ("Add Connection", lambda _: self.emit("add-requested")),
                ("Add Group", lambda _: self.emit("add-group-requested", "")),
            ]

        # Build popover with buttons
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        box.set_margin_top(4)
        box.set_margin_bottom(4)
        box.set_margin_start(4)
        box.set_margin_end(4)

        popover = Gtk.Popover()
        popover.set_autohide(True)

        for label, callback in items:
            if label is None:
                box.append(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL))
                continue
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.set_halign(Gtk.Align.FILL)

            # Close popover first, then fire the action
            def make_handler(cb, pop):
                def handler(b):
                    pop.popdown()
                    cb(b)

                return handler

            btn.connect("clicked", make_handler(callback, popover))
            box.append(btn)

        popover.set_child(box)
        popover.set_parent(self._scrolled)

        # Translate coordinates from tree_view to scrolled window
        result = self.tree_view.translate_coordinates(self._scrolled, x, y)
        if result is not None:
            sx, sy = result
        else:
            sx, sy = x, y
        rect = Gdk.Rectangle()
        rect.x = int(sx)
        rect.y = int(sy)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: p.unparent())
        popover.popup()

    def _delete_selected_group(self):
        """Delete the selected group (with confirmation)."""
        group_path = self.get_selected_group_path()
        if group_path:
            self._delete_group_confirm(group_path)

    def _delete_group_confirm(self, group_path: str):
        """Delete a group with confirmation dialog."""
        conns = self.connection_manager.get_connections_in_group(group_path)
        body = f'Delete group "{group_path}"?'
        if conns:
            body += f"\n\n{len(conns)} connection(s) in this group."
            body += "\n• Keep Connections — connections become ungrouped"
            body += "\n• Delete All — group AND all connections are deleted"
        else:
            body += "\n\nThis group has no connections."

        try:
            dialog = Adw.MessageDialog(
                transient_for=self.get_root(),
                heading="Delete Group?",
                body=body,
            )
            dialog.add_response("cancel", "Cancel")
            if conns:
                dialog.add_response("keep", "Keep Connections")
                dialog.set_response_appearance("keep", Adw.ResponseAppearance.SUGGESTED)
                dialog.add_response("delete-all", "Delete All")
                dialog.set_response_appearance(
                    "delete-all", Adw.ResponseAppearance.DESTRUCTIVE
                )
            else:
                dialog.add_response("delete", "Delete")
                dialog.set_response_appearance(
                    "delete", Adw.ResponseAppearance.DESTRUCTIVE
                )

            def on_response(d, response):
                if response in ("delete", "keep"):
                    self.connection_manager.delete_group(
                        group_path, delete_connections=False
                    )
                    self.refresh()
                elif response == "delete-all":
                    self.connection_manager.delete_group(
                        group_path, delete_connections=True
                    )
                    self.refresh()

            dialog.connect("response", on_response)
            dialog.present()
        except Exception:
            # Fallback if Adw dialog fails
            self.connection_manager.delete_group(group_path, delete_connections=False)
            self.refresh()

    def _rename_group_dialog(self, group_path: str):
        """Show dialog to rename a group."""
        current_name = (
            group_path.rsplit("/", 1)[-1] if "/" in group_path else group_path
        )

        dialog = Adw.Window(
            transient_for=self.get_root(),
            modal=True,
            title="Rename Group",
            default_width=360,
            default_height=-1,
        )

        main_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        header = Adw.HeaderBar()
        header.set_show_end_title_buttons(False)
        header.set_show_start_title_buttons(False)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: dialog.close())
        header.pack_start(cancel_btn)

        rename_btn = Gtk.Button(label="Rename")
        rename_btn.add_css_class("suggested-action")
        header.pack_end(rename_btn)

        main_box.append(header)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        content.set_margin_start(24)
        content.set_margin_end(24)
        content.set_margin_top(16)
        content.set_margin_bottom(24)

        label = Gtk.Label(label=f'Enter new name for "{current_name}":')
        label.set_xalign(0)
        content.append(label)

        entry = Gtk.Entry()
        entry.set_text(current_name)
        entry.set_activates_default(True)
        content.append(entry)

        main_box.append(content)
        dialog.set_content(main_box)

        def do_rename(*_):
            new_name = entry.get_text().strip()
            if new_name and new_name != current_name:
                if "/" in group_path:
                    parent = group_path.rsplit("/", 1)[0]
                    new_path = f"{parent}/{new_name}"
                else:
                    new_path = new_name
                self.connection_manager.rename_group(group_path, new_path)
                self.refresh()
            dialog.close()

        rename_btn.connect("clicked", do_rename)
        entry.connect("activate", do_rename)
        dialog.present()
        GLib.idle_add(entry.grab_focus)

    def _toggle_favorite(self, conn_id: str):
        """Toggle favorite status of a connection."""
        conn = self.connection_manager.get_connection(conn_id)
        if conn:
            conn.favorite = not getattr(conn, "favorite", False)
            self.connection_manager.update_connection(conn)
            self.refresh()

    def _connect_group(self, group_path: str):
        """Emit connect-requested for all connections in a group."""
        conns = self.connection_manager.get_connections_in_group(group_path)
        for conn in conns:
            self.emit("connect-requested", conn.id)

    def mark_connected(self, conn_id: str):
        """Mark a connection as currently connected (updates status indicator)."""
        self._connected_ids.add(conn_id)
        self._update_row_status(conn_id)

    def mark_disconnected(self, conn_id: str):
        """Mark a connection as disconnected."""
        self._connected_ids.discard(conn_id)
        self._update_row_status(conn_id)

    def _update_row_status(self, conn_id: str):
        """Update the icon of a specific connection row without rebuilding the tree."""
        conn = self.connection_manager.get_connection(conn_id)
        if not conn:
            return

        is_connected = conn_id in self._connected_ids

        # Walk the underlying store to find the row
        def _walk(iter_):
            while iter_:
                row_id = self.store.get_value(iter_, COL_CONNECTION_ID)
                if row_id == conn_id:
                    proto = getattr(conn, "protocol", "ssh") or "ssh"
                    if is_connected:
                        icon_map = {
                            "ssh": "network-server-symbolic",
                            "sftp": "folder-remote-symbolic",
                            "rdp": "computer-symbolic",
                            "vnc": "preferences-desktop-remote-desktop-symbolic",
                        }
                        icon = icon_map.get(proto, "network-server-symbolic")
                        # Prepend a connected indicator to display name
                        display = self.store.get_value(iter_, COL_DISPLAY_NAME)
                        if not display.startswith("● "):
                            self.store.set_value(
                                iter_, COL_DISPLAY_NAME, f"● {display}"
                            )
                    else:
                        display = self.store.get_value(iter_, COL_DISPLAY_NAME)
                        if display.startswith("● "):
                            self.store.set_value(iter_, COL_DISPLAY_NAME, display[2:])
                    return True
                child = self.store.iter_children(iter_)
                if child and _walk(child):
                    return True
                iter_ = self.store.iter_next(iter_)
            return False

        root = self.store.get_iter_first()
        if root:
            _walk(root)

    def _duplicate_connection(self, conn_id: str):
        """Duplicate a connection."""
        conn = self.connection_manager.get_connection(conn_id)
        if conn:
            clone = conn.clone()
            self.connection_manager.add_connection(clone)
            self.refresh()

    def _on_search_changed(self, entry):
        """Handle search text changes."""
        self._search_text = entry.get_text().lower()
        self.filter_model.refilter()
        if self._search_text:
            self.tree_view.expand_all()

    def _filter_func(self, model, iter_, data=None):
        """Filter function for the tree filter model."""
        if not self._search_text:
            return True

        # Show if this row matches
        name = model.get_value(iter_, COL_DISPLAY_NAME)
        if name and self._search_text in name.lower():
            return True

        # Show if any ancestor group matches (so matched folders can expand)
        parent = model.iter_parent(iter_)
        while parent:
            pname = model.get_value(parent, COL_DISPLAY_NAME)
            if pname and self._search_text in pname.lower():
                return True
            parent = model.iter_parent(parent)

        # Show groups if any child matches
        is_group = model.get_value(iter_, COL_IS_GROUP)
        if is_group:
            child = model.iter_children(iter_)
            while child:
                if self._filter_func(model, child):
                    return True
                child = model.iter_next(child)

        return False

    # =================================================================
    # SFTP Browser Integration
    # =================================================================

    def show_sftp_browser(self, connection, credential_store):
        """
        Switch the sidebar to SFTP file browser mode.

        Creates a new SftpBrowser if necessary and connects to the server.
        """
        # Clean up previous browser
        if self._sftp_browser:
            self._sftp_browser.disconnect()
            self._sftp_browser_container.remove(self._sftp_browser)
            self._sftp_browser = None

        # Create new browser
        browser = SftpBrowser()
        self._sftp_browser = browser
        self._sftp_browser_container.append(browser)

        # Update title
        name = connection.name or connection.display_name()
        self._sftp_title.set_text(f"SFTP: {name}")

        # Hide search/toolbar (not relevant in SFTP mode)
        self.search_entry.set_visible(False)

        # Switch stack
        self._stack.set_visible_child_name("sftp")

        # Connect
        browser.connect_from_connection(connection, credential_store)

    def show_connections(self):
        """Switch the sidebar back to connection tree mode."""
        # Disconnect SFTP browser if active
        if self._sftp_browser:
            self._sftp_browser.disconnect()
            self._sftp_browser_container.remove(self._sftp_browser)
            self._sftp_browser = None

        # Show search/toolbar again
        self.search_entry.set_visible(True)

        # Switch stack
        self._stack.set_visible_child_name("connections")

    @property
    def is_sftp_mode(self) -> bool:
        """Whether the sidebar is showing the SFTP browser."""
        return self._stack.get_visible_child_name() == "sftp"
