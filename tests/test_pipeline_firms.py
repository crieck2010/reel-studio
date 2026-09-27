"""FIRMS active-fire flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "firms" (fetchable in any region,
including the 5 new fire regions — the FIRMS area API is global), the
honest burn-scar refusal (FIRMS is active detections only; burn-scar
mapping is survey-burn's future imagery adapter), the run_pipeline
firms branch (fetch_firms(bbox, start, end), series=None, provenance
under fetch["firms"]), _field_to_dict adapting a FireField via
to_density_grid() with zero renderer changes, the missing-adapter
upgrade message (survey-currents>=0.6.0), fetch-failure wrapping, and
wire_peers lazily exposing fetch_firms.
"""

from __future__ import annotations

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


class FakeFireField:
    """FireField-shaped: to_density_grid() returns the render dict shape."""

    def __init__(self, provenance=None):
        self.provenance = provenance or {
            "source": "firms/NASA FIRMS",
            "combined_sha256": "firmsfakesha",
        }

    def to_density_grid(self, resolution=0.25):
        return {
            "times": ["2024-08-01T00:00:00", "2024-08-02T00:00:00"],
            "lats": np.array([32.5, 36.0, 39.5, 42.0]),
            "lons": np.array([-124.5, -121.0, -117.5, -114.0]),
            "values": np.ma.masked_array(
                np.arange(2 * 4 * 4, dtype=float).reshape(2, 4, 4)
            ),
        }


class FakeFireSpec:
    def __init__(self, variable="fire", region_key="california"):
        self.title = "California — Active Fires, 2024"
        self.region_key = region_key
        self.bbox = (-124.5, 32.5, -114.0, 42.0)
        self.variable = variable
        self.start = "2024-08-01"
        self.end = "2024-08-07"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start, "end": self.end}


def make_firms_peers(calls, fail_at=None, with_fetch=True):
    seen = {}
    namespace = _make_firms_namespace(calls, seen, fail_at, with_fetch)
    return namespace, seen


