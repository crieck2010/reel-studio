"""Source routing in the pipeline (survey-viz >= 0.2.0 / survey-currents >= 0.3.0).

Covers: plan_fetch with a resolve_source double (oisst default for ocean
regions, mur pin, legacy GLSEA fallback when the peer is too old), and
run_pipeline's global-SST fetch path (fetch_oisst, series=None, source
provenance) plus the missing-adapter error.

All offline, fake peers only.
"""

from __future__ import annotations

import os
import types

import numpy as np
import pytest

from studio import pipeline
from studio.pipeline import (UnfetchableRegionError,
                             UnsupportedVariableError)


# --- doubles ------------------------------------------------------------------

class FakeField:
    """GlseaField/SstField-shaped: .times/.lats/.lons + 3D grid + .provenance."""

    def __init__(self):
        self.times = ["2020-01-01T00:00:00+00:00", "2020-02-01T00:00:00+00:00"]
        self.lats = np.array([0.0, 30.0, 60.0])
        self.lons = np.array([-80.0, -30.0, 20.0])
        self.sst = np.ma.masked_array(np.full((2, 3, 3), 20.0), mask=False)
        self.provenance = {"url": "https://example.invalid/oisst?x",
                           "sha256": "fakesha"}


class FakeSpec:
    def __init__(self, region_key="north-atlantic", variable="sst",
                 source=""):
        self.title = "North Atlantic — Surface Water Temperature"
        self.region_key = region_key
        self.bbox = (-80.0, 0.0, 20.0, 66.0)
        self.variable = variable
        self.start = "2020-01-01"
        self.end = "2020-12-31"
        self.source = source

    def to_dict(self):
        return {"region_key": self.region_key, "variable": self.variable,
                "bbox": list(self.bbox), "source": self.source,
                "start": self.start, "end": self.end}


_DEFAULT = object()


def make_oisst_peers(calls, seen=None, fetch_oisst=_DEFAULT):
    """Fake peers with source routing: resolve_source -> oisst for oceans."""
    def _guard(name):
        def deco(fn):
            def wrapper(*a, **k):
                calls.append(name)
                return fn(*a, **k)
            return wrapper
        return deco

    @_guard("is_fetchable")
    def is_fetchable(key):
        return True

    @_guard("resolve_source")
    def resolve_source(spec):
        return getattr(spec, "source", "") or "oisst"

    @_guard("fetch_oisst")
    def _fetch_oisst(bbox, start, end, stride_days=30):
        if seen is not None:
            seen.append(tuple(bbox))
        return FakeField()

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        assert series is None  # global SST has no lake-average series
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
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".provenance.json",
                                     fps=30.0, n_frames=1,
                                     width=1080, height=1920)

    return types.SimpleNamespace(
        is_fetchable=is_fetchable,
        resolve_source=resolve_source,
        fetch_oisst=_fetch_oisst if fetch_oisst is _DEFAULT else fetch_oisst,
        fetch_mur=None,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-92.4199507342304, 38.8749871947297,
                      -75.8816402880531, 50.6059751976539),
    )


# --- plan_fetch ---------------------------------------------------------------

def test_plan_fetch_oisst_via_resolve_source():
    plan = pipeline.plan_fetch(
        FakeSpec(), lambda key: False, lambda spec: "oisst")
    assert plan.fetchable and plan.kind == "ok"
    assert plan.source == "oisst"
    assert "OISST" in plan.reason


def test_plan_fetch_mur_pin():
    plan = pipeline.plan_fetch(
        FakeSpec(source="mur"), lambda key: False, lambda spec: "mur")
    assert plan.fetchable and plan.source == "mur"
    assert "MUR" in plan.reason


def test_plan_fetch_legacy_glsea_without_resolve_source():
    # old-style peer / old viz: falls back to the GLSEA-only path.
    plan = pipeline.plan_fetch(FakeSpec(region_key="lake-michigan"),
                               lambda key: True)
    assert plan.fetchable and plan.source == "glsea"
    assert plan.lake == "michigan"


def test_plan_fetch_oisst_bad_variable():
    plan = pipeline.plan_fetch(
        FakeSpec(variable="currents"), lambda key: False, lambda spec: "oisst")
    assert not plan.fetchable and plan.kind == "bad_variable"


def test_plan_fetch_unknown_source_ignored_falls_back():
    # resolve_source raising ValueError (unknown spec.source) -> legacy path.
    def bad(spec):
        raise ValueError("unknown source 'modis'")
    plan = pipeline.plan_fetch(FakeSpec(region_key="lake-michigan"),
                               lambda key: True, bad)
    assert plan.fetchable and plan.source == "glsea"


# --- run_pipeline -------------------------------------------------------------

def test_run_pipeline_oisst_fetch_path(tmp_path):
    calls, seen = [], []
    peers = make_oisst_peers(calls, seen)
    result = pipeline.run_pipeline(FakeSpec(), peers, str(tmp_path))
    assert "fetch_sst" not in calls and "fetch_averages" not in calls
    assert "fetch_oisst" in calls
    assert seen and seen[0] == (-80.0, 0.0, 20.0, 66.0)  # spec bbox, unclamped
    assert result.source == "oisst"
    assert result.provenance["source"] == "oisst"
    assert result.provenance["lake"] == ""
    assert os.path.isfile(result.video_path)


def test_run_pipeline_oisst_missing_adapter_is_honest(tmp_path):
    calls = []
    peers = make_oisst_peers(calls, fetch_oisst=None)
    with pytest.raises(UnfetchableRegionError, match="survey-currents>=0.3.0"):
        pipeline.run_pipeline(FakeSpec(), peers, str(tmp_path))


def test_run_pipeline_oisst_fetch_failure_wrapped(tmp_path):
    calls = []

    def boom(bbox, start, end, stride_days=30):
        raise ConnectionError("simulated outage")

    peers = make_oisst_peers(calls, fetch_oisst=boom)
    with pytest.raises(RuntimeError, match="SST fetch failed"):
        pipeline.run_pipeline(FakeSpec(), peers, str(tmp_path))


def test_run_pipeline_mur_credentials_failure_wrapped(tmp_path):
    calls = []

    def boom(bbox, start, end, stride_days=30):
        raise PermissionError("earthdata login required")

    peers = make_oisst_peers(calls, fetch_oisst=boom)
    peers.resolve_source = lambda spec: "mur"
    peers.fetch_mur = boom
    with pytest.raises(RuntimeError, match="Earthdata"):
        pipeline.run_pipeline(FakeSpec(source="mur"), peers, str(tmp_path))
