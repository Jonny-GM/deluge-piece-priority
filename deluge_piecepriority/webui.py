"""WebUI plugin: serves the JS bundle that adds the piece-priority tab to
Deluge's WebUI torrent-details panel. It exports no RPC methods of its
own -- deluge-web's JSON-RPC bridge transparently proxies any method
already registered on the daemon (confirmed against
deluge/ui/web/json_api.py's JSON._exec_remote: it dynamically dispatches
`getattr(getattr(client, namespace), method)(*params)` for any method
name the daemon reports via `get_method_list`), so the browser can call
piecepriority.* directly without this module re-implementing anything.
"""

from __future__ import annotations

from deluge.plugins.pluginbase import WebPluginBase

from .common import get_resource


class WebUI(WebPluginBase):
    scripts = [get_resource('piecepriority.js')]
