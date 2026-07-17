# GTK3 UI

## Scope

The desktop-client equivalent of `03-webui.md` — same piece view, same
controls, same underlying RPC calls, added as a tab in Deluge's GTK3
client's torrent-details pane. `Gtk3PluginBase` (per
`deluge/plugins/pluginbase.py`) doesn't get its own RPC registration the
way Core/Web plugins do — the GTK3 side is a plain RPC *client*, calling
`piecepriority.*` through the GTK3 UI's existing connection to `deluged`,
same as any other GTK3 UI panel calls `core.*` methods.

The tab is a `deluge.ui.gtk3.torrentdetails.Tab` subclass added via
`component.get('TorrentDetails').add_tab(...)`, matching the pattern
Deluge's own bundled Stats plugin uses for its per-torrent graphs tab
(confirmed by reading its shipped `gtkui.py`) — not a `.ui`/Glade file,
since the widget tree here (a legend, a drawing area, a label) is
simple enough to build directly in Python.

## Piece view and controls

Same behavior and defaults as `03-webui.md`'s piece view and controls —
same color scheme, same click/multi-select-then-set-priority interaction,
same adaptive
layout that groups several pieces per square once a torrent has too many
to render one-square-per-piece (see `03-webui.md`'s "Scaling to piece
count" — same algorithm, ported to Python in
`deluge_piecepriority/piece_layout.py` rather than shared at runtime,
since the two UIs don't share a process). The two UIs are meant to be
indistinguishable in capability; whichever a user has open just reflects
which Deluge UI they run. Config (colors, sizing) is the same core-side
`get_config`/`set_config` both UIs already share — nothing GTK3-specific.

Rendered on a `Gtk.DrawingArea` with Cairo (the GTK3 analogue of the
WebUI's `<canvas>`), with piece-index/range tooltips via GTK's native
`query-tooltip` mechanism rather than a custom floating widget. The
piece-grid layout math (`piece_layout.py`) has no `gi`/`Gtk` import and
is unit-tested directly; the widget itself needs a running GTK3 client
(specifically `component.get('MainWindow')`, which `Tab.__init__`
depends on) to construct at all, so it's validated by driving a real
`deluge-gtk` process instead — confirmed working end to end against a
real `deluged`, including the adaptive layout on a real 10,240-piece
torrent and a real RPC round trip for a priority change made through the
right-click menu.

## Non-goals

Same as the WebUI: no deadline-setting UI in v1 (the "prioritize first
un-downloaded piece" checkbox is the one deadline-driven convenience both
UIs expose; see `03-webui.md`).
