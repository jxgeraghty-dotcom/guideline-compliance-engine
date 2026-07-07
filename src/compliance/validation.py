"""Strict config-key validation.

For a compliance tool, silently ignoring an unrecognised config key is the
dangerous default: a mistyped *optional* key (``look_throuh``, ``expiry``) would
quietly fall back to the safe-looking default and disable a control — a
false negative, the worst kind. So rule and waiver configs are validated
strictly: any key outside the known schema raises, with a "did you mean …?"
suggestion for near-misses.
"""

from __future__ import annotations

import difflib
from collections.abc import Iterable


def reject_unknown_keys(context: str, keys: Iterable[str], allowed: Iterable[str]) -> None:
    """Raise ``ValueError`` if ``keys`` contains anything outside ``allowed``."""
    allowed_set = set(allowed)
    unknown = sorted(k for k in keys if k not in allowed_set)
    if not unknown:
        return
    hints = []
    for key in unknown:
        close = difflib.get_close_matches(key, list(allowed_set), n=1)
        hints.append(f"{key!r} (did you mean {close[0]!r}?)" if close else repr(key))
    raise ValueError(
        f"{context}: unknown config key(s): {', '.join(hints)}. "
        f"Allowed: {', '.join(sorted(allowed_set))}."
    )
