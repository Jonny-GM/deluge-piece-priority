"""Core plugin: the RPC surface specified in docs/spec/01-core-rpc.md."""

from __future__ import annotations

from typing import Any, Callable, TypeVar

# libtorrent is a compiled extension with no type stubs; ty (unlike mypy)
# has no per-module "ignore missing" override, so this is suppressed at
# the single import site rather than globally.
import libtorrent as lt  # ty: ignore[unresolved-import]

# "as component" (not the shorter "import deluge.component") is the PEP 484
# explicit-reexport idiom: tests reach into this module's `component`
# attribute to monkeypatch it, which strict type checking otherwise flags
# as an implicit (unintentional) re-export.
import deluge.configmanager
from twisted.internet import reactor

from deluge import component as component
from deluge.core.rpcserver import export
from deluge.error import DelugeError, InvalidTorrentError
from deluge.plugins.pluginbase import CorePluginBase

T = TypeVar('T')

# docs/spec/05-packaging-compat.md's config schema.
DEFAULT_PREFS = {
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


class Core(CorePluginBase):
    def enable(self) -> None:
        self._config = deluge.configmanager.ConfigManager('piecepriority.conf', DEFAULT_PREFS)

    def disable(self) -> None:
        pass

    def update(self) -> None:
        pass

    def _handle(self, torrent_id: str) -> lt.torrent_handle:
        try:
            torrent = component.get('TorrentManager').torrents[torrent_id]
        except KeyError:
            raise InvalidTorrentError(f'Unknown torrent_id: {torrent_id}')
        handle: lt.torrent_handle = torrent.handle
        if not handle.has_metadata():
            raise ValueError(f'torrent {torrent_id} has no metadata yet')
        return handle

    def _check_piece(self, handle: lt.torrent_handle, piece: int) -> None:
        num_pieces = handle.torrent_file().num_pieces()
        if piece < 0 or piece >= num_pieces:
            raise ValueError(f'piece {piece} out of range [0, {num_pieces})')

    def _call(self, fn: Callable[..., T], *args: Any) -> T:
        try:
            return fn(*args)
        except RuntimeError as ex:
            raise DelugeError(str(ex))

    @export
    def set_piece_priority(self, torrent_id: str, piece: int, priority: int) -> None:
        handle = self._handle(torrent_id)
        self._check_piece(handle, piece)
        self._call(handle.piece_priority, piece, priority)

    @export
    def set_piece_priorities(
        self, torrent_id: str, priorities: dict[int, int] | dict[str, int]
    ) -> None:
        handle = self._handle(torrent_id)
        # JSON-RPC (the WebUI's transport) serializes all dict keys as
        # strings, so a dict sent over the wire as {0: 7} arrives here as
        # {'0': 7} -- coerce back to int before range-checking or handing
        # to libtorrent, which requires int piece indices.
        by_piece = {int(piece): priority for piece, priority in priorities.items()}
        for piece in by_piece:
            self._check_piece(handle, piece)
        for piece, priority in by_piece.items():
            self._call(handle.piece_priority, piece, priority)

    @export
    def get_piece_priorities(self, torrent_id: str) -> list[int]:
        handle = self._handle(torrent_id)
        return self._call(handle.piece_priorities)

    @export
    def rescue_piece(
        self, torrent_id: str, piece: int, ban_seconds: int = 10, min_speed: int = 8192
    ) -> 'list[str]':
        """Kick the stalled peers holding this piece's block requests.

        A piece can sit incomplete for a minute or more while its
        requested blocks are parked in a stalled peer's queue —
        libtorrent won't re-request them elsewhere until its own request
        timeout snubs that peer, long after a streaming deadline
        expired. The handle API exposes no per-peer disconnect, but the
        session IP filter does better: libtorrent disconnects matching
        peers immediately, which requeues their blocks, and the
        still-active deadline re-requests them from healthy peers at
        once.

        Only holders downloading slower than min_speed (bytes/s) are
        kicked: a slow-but-flowing peer will finish its blocks, and a
        kicked peer takes its bandwidth with it, so an indiscriminate
        ban trades one stall for a worse one. Bans each stalled
        holder's IP for ban_seconds (then re-allows) and returns the
        banned IPs; a no-op (empty list) when no stalled peer holds
        requested blocks of the piece.
        """
        handle = self._handle(torrent_id)
        self._check_piece(handle, piece)
        holders = set()
        for part in self._call(handle.get_download_queue):
            if part.get('piece_index') != piece:
                continue
            for block in part.get('blocks') or []:
                # Block states (libtorrent block_info): 0 none,
                # 1 requested, 2 writing, 3 finished. Only requested
                # blocks are hostages; writing/finished already arrived.
                if block.get('state') != 1:
                    continue
                peer = block.get('peer')
                ip = peer[0] if isinstance(peer, (tuple, list)) else None
                if ip and str(ip) not in ('0.0.0.0', '::'):
                    holders.add(str(ip))
        if not holders:
            return []

        ips = set()
        for info in self._call(handle.get_peer_info):
            ip = str(info.ip[0])
            if ip in holders and int(info.down_speed) < min_speed:
                ips.add(ip)
        if not ips:
            return []

        session = component.get('Core').session
        flt = session.get_ip_filter()
        for ip in ips:
            flt.add_rule(ip, ip, 1)  # 1 = blocked; disconnects the peer now
        session.set_ip_filter(flt)

        def unban() -> None:
            f = session.get_ip_filter()
            for ip in ips:
                f.add_rule(ip, ip, 0)  # re-allow
            session.set_ip_filter(f)

        # The reactor module's attributes are installed dynamically at
        # import time, which static analysis can't see.
        reactor.callLater(max(1, ban_seconds), unban)  # ty: ignore[unresolved-attribute]
        return sorted(ips)

    @export
    def get_peer_debug(self, torrent_id: str) -> 'list[dict]':
        """Wire-level per-peer state for stall diagnosis.

        The stock status fields say who is connected and how fast; they
        cannot distinguish "libtorrent stopped requesting" from "the
        peer stopped answering" — the two halves of a frozen transfer.
        The choke flags and request-queue depths here can.
        """
        handle = self._handle(torrent_id)
        peers = self._call(handle.get_peer_info)
        out = []
        for peer in peers:
            flags = int(peer.flags)
            out.append(
                {
                    'ip': f'{peer.ip[0]}:{peer.ip[1]}',
                    'client': peer.client.decode('utf-8', 'replace')
                    if isinstance(peer.client, bytes)
                    else str(peer.client),
                    'interesting': bool(flags & int(lt.peer_info.interesting)),
                    'choked': bool(flags & int(lt.peer_info.choked)),
                    'remote_interested': bool(
                        flags & int(lt.peer_info.remote_interested)
                    ),
                    'remote_choked': bool(flags & int(lt.peer_info.remote_choked)),
                    'download_queue_length': int(peer.download_queue_length),
                    'upload_queue_length': int(peer.upload_queue_length),
                    'downloading_piece_index': int(peer.downloading_piece_index),
                    'down_speed': int(peer.down_speed),
                    'up_speed': int(peer.up_speed),
                    'flags': flags,
                }
            )
        return out

    @export
    def set_piece_deadline(
        self,
        torrent_id: str,
        piece: int,
        deadline_ms: int,
        alert_when_available: bool = False,
    ) -> None:
        handle = self._handle(torrent_id)
        self._check_piece(handle, piece)
        flags = lt.deadline_flags_t.alert_when_available if alert_when_available else 0
        self._call(handle.set_piece_deadline, piece, deadline_ms, flags)

    @export
    def set_piece_deadlines(
        self, torrent_id: str, deadlines: dict[int, int] | dict[str, int]
    ) -> None:
        handle = self._handle(torrent_id)
        # Same string-key coercion as set_piece_priorities: JSON-RPC (the
        # WebUI's transport) serializes dict keys as strings.
        by_piece = {int(piece): deadline_ms for piece, deadline_ms in deadlines.items()}
        for piece in by_piece:
            self._check_piece(handle, piece)
        for piece, deadline_ms in by_piece.items():
            self._call(handle.set_piece_deadline, piece, deadline_ms, 0)

    @export
    def clear_piece_deadline(self, torrent_id: str, piece: int) -> None:
        handle = self._handle(torrent_id)
        self._check_piece(handle, piece)
        self._call(handle.reset_piece_deadline, piece)

    @export
    def clear_piece_deadlines(self, torrent_id: str) -> None:
        handle = self._handle(torrent_id)
        self._call(handle.clear_piece_deadlines)

    @export
    def verify(self, torrent_id: str) -> None:
        """Force a data recheck without joining the swarm.

        Deluge's own ``core.force_recheck`` resumes the handle and
        re-pauses it only when the torrent-checked alert is processed,
        leaving a window in which an idle torrent is briefly active in
        the swarm — long enough to form (and then tear down) real peer
        connections. libtorrent's ``stop_when_ready`` flag closes that
        window: the pause happens inside the session thread the moment
        checking completes, so no peer connection can ever form. The
        torrent ends paused with auto-management off; resuming it is the
        caller's decision.
        """
        handle = self._handle(torrent_id)
        self._call(handle.unset_flags, lt.torrent_flags.auto_managed)
        self._call(handle.set_flags, lt.torrent_flags.stop_when_ready)
        self._call(handle.force_recheck)
        self._call(handle.resume)

    @export
    def get_config(self) -> dict[str, object]:
        config: dict[str, object] = self._config.config
        return config

    @export
    def set_config(self, config: dict[str, object]) -> None:
        for key, value in config.items():
            self._config[key] = value
        self._config.save()
