"""NSIDC sea-ice flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "nsidc" (fetchable in the polar
regions), the honest land-ice refusal (glaciers / ice sheets / icebergs
are a different physical product — never routed to NSIDC), the
sea-ice-outside-polar-regions refusal, the run_pipeline nsidc branch
(fetch_nsidc(bbox, start, end, stride_days=...), series=None,
provenance under fetch["nsidc"]), _field_to_dict adapting an
IceField-shaped object with zero renderer changes, the missing-adapter
upgrade message (survey-currents>=0.7.0), fetch-failure wrapping, and
wire_peers lazily exposing fetch_nsidc.
"""

from __future__ import annotations

import datetime as dt
import os
import types

import numpy as np
import pytest

from studio import pipeline
from studio.pipeline import (
    UnfetchableRegionError,
    _field_to_dict,
    plan_fetch,
    run_pipeline,
)


# --- fakes --------------------------------------------------------------------


class FakeIceField:
    """IceField-shaped: times/lats/lons + 3D values (percent, NaN=land)."""

    def __init__(self, provenance=None):
        self.times = [dt.datetime(2026, 2, 1, tzinfo=dt.timezone.utc),
                      dt.datetime(2026, 2, 2, tzinfo=dt.timezone.utc)]
        self.lats = np.array([67.0, 69.0, 71.0, 73.0])
        self.lons = np.array([-45.0, -43.0, -41.0, -39.0])
        vals = np.full((2, 4, 4), 80.0)
        vals[:, 0, :] = np.nan  # a land-like NaN row
        self.values = vals
        self.hemisphere = "north"
        self.provenance = provenance or {
            "source": "NSIDC G02135 v4.0 Sea Ice Index (daily concentration)",
            "n_files": 2,
        }


class FakeIceSpec:
    def __init__(self, variable="sea-ice", region_key="arctic-ocean"):
        self.title = "Arctic Ocean — Sea Ice, 2026"
        self.region_key = region_key
        self.bbox = (-180.0, 66.0, 180.0, 90.0)
        self.variable = variable
        self.start = "2026-02-01"
        self.end = "2026-02-07"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start, "end": self.end}


