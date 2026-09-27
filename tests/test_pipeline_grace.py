"""GRACE water-storage flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "grace" (fetchable in any region,
variable "water-storage" only — other variables are bad_variable),
honest refusals for "streamflow" and "sea-level" (never answered with a
GRACE map), the run_pipeline grace branch
(fetch_grace(bbox, start, end), series=None, provenance under
fetch["grace"]), the WaterField going to render_viz as its to_dict()
form (never through _field_to_dict), the ERA5 precipitation context
("… vs rainfall" -> overlay_grids dict, bilinearly resampled onto the
GRACE grid; failed/unavailable context degrades gracefully), the
missing-adapter upgrade message (survey-currents>=0.12.0),
fetch-failure wrapping, the SOURCE_LABELS registry, and wire_peers
exposing fetch_grace.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import types

import numpy as np
import pytest

from studio import peers, pipeline
from studio.pipeline import (
    UnfetchableRegionError,
    _bilinear_resample,
    _fetch_water_context,
    plan_fetch,
    run_pipeline,
)


# --- fakes --------------------------------------------------------------------


class FakeWaterField:
    """WaterField-shaped: to_dict + provenance."""

    def __init__(self, n_times=4, all_gap_months=()):
        self.times = [dt.date(2016, m, 1) for m in range(1, n_times + 1)]
        self.lats = np.arange(30.0, 45.25, 0.25)
        self.lons = np.arange(-125.0, -109.75, 0.25)
        rng = np.random.default_rng(7)
        self.values = rng.normal(0.0, 3.0, (n_times, len(self.lats),
                                            len(self.lons)))
        self.gap_months = [dt.date(2016, m, 1) for m in all_gap_months]
        self.units = "cm"
        self.anomaly_baseline = "2004-2009"
        self.bbox = (-125.0, 30.0, -110.0, 45.0)
        self.source = "grace"
        self.provenance = {
            "source": "CSR GRACE/GRACE-FO RL06.3",
            "solution_url": "https://example/grace.nc",
            "mask_url": "https://example/mask.nc",
            "solution_sha256": "aabbcc",
            "mask_sha256": "ddeeff",
            "anomaly_baseline": "2004-2009",
            "gap_months": [d.isoformat() for d in self.gap_months],
            "units": "cm",
        }

    def to_dict(self):
        return {
            "times": [t.isoformat() for t in self.times],
            "lats": [float(x) for x in self.lats],
            "lons": [float(x) for x in self.lons],
            "values": self.values.tolist(),
            "gap_months": [d.isoformat() for d in self.gap_months],
            "units": self.units,
            "anomaly_baseline": self.anomaly_baseline,
            "bbox": list(self.bbox),
            "source": self.source,
            "provenance": dict(self.provenance),
        }


class FakeWaterSpec:
    def __init__(self, variable="water-storage", region_key="california",
                 overlays=()):
        self.title = "California — Terrestrial water storage"
        self.region_key = region_key
        self.bbox = (-124.0, 32.0, -114.0, 42.0)
        self.variable = variable
        self.start = dt.date(2016, 1, 1)
        self.end = dt.date(2016, 4, 1)
        self.overlays = overlays

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start.isoformat(),
                "end": self.end.isoformat(),
                "overlays": list(self.overlays)}


class FakeAtmo:
    """AtmoField-shaped for ERA5 tp context."""

    base_variable = "tp"

    def __init__(self):
        self.times = [
            "2016-01-05T12:00:00+00:00", "2016-01-20T12:00:00+00:00",
            "2016-02-05T12:00:00+00:00", "2016-03-05T12:00:00+00:00",
            "2016-04-05T12:00:00+00:00",
        ]
        self.lats = np.array([32.0, 36.0, 40.0, 42.0])
        self.lons = np.array([-124.0, -120.0, -116.0, -114.0])
        self.values = np.full((5, 4, 4), 0.002)   # tp (base)
        self.overlay_grids = {}


def make_water_peers(calls, fail_at=None, with_fetch=True,
                     with_era5=True, era5_fails=False):
    seen = {}

    def _guard(name):
        def deco(fn):
            def wrapper(*a, **k):
                calls.append(name)
                if fail_at == name:
                    raise ConnectionError(f"simulated {name} failure")
                return fn(*a, **k)
            return wrapper
        return deco

    @_guard("is_fetchable")
    def is_fetchable(key):
        return False  # region check must NOT gate grace (source routing does)

    @_guard("resolve_source")
    def resolve_source(spec):
        return "grace" if spec.variable == "water-storage" else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_grace")
        def fetch_grace(bbox, start, end, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["start"], seen["end"] = start, end
            return FakeWaterField()
        ns["fetch_grace"] = fetch_grace

    if with_era5:
        @_guard("fetch_era5")
        def fetch_era5(variables, bbox, start, end, stride_hours=24):
            if era5_fails:
                raise ConnectionError("simulated CDS failure")
            seen["era5_variables"] = list(variables)
            return FakeAtmo()
        ns["fetch_era5"] = fetch_era5

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field"] = field
        seen["render_series"] = series
        # The water-storage renderer contract: a dict with the "values"
        # key, NOT a scalar-grid CurrentField dict.
        assert isinstance(field, dict)
        assert "values" in field
        assert field["values"] is not None
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "frame_0001.png")
        with open(p, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        manifest = os.path.join(out_dir, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{}")
        return [p], manifest

    @_guard("render_video")
    def render_video(source, out_path, preset="reel", **kwargs):
        assert preset == "reel"
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".provenance.json",
                                     fps=30, n_frames=1)

    ns.update({"render_viz": render_viz, "render_video": render_video})
    return types.SimpleNamespace(**ns), seen


# --- plan_fetch ---------------------------------------------------------------


def test_plan_fetch_grace_fetchable_any_region():
    plan = plan_fetch(
        FakeWaterSpec(), lambda key: False,
        lambda spec: "grace" if spec.variable == "water-storage" else "")
    assert plan.fetchable is True
    assert plan.source == "grace"
    assert "CSR GRACE" in plan.reason


def test_plan_fetch_grace_bad_variable():
    spec = FakeWaterSpec(variable="sst")
    plan = plan_fetch(spec, lambda key: False, lambda s: "grace")
    assert plan.fetchable is False
    assert plan.kind == "bad_variable"
    assert "water-storage" in plan.reason


def test_plan_fetch_streamflow_refused_honestly():
    plan = plan_fetch(FakeWaterSpec(variable="streamflow"),
                      lambda key: False, lambda s: "")
    assert plan.fetchable is False
    assert plan.kind == "no_adapter"
    assert "streamflow" in plan.reason.lower()
    assert "GRACE" in plan.reason  # names the water-storage alternative


def test_plan_fetch_sea_level_refused_honestly():
    plan = plan_fetch(FakeWaterSpec(variable="sea-level"),
                      lambda key: False, lambda s: "")
    assert plan.fetchable is False
    assert plan.kind == "no_adapter"
    assert "altimetry" in plan.reason


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_grace_fetch_and_render(tmp_path):
    calls, seen = [], {}
    peers_ns, seen = make_water_peers(calls)
    spec = FakeWaterSpec()
    out = run_pipeline(spec, peers_ns, out_dir=str(tmp_path / "reel"))
    assert "fetch_grace" in calls
    assert seen["bbox"] == tuple(spec.bbox)
    assert (seen["start"], seen["end"]) == (spec.start, spec.end)
    # Provenance travels under fetch["grace"].
    assert out.provenance["fetch"]["grace"]["source"] == \
        "CSR GRACE/GRACE-FO RL06.3"
    assert out.provenance["fetch"]["grace"]["anomaly_baseline"] == \
        "2004-2009"
    assert "overlay_grids" not in seen["render_field"]
    assert seen["render_series"] is None


def test_run_pipeline_grace_no_interpolation_recorded(tmp_path):
    # The fake has no gap months here; provenance still carries the
    # gap-month list (empty) — the renderer never fills it.
    peers_ns, seen = make_water_peers([], )
    run_pipeline(FakeWaterSpec(), peers_ns, out_dir=str(tmp_path / "reel"))
    assert seen["render_field"]["gap_months"] == []
    assert seen["render_field"]["units"] == "cm"
    assert seen["render_field"]["anomaly_baseline"] == "2004-2009"


def test_run_pipeline_grace_missing_adapter():
    peers_ns, _ = make_water_peers([], with_fetch=False)
    with pytest.raises(UnfetchableRegionError) as excinfo:
        run_pipeline(FakeWaterSpec(), peers_ns, out_dir="/tmp/x-grace-nope")
    assert "survey-currents>=0.12.0" in str(excinfo.value)


def test_run_pipeline_grace_fetch_failure_wrapped(tmp_path):
    peers_ns, _ = make_water_peers([], fail_at="fetch_grace")
    with pytest.raises(RuntimeError, match="GRACE fetch failed"):
        run_pipeline(FakeWaterSpec(), peers_ns,
                     out_dir=str(tmp_path / "reel"))


def test_run_pipeline_grace_with_tp_context(tmp_path):
    peers_ns, seen = make_water_peers([])
    run_pipeline(FakeWaterSpec(overlays=("tp",)), peers_ns,
                 out_dir=str(tmp_path / "reel"))
    assert seen["era5_variables"] == ["tp"]
    context = seen["render_field"]["overlay_grids"]
    assert "tp" in context
    tp = context["tp"]
    # Monthly frames aligned cell-for-cell onto the GRACE grid.
    assert tp["times"] == [t.isoformat()
                           for t in FakeWaterField().times]
    assert np.shape(tp["grid"]) == (4, len(tp["lats"]), len(tp["lons"]))
    assert np.all(np.diff(np.asarray(tp["lons"])) > 0)
    # January's two snapshots were averaged (interior points finite —
    # the ERA5 context bbox is smaller than the GRACE grid, so border
    # cells stay NaN: no extrapolation).
    i_lon = list(tp["lons"]).index(-120.0)
    i_lat = list(tp["lats"]).index(36.0)
    assert np.isfinite(tp["grid"][0][i_lat, i_lon])
    assert tp["grid"][0][i_lat, i_lon] == pytest.approx(0.002)
    assert np.isnan(tp["grid"][:, 0, 0]).all()  # outside context bbox


def test_run_pipeline_grace_context_failure_still_renders(tmp_path):
    peers_ns, seen = make_water_peers([], era5_fails=True)
    run_pipeline(FakeWaterSpec(overlays=("tp",)), peers_ns,
                 out_dir=str(tmp_path / "reel"))
    assert "overlay_grids" not in seen["render_field"]  # graceful


def test_run_pipeline_grace_context_without_era5_peer(tmp_path):
    peers_ns, seen = make_water_peers([], with_era5=False)
    run_pipeline(FakeWaterSpec(overlays=("tp",)), peers_ns,
                 out_dir=str(tmp_path / "reel"))
    assert "overlay_grids" not in seen["render_field"]  # graceful


# --- helper unit tests --------------------------------------------------------


def test_fetch_water_context_requires_tp():
    fake = FakeWaterField().to_dict()
    spec = FakeWaterSpec(overlays=("wind",))
    calls = []
    peers_ns, _ = make_water_peers(calls)
    assert _fetch_water_context(peers_ns, ["wind"], fake, spec) == {}
    assert "fetch_era5" not in calls


def test_fetch_water_context_graceful_without_peer():
    fake = FakeWaterField().to_dict()
    spec = FakeWaterSpec()
    peers_ns, _ = make_water_peers([], with_era5=False)
    assert _fetch_water_context(peers_ns, ["tp"], fake, spec) == {}


def test_bilinear_resample_exact_on_grid():
    grid = np.array([[1.0, 2.0], [3.0, 4.0]])
    out = _bilinear_resample(grid, np.array([0.0, 1.0]),
                             np.array([0.0, 1.0]),
                             np.array([0.0]), np.array([0.0]))
    assert out[0, 0] == pytest.approx(1.0)
    out = _bilinear_resample(grid, np.array([0.0, 1.0]),
                             np.array([0.0, 1.0]),
                             np.array([0.5]), np.array([0.5]))
    assert out[0, 0] == pytest.approx(2.5)


def test_bilinear_resample_descending_axes():
    grid = np.array([[1.0, 2.0], [3.0, 4.0]])  # row0=lat1.0
    out = _bilinear_resample(grid, np.array([1.0, 0.0]),
                             np.array([1.0, 0.0]),
                             np.array([1.0]), np.array([1.0]))
    assert out[0, 0] == pytest.approx(1.0)


def test_bilinear_resample_out_of_range_nan():
    grid = np.array([[1.0, 2.0], [3.0, 4.0]])
    out = _bilinear_resample(grid, np.array([0.0, 1.0]),
                             np.array([0.0, 1.0]),
                             np.array([99.0]), np.array([0.0]))
    assert np.isnan(out[0, 0])


def test_bilinear_resample_ignores_source_nans():
    grid = np.array([[np.nan, 2.0], [3.0, 4.0]])
    out = _bilinear_resample(grid, np.array([0.0, 1.0]),
                             np.array([0.0, 1.0]),
                             np.array([0.5]), np.array([0.5]))
    # weighted mean of the 3 finite corners
    expected = (2.0 * 0.25 + 3.0 * 0.25 + 4.0 * 0.25) / 0.75
    assert out[0, 0] == pytest.approx(expected)


# --- registry / peers ---------------------------------------------------------


def test_supported_variables_and_source_labels():
    assert "water-storage" in pipeline.SUPPORTED_VARIABLES
    assert "streamflow" in pipeline.SUPPORTED_VARIABLES
    assert "sea-level" in pipeline.SUPPORTED_VARIABLES
    assert "grace" in pipeline.SOURCE_LABELS
    assert "GRACE" in pipeline.SOURCE_LABELS["grace"]


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def test_wire_peers_exposes_fetch_grace(monkeypatch):
    viz = _stub_module(
        "viz",
        parse_description=lambda *a, **k: None,
        UnparseableDescription=type("UnparseableDescription", (Exception,), {}),
        VizSpec=type("VizSpec", (), {}),
        is_fetchable=lambda key: False,
        get_region=lambda key: None,
        render_viz=lambda *a, **k: ([], ""),
    )
    glsea = _stub_module(
        "currents.glsea",
        fetch_glsea_sst=lambda *a, **k: None,
        fetch_glsea_lake_averages=lambda *a, **k: None,
        GLSEA_LON_MIN=-92.0, GLSEA_LAT_MIN=41.0,
        GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0,
    )
    currents = _stub_module("currents", glsea=glsea)
    grace = _stub_module("currents.grace",
                         fetch_grace=lambda *a, **k: None)
    animate = _stub_module("animate", render_video=lambda *a, **k: None)
    for name, mod in (("viz", viz), ("currents", currents),
                      ("currents.glsea", glsea),
                      ("currents.grace", grace),
                      ("animate", animate)):
        monkeypatch.setitem(sys.modules, name, mod)
    statuses = peers.load_peers()
    assert statuses["survey-currents"].installed is True
    ns = peers.wire_peers(statuses)
    assert callable(ns.fetch_grace)
