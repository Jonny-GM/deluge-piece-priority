"""Unit tests for the GTK3 UI's piece-grid layout math (piece_layout.py) --
no display or GTK needed, since this module is deliberately kept free of
any gi/Gtk import.
"""

from __future__ import annotations

from deluge_piecepriority.piece_layout import (
    PIECE_STATE_AVAILABLE,
    PIECE_STATE_UNAVAILABLE,
    MAX_GRID_HEIGHT_PX,
    PIECE_STATE_DOWNLOADING,
    PIECE_STATE_HAVE,
    Layout,
    cell_index_from_xy,
    color_for_cell,
    compute_layout,
    piece_range_for_cell,
)

COLORS = {
    'not_dled_color': '#000000',
    'unavailable_color': '#FF00FF',
    'dled_color': '#FF0000',
    'dling_color': '#0000FF',
    'urgent_color': '#FFA500',
    'skipped_color': '#8A8A8A',
}


def test_small_piece_count_fits_one_row() -> None:
    layout = compute_layout(num_pieces=8, width=900, cell_size=10)

    assert layout.pieces_per_cell == 1
    assert layout.rows == 1
    assert layout.cells == 8


def test_medium_piece_count_wraps_without_binning() -> None:
    # 200 squares at 10px (+1px gap) fit about 81 per row in a 900px
    # panel, so this should wrap into a couple of rows but still render
    # one square per piece.
    layout = compute_layout(num_pieces=200, width=900, cell_size=10)

    assert layout.pieces_per_cell == 1
    assert layout.rows > 1
    assert layout.cols * layout.rows >= 200


def test_huge_piece_count_gets_binned_within_height_budget() -> None:
    layout = compute_layout(num_pieces=10240, width=900, cell_size=10)

    assert layout.pieces_per_cell > 1
    assert layout.cells * layout.pieces_per_cell >= 10240
    assert layout.rows * (layout.cell_size + layout.gap) <= MAX_GRID_HEIGHT_PX


def test_single_piece_torrent() -> None:
    layout = compute_layout(num_pieces=1, width=900, cell_size=10)

    assert layout.cells == 1
    assert layout.pieces_per_cell == 1
    assert layout.cols == 1
    assert layout.rows == 1


def test_narrow_width_still_terminates() -> None:
    # A pathologically narrow panel shouldn't hang computing a layout --
    # cols clamps to at least 1, so pieces_per_cell has to grow to keep
    # rows within budget.
    layout = compute_layout(num_pieces=5000, width=20, cell_size=10)

    assert layout.cols >= 1
    assert layout.rows * (layout.cell_size + layout.gap) <= MAX_GRID_HEIGHT_PX


def test_color_for_cell_ungrouped_reflects_exact_piece_state() -> None:
    layout = compute_layout(num_pieces=3, width=900, cell_size=10)
    pieces = [PIECE_STATE_AVAILABLE, PIECE_STATE_DOWNLOADING, PIECE_STATE_HAVE]

    assert color_for_cell(0, layout, 3, pieces, COLORS) == COLORS['not_dled_color']
    assert color_for_cell(1, layout, 3, pieces, COLORS) == COLORS['dling_color']
    assert color_for_cell(2, layout, 3, pieces, COLORS) == COLORS['dled_color']


def test_color_for_cell_grouped_all_have_is_have() -> None:
    layout = compute_layout(num_pieces=4, width=900, cell_size=10)
    grouped_layout = Layout(
        cell_size=layout.cell_size,
        gap=layout.gap,
        pieces_per_cell=4,
        cols=1,
        rows=1,
        cells=1,
    )
    pieces = [PIECE_STATE_HAVE] * 4

    assert color_for_cell(0, grouped_layout, 4, pieces, COLORS) == COLORS['dled_color']


