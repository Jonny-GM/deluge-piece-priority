"""GTK3 UI plugin: adds a Pieces tab to Deluge's GTK3 client's
torrent-details panel. Calls piecepriority.* directly through the GTK3
client's existing RPC connection -- Gtk3PluginBase doesn't get its own RPC
registration the way CorePluginBase/WebPluginBase do (confirmed against
deluge/plugins/pluginbase.py); the GTK3 side is a plain RPC client, same
as any other GTK3 UI panel calling core.* methods.
"""

from __future__ import annotations

import gi  # isort:skip (Required before Gtk import).

gi.require_version('Gtk', '3.0')
gi.require_version('Gdk', '3.0')

# gi.repository's submodules are generated at runtime from introspection
# data, not real static Python modules -- ty (like most checkers without a
# PyGObject stub package) can't resolve their members. Same category of
# gap as libtorrent's lack of stubs (see core.py's import), suppressed at
# this one import site rather than globally.
from gi.repository import Gdk, Gtk  # ty: ignore[unresolved-import]

from typing import TYPE_CHECKING, Any

import deluge.common
import deluge.component as component
from deluge.plugins.pluginbase import Gtk3PluginBase
from deluge.ui.client import client
from deluge.ui.gtk3.torrentdetails import Tab

from .piece_layout import (
    PIECE_STATE_UNAVAILABLE,
    Layout,
    cell_index_from_xy,
    color_for_cell,
    compute_layout,
    piece_range_for_cell,
)

if TYPE_CHECKING:
    # `_` is installed onto builtins by Deluge's own i18n setup
    # (gettext.install(), confirmed in deluge/i18n/util.py) before any
    # plugin is loaded -- genuinely available at runtime with no import
    # needed, matching every other Deluge/plugin GTK3 module. This never
    # executes; it only gives ty a signature to resolve the name against.
    def _(text: str) -> str: ...

# Matches Deluge's own FILE_PRIORITY convention so the menu reads the same
# as the stock file-priority right-click menu.
PRIORITY_OPTIONS = [
    (_('Skip'), 0),
    (_('Low'), 1),
    (_('Normal'), 4),
    (_('High'), 7),
]

