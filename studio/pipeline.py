"""UI-free orchestration: description -> fetch -> frames -> MP4.

Everything here works with injected callables, so the pipeline is fully
testable with fake peers and never hard-imports survey-viz,
survey-currents, or survey-animate. The Streamlit app (``app.py``) builds
the real callables with :func:`studio.peers.wire_peers`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import types
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

#: survey-viz region_key -> survey-currents lake name for
#: ``fetch_glsea_lake_averages``. Only the 5 Great Lakes are fetchable
#: today (see ``viz.gazetteer.is_fetchable``).
FETCHABLE_REGION_TO_LAKE = {
    "lake-superior": "superior",
    "lake-michigan": "michigan",
    "lake-huron": "huron",
    "lake-erie": "erie",
    "lake-ontario": "ontario",
}

#: The only variable with a fetch adapter today (NOAA GLSEA SST).
SUPPORTED_VARIABLES = ("sst",)

#: GLSEA daily SST, sampled this often for the reel. Monthly-ish cadence
#: keeps multi-year windows to a manageable frame count.
DEFAULT_STRIDE_DAYS = 30


class UnfetchableRegionError(ValueError):
    """The spec's region has no fetch adapter yet (honest, not a crash)."""


class UnsupportedVariableError(ValueError):
    """The spec's variable has no fetch adapter yet."""


@dataclass
class FetchPlan:
    """What :func:`plan_fetch` decided, and why."""

    fetchable: bool
    reason: str
    lake: Optional[str] = None       # survey-currents lake name, when fetchable
    region_key: str = ""
    variable: str = ""
    #: "ok" | "no_adapter" (region not fetchable) | "bad_variable"
    kind: str = "ok"


def region_to_lake(region_key: str) -> Optional[str]:
    """Map a survey-viz region key to a survey-currents lake name."""
    return FETCHABLE_REGION_TO_LAKE.get(region_key)


def plan_fetch(spec: Any, is_fetchable: Callable[[str], bool]) -> FetchPlan:
    """Decide whether ``spec`` can be fetched, without touching the network.

    ``spec`` is any object with ``region_key`` and ``variable`` attributes
    (a real ``VizSpec`` or a test double); ``is_fetchable`` is the
    ``viz.gazetteer.is_fetchable`` callable (or a test double).

    Returns a :class:`FetchPlan`. Callers turn ``fetchable=False`` into a
    clear user-facing message instead of a crash.
    """
    region_key = str(getattr(spec, "region_key", ""))
    variable = str(getattr(spec, "variable", ""))

    if not is_fetchable(region_key):
        return FetchPlan(
            fetchable=False,
            reason=(
                f"Region '{region_key}' has no fetch adapter yet — only the 5 "
                "Great Lakes (Superior, Michigan, Huron, Erie, Ontario) can "
                "be fetched today, via NOAA GLSEA. The description parsed "
                "fine; fetching other regions needs a new data adapter "
                "(see docs/INTEROP.md)."
            ),
            region_key=region_key,
            variable=variable,
            kind="no_adapter",
        )
    if variable not in SUPPORTED_VARIABLES:
        return FetchPlan(
            fetchable=False,
            reason=(
                f"Variable '{variable}' has no fetch adapter — only 'sst' "
                "(surface water temperature) is supported today, via NOAA "
                "GLSEA. The description parsed fine; other variables need a "
                "new data adapter (see docs/INTEROP.md)."
            ),
            region_key=region_key,
            variable=variable,
            kind="bad_variable",
        )
    return FetchPlan(
        fetchable=True,
        reason=(
            f"Region '{region_key}' is fetchable via NOAA GLSEA "
            f"(variable '{variable}')."
        ),
        lake=region_to_lake(region_key),
        region_key=region_key,
        variable=variable,
    )


def parse_with_fallback(
    text: str,
    parse_fn: Callable[[str], Any],
    parse_exc: type,
    assist_fn: Optional[Callable[[str], Any]] = None,
) -> Tuple[Any, bool]:
    """Parse ``text`` with the deterministic parser, optionally LLM-assisted.

    Returns ``(spec, assist_used)``. When the deterministic parser raises
    ``parse_exc`` and ``assist_fn`` is given, one assist attempt is made;
    if it also fails, the ORIGINAL parse error is re-raised (per the
    documented contract).
    """
    try:
        return parse_fn(text), False
    except parse_exc as first_error:
        if assist_fn is None:
            raise
        try:
            return assist_fn(text), True
        except Exception:
            raise first_error from None