def test_color_for_cell_grouped_any_downloading_is_downloading() -> None:
    layout = compute_layout(num_pieces=4, width=900, cell_size=10)
    grouped_layout = Layout(
        cell_size=layout.cell_size,
        gap=layout.gap,
        pieces_per_cell=4,
        cols=1,
        rows=1,
        cells=1,
    )
    pieces = [PIECE_STATE_HAVE, PIECE_STATE_AVAILABLE, PIECE_STATE_DOWNLOADING, PIECE_STATE_HAVE]

    assert color_for_cell(0, grouped_layout, 4, pieces, COLORS) == COLORS['dling_color']


def test_color_for_cell_grouped_missing_only_is_missing() -> None:
    layout = compute_layout(num_pieces=4, width=900, cell_size=10)
    grouped_layout = Layout(
        cell_size=layout.cell_size,
        gap=layout.gap,
        pieces_per_cell=4,
        cols=1,
        rows=1,
        cells=1,
    )
    pieces = [1, 1, 1, 1]

    assert color_for_cell(0, grouped_layout, 4, pieces, COLORS) == COLORS['not_dled_color']


def test_piece_range_for_cell_ungrouped() -> None:
    layout = compute_layout(num_pieces=8, width=900, cell_size=10)

    assert piece_range_for_cell(3, layout, 8) == (3, 4)


def test_piece_range_for_cell_clamps_last_group() -> None:
    # 10 pieces at pieces_per_cell=4 -> 3 cells (4, 4, 2); the last cell's
    # range must clamp to num_pieces, not run past it.
    layout = compute_layout(num_pieces=10, width=900, cell_size=10)
    grouped_layout = Layout(
        cell_size=layout.cell_size,
        gap=layout.gap,
        pieces_per_cell=4,
        cols=3,
        rows=1,
        cells=3,
    )

    assert piece_range_for_cell(0, grouped_layout, 10) == (0, 4)
    assert piece_range_for_cell(1, grouped_layout, 10) == (4, 8)
    assert piece_range_for_cell(2, grouped_layout, 10) == (8, 10)


def test_cell_index_from_xy_hits_correct_cell() -> None:
    layout = compute_layout(num_pieces=8, width=900, cell_size=10)
    step = layout.cell_size + layout.gap

    assert cell_index_from_xy(0, 0, layout) == 0
    assert cell_index_from_xy(2 * step + 2, 0, layout) == 2


def test_cell_index_from_xy_in_gap_returns_none() -> None:
    layout = compute_layout(num_pieces=8, width=900, cell_size=10)
    step = layout.cell_size + layout.gap

    # The last pixel of each step-sized cell is the 1px gap.
    assert cell_index_from_xy(layout.cell_size, 0, layout) is None


def test_cell_index_from_xy_out_of_bounds_returns_none() -> None:
    layout = compute_layout(num_pieces=8, width=900, cell_size=10)

    assert cell_index_from_xy(-1, 0, layout) is None
    assert cell_index_from_xy(0, -1, layout) is None
    assert cell_index_from_xy(100000, 0, layout) is None


def test_color_for_cell_priority_buckets_missing_pieces() -> None:
    layout = compute_layout(num_pieces=3, width=900, cell_size=10)
    pieces = [PIECE_STATE_AVAILABLE] * 3

    assert color_for_cell(0, layout, 3, pieces, COLORS, [7, 4, 0]) == COLORS['urgent_color']
    assert color_for_cell(1, layout, 3, pieces, COLORS, [7, 4, 0]) == COLORS['not_dled_color']
    assert color_for_cell(2, layout, 3, pieces, COLORS, [7, 4, 0]) == COLORS['skipped_color']


def test_color_for_cell_download_state_wins_over_priority() -> None:
    layout = compute_layout(num_pieces=2, width=900, cell_size=10)

    # An urgent piece that's already downloading/downloaded shows its
    # state, not its priority -- priority only matters for idle bytes.
    assert color_for_cell(0, layout, 2, [2, 0], COLORS, [7, 7]) == COLORS['dling_color']
    assert color_for_cell(0, layout, 2, [3, 3], COLORS, [7, 7]) == COLORS['dled_color']


