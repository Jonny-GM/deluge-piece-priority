# WebUI

## Scope

A panel added to Deluge's stock WebUI (the same browser-based UI
`deluge-web` already serves), giving a human operator the same capability
the RPC surface gives a programmatic caller: view per-piece state and set
priority/deadlines by hand. It is a client of `01-core-rpc.md`'s methods —
it does not talk to libtorrent directly, and it registers no RPC methods
of its own.

Deluge's WebUI JSON-RPC bridge (`deluge/ui/web/json_api.py`'s
`JSON._exec_remote`) dynamically proxies any method already registered on
the daemon: `deluge.client.piecepriority.get_piece_priorities(...)` in the
browser reaches `Core.get_piece_priorities` from `01-core-rpc.md` with no
web-side re-implementation. The `WebPluginBase` half of this plugin
(`deluge_piecepriority/webui.py`) therefore does one thing: serve the
panel's JS bundle (`scripts = [...]`) to the browser.

## Piece view

A per-torrent panel (added to the existing torrent-details tabs), rendered
on a `<canvas>`, showing one square per piece (or, for large piece
counts, one square per group of pieces — see "Scaling to piece count"
below), colored by download state, with the priority of not-yet-downloaded
pieces overlaid as their fill:

- not downloaded and **no connected peer has it** (piece state `0`),
  unless skipped — unavailable color, default magenta (`#FF00FF`): no
  amount of priority or bandwidth fixes this square, only new peers can,
  so it must read differently from every "just not here yet" shade
- not downloaded and available from a peer, priority 1–6 — missing
  color, default gray (`#8A8A8A`)
- not downloaded, priority 7 — urgent color, default amber (`#FFA500`):
  the tier streaming-style callers assign to the pieces they need next,
  so an external scheduler's moving window is visible live
- not downloaded, priority 0 — skipped color, default black
  (`#000000`): will never download (file unwanted, or manually
  skipped), so it renders as the most background-like color — and its
  swarm availability is deliberately not shown (an unavailable piece
  nobody wants is noise, not signal)
- currently downloading — accent color, default blue
- downloaded — fill color, default red

Download state wins over priority: once bytes are moving or present, the
piece shows blue/red whatever its priority says — priority only matters
for idle bytes. Swarm unavailability ranks between the download states
and the priority shades: downloading proves the swarm is delivering, so
it wins; urgency loses, because urgency that can't be acted on is
exactly the condition worth surfacing. Priorities come from
`piecepriority.get_piece_priorities` on the same refresh cadence as
piece state; if the call fails (metadata still resolving) the grid
renders without the overlay for that tick.

A legend above the grid labels the six colors in lifecycle order, dead
to done (Skipped, Unavailable, Missing, Urgent, Downloading, Have), and
a line below it states the piece count and piece size (e.g. "8 pieces ×
256.0 KiB each"), since both vary enormously by torrent. Hovering a
square shows a tooltip with its piece index (or piece range, when
grouped), the pieces' priority (or priority range), and — when any
piece under the cursor is unavailable — how many pieces no connected
peer has.

Colors, selection/hover border colors, and square size are configurable
(see `05-packaging-compat.md` for the schema) — the defaults intentionally
match the reference `Deluge-Pieces-Plugin` UI's palette for anyone porting
settings over. Config lives on the Core RPC surface
(`get_config`/`set_config` in `01-core-rpc.md`), not stored web-side, so
the WebUI and a future GTK3 UI (`04-gtkui.md`) share one copy.

Piece state itself is read from Deluge's own stock
`core.get_torrent_status(torrent_id, ['pieces', 'num_pieces'])` — no
custom RPC method was needed for it; the `pieces` field already encodes
missing/downloading/have per piece. A finished torrent's `pieces` field is
`null` (Deluge stops tracking per-piece state once nothing's left to
download) — treated as "every piece has" rather than an empty view.

### Scaling to piece count

Torrents range from a handful of pieces to tens of thousands, so the grid
can't always be one square per piece: a few thousand 10px squares would
make an unreadably tall panel. The layout is recomputed against the
panel's actual width on every render (not read from the canvas's own
size, which is circular): squares are laid out in as many columns as fit,
wrapping to further rows, and if that would still exceed a fixed row
budget, consecutive pieces are grouped several-to-a-square (doubling the
group size until it fits) so the view stays a compact, fixed-height
overview regardless of piece count. A grouped square's color is the
worst-case of its underlying pieces (all have → have; else any
downloading → downloading; else missing, bucketed by the *maximum*
priority among its missing pieces — a group holding even one urgent piece
pops as urgent, and reads skipped only when every missing piece in it is
skipped) and selecting it for a priority change applies to every piece in
the group, not just one.

## Controls

- Click a piece (or shift/ctrl-click / drag to multi-select) to select it;
  right-click a selection to open a priority menu, and selecting a
  priority calls `set_piece_priorities` for the whole selection.
- No sequential-download convenience control. Deluge itself ships a
  per-torrent **Sequential Download** toggle (stock Options tab, mapped
  to libtorrent's `sequential_download` flag), so a plugin-side version
  would duplicate it while being strictly worse: a UI-driven polling loop
  only runs while the tab is open, and deadline-based sequencing silently
  forces pieces of unwanted files to priority 7 — fighting any external
  caller that manages file selection. Users who want in-order background
  fill use the stock toggle; per-piece urgency stays the domain of the
  RPC surface and the priority menu above.
- The panel registers as a tab on Deluge's existing torrent-details panel
  (`deluge.details.add(...)`, alongside the stock Status/Peers/Files
  tabs), not a standalone window — consistent with how Deluge's own
  bundled plugins add per-torrent UI.

## Non-goals

No deadline-setting UI in v1 (priority only) — deadlines are meant for
programmatic callers with a specific "I need this now" moment; a human
clicking through a UI is well served by priority alone. Add deadline
controls if real usage shows a need.
