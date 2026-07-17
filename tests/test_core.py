"""Unit tests for the Core RPC surface (docs/spec/01-core-rpc.md), against a
fake torrent_handle rather than a live Deluge/libtorrent session.
"""

from __future__ import annotations

import pytest

import deluge_piecepriority.core as core_mod
from deluge.error import DelugeError, InvalidTorrentError


class FakeTorrentFile:
    def __init__(self, num_pieces: int) -> None:
        self._num_pieces = num_pieces

    def num_pieces(self) -> int:
        return self._num_pieces


class FakeHandle:
    def __init__(self, num_pieces: int = 10, metadata: bool = True) -> None:
        self._num_pieces = num_pieces
        self._metadata = metadata
        # 4, not 1, matches real libtorrent's default piece priority
        # (confirmed live — see docs/spec/02-libtorrent-semantics.md).
        self._priorities: list[int] = [4] * num_pieces
        self.deadlines: dict[int, tuple[int, int]] = {}
        self.cleared = False
        self.calls: list[tuple] = []

    def has_metadata(self) -> bool:
        return self._metadata

    def torrent_file(self) -> FakeTorrentFile:
        return FakeTorrentFile(self._num_pieces)

    def piece_priority(self, piece: int, priority: int) -> None:
        self._priorities[piece] = priority

    def piece_priorities(self) -> list[int]:
        return list(self._priorities)

    def set_piece_deadline(self, piece: int, deadline_ms: int, flags: int = 0) -> None:
        self.deadlines[piece] = (deadline_ms, flags)

    def reset_piece_deadline(self, piece: int) -> None:
        self.deadlines.pop(piece, None)

    def clear_piece_deadlines(self) -> None:
        self.deadlines.clear()
        self.cleared = True

    def set_flags(self, flags: int) -> None:
        self.calls.append(('set_flags', flags))

    def unset_flags(self, flags: int) -> None:
        self.calls.append(('unset_flags', flags))

    def force_recheck(self) -> None:
        self.calls.append(('force_recheck',))

    def resume(self) -> None:
        self.calls.append(('resume',))


class FakeTorrent:
    def __init__(self, handle: FakeHandle) -> None:
        self.handle = handle


class FakeTorrentManager:
    def __init__(self, torrents: dict[str, FakeTorrent]) -> None:
        self.torrents = torrents


class FakeConfig:
    def __init__(self, config: dict[str, object]) -> None:
        self.config = config
        self.saved = False

    def __setitem__(self, key: str, value: object) -> None:
        self.config[key] = value

    def save(self) -> None:
        self.saved = True


@pytest.fixture
def plugin_core() -> core_mod.Core:
    # Bypass CorePluginBase.__init__, which registers with a live RPCServer
    # component — irrelevant to testing the RPC methods' own logic.
    return core_mod.Core.__new__(core_mod.Core)


def patch_manager(
    monkeypatch: pytest.MonkeyPatch, torrents: dict[str, FakeTorrent]
) -> FakeTorrentManager:
    manager = FakeTorrentManager(torrents)

    def fake_get(name: str) -> FakeTorrentManager:
        return manager

    monkeypatch.setattr(core_mod.component, 'get', fake_get)
    return manager


