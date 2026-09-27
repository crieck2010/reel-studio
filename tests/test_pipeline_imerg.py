"""GPM IMERG precipitation flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "imerg" (fetchable in any region,
variable "tp" only — other variables are bad_variable), the
run_pipeline imerg branch (fetch_imerg(bbox, start, end,
accumulate="daily", run="late", stride_days=...), series=None,
provenance under fetch["imerg"]), _field_to_dict adapting a
RainField-shaped object with zero renderer changes, the missing-adapter
upgrade message (survey-currents>=0.8.0), fetch-failure wrapping, and
wire_peers lazily exposing fetch_imerg.
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


class FakeRainField:
    """RainField-shaped: times/lats/lons + 3D values (mm/day, NaN=missing)."""

    def __init__(self, provenance=None):
        self.times = [dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc),
                      dt.datetime(2024, 1, 2, tzinfo=dt.timezone.utc)]
        self.lats = np.array([25.0, 26.0, 27.0, 28.0])
        self.lons = np.array([-90.0, -89.0, -88.0, -87.0])
        vals = np.full((2, 4, 4), 12.5)
        vals[:, 0, :] = np.nan  # a missing-like NaN row
        self.values = vals
        self.units = "mm/day"
        self.run = "late"
        self.accumulate = "daily"
        self.provenance = provenance or {
            "source": "NASA GPM IMERG V07 (GPM_3IMERGHHL)",
            "n_files": 96,
            "run": "late",
            "accumulate": "daily",
        }


class FakeRainSpec:
    def __init__(self, variable="tp", region_key="gulf-of-mexico"):
        self.title = "Gulf of Mexico — Precipitation, 2024"
        self.region_key = region_key
        self.bbox = (-97.0, 18.0, -82.0, 31.0)
        self.variable = variable
        self.start = "2024-01-01"
        self.end = "2024-01-02"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start, "end": self.end}


def make_imerg_peers(calls, fail_at=None, with_fetch=True):
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
        return False  # region check must NOT gate imerg (source routing does)

    @_guard("resolve_source")
    def resolve_source(spec):
        return "imerg" if spec.variable == "tp" else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_imerg")
        def fetch_imerg(bbox, start, end, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["start"] = start
            seen["end"] = end
            seen["kwargs"] = kwargs
            return FakeRainField()
        ns["fetch_imerg"] = fetch_imerg

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


def _resolve_imerg(spec):
    return "imerg" if spec.variable == "tp" else ""


def test_plan_fetch_imerg_fetchable_any_region():
    for region in ("gulf-of-mexico", "north-atlantic", "caribbean-sea"):
        spec = FakeRainSpec(region_key=region)
        plan = plan_fetch(spec, lambda key: False, _resolve_imerg)
        assert plan.fetchable, f"region {region!r}: {plan.reason}"
        assert plan.source == "imerg"


def test_plan_fetch_imerg_wrong_variable_refused():
    spec = FakeRainSpec(variable="wind")
    plan = plan_fetch(spec, lambda key: False, lambda s: "imerg")
    assert not plan.fetchable
    assert plan.kind == "bad_variable"
    assert "'tp'" in plan.reason


def test_source_labels_for_imerg():
    assert pipeline.SOURCE_LABELS["imerg"] == "NASA GPM IMERG V07"


def test_tp_in_supported_variables():
    assert "tp" in pipeline.SUPPORTED_VARIABLES


# --- _field_to_dict -----------------------------------------------------------


def test_field_to_dict_adapts_rain_field_unchanged():
    d = _field_to_dict(FakeRainField())
    assert sorted(d.keys()) == ["lats", "lons", "times", "values"]
    assert d["times"] == ["2024-01-01", "2024-01-02"]
    assert d["values"].shape == (2, 4, 4)
    assert np.isnan(d["values"][:, 0, :]).all()  # missing NaNs survive


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_imerg_branch(tmp_path):
    calls = []
    peers, seen = make_imerg_peers(calls)
    out = str(tmp_path / "reel")
    result = run_pipeline(FakeRainSpec(), peers, out)

    assert "fetch_imerg" in calls
    assert seen["bbox"] == (-97.0, 18.0, -82.0, 31.0)
    assert seen["start"] == "2024-01-01"
    assert seen["end"] == "2024-01-02"
    # the pipeline relies on the fetch_imerg defaults for these two
    assert seen["kwargs"] == {"accumulate": "daily", "run": "late",
                              "stride_days": 30}  # DEFAULT_STRIDE_DAYS
    assert seen["render_series"] is None  # precipitation has no lake series
    assert seen["render_field_keys"] == ["lats", "lons", "times", "values"]
    assert result.source == "imerg"
    assert result.n_frames == 2
    assert result.provenance["fetch"]["imerg"]["n_files"] == 96
    assert result.provenance["fetch"]["imerg"]["run"] == "late"


def test_run_pipeline_imerg_missing_adapter(tmp_path):
    peers, _seen = make_imerg_peers([], with_fetch=False)
    with pytest.raises(UnfetchableRegionError) as excinfo:
        run_pipeline(FakeRainSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "survey-currents>=0.8.0" in msg
    assert "imerg" in msg


def test_run_pipeline_imerg_fetch_failure_wrapped(tmp_path):
    peers, _seen = make_imerg_peers([], fail_at="fetch_imerg")
    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline(FakeRainSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "IMERG fetch failed" in msg
    assert "EARTHDATA_USERNAME" in msg  # actionable credential hint
    assert "h5py" in msg


# --- wire_peers -----------------------------------------------------------------


def _fake_import_imerg(name, *a, **k):
    if name == "currents.imerg":
        return types.SimpleNamespace(fetch_imerg=lambda *a, **k: "imerg")
    if name == "currents.glsea":
        return types.SimpleNamespace(
            fetch_glsea_sst=lambda *a, **k: None,
            fetch_glsea_lake_averages=lambda *a, **k: None,
            GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
            GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0)
    if name in ("viz.sources", "currents.sst_global", "currents.era5",
                "currents.currents_global", "currents.fires",
                "currents.sea_ice",
                "currents.blackmarble", "currents.basemaps", "currents.storms",
                "currents.grace", "currents.streamgages",
                "currents.oceancolor", "currents.earthquakes"):
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


def test_wire_peers_exposes_fetch_imerg_lazily(monkeypatch):
    import importlib
    from studio import peers as peers_mod

    monkeypatch.setattr(importlib, "import_module", _fake_import_imerg)
    ns = peers_mod.wire_peers(_statuses())
    assert ns.fetch_imerg() == "imerg"
    assert ns.fetch_nsidc is None  # simulated old peer lacks sea_ice
