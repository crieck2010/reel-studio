"""Suggested time-window framing via the optional survey-timescales peer.

The survey-timescales engine answers the "when" question for a
(variable, region) pair: season alignment (fire season, melt season),
trailing event-density windows, annual-cycle framing, trend horizons —
returning a suggested [start, end] plus the framing mode and a
human-readable reason.

This module is UI-free (no Streamlit imports): pure helpers the
Streamlit app and the tests both call. The engine suggests; the caller
decides — user-typed dates always stay authoritative.
"""

from __future__ import annotations

import datetime as _dt
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

#: Session-state keys the app's date pickers / suggestion note live under.
START_KEY = "timescale_start"
END_KEY = "timescale_end"
SUGGESTION_KEY = "timescale_suggestion"
#: ("warning"|"error", message) from the last failed suggestion attempt.
ERROR_KEY = "timescale_error"


def _coerce_date(value: Any) -> _dt.date:
    """date/datetime/ISO-string -> date. Raises ValueError when empty."""
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("empty date")
    return _dt.date.fromisoformat(text)


def resolve_source(spec: Any,
                   resolver: Optional[Callable[[Any], Any]] = None
                   ) -> Optional[str]:
    """Best-effort source string for the suggest_window() call.

    Precedence mirrors the pipeline's source routing: the spec's own
    pinned ``source`` wins; otherwise the optional ``resolver`` (viz.
    ``viz.sources.resolve_source``) picks the regional default — this
    is what disambiguates ERA5 from IMERG for the ``tp`` variable.
    Returns ``None`` when no source is known so the engine does its own
    registry fallback.
    """
    pinned = str(getattr(spec, "source", "") or "").strip().lower()
    if pinned:
        return pinned
    if resolver is not None:
        try:
            resolved = str(resolver(spec) or "").strip().lower()
        except ValueError:
            resolved = ""
        return resolved or None
    return None


def suggest(spec: Any, *, today: _dt.date, timescales_module: Any,
            resolver: Optional[Callable[[Any], Any]] = None) -> Any:
    """Ask the engine for the (variable, region) window.

    Returns the engine's ``WindowSuggestion`` (``.start`` / ``.end`` /
    ``.mode`` / ``.reason``). Raises the peer's ``StaticVariableError``
    for time-invariant variables (bathymetry/elevation) — callers
    surface that as "this underlay has no time dimension" rather than a
    window. Raises ``RuntimeError`` when the installed peer is too old
    to have ``suggest_window``.
    """
    fn = getattr(timescales_module, "suggest_window", None)
    if not callable(fn):
        raise RuntimeError(
            "installed survey-timescales has no suggest_window() — "
            "upgrade it "
            "(pip install git+https://github.com/crieck2010/survey-timescales.git)")
    region_key = str(getattr(spec, "region_key", "") or "")
    kwargs: Dict[str, Any] = {}
    source = resolve_source(spec, resolver)
    if source:
        kwargs["source"] = source
    return fn(str(getattr(spec, "variable", "")),
              region=region_key or None, today=today, **kwargs)


def apply_window(spec_dict: Mapping[str, Any], start: Any,
                 end: Any) -> Dict[str, Any]:
    """Copy of ``spec_dict`` with the start/end set to the given dates.

    Dates may be ``date``/``datetime`` or ISO strings; the result keeps
    the spec's ISO-string convention. Raises ``ValueError`` when start
    is after end.
    """
    start_d, end_d = _coerce_date(start), _coerce_date(end)
    if start_d > end_d:
        raise ValueError(
            f"Start {start_d.isoformat()} is after end {end_d.isoformat()} "
            "— pick a start on or before the end.")
    out = dict(spec_dict)
    out["start"] = start_d.isoformat()
    out["end"] = end_d.isoformat()
    return out


def override_with_pickers(spec_dict: Mapping[str, Any],
                          values: Mapping[str, Any],
                          *, enabled: bool = True) -> Dict[str, Any]:
    """Apply the UI date-picker values (if any) to a copy of the spec.

    ``values`` maps session-state keys to values (the app passes
    ``st.session_state``); when the pickers were never rendered the
    spec comes back unchanged. Picker dates are the user's explicit
    dates, so they win over whatever the spec carried — including a
    previously applied suggestion the user then edited. Raises
    ``ValueError`` on a start-after-end pair; a spec that cannot supply
    its own dates is left for ``VizSpec.from_dict`` to reject.
    """
    if not enabled:
        return dict(spec_dict)
    start = values.get(START_KEY)
    end = values.get(END_KEY)
    if start is None and end is None:
        return dict(spec_dict)
    out = dict(spec_dict)
    if start is not None:
        out["start"] = _coerce_date(start).isoformat()
    if end is not None:
        out["end"] = _coerce_date(end).isoformat()
    try:
        start_d, end_d = _coerce_date(out.get("start")), _coerce_date(out.get("end"))
    except ValueError:
        return out  # let VizSpec.from_dict report the real problem
    if start_d > end_d:
        raise ValueError(
            f"Start {start_d.isoformat()} is after end {end_d.isoformat()} "
            "— fix the Time window pickers before running.")
    return out


def picker_defaults(spec_dict: Mapping[str, Any],
                    *, today: Optional[_dt.date] = None
                    ) -> Tuple[_dt.date, _dt.date]:
    """(start, end) defaults for the pickers: the spec's own dates.

    Falls back to ``today`` for a missing or unparseable bound, so the
    pickers always render something valid.
    """
    today = today or _dt.date.today()
    try:
        start = _coerce_date(spec_dict.get("start"))
    except ValueError:
        start = today
    try:
        end = _coerce_date(spec_dict.get("end"))
    except ValueError:
        end = today
    return start, end


def describe(suggestion: Any) -> str:
    """One-line ``mode — reason`` summary of a ``WindowSuggestion``."""
    return (f"{getattr(suggestion, 'mode', '?')} — "
            f"{getattr(suggestion, 'reason', '')}")