DEFAULT_CONFIG: dict[str, Any] = {
    'not_dled_color': '#8A8A8A',
    'unavailable_color': '#FF00FF',
    'dled_color': '#FF0000',
    'dling_color': '#0000FF',
    'urgent_color': '#FFA500',
    'skipped_color': '#000000',
    'selected_border': '#FFFFFF',
    'hover_border': '#5a5a5a',
    'square_size': 10,
    'square_border_size': 2,
}


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    color = color.lstrip('#')
    r, g, b = (int(color[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return r, g, b


class PiecePriorityTab(Tab):
    def __init__(self) -> None:
        super().__init__()
        self._name = 'Pieces'

        self.torrent_id: str | None = None
        self.selected: set[int] = set()
        self.num_pieces = 0
        self.piece_length = 0
        self.render_pieces: list[int] | None = None
        self.raw_pieces: list[int] | None = None
        self.priorities: list[int] | None = None
        self.layout: Layout | None = None
        self.hover_cell: int | None = None
        self.config: dict[str, Any] = dict(DEFAULT_CONFIG)
        self.config_loaded = False
        self.dragging = False
        self.drag_value = False
        self.last_clicked: int | None = None
        self.legend_swatches: list[tuple[Gtk.DrawingArea, str]] = []

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.set_border_width(6)

        box.pack_start(self._build_legend(), False, False, 0)

        self.drawing_area = Gtk.DrawingArea()
        self.drawing_area.set_has_tooltip(True)
        self.drawing_area.add_events(
            Gdk.EventMask.BUTTON_PRESS_MASK
            | Gdk.EventMask.BUTTON_RELEASE_MASK
            | Gdk.EventMask.POINTER_MOTION_MASK
            | Gdk.EventMask.LEAVE_NOTIFY_MASK
        )
        self.drawing_area.connect('draw', self.on_draw)
        self.drawing_area.connect('button-press-event', self.on_button_press)
        self.drawing_area.connect('button-release-event', self.on_button_release)
        self.drawing_area.connect('motion-notify-event', self.on_motion)
        self.drawing_area.connect('leave-notify-event', self.on_leave)
        self.drawing_area.connect('query-tooltip', self.on_query_tooltip)
        self.drawing_area.connect('size-allocate', self.on_size_allocate)
        box.pack_start(self.drawing_area, True, True, 0)

        self.info_label = Gtk.Label(label='')
        self.info_label.set_halign(Gtk.Align.START)
        box.pack_start(self.info_label, False, False, 0)

        box.show_all()

        self._child_widget = box
        self._tab_label = Gtk.Label(label=_('Pieces'))
        self._tab_label.show()

        self.priority_menu = self._build_priority_menu()

    def _build_legend(self) -> Gtk.Box:
        legend = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        # Ordered as the piece lifecycle, dead to done: skipped pieces
        # never advance, unavailable ones can't advance until a peer that
        # has them shows up, missing ones are wanted but idle, urgent
        # ones are wanted next, then in-flight, then complete.
        for key, label in (
            ('skipped_color', _('Skipped')),
            ('unavailable_color', _('Unavailable')),
            ('not_dled_color', _('Missing')),
            ('urgent_color', _('Urgent')),
            ('dling_color', _('Downloading')),
            ('dled_color', _('Have')),
        ):
            item = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
            swatch = Gtk.DrawingArea()
            swatch.set_size_request(12, 12)
            swatch.connect('draw', self._on_swatch_draw, key)
            item.pack_start(swatch, False, False, 0)
            item.pack_start(Gtk.Label(label=label), False, False, 0)
            legend.pack_start(item, False, False, 0)
            self.legend_swatches.append((swatch, key))
        return legend

    def _on_swatch_draw(self, widget: Gtk.DrawingArea, ctx: Any, color_key: str) -> bool:
        ctx.set_source_rgb(*_hex_to_rgb(self.config[color_key]))
        ctx.rectangle(0, 0, widget.get_allocated_width(), widget.get_allocated_height())
        ctx.fill()
        return False

    def _build_priority_menu(self) -> Gtk.Menu:
        menu = Gtk.Menu()
        for label, priority in PRIORITY_OPTIONS:
            item = Gtk.MenuItem(label=label)
            item.connect('activate', self._on_priority_selected, priority)
            menu.append(item)
        menu.show_all()
        return menu

    # --- config ---

    def load_config(self) -> None:
        if self.config_loaded:
            return
        self.config_loaded = True
        client.piecepriority.get_config().addCallback(self._on_config)

    def _on_config(self, config: dict[str, Any]) -> None:
        self.config.update(config)
        self.layout = None
        for swatch, _key in self.legend_swatches:
            swatch.queue_draw()
        if self.torrent_id:
            self.update()

    # --- per-torrent update, driven by TorrentDetails ---

    def update(self) -> None:
        self.load_config()
        torrent_ids = component.get('TorrentView').get_selected_torrents()
        if not torrent_ids:
            self.clear()
            return
        torrent_id = torrent_ids[0]
        if torrent_id != self.torrent_id:
            self.torrent_id = torrent_id
            self.selected = set()
            self.last_clicked = None
            self.layout = None
        component.get('SessionProxy').get_torrent_status(
            torrent_id, ['pieces', 'num_pieces', 'piece_length']
        ).addCallback(self._on_status)
        # Priorities render as an overlay on missing pieces; fetch them on
        # the same cadence so the view tracks scheduling (a streaming
        # client's moving deadline window, a manual skip) live. Failure
        # (metadata still resolving) just means no overlay this tick.
        client.piecepriority.get_piece_priorities(torrent_id).addCallbacks(
            self._on_priorities, lambda _err: self._on_priorities(None)
        )

    def _on_status(self, status: dict[str, Any]) -> None:
        self.raw_pieces = status.get('pieces')
        num_pieces = status.get('num_pieces') or 0
        # A finished torrent's 'pieces' field is None -- Deluge stops
        # tracking per-piece download state once there's nothing left to
        # download -- so treat that as "every piece has" rather than
        # indexing into None.
        self.render_pieces = self.raw_pieces or [3] * num_pieces
        self.piece_length = status.get('piece_length') or 0

        if num_pieces != self.num_pieces or self.layout is None:
            self.num_pieces = num_pieces
            self.selected = set()
            self._relayout()

        self._update_info_label()
        self.drawing_area.queue_draw()

    def _on_priorities(self, priorities: list[int] | None) -> None:
        self.priorities = priorities
        self.drawing_area.queue_draw()

    def clear(self) -> None:
        self.torrent_id = None
        self.num_pieces = 0
        self.render_pieces = None
        self.raw_pieces = None
        self.priorities = None
        self.layout = None
        self.selected = set()
        self.last_clicked = None
        self.dragging = False
        self.hover_cell = None
        self.info_label.set_text('')
        self.drawing_area.queue_draw()

    # --- layout ---

    def _relayout(self) -> None:
        width = self.drawing_area.get_allocated_width() or 300
        self.layout = compute_layout(self.num_pieces, width, int(self.config['square_size']))
        cell_size, gap = self.layout.cell_size, self.layout.gap
        # Only pin height -- width is left to the parent's allocation (the
        # drawing area is packed with expand=True, fill=True) so
        # get_allocated_width() above reflects the real available width
        # rather than a value this same call already constrained.
        self.drawing_area.set_size_request(-1, self.layout.rows * (cell_size + gap) - gap)

    def on_size_allocate(self, widget: Gtk.DrawingArea, allocation: Any) -> None:
        if self.render_pieces is None:
            return
        self._relayout()
        self.drawing_area.queue_draw()

    def _update_info_label(self) -> None:
        size = deluge.common.fsize(self.piece_length)
        text = _('{0} pieces × {1} each').format(self.num_pieces, size)
        if self.layout and self.layout.pieces_per_cell > 1:
            text += ' — ' + _('each square represents {0} pieces').format(
                self.layout.pieces_per_cell
            )
        self.info_label.set_text(text)

    # --- drawing ---

    def on_draw(self, widget: Gtk.DrawingArea, ctx: Any) -> bool:
        if self.layout is None or self.render_pieces is None:
            return False
        grid = self.layout
        border_lw = max(1, min(int(self.config['square_border_size']), grid.cell_size // 2))
        priorities = self.priorities
        if priorities is not None and len(priorities) != self.num_pieces:
            priorities = None  # stale fetch from a previous torrent

        for cell in range(grid.cells):
            col = cell % grid.cols
            row = cell // grid.cols
            x = col * (grid.cell_size + grid.gap)
            y = row * (grid.cell_size + grid.gap)

            color = color_for_cell(
                cell, grid, self.num_pieces, self.render_pieces, self.config, priorities
            )
            ctx.set_source_rgb(*_hex_to_rgb(color))
            ctx.rectangle(x, y, grid.cell_size, grid.cell_size)
            ctx.fill()

            if cell in self.selected:
                ctx.set_source_rgb(*_hex_to_rgb(self.config['selected_border']))
                ctx.set_line_width(border_lw)
                ctx.rectangle(
                    x + border_lw / 2,
                    y + border_lw / 2,
                    grid.cell_size - border_lw,
                    grid.cell_size - border_lw,
                )
                ctx.stroke()
            elif cell == self.hover_cell:
                ctx.set_source_rgb(*_hex_to_rgb(self.config['hover_border']))
                ctx.set_line_width(1)
                ctx.rectangle(x + 0.5, y + 0.5, grid.cell_size - 1, grid.cell_size - 1)
                ctx.stroke()
        return False

    # --- selection / hover / tooltip ---

    def on_button_press(self, widget: Gtk.DrawingArea, event: Any) -> bool:
        if self.layout is None:
            return False
        cell = cell_index_from_xy(event.x, event.y, self.layout)
        if cell is None:
            return False

        if event.button == 3:
            if cell not in self.selected:
                self.selected = {cell}
                self.drawing_area.queue_draw()
            self.priority_menu.popup_at_pointer(event)
            return True

        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(event.state & Gdk.ModifierType.SHIFT_MASK)
        if not ctrl and not shift:
            self.selected = set()
        if shift and self.last_clicked is not None:
            lo, hi = sorted((self.last_clicked, cell))
            self.selected.update(range(lo, hi + 1))
        elif ctrl and cell in self.selected:
            self.selected.discard(cell)
        else:
            self.selected.add(cell)
        self.last_clicked = cell
        self.dragging = True
        self.drag_value = cell in self.selected
        self.drawing_area.queue_draw()
        return True

    def on_button_release(self, widget: Gtk.DrawingArea, event: Any) -> bool:
        self.dragging = False
        return False

    def on_motion(self, widget: Gtk.DrawingArea, event: Any) -> bool:
        if self.layout is None:
            return False
        cell = cell_index_from_xy(event.x, event.y, self.layout)
        if self.dragging and cell is not None:
            if self.drag_value:
                self.selected.add(cell)
            else:
                self.selected.discard(cell)
        if cell != self.hover_cell:
            self.hover_cell = cell
            self.drawing_area.queue_draw()
        return False

    def on_leave(self, widget: Gtk.DrawingArea, event: Any) -> bool:
        self.hover_cell = None
        self.drawing_area.queue_draw()
        return False

    def on_query_tooltip(
        self,
        widget: Gtk.DrawingArea,
        x: int,
        y: int,
        keyboard_mode: bool,
        tooltip: Gtk.Tooltip,
    ) -> bool:
        if self.layout is None:
            return False
        cell = cell_index_from_xy(x, y, self.layout)
        if cell is None:
            return False
        start, end = piece_range_for_cell(cell, self.layout, self.num_pieces)
        if self.layout.pieces_per_cell > 1:
            text = _('Pieces {0}–{1}').format(start, end - 1)
        else:
            text = _('Piece {0}').format(start)
        if self.priorities is not None and len(self.priorities) == self.num_pieces:
            prios = self.priorities[start:end]
            if prios:
                lo, hi = min(prios), max(prios)
                if lo == hi:
                    text += ' — ' + _('priority {0}').format(lo)
                else:
                    text += ' — ' + _('priority {0}–{1}').format(lo, hi)
        if self.raw_pieces is not None and len(self.raw_pieces) >= end:
            unavailable = sum(
                1 for st in self.raw_pieces[start:end] if st == PIECE_STATE_UNAVAILABLE
            )
            if unavailable:
                text += ' — ' + _('{0} unavailable in swarm').format(unavailable)
        tooltip.set_text(text)
        return True

    # --- priority menu ---

    def _on_priority_selected(self, _menu_item: Gtk.MenuItem, priority: int) -> None:
        if not self.torrent_id or self.layout is None:
            return
        priorities: dict[int, int] = {}
        for cell in self.selected:
            start, end = piece_range_for_cell(cell, self.layout, self.num_pieces)
            for piece in range(start, end):
                priorities[piece] = priority
        if not priorities:
            return
        client.piecepriority.set_piece_priorities(self.torrent_id, priorities)


class GtkUI(Gtk3PluginBase):
    def enable(self) -> None:
        self.tab = PiecePriorityTab()
        component.get('TorrentDetails').add_tab(self.tab)

    def disable(self) -> None:
        component.get('TorrentDetails').remove_tab(self.tab.get_name())