@dataclass
class RunResult:
    """Everything one :func:`run_pipeline` call produced."""

    video_path: str
    sidecar_path: str
    frames_dir: str
    manifest_path: str
    n_frames: int
    spec: Dict[str, Any]
    lake: str
    provenance: Dict[str, Any] = field(default_factory=dict)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clamp_bbox(bbox: Any, bounds: Tuple[float, float, float, float],
                provenance_note: List[str]) -> Tuple[float, float, float, float]:
    """Clamp ``bbox`` into the GLSEA grid ``bounds``.

    The survey-viz gazetteer bbox for lake-superior starts at lon -92.5,
    slightly west of the GLSEA grid's longitude floor (-92.4199507342304),
    which ``validate_glsea_bbox`` would reject. Clamping keeps the fetch
    honest: any clamping is recorded in ``provenance_note``.
    """
    lon_min, lat_min, lon_max, lat_max = (float(x) for x in bbox)
    b_lon_min, b_lat_min, b_lon_max, b_lat_max = bounds
    clamped = (max(lon_min, b_lon_min), max(lat_min, b_lat_min),
               min(lon_max, b_lon_max), min(lat_max, b_lat_max))
    if clamped != (lon_min, lat_min, lon_max, lat_max):
        provenance_note.append(
            f"fetch bbox clamped from {list(bbox)} to {list(clamped)} "
            "to fit the GLSEA grid")
    if not (clamped[0] < clamped[2] and clamped[1] < clamped[3]):
        raise UnfetchableRegionError(
            f"Region bbox {list(bbox)} does not overlap the GLSEA grid "
            f"{list(bounds)}")
    return clamped


def _time_to_date_str(value: Any) -> str:
    """Coerce a timestep label to a ``YYYY-MM-DD`` string.

    survey-viz's ``_normalize_field`` only parses date-only ISO strings
    (it raises on ISO *datetimes* like ``"2026-06-01T12:00:00+00:00"``),
    which is exactly what ``GlseaField.times`` holds — real fetched data
    included. We normalize here so the real peer field just works
    (see docs/INTEROP.md).
    """
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return _dt.date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    try:
        return _dt.datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    return text[:10]


def _field_to_dict(field: Any) -> Dict[str, Any]:
    """Adapt a GlseaField-shaped object to survey-viz's documented dict form.

    viz's ``render_viz`` documents plain dicts with ``times``/``lats``/
    ``lons``/``values`` keys; we use that form (rather than passing the
    field through) so the ISO-datetime ``times`` get normalized by
    :func:`_time_to_date_str` first.
    """
    if isinstance(field, dict):
        return {
            "times": [_time_to_date_str(t) for t in field["times"]],
            "lats": field["lats"],
            "lons": field["lons"],
            "values": field["values"],
        }
    values = None
    for attr in ("values", "data", "sst", "grids"):
        arr = getattr(field, attr, None)
        if arr is not None:
            candidate = np.ma.asarray(arr, dtype=float)
            if candidate.ndim == 3:
                values = candidate
                break
    if values is None:
        raise TypeError(
            "field has no 3D grid data: expected a (ntime, nlat, nlon) "
            "attribute among ('values', 'data', 'sst', 'grids')")
    return {
        "times": [_time_to_date_str(t) for t in field.times],
        "lats": np.asarray(field.lats, dtype=float),
        "lons": np.asarray(field.lons, dtype=float),
        "values": np.ma.filled(values, np.nan),
    }


def _series_to_dict(series: Any) -> Dict[str, Any]:
    """Adapt a survey-currents LakeSeries to survey-viz's duck-typed series.

    survey-viz's ``_normalize_series`` accepts a dict with ``dates``/``values``
    keys; LakeSeries exposes ``.dates``/``.temps``, so we adapt explicitly
    instead of relying on attribute coincidence.
    """
    if isinstance(series, dict):
        return series
    return {
        "dates": list(getattr(series, "dates")),
        "values": [float(t) for t in getattr(series, "temps")],
    }