def _make_firms_namespace(calls, seen, fail_at=None, with_fetch=True):
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
        return False  # region check must NOT gate firms (global area API)

    @_guard("resolve_source")
    def resolve_source(spec):
        return "firms"

    if with_fetch:
        @_guard("fetch_firms")
        def fetch_firms(bbox, start, end, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["start"] = start
            seen["end"] = end
            seen["kwargs"] = kwargs
            return FakeFireField()

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field_keys"] = sorted(field.keys())
        seen["render_series"] = series
        assert field["values"].shape == (2, 4, 4)
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

    return types.SimpleNamespace(
        is_fetchable=is_fetchable,
        resolve_source=resolve_source,
        render_viz=render_viz,
        render_video=render_video,
        **({"fetch_firms": fetch_firms} if with_fetch else {}),
    )


# --- plan_fetch ---------------------------------------------------------------


def _resolve_firms(spec):
    return "firms"


def test_plan_fetch_firms_fetchable_any_region():
    for region in ("california", "amazon-basin", "pacific-northwest",
                   "global", "lake-superior"):
        spec = FakeFireSpec(region_key=region)
        plan = plan_fetch(spec, lambda key: False, _resolve_firms)
        assert plan.fetchable, f"region {region!r}: {plan.reason}"
        assert plan.source == "firms"


def test_plan_fetch_burn_scar_refused_with_survey_burn_pointer():
    spec = FakeFireSpec(variable="burn-scar")
    plan = plan_fetch(spec, lambda key: False, _resolve_firms)
    assert not plan.fetchable
    assert plan.kind == "no_adapter"
    assert "survey-burn" in plan.reason
    assert "active" in plan.reason.lower()  # FIRMS is detections-only


def test_plan_fetch_firms_wrong_variable_refused():
    spec = FakeFireSpec(variable="sst")
    plan = plan_fetch(spec, lambda key: False, _resolve_firms)
    assert not plan.fetchable
    assert plan.kind == "bad_variable"


def test_source_labels_for_firms():
    assert pipeline.SOURCE_LABELS["firms"] == "NASA FIRMS"


def test_fires_in_supported_variables():
    assert "fire" in pipeline.SUPPORTED_VARIABLES
    assert "burn-scar" not in pipeline.SUPPORTED_VARIABLES


# --- _field_to_dict -----------------------------------------------------------


def test_field_to_dict_adapts_fire_field_via_density_grid():
    d = _field_to_dict(FakeFireField())
    assert sorted(d.keys()) == ["lats", "lons", "times", "values"]
    # _time_to_date_str normalized the ISO-datetime times.
    assert d["times"] == ["2024-08-01", "2024-08-02"]
    assert d["values"].shape == (2, 4, 4)


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_firms_branch(tmp_path):
    calls = []
    peers, seen = make_firms_peers(calls)
    out = str(tmp_path / "reel")
    result = run_pipeline(FakeFireSpec(), peers, out)

    assert "fetch_firms" in calls
    assert seen["bbox"] == (-124.5, 32.5, -114.0, 42.0)
    assert seen["start"] == "2024-08-01"
    assert seen["end"] == "2024-08-07"
    assert seen["kwargs"] == {}  # no stride for FIRMS (daily by construction)
    assert seen["render_series"] is None  # fires have no lake-average series
    assert seen["render_field_keys"] == ["lats", "lons", "times", "values"]
    assert result.source == "firms"
    assert result.n_frames == 2
    assert result.provenance["fetch"]["firms"]["combined_sha256"] == "firmsfakesha"


def test_run_pipeline_firms_missing_adapter(tmp_path):
    peers, _seen = make_firms_peers([], with_fetch=False)
    with pytest.raises(UnfetchableRegionError) as excinfo:
        run_pipeline(FakeFireSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "survey-currents>=0.6.0" in msg
    assert "currents.fires" in msg or "firms" in msg


def test_run_pipeline_firms_fetch_failure_wrapped(tmp_path):
    peers, _seen = make_firms_peers([], fail_at="fetch_firms")
    with pytest.raises(RuntimeError) as excinfo:
        run_pipeline(FakeFireSpec(), peers, str(tmp_path / "reel"))
    msg = str(excinfo.value)
    assert "FIRMS fetch failed" in msg
    assert "FIRMS_MAP_KEY" in msg  # actionable credential hint


# --- wire_peers -----------------------------------------------------------------


def _fake_import_fires(name, *a, **k):
    if name == "currents.fires":
        return types.SimpleNamespace(fetch_firms=lambda *a, **k: "firms")
    if name == "currents.glsea":
        return types.SimpleNamespace(
            fetch_glsea_sst=lambda *a, **k: None,
            fetch_glsea_lake_averages=lambda *a, **k: None,
            GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
            GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0)
    if name in ("viz.sources", "currents.sst_global", "currents.era5",
                "currents.currents_global", "currents.sea_ice"):
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


def test_wire_peers_exposes_fetch_firms_lazily(monkeypatch):
    import importlib
    from studio import peers as peers_mod

    real_import = importlib.import_module

    def fake_import(name, *a, **k):
        if name in ("viz.sources", "currents.sst_global", "currents.era5",
                    "currents.currents_global"):
            raise ImportError(f"No module named {name!r} (simulated)")
        if name == "currents.fires":
            return types.SimpleNamespace(
                fetch_firms=lambda *a, **k: "firms-field")
        return _fake_import_fires(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    ns = peers_mod.wire_peers(_statuses())
    assert callable(ns.fetch_firms)
    assert ns.fetch_firms() == "firms-field"
    assert ns.fetch_oscar is None  # simulated old peer still degrades


def test_wire_peers_firms_missing_on_old_survey_currents(monkeypatch):
    import importlib
    from studio import peers as peers_mod

    def fake_import(name, *a, **k):
        if name == "currents.fires":
            raise ImportError("No module named 'currents.fires' (old peer)")
        return _fake_import_fires(name, *a, **k)

    monkeypatch.setattr(importlib, "import_module", fake_import)
    ns = peers_mod.wire_peers(_statuses())
    assert ns.fetch_firms is None  # pipeline raises the actionable upgrade msg
