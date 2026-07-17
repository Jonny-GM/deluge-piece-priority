# libtorrent interaction semantics

## Priority vs. deadline: which to use

These are two different libtorrent mechanisms, not two names for the same
thing:

- **`piece_priority`** adjusts a piece's standing in the general
  piece-picker ranking — it's one input among several (rarity, how close a
  piece already is to completion, sequential-download salt) that the
  picker weighs when deciding what to request next. Setting a piece to
  priority 7 makes it *more likely* to be picked soon; it does not
  guarantee it.
- **`set_piece_deadline`** is libtorrent's dedicated streaming primitive:
  pieces with a deadline are served from a separate, more aggressive path
  that takes precedence over the general picker's ranking. This is the one
  to reach for when a caller has a blocked read waiting on a specific
  piece.

Use `set_piece_priority`/`set_piece_priorities` to shape a broad
read-ahead window (deprioritize far-future pieces, keep the near window
elevated); use `set_piece_deadline` for the one or few pieces something is
actively blocked on right now.

A newly-added torrent's pieces default to priority **4**, not 1 —
confirmed live against a real `torrent_handle` (`get_piece_priorities`
read back `[4, 4, ...]` before this plugin touched anything). Don't
assume 1 as a baseline when reasoning about "untouched" pieces.

## Interaction with Deluge's own sequential-download and first/last-piece features

Deluge's `sequential_download` toggle and `prioritize_first_last_pieces`
option are themselves implemented on top of the same `piece_priority`
mechanism this plugin exposes directly. Running both at once on the same
torrent means two independent callers (Deluge core and whoever's driving
this plugin's RPC) are adjusting the same underlying state without
coordinating.

Decision: **callers driving a torrent through this plugin's RPC must turn
Deluge's own `sequential_download` off for that torrent first**, via
Deluge's stock `core.set_torrent_options` — not this plugin's job to
enforce (this plugin has no way to intercept that call), but the
documented precondition for predictable behavior.
`prioritize_first_last_pieces` is left as Deluge's own concern; it doesn't
fight with per-piece priority/deadline calls the same way
`sequential_download` does, since it only ever touches piece 0 and the
last piece.

## Persistence

Deadlines are never persisted — libtorrent treats them as ephemeral,
session-scoped state that only makes sense in the context of an active
read. Piece priorities set via this plugin are similarly treated as
ephemeral by this plugin's contract, regardless of whether the underlying
libtorrent/resume-data format happens to round-trip a priority array
across a restart in a given version: **callers must re-assert desired
priority/deadline state after `deluged` restarts, after a torrent is
paused/resumed, or after re-adding a torrent.**

## Metadata-not-yet-available

A magnet add without metadata yet has no piece count and no valid piece
indices. All RPC methods in `01-core-rpc.md` check `handle.has_metadata()`
and raise `ValueError` rather than silently no-op or block waiting for
metadata — callers are expected to wait for Deluge's own metadata-received
signal (stock `TorrentMetadataAddedEvent`) before calling in.