def test_set_piece_priority(plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.set_piece_priority('abc', 2, 7)

    assert handle.piece_priorities()[2] == 7


def test_set_piece_priority_unknown_torrent(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_manager(monkeypatch, {})

    with pytest.raises(InvalidTorrentError):
        plugin_core.set_piece_priority('nope', 0, 7)


def test_set_piece_priority_out_of_range(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    with pytest.raises(ValueError):
        plugin_core.set_piece_priority('abc', 5, 7)
    with pytest.raises(ValueError):
        plugin_core.set_piece_priority('abc', -1, 7)


def test_no_metadata_raises(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5, metadata=False)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    with pytest.raises(ValueError):
        plugin_core.set_piece_priority('abc', 0, 7)


def test_set_piece_priorities_bulk(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.set_piece_priorities('abc', {0: 7, 1: 3})

    assert handle.piece_priorities()[0] == 7
    assert handle.piece_priorities()[1] == 3


def test_set_piece_priorities_bulk_accepts_string_keys(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The WebUI's JSON-RPC transport serializes dict keys as strings, so a
    # dict sent as {0: 7} arrives here as {'0': 7} -- this is the shape a
    # real browser client actually sends, not just direct Python calls.
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.set_piece_priorities('abc', {'0': 7, '1': 3})

    assert handle.piece_priorities()[0] == 7
    assert handle.piece_priorities()[1] == 3


def test_set_piece_priorities_bulk_fails_atomically(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    with pytest.raises(ValueError):
        plugin_core.set_piece_priorities('abc', {0: 7, 99: 3})

    # Validated before any calls were made — piece 0 must be untouched.
    assert handle.piece_priorities()[0] == 4


def test_get_piece_priorities(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=3)
    handle.piece_priority(1, 5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    assert plugin_core.get_piece_priorities('abc') == [4, 5, 4]


def test_set_piece_deadline(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.set_piece_deadline('abc', 2, 500)

    assert handle.deadlines[2][0] == 500
    assert handle.deadlines[2][1] == 0


def test_set_piece_deadline_alert_when_available(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.set_piece_deadline('abc', 2, 500, alert_when_available=True)

    assert handle.deadlines[2][1] != 0


def test_set_piece_deadline_out_of_range(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    with pytest.raises(ValueError):
        plugin_core.set_piece_deadline('abc', 5, 500)


def test_set_piece_deadlines_bulk(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.set_piece_deadlines('abc', {0: 500, 3: 2000})

    assert handle.deadlines[0] == (500, 0)
    assert handle.deadlines[3] == (2000, 0)


def test_set_piece_deadlines_bulk_accepts_string_keys(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same wire shape as set_piece_priorities: JSON-RPC serializes dict
    # keys as strings.
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.set_piece_deadlines('abc', {'0': 500, '3': 2000})

    assert handle.deadlines[0] == (500, 0)
    assert handle.deadlines[3] == (2000, 0)


def test_set_piece_deadlines_bulk_fails_atomically(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    with pytest.raises(ValueError):
        plugin_core.set_piece_deadlines('abc', {0: 500, 99: 2000})

    # Validated before any calls were made — piece 0 must be untouched.
    assert 0 not in handle.deadlines


def test_clear_piece_deadline(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})
    plugin_core.set_piece_deadline('abc', 2, 500)

    plugin_core.clear_piece_deadline('abc', 2)

    assert 2 not in handle.deadlines


def test_clear_piece_deadlines(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})
    plugin_core.set_piece_deadline('abc', 1, 100)
    plugin_core.set_piece_deadline('abc', 2, 200)

    plugin_core.clear_piece_deadlines('abc')

    assert handle.deadlines == {}
    assert handle.cleared


def test_libtorrent_failure_wrapped_as_delugeerror(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    handle = FakeHandle(num_pieces=5)

    def boom(piece: int, priority: int) -> None:
        raise RuntimeError('invalid torrent handle')

    handle.piece_priority = boom  # ty: ignore[invalid-assignment]
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    with pytest.raises(DelugeError):
        plugin_core.set_piece_priority('abc', 0, 7)


def test_verify_rechecks_with_stop_when_ready(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Same stubs gap as core.py's import site, suppressed the same way.
    import libtorrent as lt  # ty: ignore[unresolved-import]

    handle = FakeHandle(num_pieces=5)
    patch_manager(monkeypatch, {'abc': FakeTorrent(handle)})

    plugin_core.verify('abc')

    # Order matters: flags must be in place before the recheck/resume so
    # the session-thread pause is armed for the checked transition.
    assert handle.calls == [
        ('unset_flags', lt.torrent_flags.auto_managed),
        ('set_flags', lt.torrent_flags.stop_when_ready),
        ('force_recheck',),
        ('resume',),
    ]


def test_verify_unknown_torrent(
    plugin_core: core_mod.Core, monkeypatch: pytest.MonkeyPatch
) -> None:
    patch_manager(monkeypatch, {})

    with pytest.raises(InvalidTorrentError):
        plugin_core.verify('missing')


def test_get_config(plugin_core: core_mod.Core) -> None:
    plugin_core._config = FakeConfig({'dled_color': '#FF0000'})

    assert plugin_core.get_config() == {'dled_color': '#FF0000'}


def test_set_config_merges_and_saves(plugin_core: core_mod.Core) -> None:
    fake = FakeConfig({'dled_color': '#FF0000', 'square_size': 10})
    plugin_core._config = fake

    plugin_core.set_config({'square_size': 12})

    assert fake.config == {'dled_color': '#FF0000', 'square_size': 12}
    assert fake.saved