def test_color_for_cell_grouped_priority_takes_maximum() -> None:
    # 4 pieces per cell: one urgent piece among skipped/normal pops the
    # whole cell as urgent; skip shows only when every missing piece is
    # skipped.
    grouped_layout = Layout(cell_size=10, gap=1, pieces_per_cell=4, cols=1, rows=1, cells=1)
    pieces = [PIECE_STATE_AVAILABLE] * 4

    assert (
        color_for_cell(0, grouped_layout, 4, pieces, COLORS, [0, 4, 0, 7])
        == COLORS['urgent_color']
    )
    assert (
        color_for_cell(0, grouped_layout, 4, pieces, COLORS, [0, 4, 0, 4])
        == COLORS['not_dled_color']
    )
    assert (
        color_for_cell(0, grouped_layout, 4, pieces, COLORS, [0, 0, 0, 0])
        == COLORS['skipped_color']
    )


def test_color_for_cell_no_priorities_falls_back_to_missing() -> None:
    layout = compute_layout(num_pieces=2, width=900, cell_size=10)

    missing = [PIECE_STATE_AVAILABLE, PIECE_STATE_AVAILABLE]
    assert color_for_cell(0, layout, 2, missing, COLORS) == COLORS['not_dled_color']
    assert color_for_cell(0, layout, 2, missing, COLORS, None) == COLORS['not_dled_color']


def _grouped(n: int) -> Layout:
    base = compute_layout(num_pieces=n, width=900, cell_size=10)
    return Layout(
        cell_size=base.cell_size,
        gap=base.gap,
        pieces_per_cell=n,
        cols=1,
        rows=1,
        cells=1,
    )


def test_unavailable_wanted_piece_colors_cell_unavailable() -> None:
    # State 0 = no connected peer has the piece. A wanted missing piece in
    # that state colors the cell unavailable — over the priority shades,
    # under the download states (downloading proves availability).
    layout = compute_layout(num_pieces=2, width=900, cell_size=10)
    pieces = [PIECE_STATE_UNAVAILABLE, PIECE_STATE_AVAILABLE]

    # Without a priority overlay every missing piece counts as wanted.
    assert color_for_cell(0, layout, 2, pieces, COLORS) == COLORS['unavailable_color']
    assert color_for_cell(1, layout, 2, pieces, COLORS) == COLORS['not_dled_color']

    # An urgent-but-unavailable piece reads unavailable, not urgent: the
    # urgency can't be acted on until a peer with the piece shows up.
    assert (
        color_for_cell(0, layout, 2, pieces, COLORS, priorities=[7, 7])
        == COLORS['unavailable_color']
    )


def test_unavailable_skipped_piece_is_noise_not_signal() -> None:
    # A skipped piece was never going to download; its unavailability
    # must not light the cell up.
    layout = compute_layout(num_pieces=1, width=900, cell_size=10)
    pieces = [PIECE_STATE_UNAVAILABLE]

    assert (
        color_for_cell(0, layout, 1, pieces, COLORS, priorities=[0])
        == COLORS['skipped_color']
    )


def test_unavailable_beats_priority_within_grouped_cell() -> None:
    grouped = _grouped(4)
    pieces = [
        PIECE_STATE_HAVE,
        PIECE_STATE_AVAILABLE,
        PIECE_STATE_UNAVAILABLE,
        PIECE_STATE_AVAILABLE,
    ]

    assert (
        color_for_cell(0, grouped, 4, pieces, COLORS, priorities=[4, 7, 4, 4])
        == COLORS['unavailable_color']
    )


def test_downloading_beats_unavailable_within_grouped_cell() -> None:
    # Downloading is proof the swarm is delivering the cell; that beats
    # the unavailability of a sibling piece.
    grouped = _grouped(2)
    pieces = [PIECE_STATE_DOWNLOADING, PIECE_STATE_UNAVAILABLE]

    assert color_for_cell(0, grouped, 2, pieces, COLORS) == COLORS['dling_color']
