"""Shared helper for locating packaged data files (JS, etc.)."""

from __future__ import annotations

import os.path

from pkg_resources import resource_filename


def get_resource(filename: str) -> str:
    # resource_filename is bound onto the pkg_resources module dynamically
    # by a ResourceManager singleton at import time, not a plain top-level
    # def -- ty's stub can't represent that and infers None. Confirmed
    # callable at runtime; this is the same pattern Deluge's own shipped
    # plugins (Label, etc.) use in 2.2.0.
    return resource_filename(  # ty: ignore[call-non-callable]
        __package__, os.path.join('data', filename)
    )