def make_nsidc_peers(calls, fail_at=None, with_fetch=True):
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
        return False  # region check must NOT gate nsidc (source routing does)

    @_guard("resolve_source")
    def resolve_source(spec):
        return "nsidc" if spec.variable == "sea-ice" else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_nsidc")
        def fetch_nsidc(bbox, start, end, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["start"] = start
            seen["end"] = end
            seen["kwargs"] = kwargs
            return FakeIceField()
        ns["fetch_nsidc"] = fetch_nsidc

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field_keys"] = sorted(field.keys())
        seen["render_series"] = series
        assert field["values"].shape == (2, 4, 4)
        assert np.isnan(field["values"][:, 0, :]).all()  # NaN row survived
        os.makedirs(out_dir, exist_ok=True)
        frames = []
        for i in range(2):
            p = os.path.join(out_dir, f"frame_{i+1:04d}.png")
            with open(p, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
            frames.append(p)
        manifest = os.path.join(out_dir, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{}")
        return frames, manifest

    @_guard("render_video")
    def render_video(source, out_path, preset="reel", **kwargs):
        assert preset == "reel"
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".provenance.json",
                                     fps=30, n_frames=2)

    ns.update({"render_viz": render_viz, "render_video": render_video})
    return types.SimpleNamespace(**ns), seen


# --- plan_fetch ---------------------------------------------------------------


def _resolve_nsidc(spec):
    return "nsidc" if spec.variable == "sea-ice" else ""


def test_plan_fetch_nsidc_fetchable_polar_regions():
    for region in ("arctic-ocean", "southern-ocean"):
        spec = FakeIceSpec(region_key=region)
        plan = plan_fetch(spec, lambda key: False, _resolve_nsidc)
        assert plan.fetchable, f"region {region!r}: {plan.reason}"
        assert plan.source == "nsidc"


def test_plan_fetch_land_ice_refused_never_nsidc():
    spec = FakeIceSpec(variable="land-ice")
    plan = plan_fetch(spec, lambda key: False, _resolve_nsidc)
    assert not plan.fetchable
    assert plan.kind == "no_adapter"
    assert "never routed to NSIDC" in plan.reason
    assert "glacier" in plan.reason.lower()


def test_plan_fetch_sea_ice_non_polar_refused():
    spec = FakeIceSpec(region_key="north-atlantic")
    plan = plan_fetch(spec, lambda key: False, lambda s: "")
    assert not plan.fetchable
    assert plan.kind == "no_adapter"
    assert "polar" in plan.reason.lower()


def test_plan_fetch_nsidc_wrong_variable_refused():
    spec = FakeIceSpec(variable="sst")
    plan = plan_fetch(spec, lambda key: False, lambda s: "nsidc")
    assert not plan.fetchable
    assert plan.kind == "bad_variable"


def test_source_labels_for_nsidc():
    assert pipeline.SOURCE_LABELS["nsidc"] == "NSIDC Sea Ice Index (G02135 v4.0)"


def test_sea_ice_in_supported_variables():
    assert "sea-ice" in pipeline.SUPPORTED_VARIABLES
    assert "land-ice" not in pipeline.SUPPORTED_VARIABLES


# --- _field_to_dict -----------------------------------------------------------


def test_field_to_dict_adapts_ice_field_unchanged():
    d = _field_to_dict(FakeIceField())
    assert sorted(d.keys()) == ["lats", "lons", "times", "values"]
    assert d["times"] == ["2026-02-01", "2026-02-02"]
    assert d["values"].shape == (2, 4, 4)
    assert np.isnan(d["values"][:, 0, :]).all()  # land NaNs survive


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_nsidc_branch(tmp_path):
    calls = []
    peers, seen = make_nsidc_peers(calls)
    out = str(tmp_path / "reel")
    result = run_pipeline(FakeIceSpec(), peers, out)

    assert "fetch_nsidc" in calls
    assert seen["bbox"] == (-180.0, 66.0, 180.0, 90.0)
    assert seen["start"] == "2026-02-01"
    assert seen["end"] == "2026-02-07"
    assert seen["kwargs"] == {"stride_days": 30}  # DEFAULT_STRIDE_DAYS
    assert seen["render_series"] is None  # sea ice has no lake-average series
    assert seen["render_field_keys"] == ["lats", "lons", "times", "values"]
    assert result.source == "nsidc"
    assert result.n_frames == 2
    assert result.provenance["fetch"]["nsidc"]["n_files"] == 2


def test_run_pipeline_nsidc_missing_adapter(tmp_path):
    peers, _seen = make_nsidc_peers([], with_fetch=False)
    with pytest.raises(UnfetchableRegionError) as excinfo:
        run_pipeline(FakeIceSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "survey-currents>=0.7.0" in msg
    assert "nsidc" in msg


def test_run_pipeline_nsidc_fetch_failure_wrapped(tmp_path):
    peers, _seen = make_nsidc_peers([], fail_at="fetch_nsidc")
    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline(FakeIceSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "NSIDC fetch failed" in msg
    assert "keyless" in msg  # actionable access hint


# --- wire_peers -----------------------------------------------------------------


def _fake_import_sea_ice(name, *a, **k):
    if name == "currents.sea_ice":
        return types.SimpleNamespace(fetch_nsidc_sic=lambda *a, **k: "nsidc")
    if name == "currents.glsea":
        return types.SimpleNamespace(
            fetch_glsea_sst=lambda *a, **k: None,
            fetch_glsea_lake_averages=lambda *a, **k: None,
            GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
            GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0)
    if name in ("viz.sources", "currents.sst_global", "currents.era5",
                "currents.currents_global", "currents.fires"):
        raise ImportError(f"No module named {name!r} (simulated old peer)")
    import importlib
    return importlib.import_module(name, *a, **k)


def _statuses():
    return {
        "survey-viz": types.SimpleNamespace(
            repo="survey-viz", module_name="viz",
            pip_command="p", needed_for="n",
            module=types.SimpleNamespace(
                parse_description=lambda t: t,
                UnparseableDescription=Exception,
                VizSpec=object, is_fetchable=lambda k: True,
                get_region=lambda k: None, render_viz=lambda *a, **k: None)),
        "survey-currents": types.SimpleNamespace(
            repo="survey-currents",
            module_name="currents", pip_command="p", needed_for="n",
            module=types.SimpleNamespace(
                glsea=types.SimpleNamespace(
                    fetch_glsea_sst=lambda *a, **k: None,
                    fetch_glsea_lake_averages=lambda *a, **k: None,
                    GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
                    GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0))),
        "survey-animate": types.SimpleNamespace(
            repo="survey-animate", module_name="animate",
            pip_command="p", needed_for="n",
            module=types.SimpleNamespace(
                render_video=lambda *a, **k: None)),
    }


def test_wire_peers_exposes_fetch_nsidc_lazily(monkeypatch):
    import importlib
    from studio import peers as peers_mod

    real_import = importlib.import_module
    monkeypatch.setattr(importlib, "import_module", _fake_import_sea_ice)
    ns = peers_mod.wire_peers(_statuses())
    assert ns.fetch_nsidc() == "nsidc"
    assert ns.fetch_firms is None  # simulated old peer lacks fires
