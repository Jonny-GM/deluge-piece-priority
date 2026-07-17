# Core RPC surface

## Namespace and transport

Deluge dispatches RPC methods exported (`@export`) from a
`CorePluginBase` subclass under `<plugin_name.lower()>.<method_name>`,
over Deluge's own existing daemon RPC channel (rencode-serialized, TLS,
the same port and authentication `deluged` already uses — confirmed
directly against `deluge/plugins/pluginbase.py`: `CorePluginBase.__init__`
calls `component.get('RPCServer').register_object(self, plugin_name.lower())`).
This plugin registers as `PiecePriority`, so every method below is called
as `piecepriority.<name>` — e.g. `piecepriority.set_piece_priority(...)`.
There is no separate listening port and no separate authentication: a
caller that already has valid Deluge daemon credentials can call these
methods; nothing new to secure.

## Methods

### `set_piece_priority(torrent_id: str, piece: int, priority: int) -> None`

Direct passthrough to `handle.piece_priority(piece, priority)`. `priority`
is libtorrent's native 0-7 scale (0 = don't download, 1 = default, 7 =
highest). Raises `InvalidTorrentError` if `torrent_id` is unknown,
`ValueError` if `piece` is out of range or the torrent has no metadata yet
(checked before calling into libtorrent, so the caller gets a clean RPC
error instead of a libtorrent-internal exception).

### `set_piece_priorities(torrent_id: str, priorities: dict[int, int]) -> None`

Bulk form of the above — one RPC round trip for a whole window of pieces,
since a caller managing a read-ahead window sets many pieces at once. Same
validation as the single-piece form, applied per entry; a single bad piece
index fails the whole call (no partial application) so callers don't have
to reconcile which entries silently didn't take.

### `get_piece_priorities(torrent_id: str) -> list[int]`

Passthrough to `handle.piece_priorities()` — the full per-piece priority
array, index-aligned to piece number. Mainly useful for debugging/
inspection (the WebUI/GTK3 UI use it); callers driving playback don't need
to poll this since they're the one setting priorities.

### `rescue_piece(torrent_id: str, piece: int, ban_seconds: int = 10, min_speed: int = 8192) -> list[str]`

Kicks the stalled peers holding a piece's outstanding block requests. A
piece can sit incomplete for a minute-plus while its requested blocks
are parked in a stalled peer's queue — libtorrent won't re-request them
elsewhere until its own request timeout snubs that peer, long after any
streaming deadline expired. The handle API has no per-peer disconnect,
so the kick goes through the session IP filter (libtorrent disconnects
matching peers immediately, requeueing their blocks); each kicked IP is
re-allowed after `ban_seconds`.

Only holders downloading slower than `min_speed` bytes/s are kicked — a
slow-but-flowing peer will finish its blocks, and a kicked peer takes
its bandwidth with it, so an indiscriminate ban trades one stall for a
worse one. Returns the banned IPs; an empty list
means no stalled peer held requested blocks of the piece (nothing was
touched). Note the ban briefly layers rules onto the session-wide IP
filter, so it can momentarily interact with a blocklist plugin's rules
for those specific IPs.

### `get_peer_debug(torrent_id: str) -> list[dict]`

Wire-level per-peer state from `handle.get_peer_info()`: for each
connected peer, `ip`, `client`, the four choke/interest flags
(`interesting`, `choked`, `remote_interested`, `remote_choked`), the
request-queue depths (`download_queue_length`, `upload_queue_length`),
`downloading_piece_index`, current transfer rates, and the raw `flags`
bitmask. Pure diagnostics — Deluge's stock status fields say who is
connected and how fast, but cannot distinguish "libtorrent stopped
requesting" from "the peer stopped answering", which is exactly the
split a frozen transfer needs (this method exists because a live
harness hit such freezes and the stock fields couldn't attribute them).

### `set_piece_deadline(torrent_id: str, piece: int, deadline_ms: int, alert_when_available: bool = False) -> None`

Passthrough to `handle.set_piece_deadline(piece, deadline_ms, flags)`,
where `flags = deadline_flags_t.alert_when_available if alert_when_available else 0`.
This is the actual "I need this piece soon" primitive — libtorrent's
streaming-oriented deadline queue takes priority over the general
piece-picker ranking, unlike `set_piece_priority` which only adjusts
standing among the picker's other criteria (see
`02-libtorrent-semantics.md`). Use this, not priority alone, for a
blocked read waiting on a specific piece.

