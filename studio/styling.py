"""survey-style preset helpers (UI-free).

The survey-style peer is optional: every function here takes the peer
module (``statuses["survey-style"].module``) as its first argument, so
callers — and tests — never hard-import it. When the peer is missing
the Aesthetics step simply doesn't offer presets.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


def preset_names(style: Any) -> List[str]:
    """Sorted names of the available style presets."""
    return list(style.list_styles())


def preset_label(style: Any, name: str) -> str:
    """Human label + description for a preset picker."""
    s = style.get_style(name)
    label = s.label or name
    return f"{label} — {s.description}" if s.description else label


def suggested_preset(style: Any, variable: Optional[str]) -> str:
    """The preset suggested for ``variable`` (falls back to reel-dark)."""
    try:
        return style.suggest_style(variable or "")
    except Exception:  # noqa: BLE001 - a suggestion must never break the UI
        return "reel-dark"


def preset_needs_channel(style: Any, name: str) -> bool:
    """Whether the preset's templates reference ``{channel}``."""
    try:
        s = style.get_style(name)
    except Exception:  # noqa: BLE001 - unknown name: no channel prompt
        return False
    return "{channel}" in (s.footer_template or "") or \
        "{channel}" in (s.title_template or "")


def apply_preset(style: Any,
                 spec_dict: Dict[str, Any],
                 name: str,
                 channel: Optional[str] = None) -> Tuple[Dict[str, Any], Optional[str]]:
    """Apply preset ``name`` to ``spec_dict``.

    Returns ``(new_spec_dict, cmap)`` — the styled spec dict and the
    colormap for ``render_viz``'s ``cmap=``. Raises the peer's
    ``StyleError`` (a ``ValueError``) for unknown names, bad templates,
    or a missing ``channel``. The input dict is never mutated.
    """
    new_spec, render_kwargs = style.apply_style(
        spec_dict, name, channel=(channel.strip() or None)
        if channel else None)
    return new_spec, render_kwargs.get("cmap")
