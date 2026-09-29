"""Derived climatological anomaly products (survey-derive peer).

Covers: _normalize_derived validation (defaults, full config, every
malformed shape), the PeerTooOldError paths (derive peer missing,
survey-viz < 0.19.0 without derived_note), the honest ValueError
refusals for ineligible sources, baseline-fetch failure wrapping, the
happy path for "anomaly"/"standardized"/"percent" (spec mutation to
"<base>-anomaly", symmetric shared limits, RdBu_r default cmap with
explicit-cmap override, baseline note, derived provenance, cache key
carrying the derived config), the GLSEA series replacement with
per-frame spatial-mean anomalies, and wire_peers exposing derive=None
when the peer is missing.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import types

import numpy as np
import pytest

from studio import peers, pipeline
from studio.pipeline import (
    PeerTooOldError,
    _normalize_derived,
    plan_fetch,
    run_pipeline,
)


# --- fake derive engine (same contract shape as survey-derive v0.1.0) --------

def _doy(timestr):
    return dt.date.fromisoformat(timestr).timetuple().tm_yday


def fake_climatology(field, window_days=15, min_samples=10):
    times = list(field["times"])
    values = np.asarray(field["values"], dtype=float)
    doys = np.array([_doy(t) for t in times])
    stats = {}
    for d in range(1, 367):
        sel = np.abs(((doys - d + 183) % 366) - 183) <= window_days
        block = values[sel]
        stats[d] = {
            "mean": np.nanmean(block, axis=0),
            "std": np.nanstd(block, axis=0),
            "count": int(np.sum(~np.isnan(block), axis=0).min()),
        }
    fake_climatology.seen = {"window_days": window_days,
                             "min_samples": min_samples}
    return {
        "stats": stats,
        "baseline_start": "1991-01-01",
        "baseline_end": "2020-12-31",
        "window_days": window_days,
        "units": field.get("units", ""),
    }


def _product_out(field, values, units):
    out = dict(field)
    out["values"] = values
    out["units"] = units
    out["variable"] = f"{field.get('variable', 'unknown')}-anomaly"
    return out


def fake_anomaly(field, clim):
    vals = np.asarray(field["values"], dtype=float)
    doys = [_doy(t) for t in field["times"]]
    anom = np.stack([vals[i] - clim["stats"][d]["mean"]
                     for i, d in enumerate(doys)])
    return _product_out(field, anom, field.get("units", ""))


def fake_standardized(field, clim):
    vals = np.asarray(field["values"], dtype=float)
    doys = [_doy(t) for t in field["times"]]
    std = np.stack([(vals[i] - clim["stats"][d]["mean"])
                    / clim["stats"][d]["std"]
                    for i, d in enumerate(doys)])
    return _product_out(field, std, "σ")


def fake_percent(field, clim):
    vals = np.asarray(field["values"], dtype=float)
    doys = [_doy(t) for t in field["times"]]
    pct = np.stack([100.0 * vals[i] / clim["stats"][d]["mean"]
                    for i, d in enumerate(doys)])
    return _product_out(field, pct, "%")


def fake_limits(values, quantile=0.99):
    fake_limits.seen = {"quantile": quantile}
    a = np.abs(np.asarray(values, dtype=float))
    a = a[np.isfinite(a)]
    m = float(np.quantile(a, quantile)) if a.size else 0.0
    return (-m, m) if m > 0 else (-1.0, 1.0)


FAKE_DERIVE = types.SimpleNamespace(
    climatology=fake_climatology,
    anomaly=fake_anomaly,
    standardized_anomaly=fake_standardized,
    percent_of_normal=fake_percent,
    suggest_symmetric_limits=fake_limits,
)


# --- fake viz spec / peers ----------------------------------------------------

@dataclasses.dataclass
class FakeVizSpecClass:
    """Stands in for viz.VizSpec: only __dataclass_fields__ matters."""
    derived_note: object = None


class FakeSpec:
    def __init__(self, variable="sst", region_key="north-atlantic",
                 cadence="daily"):
        self.title = "t"
        self.variable = variable
        self.region_key = region_key
        self.start = "2024-08-01"
        self.end = "2024-08-31"
        self.bbox = (-80.0, 20.0, -60.0, 40.0)
        self.cadence = cadence
        self.cmap = None
        self.vmin = None
        self.vmax = None
        self.style = "dark"
        self.overlays = ()
        self.context = ()
        self.derived_note = None

    def to_dict(self):
        return {"variable": self.variable, "vmin": self.vmin,
                "vmax": self.vmax, "derived_note": self.derived_note}


class FakeGridField:
    """Duck-typed field: .times/.lats/.lons/.values + provenance."""

    def __init__(self, start, end, offset=0.0):
        self.times = [f"2024-08-{d:02d}" for d in range(1, 4)]
        if start.startswith("1991"):
            # baseline: one frame per year, same calendar days
            self.times = [f"{y}-08-{d:02d}" for y in range(1991, 2021)
                          for d in (1, 2, 3)][:30]
        self.lats = np.linspace(20.0, 40.0, 4)
        self.lons = np.linspace(-80.0, -60.0, 5)
        n = len(self.times)
        base = 20.0 + offset
        self.values = (base + np.arange(n)[:, None, None] * 0.1
                       + np.zeros((n, 4, 5)))
        self.provenance = {"source": "fake"}


class FakeSeries:
    def __init__(self):
        self.dates = [dt.date(2024, 8, d) for d in range(1, 4)]
        self.temps = [21.0, 21.5, 22.0]
        self.provenance = {"source": "fake-averages"}


def make_peers(source="oisst", variable="sst", with_derive=True,
               with_viz_fields=True, fetch_fails_at=None,
               baseline_fails=False):
    seen = {}
    calls = []

    def fetch_grid(bbox, start="2024-08-01", end="2024-08-31", **kwargs):
        calls.append(("fetch", start, end, dict(kwargs)))
        if fetch_fails_at == "analysis" and start == "2024-08-01":
            raise ConnectionError("simulated analysis failure")
        if baseline_fails and start != "2024-08-01":
            raise ConnectionError("simulated baseline failure")
        return FakeGridField(start, end)

    def fetch_averages(lake, start, end):
        return FakeSeries()

    def render_viz(spec, field, series, out_dir, **kwargs):
        seen["spec"] = spec
        seen["field"] = field
        seen["series"] = series
        seen["kwargs"] = dict(kwargs)
        assert isinstance(field, dict) and "values" in field
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "frame_0001.png")
        with open(p, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        manifest = os.path.join(out_dir, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{}")
        return [p], manifest

    def render_video(source_, out_path, preset="reel", **kwargs):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".sidecar",
                                     fps=30, n_frames=1)

    ns = {
        "is_fetchable": lambda key: True,
        "resolve_source": lambda spec: source,
        "fetch_oisst": fetch_grid,
        "fetch_sst": fetch_grid,
        "fetch_averages": fetch_averages,
        "fetch_grace": fetch_grid,
        "fetch_firms": fetch_grid,
        "fetch_gebco": fetch_grid,
        "fetch_ibtracs": fetch_grid,
        "fetch_usgs": fetch_grid,
        "fetch_earthquakes": fetch_grid,
        "glsea_bounds": (-180.0, -90.0, 180.0, 90.0),
        "render_viz": render_viz,
        "render_video": render_video,
        "derive": FAKE_DERIVE if with_derive else None,
        "derive_pip": ("pip install "
                       "git+https://github.com/crieck2010/survey-derive.git"),
    }
    if with_viz_fields:
        ns["VizSpec"] = FakeVizSpecClass
    return types.SimpleNamespace(**ns), seen, calls


def run(tmp_path, source="oisst", variable="sst", derived=None,
        with_derive=True, with_viz_fields=True, baseline_fails=False,
        **kwargs):
    ns, seen, calls = make_peers(
        source=source, variable=variable, with_derive=with_derive,
        with_viz_fields=with_viz_fields, baseline_fails=baseline_fails)
    out = tmp_path / f"reel-{source}"
    result = run_pipeline(FakeSpec(variable=variable), ns, str(out),
                          derived=derived, **kwargs)
    return result, seen, calls


DERIVED = {"product": "anomaly", "baseline_start": "1991-01-01",
           "baseline_end": "2020-12-31"}


# --- _normalize_derived --------------------------------------------------------

def test_normalize_derived_none():
    assert _normalize_derived(None) is None


def test_normalize_derived_defaults():
    cfg = _normalize_derived({"product": "anomaly"})
    assert cfg == {"product": "anomaly", "baseline_start": "1991-01-01",
                   "baseline_end": "2020-12-31",
                   "baseline_stride_days": 30, "window_days": 15,
                   "min_samples": 10, "symmetric_quantile": 0.99}


def test_normalize_derived_full():
    cfg = _normalize_derived({"product": "percent",
                              "baseline_start": "2000-01-01",
                              "baseline_end": "2019-12-31",
                              "baseline_stride_days": 60,
                              "window_days": 10,
                              "min_samples": 5,
                              "symmetric_quantile": 0.95})
    assert cfg["product"] == "percent"
    assert cfg["baseline_stride_days"] == 60
    assert cfg["window_days"] == 10
    assert cfg["min_samples"] == 5
    assert cfg["symmetric_quantile"] == 0.95


@pytest.mark.parametrize("bad", [
    {"product": "trend"},
    {"product": "anomaly", "baseline_start": "2020-13-01"},
    {"product": "anomaly", "baseline_start": "2021-01-01",
     "baseline_end": "2020-12-31"},
    {"product": "anomaly", "baseline_stride_days": 0},
    {"product": "anomaly", "window_days": -1},
    {"product": "anomaly", "window_days": 1.5},
    {"product": "anomaly", "min_samples": 0},
    {"product": "anomaly", "min_samples": True},
    {"product": "anomaly", "min_samples": 2.5},
    {"product": "anomaly", "symmetric_quantile": 0.0},
    {"product": "anomaly", "symmetric_quantile": 1.0},
    {"product": "anomaly", "symmetric_quantile": 1.5},
    {"product": "anomaly", "symmetric_quantile": "high"},
    {"product": "anomaly", "bogus": 1},
    ["anomaly"],
])
def test_normalize_derived_rejects_malformed(bad):
    with pytest.raises(ValueError):
        _normalize_derived(bad)


def test_normalize_derived_window_zero_allowed():
    # Exact day-of-year pooling is what the engine documents.
    cfg = _normalize_derived({"product": "anomaly", "window_days": 0})
    assert cfg["window_days"] == 0


# --- capability / eligibility errors ------------------------------------------

def test_missing_derive_peer_raises_peer_too_old(tmp_path):
    with pytest.raises(PeerTooOldError) as excinfo:
        run(tmp_path, derived=DERIVED, with_derive=False)
    assert "survey-derive" in str(excinfo.value)


def test_old_viz_peer_raises_peer_too_old(tmp_path):
    with pytest.raises(PeerTooOldError) as excinfo:
        run(tmp_path, derived=DERIVED, with_viz_fields=False)
    assert "survey-viz" in str(excinfo.value)
    assert "0.19.0" in str(excinfo.value)


def test_no_derived_needs_no_new_peers(tmp_path):
    # Raw-variable reels work with neither derive nor VizSpec wired.
    result, seen, calls = run(tmp_path, derived=None, with_derive=False,
                              with_viz_fields=False)
    assert seen["spec"].variable == "sst"
    assert result.provenance["derived"] is None


@pytest.mark.parametrize("source,variable", [
    ("grace", "water-storage"),
    ("firms", "fire"),
    ("gebco", "bathymetry"),
    ("ibtracs", "storm-tracks"),
    ("usgs", "streamflow"),
    ("comcat", "earthquakes"),
])
def test_ineligible_sources_refuse_honestly(tmp_path, source, variable):
    with pytest.raises(ValueError, match="not available for source"):
        run(tmp_path, source=source, variable=variable, derived=DERIVED)


def test_grace_refusal_names_anomaly_of_anomaly(tmp_path):
    with pytest.raises(ValueError, match="anomaly of an anomaly"):
        run(tmp_path, source="grace", variable="water-storage",
            derived=DERIVED)


def test_baseline_fetch_failure_wrapped(tmp_path):
    with pytest.raises(RuntimeError, match="Climatology baseline fetch failed"):
        run(tmp_path, derived=DERIVED, baseline_fails=True)


# --- happy path ----------------------------------------------------------------

def test_anomaly_happy_path(tmp_path):
    result, seen, calls = run(tmp_path, derived=DERIVED)
    spec = seen["spec"]
    assert spec.variable == "sst-anomaly"
    assert spec.vmin == -spec.vmax and spec.vmin < 0
    assert spec.derived_note == "anomaly vs 1991–2020 climatology"
    # Default diverging cmap; the field carries anomaly values.
    assert seen["kwargs"]["cmap"] == "RdBu_r"
    vals = np.asarray(seen["field"]["values"], dtype=float)
    assert vals.shape == (3, 4, 5)
    # Baseline was fetched with the configured stride, analysis untouched.
    fetch_calls = [c for c in calls if c[0] == "fetch"]
    assert ("fetch", "1991-01-01", "2020-12-31",
            {"stride_days": 30}) in fetch_calls
    assert ("fetch", "2024-08-01", "2024-08-31",
            {"stride_days": 30}) in fetch_calls
    prov = result.provenance["derived"]
    assert prov["product"] == "anomaly"
    assert prov["variable"] == "sst-anomaly"
    assert prov["baseline_start"] == "1991-01-01"
    assert prov["engine"] == "survey-derive"
    assert prov["vmin"] == -prov["vmax"]


def test_anomaly_values_are_field_minus_climatology(tmp_path):
    _, seen, _ = run(tmp_path, derived=DERIVED)
    vals = np.asarray(seen["field"]["values"], dtype=float)
    # Analysis frames are 20.0/20.1/20.2 + offset 0; baseline frames are
    # 20.0..22.9 over the same calendar days, so the anomaly of the
    # first frame is 20.0 - mean(baseline day-1 values) < 0.
    assert (vals[0] < 0).all()
    assert (vals[2] > vals[0]).all()


def test_standardized_and_percent_products(tmp_path):
    for product, units in (("standardized", "σ"), ("percent", "%")):
        _, seen, _ = run(tmp_path,
                         derived=dict(DERIVED, product=product))
        assert seen["spec"].variable == "sst-anomaly"
        assert seen["field"]["units"] == units
        note = seen["spec"].derived_note
        assert note.startswith(
            {"standardized": "standardized anomaly",
             "percent": "percent of normal"}[product])


def test_percent_renders_deviation_from_100(tmp_path):
    # Percent-of-normal is a ratio centered at 100 — the map must show
    # the percentage-point deviation, or the symmetric scale would
    # saturate the whole frame at the top end.
    _, seen, _ = run(tmp_path, derived=dict(DERIVED, product="percent"))
    vals = np.asarray(seen["field"]["values"], dtype=float)
    assert abs(float(np.nanmean(vals))) < 50.0  # near 0, not near 100
    assert (np.abs(vals) < 50.0).all()  # nothing near the raw-ratio range
    assert "(deviation from 100%)" in seen["spec"].derived_note


def test_min_samples_and_quantile_reach_engine(tmp_path):
    fake_climatology.seen = {}
    fake_limits.seen = {}
    derived = dict(DERIVED, min_samples=7, symmetric_quantile=0.9)
    result, seen, calls = run(tmp_path, derived=derived)
    assert fake_climatology.seen == {"window_days": 15, "min_samples": 7}
    assert fake_limits.seen == {"quantile": 0.9}
    prov = result.provenance["derived"]
    assert prov["min_samples"] == 7
    assert prov["symmetric_quantile"] == 0.9
    # The engine got the real variable name, not "unknown".
    assert seen["field"]["variable"] == "sst-anomaly"


def test_explicit_cmap_wins_over_default(tmp_path):
    _, seen, _ = run(tmp_path, derived=DERIVED, cmap="viridis")
    assert seen["kwargs"]["cmap"] == "viridis"


def test_glsea_series_replaced_with_frame_mean_anomalies(tmp_path):
    result, seen, _ = run(tmp_path, source="glsea", derived=DERIVED)
    series = seen["series"]
    assert series is not None
    vals = np.asarray(seen["field"]["values"], dtype=float)
    expected = [float(np.nanmean(vals[i])) for i in range(vals.shape[0])]
    assert list(series["values"]) == pytest.approx(expected)
    # The raw lake-average temperatures are gone from the chart.
    assert list(series["values"]) != [21.0, 21.5, 22.0]


def test_derived_config_in_cache_key(tmp_path, monkeypatch):
    captured = {}

    def spy(spec_dict, render_field, series_dict, render_viz_kwargs,
            layout_canvas, style_preset, platform, viz_version=None,
            derived=None):
        captured["derived"] = derived
        captured["spec"] = dict(spec_dict)
        return "batch-key"

    monkeypatch.setattr(pipeline.caching, "frame_batch_key", spy)
    monkeypatch.setattr(pipeline.caching, "restore_frame_batch",
                        lambda *a: None)
    monkeypatch.setattr(pipeline.caching, "store_frame_batch",
                        lambda cache, key, frames, manifest: {"frames": []})
    monkeypatch.setattr(pipeline.caching, "video_key",
                        lambda *a, **k: "video-key")
    monkeypatch.setattr(pipeline.caching, "restore_video", lambda *a: None)
    monkeypatch.setattr(pipeline.caching, "store_video", lambda *a: None)
    result, seen, _ = run(tmp_path, derived=DERIVED,
                          cache=types.SimpleNamespace())
    assert captured["derived"] == _normalize_derived(DERIVED)
    assert captured["spec"]["variable"] == "sst-anomaly"
    assert result.provenance["cache"]["enabled"] is True


def test_wire_peers_derive_none_when_missing(monkeypatch):
    import sys
    monkeypatch.delitem(sys.modules, "derive", raising=False)

    real_import_module = peers.importlib.import_module

    def fake_import_module(name, *args, **kwargs):
        if name == "derive":
            raise ImportError("No module named 'derive'")
        return real_import_module(name, *args, **kwargs)

    monkeypatch.setattr(peers.importlib, "import_module", fake_import_module)

    viz = types.ModuleType("viz")
    viz.parse_description = lambda *a, **k: None
    viz.UnparseableDescription = type("UnparseableDescription",
                                      (Exception,), {})
    viz.VizSpec = FakeVizSpecClass
    viz.is_fetchable = lambda *a, **k: False
    viz.get_region = lambda *a, **k: None
    viz.render_viz = lambda *a, **k: None
    currents = types.ModuleType("currents")
    animate = types.ModuleType("animate")
    animate.render_video = lambda *a, **k: None
    statuses = {
        "survey-viz": peers.PeerStatus(
            repo="survey-viz", module_name="viz", module=viz,
            pip_command="pip install git+https://github.com/crieck2010/survey-viz.git",
            needed_for="x"),
        "survey-currents": peers.PeerStatus(
            repo="survey-currents", module_name="currents", module=currents,
            pip_command="pip install git+https://github.com/crieck2010/survey-currents.git",
            needed_for="x"),
        "survey-animate": peers.PeerStatus(
            repo="survey-animate", module_name="animate", module=animate,
            pip_command="pip install git+https://github.com/crieck2010/survey-animate.git",
            needed_for="x"),
    }
    # currents.glsea is imported unconditionally by wire_peers.
    glsea = types.ModuleType("currents.glsea")
    glsea.fetch_glsea_sst = lambda *a, **k: None
    glsea.fetch_glsea_lake_averages = lambda *a, **k: None
    glsea.GLSEA_LON_MIN, glsea.GLSEA_LAT_MIN = -92.5, 41.5
    glsea.GLSEA_LON_MAX, glsea.GLSEA_LAT_MAX = -76.0, 49.0
    monkeypatch.setitem(sys.modules, "currents.glsea", glsea)
    ns = peers.wire_peers(statuses)
    assert ns.derive is None
    assert ns.derive_pip.startswith(
        "pip install git+https://github.com/crieck2010/survey-derive.git")
