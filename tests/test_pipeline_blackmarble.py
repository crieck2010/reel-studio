"""NASA Black Marble night-lights flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "blackmarble" (fetchable in any
region, variable "night-lights" only — other variables are
bad_variable), the honest "power-outage" refusal (no_adapter, naming
change detection), the run_pipeline blackmarble branch
(fetch_blackmarble(bbox, start, end, product="daily", stride_days=...),
series=None, provenance under fetch["blackmarble"]), _field_to_dict
adapting a LightsField-shaped object with zero renderer changes, the
missing-adapter upgrade message (survey-currents>=0.9.0), fetch-failure
wrapping, and wire_peers lazily exposing fetch_blackmarble.
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


class FakeLightsField:
    """LightsField-shaped: times/lats/lons + 3D values (nW/cm²/sr, NaN=unlit)."""

    def __init__(self, provenance=None):
        self.times = [dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                      dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)]
        self.lats = np.array([34.0, 35.0, 36.0, 37.0])
        self.lons = np.array([-122.0, -121.0, -120.0, -119.0])
        vals = np.full((2, 4, 4), 25.0)
        vals[:, 0, :] = np.nan  # an unlit/missing-like NaN row
        self.values = vals
        self.units = "nW/cm²/sr"
        self.provenance = provenance or {
            "source": "NASA Black Marble VNP46A2 V002",
            "product": "VNP46A2",
            "n_files": 2,
        }


class FakeLightsSpec:
    def __init__(self, variable="night-lights", region_key="california"):
        self.title = "California — Night Lights, 2024"
        self.region_key = region_key
        self.bbox = (-124.0, 32.0, -114.0, 42.0)
        self.variable = variable
        self.start = "2024-01-01"
        self.end = "2024-01-02"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start, "end": self.end}


def make_blackmarble_peers(calls, fail_at=None, with_fetch=True):
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
        return False  # region check must NOT gate blackmarble (source routing does)

    @_guard("resolve_source")
    def resolve_source(spec):
        return "blackmarble" if spec.variable == "night-lights" else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_blackmarble")
        def fetch_blackmarble(bbox, start, end, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["start"] = start
            seen["end"] = end
            seen["kwargs"] = kwargs
            return FakeLightsField()
        ns["fetch_blackmarble"] = fetch_blackmarble

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


def _resolve_blackmarble(spec):
    return "blackmarble" if spec.variable == "night-lights" else ""


def test_plan_fetch_blackmarble_fetchable_any_region():
    for region in ("california", "north-atlantic", "amazon-basin", "global"):
        spec = FakeLightsSpec(region_key=region)
        plan = plan_fetch(spec, lambda key: False, _resolve_blackmarble)
        assert plan.fetchable, f"region {region!r}: {plan.reason}"
        assert plan.source == "blackmarble"


def test_plan_fetch_blackmarble_wrong_variable_refused():
    spec = FakeLightsSpec(variable="sst")
    plan = plan_fetch(spec, lambda key: False, lambda s: "blackmarble")
    assert not plan.fetchable
    assert plan.kind == "bad_variable"
    assert "'night-lights'" in plan.reason


def test_plan_fetch_power_outage_refused_honestly():
    spec = FakeLightsSpec(variable="power-outage")
    plan = plan_fetch(spec, lambda key: True, lambda s: "")
    assert not plan.fetchable
    assert plan.kind == "no_adapter"
    assert "change detection" in plan.reason
    assert "single daily Black Marble" in plan.reason


def test_plan_fetch_power_outage_refused_in_unfetchable_region_too():
    spec = FakeLightsSpec(variable="power-outage")
    plan = plan_fetch(spec, lambda key: False, lambda s: "")
    assert not plan.fetchable
    assert plan.kind == "no_adapter"
    assert "change detection" in plan.reason


def test_source_labels_for_blackmarble():
    assert pipeline.SOURCE_LABELS["blackmarble"] == "NASA Black Marble VNP46A2"


def test_night_lights_in_supported_variables():
    assert "night-lights" in pipeline.SUPPORTED_VARIABLES
    assert "power-outage" not in pipeline.SUPPORTED_VARIABLES


# --- _field_to_dict -----------------------------------------------------------


def test_field_to_dict_adapts_lights_field_unchanged():
    d = _field_to_dict(FakeLightsField())
    assert sorted(d.keys()) == ["lats", "lons", "times", "values"]
    assert d["times"] == ["2024-01-01", "2024-01-02"]
    assert d["values"].shape == (2, 4, 4)
    assert np.isnan(d["values"][:, 0, :]).all()  # unlit NaNs survive


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_blackmarble_branch(tmp_path):
    calls = []
    peers, seen = make_blackmarble_peers(calls)
    out = str(tmp_path / "reel")
    result = run_pipeline(FakeLightsSpec(), peers, out)

    assert "fetch_blackmarble" in calls
    assert seen["bbox"] == (-124.0, 32.0, -114.0, 42.0)
    assert seen["start"] == "2024-01-01"
    assert seen["end"] == "2024-01-02"
    assert seen["kwargs"] == {"product": "daily",
                              "stride_days": 30}  # DEFAULT_STRIDE_DAYS
    assert seen["render_series"] is None  # night lights have no lake series
    assert seen["render_field_keys"] == ["lats", "lons", "times", "values"]
    assert result.source == "blackmarble"
    assert result.n_frames == 2
    assert result.provenance["fetch"]["blackmarble"]["n_files"] == 2
    assert result.provenance["fetch"]["blackmarble"]["product"] == "VNP46A2"


def test_run_pipeline_blackmarble_missing_adapter(tmp_path):
    peers, _seen = make_blackmarble_peers([], with_fetch=False)
    with pytest.raises(UnfetchableRegionError) as excinfo:
        run_pipeline(FakeLightsSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "survey-currents>=0.9.0" in msg
    assert "blackmarble" in msg


def test_run_pipeline_blackmarble_fetch_failure_wrapped(tmp_path):
    peers, _seen = make_blackmarble_peers([], fail_at="fetch_blackmarble")
    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline(FakeLightsSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "Black Marble fetch failed" in msg
    assert "EARTHDATA_USERNAME" in msg  # actionable credential hint
    assert "h5py" in msg


# --- wire_peers -----------------------------------------------------------------


def _fake_import_blackmarble(name, *a, **k):
    if name == "currents.blackmarble":
        return types.SimpleNamespace(
            fetch_blackmarble=lambda *a, **k: "blackmarble")
    if name == "currents.glsea":
        return types.SimpleNamespace(
            fetch_glsea_sst=lambda *a, **k: None,
            fetch_glsea_lake_averages=lambda *a, **k: None,
            GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
            GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0)
    if name in ("viz.sources", "currents.sst_global", "currents.era5",
                "currents.currents_global", "currents.fires",
                "currents.sea_ice", "currents.imerg", "currents.basemaps", "currents.storms",
                "currents.grace", "currents.streamgages"):
        raise ImportError(f"No module named {name!r} (simulated old peer)")
    import importlib
    return importlib.import_module(name, *a, **k)


def _statuses():
    from studio import peers as peers_mod
    return {
        "survey-viz": peers_mod.PeerStatus(
            repo="survey-viz", module_name="viz",
            pip_command="p", needed_for="n",
            module=types.SimpleNamespace(
                parse_description=lambda t: t,
                UnparseableDescription=Exception,
                VizSpec=object, is_fetchable=lambda k: True,
                get_region=lambda k: None, render_viz=lambda *a, **k: None)),
        "survey-currents": peers_mod.PeerStatus(
            repo="survey-currents",
            module_name="currents", pip_command="p", needed_for="n",
            module=types.SimpleNamespace(
                glsea=types.SimpleNamespace(
                    fetch_glsea_sst=lambda *a, **k: None,
                    fetch_glsea_lake_averages=lambda *a, **k: None,
                    GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
                    GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0))),
        "survey-animate": peers_mod.PeerStatus(
            repo="survey-animate", module_name="animate",
            pip_command="p", needed_for="n",
            module=types.SimpleNamespace(
                render_video=lambda *a, **k: None)),
    }


def test_wire_peers_exposes_fetch_blackmarble_lazily(monkeypatch):
    import importlib
    from studio import peers as peers_mod

    monkeypatch.setattr(importlib, "import_module",
                        _fake_import_blackmarble)
    ns = peers_mod.wire_peers(_statuses())
    assert ns.fetch_blackmarble() == "blackmarble"
    assert ns.fetch_imerg is None  # simulated old peer lacks imerg