`alert_when_available` is accepted but v1 does not forward the resulting
`read_piece_alert` over RPC — no alert-forwarding channel exists yet (see
Known limitations below). Set it to `false` unless you have another reason
to want it queued; callers should poll
`core.get_torrent_status(torrent_id, ["pieces"])` (a stock Deluge status
field, not part of this plugin) to observe completion.

### `set_piece_deadlines(torrent_id: str, deadlines: dict[int, int]) -> None`

Bulk form of `set_piece_deadline` — one RPC round trip for many pieces,
since a caller re-ranking urgency across several read-ahead windows sets
many deadlines at once. Values are per-piece `deadline_ms`. Same
validation as the single-piece form, applied per entry; a single bad piece
index fails the whole call (no partial application), matching
`set_piece_priorities`. No `alert_when_available` parameter — the bulk
form always passes flags `0`, since the alert has no RPC forwarding
channel anyway (see Known limitations) and per-piece flag control in bulk
has no caller to justify it.

### `clear_piece_deadline(torrent_id: str, piece: int) -> None`

Passthrough to `handle.reset_piece_deadline(piece)`.

### `clear_piece_deadlines(torrent_id: str) -> None`

Passthrough to `handle.clear_piece_deadlines()` — clears every deadline on
the torrent in one call. Callers should call this when a stream closes, to
release the torrent back to whatever priority-only or default behavior
applies, rather than leaving stale deadlines around.

### `verify(torrent_id: str) -> None`

Forces a data recheck without joining the swarm, for callers that add
torrents paused (say, to verify pre-existing data on disk) and want them
to stay effectively paused throughout.

Deluge's own `core.force_recheck` resumes the handle and re-pauses it
only when the torrent-checked alert is processed, which leaves a window —
alert-latency sized, easily hundreds of milliseconds — in which an
otherwise-idle torrent is briefly live in the swarm: it announces, forms
real peer connections, and then has them torn down by the re-pause,
leaving both sides of each connection in libtorrent's ~60s reconnect
backoff. `verify` closes that window with libtorrent's `stop_when_ready`
flag: the pause is applied inside the session thread the moment checking
completes, so the torrent never announces and no peer connection can
form. Verified live: a 256 MiB torrent rechecks to 100% and lands paused
with zero tracker announces.

The torrent ends paused with auto-management off; resuming it (which
restores auto-management via Deluge's normal `core.resume_torrent` path)
is the caller's decision. Metadata is required, as everywhere else in
this namespace — the method rechecks data, and a metadataless magnet has
none.

### `get_config() -> dict`

Returns the plugin's config dict — the color/sizing preferences in
`05-packaging-compat.md`'s schema. Not per-torrent, no arguments.

### `set_config(config: dict) -> None`

Merges the given keys into the config and persists it (`ConfigManager`
handles the write). Unknown keys are accepted and stored as-is — no
schema validation — matching Deluge's own convention for plugin config
(`core.set_config`, `Label.set_config`, etc. all behave the same way).

This is core-side and shared by both UIs deliberately, not split into a
WebUI-local and a GTK3-UI-local copy: colors are a "how do I want pieces
drawn" preference a user sets once, not something that should differ by
which client they happen to have open. `01-core-rpc.md`'s other methods
have no config-driven behavior — this only affects rendering in the two
UIs, never `set_piece_priority`/`set_piece_deadline`'s actual effect.

## Error semantics

- Unknown `torrent_id`: `InvalidTorrentError` (Deluge's own standard error
  type for this — reusing it rather than inventing a new one keeps
  caller-side error handling consistent with every other Deluge RPC
  method).
- Out-of-range `piece`, or torrent has no metadata yet
  (`handle.has_metadata()` is `False` — a magnet still resolving):
  `ValueError` with a message naming which check failed. Never let a raw
  libtorrent exception or assertion propagate to the caller.
- All other libtorrent-side failures (a call libtorrent itself rejects)
  propagate as `DelugeError` with the underlying message attached — not
  swallowed.

## Known limitations (v1)

- No alert-forwarding: nothing here streams `read_piece_alert`/
  `piece_finished_alert` back to an RPC caller. Poll
  `core.get_torrent_status` instead.