def run_pipeline(
    spec: Any,
    peers: types.SimpleNamespace,
    out_dir: str,
    progress: Optional[Callable[[float, str], None]] = None,
    stride_days: int = DEFAULT_STRIDE_DAYS,
) -> RunResult:
    """Run the full fetch -> render -> encode pipeline for ``spec``.

    Args:
        spec: a ``VizSpec`` (from ``parse_with_fallback``).
        peers: namespace from :func:`studio.peers.wire_peers` (or test doubles)
            with ``is_fetchable``, ``fetch_sst``, ``fetch_averages``,
            ``render_viz``, ``render_video``.
        out_dir: working directory for frames + the MP4 (created if needed).
        progress: optional ``(fraction, message)`` callback.
        stride_days: GLSEA time-axis stride for ``fetch_glsea_sst``.

    Raises:
        UnfetchableRegionError / UnsupportedVariableError: honest,
            no-crash refusals naming what is missing.
        RuntimeError: wrapped fetch failures (network, NetCDF, ...).
    """
    def report(frac: float, message: str) -> None:
        if progress is not None:
            progress(frac, message)

    plan = plan_fetch(spec, peers.is_fetchable)
    if not plan.fetchable:
        if plan.kind == "bad_variable":
            raise UnsupportedVariableError(plan.reason)
        raise UnfetchableRegionError(plan.reason)
    lake = plan.lake or ""

    os.makedirs(out_dir, exist_ok=True)
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # -- 1. fetch -----------------------------------------------------------
    report(0.05, "Fetching GLSEA sea-surface-temperature grid…")
    clamp_notes: List[str] = []
    fetch_bbox = _clamp_bbox(
        spec.bbox, getattr(peers, "glsea_bounds",
                           (-180.0, -90.0, 180.0, 90.0)), clamp_notes)
    try:
        field = peers.fetch_sst(
            fetch_bbox, spec.start, spec.end, stride_days=stride_days)
    except Exception as exc:
        raise RuntimeError(
            f"SST fetch failed ({type(exc).__name__}: {exc}). "
            "Check the network connection and that netCDF4 is installed "
            "(pip install netCDF4)."
        ) from exc

    report(0.35, f"Fetching {lake} lake-average temperature series…")
    try:
        series = peers.fetch_averages(lake, spec.start, spec.end)
    except Exception as exc:
        raise RuntimeError(
            f"Lake-average fetch failed ({type(exc).__name__}: {exc})."
        ) from exc

    # -- 2. render frames ----------------------------------------------------
    # render_viz is duck-typed: the field is adapted to its documented dict
    # form (also normalizing GlseaField's ISO-datetime times, which viz's
    # own date coercion cannot parse — see docs/INTEROP.md), and the series
    # to the documented dates/values dict.
    report(0.50, "Rendering reel frames…")
    frames, manifest_path = peers.render_viz(
        spec, _field_to_dict(field), _series_to_dict(series),
        out_dir=frames_dir)

    # -- 3. encode ------------------------------------------------------------
    # NOTE: pass the frames DIRECTORY, not the viz manifest path:
    # survey-animate's manifest reader only accepts the
    # "survey-flow.frame-manifest/" schema prefix, while survey-viz writes
    # "survey-viz.frame-manifest/1.0" (see docs/INTEROP.md).
    report(0.85, "Encoding MP4…")
    video_path = os.path.join(out_dir, "reel.mp4")
    result = peers.render_video(
        frames_dir, video_path, preset="reel",
        title=getattr(spec, "title", ""), burn_timestamps_=False)

    report(1.0, "Done")

    provenance = {
        "spec": spec.to_dict() if hasattr(spec, "to_dict") else dict(spec),
        "lake": lake,
        "fetch": {
            "sst": dict(getattr(field, "provenance", {}) or {}),
            "averages": dict(getattr(series, "provenance", {}) or {}),
            "bbox_clamp_notes": clamp_notes,
        },
        "render": {
            "frames_dir": frames_dir,
            "manifest_path": manifest_path,
            "n_frames": len(frames),
        },
        "encode": {
            "video_path": os.path.abspath(video_path),
            "sidecar_path": getattr(result, "sidecar_path", ""),
            "fps": getattr(result, "fps", None),
            "n_frames": getattr(result, "n_frames", len(frames)),
            "video_sha256": _sha256_file(video_path),
        },
    }

    return RunResult(
        video_path=os.path.abspath(video_path),
        sidecar_path=getattr(result, "sidecar_path", ""),
        frames_dir=frames_dir,
        manifest_path=manifest_path,
        n_frames=len(frames),
        spec=provenance["spec"],
        lake=lake,
        provenance=provenance,
    )
