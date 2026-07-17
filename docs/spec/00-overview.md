# Overview

## What this is

A Deluge Core plugin — plain Python, loaded through Deluge's own documented
plugin architecture — that exposes libtorrent's native per-piece priority
and deadline control, which Deluge's own core RPC never exposes (Deluge
core only offers a torrent-wide `sequential_download` toggle and per-file
priority). It runs in-process with `deluged`; there is no separate daemon
and no new listening port.

## Why

External seek-driven controllers — a media-streaming backend that needs
to say "fetch piece N now" and have it actually happen promptly, for
instance — can't express that through Deluge's stock RPC. libtorrent —
the engine underneath Deluge — already can: `torrent_handle.piece_priority()`
and `torrent_handle.set_piece_deadline()` are real, mature APIs Deluge's
own "prioritize first/last piece" feature already uses internally. This
plugin exposes them.

## Scope

- The RPC surface (`01-core-rpc.md`) is the contract. Everything else —
  the bundled WebUI panel and GTK3 UI — is a client of that same RPC
  surface, not a separate code path with private access to libtorrent. If
  the UI needs a capability, the RPC surface gains it first.
- Read access to piece state (bitfield, per-piece completion) is Deluge's
  own job (`core.get_torrent_status(torrent_id, ["pieces"])` is already a
  stock Deluge status field) — this plugin does not duplicate it. This
  plugin only adds what stock Deluge doesn't have: writing piece priority
  and deadlines.

## Non-goals

- Not a torrent download manager and not a general Deluge UI replacement —
  no add/remove/list-torrents RPC (Deluge core already has that).
- Not a tracker/DHT/session-settings tool.
- Does not persist its own state across a `deluged` restart — see
  `02-libtorrent-semantics.md`.

## Compatibility

- Deluge 2.0.x, 2.1.x, 2.2.x.
- libtorrent 1.2.x and 2.0.x — `piece_priority()` and `set_piece_deadline()`
  predate both, so either build works. The BitTorrent v2/hybrid-torrent
  behavior differences between libtorrent 1.2 and 2.0 are out of scope
  here since piece-level priority/deadline control is v1/v2-agnostic.
- Not tested against Deluge 1.x (Python 2, GTK2, a different plugin base
  API) — no support planned.

## Process model

Installed as a standard Deluge plugin package (setuptools entry points
under `deluge.plugin.core` / `deluge.plugin.gtk3ui` / `deluge.plugin.web`,
per `05-packaging-compat.md`), enabled/disabled through Deluge's own
plugin manager (GTK3 UI, WebUI, or `deluge-console`). No install step
beyond what any other Deluge plugin needs.
