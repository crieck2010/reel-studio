"""GEBCO topography/bathymetry flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "gebco" (fetchable in any
region, variables "bathymetry"/"elevation" only — other variables are
bad_variable), the honest "country-borders" refusal (no_adapter,
naming the underlay), the run_pipeline gebco branch
(fetch_gebco(bbox, resolution=...), series=None, provenance under
fetch["gebco"]), _field_to_dict adapting a TopoField-shaped object
with zero renderer changes, the missing-adapter upgrade message
(survey-currents>=0.10.0), and fetch-failure wrapping.
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


class FakeTopoField:
    """TopoField-shaped: times/lats/lons + 3D values (metres, +up)."""

    def __init__(self, provenance=None):
        self.times = [dt.datetime(2024, 1, 1, tzinfo=dt.timezone.utc)]
        self.lats = np.array([36.0, 35.0, 34.0])
        self.lons = np.array([-122.0, -121.0, -120.0, -119.0])
        vals = np.full((1, 3, 4), -2500.0)
        vals[0, -1, -1] = 350.0  # a land cell
        self.values = vals
        self.units = "m"
        self.provenance = provenance or {
            "source": "GEBCO 2024",
            "grid": "GEBCO_2024",
            "static_compilation": True,
        }


class FakeGebcoSpec:
    def __init__(self, variable="bathymetry", region_key="north-pacific"):
        self.title = "North Pacific — Bathymetry"
        self.region_key = region_key
        self.bbox = (-180.0, 0.0, -90.0, 60.0)
        self.variable = variable
        self.start = "2026-09-27"
        self.end = "2026-09-27"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start, "end": self.end}


def make_gebco_peers(calls, fail_at=None, with_fetch=True):
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
        return False  # region check must NOT gate gebco (source routing does)

    @_guard("resolve_source")
    def resolve_source(spec):
        return "gebco" if spec.variable in ("bathymetry", "elevation") else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_gebco")
        def fetch_gebco(bbox, resolution="15s", **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["resolution"] = resolution
            return FakeTopoField()
        ns["fetch_gebco"] = fetch_gebco

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field_keys"] = sorted(field.keys())
        seen["render_series"] = series
        assert field["values"].shape == (1, 3, 4)
        assert field["values"][0, -1, -1] == 350.0  # land cell survived
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


def test_plan_fetch_gebco_fetchable_any_region():
    for var in ("bathymetry", "elevation"):
        plan = plan_fetch(FakeGebcoSpec(variable=var),
                          lambda key: False,
                          lambda s: "gebco")
        assert plan.fetchable is True
        assert plan.source == "gebco"
        assert "GEBCO 2024" in plan.reason


def test_plan_fetch_gebco_bad_variable():
    plan = plan_fetch(FakeGebcoSpec(variable="sst"),
                      lambda key: False,
                      lambda s: "gebco")
    assert plan.fetchable is False
    assert plan.kind == "bad_variable"
    assert "bathymetry" in plan.reason and "elevation" in plan.reason


def test_plan_fetch_country_borders_honest_refusal():
    spec = FakeGebcoSpec(variable="country-borders",
                         region_key="mediterranean-sea")
    plan = plan_fetch(spec, lambda key: False, lambda s: "")
    assert plan.fetchable is False
    assert plan.kind == "no_adapter"
    assert "underlay" in plan.reason


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_gebco_fetch_and_provenance(tmp_path):
    calls = []
    peers, seen = make_gebco_peers(calls)
    out = run_pipeline(FakeGebcoSpec(), peers,
                       out_dir=str(tmp_path / "reel"))
    assert "fetch_gebco" in calls
    assert seen["resolution"] == 0.25  # 90° span -> 360 cells at 0.25°
    assert seen["bbox"] == (-180.0, 0.0, -90.0, 60.0)
    assert seen["render_series"] is None
    assert "gebco" in out.provenance["fetch"]
    assert out.provenance["fetch"]["gebco"]["source"] == "GEBCO 2024"


def test_run_pipeline_gebco_missing_adapter():
    calls = []
    peers, _ = make_gebco_peers(calls, with_fetch=False)
    with pytest.raises(UnfetchableRegionError, match="0.10.0"):
        run_pipeline(FakeGebcoSpec(), peers, out_dir="/tmp/x-gebco-nope")


def test_run_pipeline_gebco_fetch_failure_wrapped(tmp_path):
    calls = []
    peers, _ = make_gebco_peers(calls, fail_at="fetch_gebco")
    with pytest.raises(RuntimeError, match="GEBCO fetch failed"):
        run_pipeline(FakeGebcoSpec(), peers,
                     out_dir=str(tmp_path / "reel"))


def test_field_to_dict_topo_passthrough():
    d = _field_to_dict(FakeTopoField())
    assert d["values"].shape == (1, 3, 4)
    assert d["values"][0, -1, -1] == 350.0


def test_supported_variables_registry():
    assert "bathymetry" in pipeline.SUPPORTED_VARIABLES
    assert "elevation" in pipeline.SUPPORTED_VARIABLES
    assert "country-borders" not in pipeline.SUPPORTED_VARIABLES
