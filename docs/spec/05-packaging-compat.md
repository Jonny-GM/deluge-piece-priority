# Packaging and compatibility

## Entry points

Standard Deluge plugin packaging (setuptools), three entry-point groups
matching the three components — each pointing at a `PluginInitBase`
wrapper in the package's `__init__.py`, not at the `CorePluginBase`/
GTK3/Web plugin class directly. Confirmed live (see
`02-libtorrent-semantics.md`): pointing an entry point straight at
`Core` loads fine and registers RPC methods fine, but crashes Deluge's
own post-enable lifecycle callback, which expects the loaded instance to
have a `.plugin` attribute (`PluginInitBase.__init__` sets that up by
instantiating the real class and storing it there) — the plugin ends up
functional but reported as failed to enable.

```
[deluge.plugin.core]
PiecePriority = deluge_piecepriority:CorePlugin

[deluge.plugin.gtk3ui]
PiecePriority = deluge_piecepriority:Gtk3UIPlugin

[deluge.plugin.web]
PiecePriority = deluge_piecepriority:WebUIPlugin
```

Registered plugin name: `PiecePriority` (no hyphens — see
`01-core-rpc.md` for why: it directly becomes the RPC namespace,
lowercased).

## Config schema

Persisted via Deluge's own `ConfigManager` (`piecepriority.conf`),
matching the reference plugin's shape:

| key | default | meaning |
|---|---|---|
| `not_dled_color` | `#8A8A8A` | missing (wanted, priority 1–6) fill color |
| `unavailable_color` | `#FF00FF` | fill for wanted missing pieces no connected peer has (piece state `0`) — the swarm-availability alarm, distinct from every "not here yet" shade |
| `dled_color` | `#FF0000` | piece-downloaded fill color |
| `dling_color` | `#0000FF` | piece-currently-downloading fill color |
| `urgent_color` | `#FFA500` | missing-piece fill at priority 7 — an external scheduler's active window, visible live |
| `skipped_color` | `#000000` | missing-piece fill at priority 0 — will never download, so it renders as the darkest, most background-like color and wanted pieces stand out against it |
| `selected_border` | `#FFFFFF` | selection outline color — deliberately far from every fill color, so a selected piece reads as outlined rather than recolored |
| `hover_border` | `#5a5a5a` | hover outline color |
| `square_size` | `10` | piece square size, px |
| `square_border_size` | `2` | piece square border width, px — thin relative to the square, so selection/hover borders read as outlines instead of covering the fill |

This config is UI-only (colors/sizing) — there is no config affecting RPC
behavior; `01-core-rpc.md`'s methods have no tunables.

## Version matrix

| Deluge | libtorrent | Status |
|---|---|---|
| 2.0.x | 1.2.x / 2.0.x | supported |
| 2.1.x | 1.2.x / 2.0.x | supported |
| 2.2.x | 1.2.x / 2.0.x | supported |
| 1.x | — | not supported (Python 2, GTK2, different plugin base — no support planned) |

## Python version

`requires-python = ">=3.9"` in `pyproject.toml`. The floor tracks where
released Deluge is actually run today, not where the project is headed:
Deluge 2.2.0 declares `python_requires='>=3.6'`, and real deployments on
3.9 exist (Debian 11 ships exactly that pairing), so the plugin supports
them. Nothing in the codebase wants newer syntax at runtime — every
module carries `from __future__ import annotations`, so modern annotation
syntax (PEP 604 unions, builtin generics) is never evaluated on the
running interpreter.

Deluge's `develop` branch has already moved to `python_requires='>=3.10'`
and `ruff target-version = "py310"` (checked directly against source), so
the floor rises to match whenever a Deluge release actually ships that
requirement — 3.9 support is about released Deluge's real userbase, not a
commitment to old Pythons indefinitely.

The floor is enforced, not just declared: CI runs the test suite on 3.9
itself (`test-floor` in `ci.yml`, with `libtorrent` from PyPI since apt's
build targets the runner's system Python), and the release workflow
builds eggs for 3.9 through 3.13. The type check does not repeat on 3.9 —
`ty` already targets the floor version everywhere via `requires-python`,
and the PyPI-provisioned venv resolves a different set of modules/stubs
than the apt one, which would demand a second, contradictory suppression
set for no added coverage.

## Installation

Confirmed live end-to-end (real `deluged`, real torrent, real RPC calls):
Deluge's plugin discovery is scoped to its own bundled plugins directory
and `<config-dir>/plugins/` — it does not scan the general Python
environment via standard `entry_points` discovery, so a plain
`pip install` of this package is not enough on its own, and there is
nothing to publish to PyPI. What Deluge actually consumes is a `.egg`
dropped into that folder:

```bash
python -c "from setuptools import setup; setup()" bdist_egg
cp dist/PiecePriority-*.egg <config-dir>/plugins/
```

(`bdist_egg` still works from pure `pyproject.toml` metadata with no
`setup.py` present, via that one-line shim — confirmed, though setuptools
prints a deprecation warning for the legacy command.) Then enable it from
the GTK3/WebUI plugin manager or `deluge-console plugin -e PiecePriority`.

`install.sh` / `install.ps1` (see the README's Install section) automate
exactly this: resolve `<config-dir>` the same way Deluge's own
`deluge.common.get_default_config_dir()` does (`%APPDATA%\deluge` on
Windows; `$XDG_CONFIG_HOME/deluge`, defaulting to `~/.config/deluge`, on
Linux **and** macOS — Deluge has no macOS-specific branch here, confirmed
against its source, so this is not the usual
`~/Library/Application Support` path), download the prebuilt egg matching
**the Python minor version Deluge itself runs under**, and fall back to
building one locally from source if no matching prebuilt egg exists in
that release.

Resolving that Python version is platform-specific. On Windows, Deluge
distributions bundle their own interpreter, so any separately installed
Python is the wrong target: `install.ps1` finds the real Deluge
executable — `deluged.exe`/`deluge.exe` from `PATH`, resolving launcher
shims like scoop's (whose target path sits in a sibling `.shim` text
file), with the standard install roots as a fallback — and reads the
version off the `python3xx.dll` shipped near it. `-PyVersion`/`-Python`
override the detection. A plain `python` from `PATH` is only a last
resort, and the `WindowsApps` `python.exe` is explicitly skipped — it is
a Microsoft Store stub that prints an install prompt rather than running
Python. On Linux/macOS, Deluge runs under a system interpreter, so
`install.sh` asks the `python3` that `deluged` resolves against. Building
from source additionally requires a *real* interpreter of that same minor
version (a bundled-Python Deluge can't run builds), which the scripts
verify before attempting it.

### Releases

`.github/workflows/release.yml` builds one `.egg` per entry in its Python
version matrix (kept in sync with the floor above and Deluge's own
supported range) on every pushed `vX.Y.Z` tag, since `.egg` filenames —
and `pkg_resources`'s compatibility check when Deluge scans for plugins —
are tied to a specific Python minor version, so one prebuilt artifact
can't cover every installation. All the built eggs, plus a
`sha256sums.txt`, are attached to a GitHub release for that tag, which is
what `install.sh`/`install.ps1` download from.

`.github/workflows/ci.yml` separately runs the test suite, `ty check`,
and a JS syntax check on every push and pull request. Unlike the release
build (which only packages this project's own pure-Python source), CI
needs the real `deluge` and `libtorrent` packages, since the tests
actually import this project's code — `apt install python3-libtorrent`
plus `uv venv --system-site-packages`, matching the README's local dev
setup, rather than an isolated interpreter from `actions/setup-python`.
