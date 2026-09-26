"""ERA5 atmosphere flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "era5" (fetchable in any region),
the run_pipeline ERA5 branch (variables incl. overlays, stride_hours,
overlay_grids carried into render_viz, era5 provenance), the missing-
adapter refusal, and fetch-failure wrapping with the CDS setup hint.
"""

from __future__ import annotations

import os
import types

import numpy as np
import pytest

from studio import pipeline
from studio.pipeline import (
    UnfetchableRegionError,
    UnsupportedVariableError,
    _field_to_dict,
    plan_fetch,
    run_pipeline,
)


# --- fakes --------------------------------------------------------------------


class FakeEra5Field:
    """Era5Field-shaped: .times/.lats/.lons + .values + .overlay_grids."""

    def __init__(self):
        self.times = ["2024-01-06T12:00:00", "2024-01-07T12:00:00"]
        self.lats = np.array([18.0, 24.0, 31.0])
        self.lons = np.array([-98.0, -89.0, -80.0])
        self._u = np.full((2, 3, 3), 4.0)
        self._v = np.full((2, 3, 3), 1.0)
        self._msl = np.full((2, 3, 3), 1013.0)
        self.provenance = {
            "source": "cds",
            "dataset": "reanalysis-era5-single-levels",
            "sha256": "era5fakesha",
        }

    @property
    def values(self):
        return np.hypot(self._u, self._v)

    @property
    def overlay_grids(self):
        return {"msl": self._msl}


class FakeEra5Spec:
    def __init__(self, variable="wind", overlays=("msl",)):
        self.title = "Gulf of Mexico — 10-m Wind, 2024"
        self.region_key = "gulf-of-mexico"
        self.bbox = (-98.0, 18.0, -80.0, 31.0)
        self.variable = variable
        self.overlays = overlays
        self.start = "2024-01-01"
        self.end = "2024-01-31"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "overlays": list(self.overlays),
                "start": self.start, "end": self.end}


def _resolve_era5(spec):
    return "era5"


def make_era5_peers(calls, fail_at=None, with_fetch_era5=True):
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
        return False  # region check must NOT gate ERA5 (global grid)

    @_guard("resolve_source")
    def resolve_source(spec):
        return _resolve_era5(spec)

    if with_fetch_era5:
        @_guard("fetch_era5")
        def fetch_era5(variables, bbox, start, end, stride_hours=24):
            seen["variables"] = list(variables)
            seen["bbox"] = tuple(bbox)
            seen["stride_hours"] = stride_hours
            return FakeEra5Field()

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field_keys"] = sorted(field.keys())
        seen["render_series"] = series
        assert field["values"].shape[0] == 2
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

    ns = {
        "is_fetchable": is_fetchable,
        "resolve_source": resolve_source,
        "render_viz": render_viz,
        "render_video": render_video,
        "glsea_bounds": (-180.0, -90.0, 180.0, 90.0),
    }
    if with_fetch_era5:
        ns["fetch_era5"] = fetch_era5
    return types.SimpleNamespace(**ns), seen


# --- plan_fetch ---------------------------------------------------------------


def test_plan_fetch_era5_fetchable_any_region():
    plan = plan_fetch(FakeEra5Spec(), lambda key: False, _resolve_era5)
    assert plan.fetchable
    assert plan.source == "era5"
    assert plan.variable == "wind"
    assert plan.kind == "ok"
    assert "era5" in plan.reason.lower() or "ERA5" in plan.reason


def test_plan_fetch_era5_wrong_variable_refused():
    plan = plan_fetch(FakeEra5Spec(variable="sst", overlays=()),
                      lambda key: True, _resolve_era5)
    assert not plan.fetchable
    assert plan.kind == "bad_variable"
    assert "era5" in plan.reason


def test_plan_fetch_era5_label_known():
    assert pipeline.SOURCE_LABELS["era5"] == "Copernicus ERA5 (CDS)"


# --- _field_to_dict -----------------------------------------------------------


def test_field_to_dict_carries_overlay_grids_from_object():
    d = _field_to_dict(FakeEra5Field())
    assert d["values"].shape == (2, 3, 3)
    assert list(d["overlay_grids"].keys()) == ["msl"]
    assert d["overlay_grids"]["msl"].shape == (2, 3, 3)
    assert d["times"] == ["2024-01-06", "2024-01-07"]  # datetimes normalized


def test_field_to_dict_carries_overlay_grids_from_dict():
    field = {
        "times": ["2024-01-06"],
        "lats": np.array([18.0]),
        "lons": np.array([-98.0]),
        "values": np.ones((1, 1, 1)),
        "overlay_grids": {"msl": np.full((1, 1, 1), 1013.0)},
    }
    d = _field_to_dict(field)
    assert d["overlay_grids"]["msl"].shape == (1, 1, 1)


def test_field_to_dict_without_overlays_omits_key():
    field = {
        "times": ["2024-01-06"],
        "lats": np.array([18.0]),
        "lons": np.array([-98.0]),
        "values": np.ones((1, 1, 1)),
    }
    assert "overlay_grids" not in _field_to_dict(field)


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_era5_branch(tmp_path):
    calls = []
    peers, seen = make_era5_peers(calls)
    result = run_pipeline(FakeEra5Spec(), peers, str(tmp_path))

    assert calls[:3] == ["is_fetchable", "resolve_source", "fetch_era5"] \
        or "fetch_era5" in calls
    # base variable + overlay requested in one CDS call, daily stride
    assert seen["variables"] == ["wind", "msl"]
    assert seen["bbox"] == (-98.0, 18.0, -80.0, 31.0)
    assert seen["stride_hours"] == 24
    # overlay grids reach the renderer; no lake series for ERA5
    assert "overlay_grids" in seen["render_field_keys"]
    assert seen["render_series"] is None
    # provenance + result record the era5 source
    assert result.source == "era5"
    assert result.provenance["source"] == "era5"
    assert result.provenance["fetch"]["era5"]["sha256"] == "era5fakesha"
    assert result.n_frames == 2


def test_run_pipeline_era5_custom_stride_hours(tmp_path):
    calls = []
    peers, seen = make_era5_peers(calls)
    run_pipeline(FakeEra5Spec(), peers, str(tmp_path), stride_hours=6)
    assert seen["stride_hours"] == 6


def test_run_pipeline_era5_no_overlay_single_variable(tmp_path):
    calls = []
    peers, seen = make_era5_peers(calls)
    run_pipeline(FakeEra5Spec(variable="t2m", overlays=()), peers, str(tmp_path))
    assert seen["variables"] == ["t2m"]


def test_run_pipeline_era5_missing_adapter_refused(tmp_path):
    calls = []
    peers, _ = make_era5_peers(calls, with_fetch_era5=False)
    with pytest.raises(UnfetchableRegionError, match="survey-currents>=0.4.0"):
        run_pipeline(FakeEra5Spec(), peers, str(tmp_path))


def test_run_pipeline_era5_fetch_failure_wrapped(tmp_path):
    calls = []
    peers, _ = make_era5_peers(calls, fail_at="fetch_era5")
    with pytest.raises(RuntimeError, match="CDS"):
        run_pipeline(FakeEra5Spec(), peers, str(tmp_path))


def test_run_pipeline_era5_bad_variable_refused(tmp_path):
    calls = []
    peers, _ = make_era5_peers(calls)

    def resolve_bad(spec):
        return "era5"

    peers.resolve_source = resolve_bad
    with pytest.raises(UnsupportedVariableError):
        run_pipeline(FakeEra5Spec(variable="sst", overlays=()), peers,
                     str(tmp_path))
