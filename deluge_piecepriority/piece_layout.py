"""Piece-grid layout math shared by the GTK3 UI's Cairo-drawn grid --
factored out of deluge_piecepriority/gtkui.py so it's unit-testable
without a real display. gtkui.py imports gi.repository.Gtk at module
level, which needs a running X/Wayland-adjacent environment just to
import; this module needs neither Gtk nor a display.

Mirrors deluge_piecepriority/data/piecepriority.js's layout logic for the
WebUI's canvas grid -- same algorithm, ported rather than shared, since
the two UIs don't share a runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

PIECE_STATE_HAVE = 3
PIECE_STATE_DOWNLOADING = 2
PIECE_STATE_AVAILABLE = 1  # a connected peer has it; not downloading
PIECE_STATE_UNAVAILABLE = 0  # no connected peer has it

# libtorrent's 0-7 piece-priority scale, bucketed for display: 0 means the
# piece will never download (file skipped / manual skip), 7 is the top
# priority streaming-style callers assign to the pieces they need next.
# Everything in between renders as ordinary "missing".
PIECE_PRIORITY_SKIP = 0
PIECE_PRIORITY_URGENT = 7

MIN_CELL_PX = 4
CELL_GAP_PX = 1
# A torrent can have anywhere from a handful of pieces to tens of
# thousands. Rows are capped to this pixel budget -- compute_layout()
# groups multiple pieces per square as needed to stay under it, so the
# grid stays a compact overview no matter how many pieces the torrent has.
MAX_GRID_HEIGHT_PX = 360


@dataclass(frozen=True)
class Layout:
    cell_size: int
    gap: int
    pieces_per_cell: int
    cols: int
    rows: int
    cells: int


def compute_layout(num_pieces: int, width: int, cell_size: int) -> Layout:
    cell_size = max(MIN_CELL_PX, cell_size)
    gap = CELL_GAP_PX
    pieces_per_cell = 1
    while True:
        cells = -(-num_pieces // pieces_per_cell)  # ceil division
        cols = max(1, min(cells, (width + gap) // (cell_size + gap)))
        rows = -(-cells // cols)
        if rows * (cell_size + gap) <= MAX_GRID_HEIGHT_PX or pieces_per_cell >= num_pieces:
            break
        pieces_per_cell *= 2
    return Layout(cell_size, gap, pieces_per_cell, cols, rows, cells)


def piece_range_for_cell(cell_index: int, layout: Layout, num_pieces: int) -> tuple[int, int]:
    """The [start, end) piece indices a cell covers."""
    start = cell_index * layout.pieces_per_cell
    end = min(num_pieces, start + layout.pieces_per_cell)
    return start, end


def color_for_cell(
    cell_index: int,
    layout: Layout,
    num_pieces: int,
    pieces: list[int],
    colors: dict[str, str],
    priorities: list[int] | None = None,
) -> str:
    """Fill color for one cell.

    Download state wins: a cell with everything downloaded is dled_color
    and one with anything actively downloading is dling_color, whatever
    the priorities say — priority only matters for bytes that aren't
    moving yet. Next comes swarm availability: a cell holding a *wanted*
    missing piece that no connected peer has (Deluge piece state 0) is
    unavailable_color — bandwidth can't fix that cell, only new peers
    can, which is exactly what makes it worth surfacing over the
    priority shades. Unavailability of skipped pieces is noise (they
    were never going to download) and doesn't trigger it. The remaining
    purely-missing cells are colored by the *maximum* priority among
    their missing pieces (so a binned cell holding even one urgent piece
    pops as urgent, and reads as skipped only when every missing piece
    in it is skipped): urgent_color at priority 7, skipped_color at 0,
    not_dled_color in between — or always not_dled_color when no
    priority list is available (metadata pending, RPC failure).
    """
    start, end = piece_range_for_cell(cell_index, layout, num_pieces)
    all_have = True
    any_downloading = False
    any_unavailable_wanted = False
    max_missing_priority = None
    for i in range(start, end):
        state = pieces[i]
        if state != PIECE_STATE_HAVE:
            all_have = False
            prio = None
            if priorities is not None and i < len(priorities):
                prio = priorities[i]
                if max_missing_priority is None or prio > max_missing_priority:
                    max_missing_priority = prio
            if state == PIECE_STATE_UNAVAILABLE and (
                prio is None or prio > PIECE_PRIORITY_SKIP
            ):
                any_unavailable_wanted = True
        if state == PIECE_STATE_DOWNLOADING:
            any_downloading = True
    if all_have:
        return colors['dled_color']
    if any_downloading:
        return colors['dling_color']
    if any_unavailable_wanted:
        return colors['unavailable_color']
    if max_missing_priority is not None:
        if max_missing_priority >= PIECE_PRIORITY_URGENT:
            return colors['urgent_color']
        if max_missing_priority == PIECE_PRIORITY_SKIP:
            return colors['skipped_color']
    return colors['not_dled_color']


def cell_index_from_xy(x: float, y: float, layout: Layout) -> int | None:
    step = layout.cell_size + layout.gap
    col = int(x // step)
    row = int(y // step)
    if col < 0 or col >= layout.cols or row < 0:
        return None
    if x - col * step >= layout.cell_size:
        return None
    if y - row * step >= layout.cell_size:
        return None
    idx = row * layout.cols + col
    return idx if idx < layout.cells else None
