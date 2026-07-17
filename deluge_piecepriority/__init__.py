"""Entry-point wrapper: Deluge's plugin loader expects entry points to
reference a PluginInitBase subclass (which it instantiates directly and
then calls .enable()/.disable() on), not a CorePluginBase subclass itself
-- pointing an entry point straight at Core, skipping this wrapper, loads
and registers RPC methods fine but crashes Deluge's own post-enable
lifecycle callback (it expects instance.plugin to exist), leaving the
plugin functional but reported as failed to enable.
"""

from __future__ import annotations

from deluge.plugins.init import PluginInitBase


class CorePlugin(PluginInitBase):
    def __init__(self, plugin_name: str) -> None:
        from .core import Core as _plugin_cls

        self._plugin_cls = _plugin_cls
        super().__init__(plugin_name)


class WebUIPlugin(PluginInitBase):
    def __init__(self, plugin_name: str) -> None:
        from .webui import WebUI as _plugin_cls

        self._plugin_cls = _plugin_cls
        super().__init__(plugin_name)


class Gtk3UIPlugin(PluginInitBase):
    def __init__(self, plugin_name: str) -> None:
        from .gtkui import GtkUI as _plugin_cls

        self._plugin_cls = _plugin_cls
        super().__init__(plugin_name)
