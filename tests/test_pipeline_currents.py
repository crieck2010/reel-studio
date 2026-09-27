"""Global-currents flow-through (no network, no peers needed).

Covers: plan_fetch routing for sources "oscar" / "cmems-currents"
(fetchable in any non-Great-Lakes region; Great Lakes currents stay an
honest refusal), the run_pipeline currents branch
(fetch(bbox, start, end, stride_days=...), series=None, provenance under
fetch["oscar"] / fetch["cmems-currents"]), _field_to_dict computing the
scalar current speed sqrt(u^2+v^2) from CurrentField u/v, the missing-
adapter upgrade message, fetch-failure wrapping, and wire_peers lazily
exposing the new fetch callables.
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


class FakeCurrentField:
    """CurrentField-shaped: .times/.lats/.lons + 3D .u/.v."""

    def __init__(self, provenance=None):
        self.times = ["2024-01-06T00:00:00", "2024-01-07T00:00:00"]
        self.lats = np.array([25.0, 30.0, 35.0])
        self.lons = np.array([-81.0, -70.0, -55.0])
        self.u = np.ma.masked_array(np.full((2, 3, 3), 3.0))
        self.v = np.ma.masked_array(np.full((2, 3, 3), 4.0))
        self.u[0, 0, 0] = np.ma.masked  # masked cell -> NaN speed
        self.provenance = provenance or {
            "source": "podaac/OSCAR_L4_OC_V2.0",
            "combined_sha256": "oscarfakesha",
        }


class FakeCurrentsSpec:
    def __init__(self, variable="currents", region_key="gulf-stream"):
        self.title = "Gulf Stream — Surface Currents, 2024"
        self.region_key = region_key
        self.bbox = (-81.0, 25.0, -55.0, 43.0)
        self.variable = variable
        self.start = "2024-01-01"
        self.end = "2024-01-31"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start, "end": self.end}


def _resolve_oscar(spec):
    return "oscar"


def _resolve_cmems(spec):
    return "cmems-currents"


def make_currents_peers(calls, source="oscar", fail_at=None,
                        with_fetch=True):
    seen = {}
    attr = "fetch_oscar" if source == "oscar" else "fetch_cmems_currents"

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
        return False  # region check must NOT gate currents (global grids)

    @_guard("resolve_source")
    def resolve_source(spec):
        return _resolve_oscar(spec) if source == "oscar" else _resolve_cmems(spec)

    if with_fetch:
        @_guard(attr)
        def fetch(bbox, start, end, stride_days=30):
            seen["bbox"] = tuple(bbox)
            seen["start"] = start
            seen["end"] = end
            seen["stride_days"] = stride_days
            return FakeCurrentField()

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field_keys"] = sorted(field.keys())
        seen["render_series"] = series
        assert field["values"].shape == (2, 3, 3)
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
    if with_fetch:
        ns[attr] = fetch
    return types.SimpleNamespace(**ns), seen


# --- plan_fetch ---------------------------------------------------------------


def test_plan_fetch_oscar_fetchable_ocean():
    plan = plan_fetch(FakeCurrentsSpec(), lambda key: False, _resolve_oscar)
    assert plan.fetchable
    assert plan.source == "oscar"
    assert plan.variable == "currents"
    assert plan.kind == "ok"
    assert "oscar" in plan.reason.lower()


def test_plan_fetch_cmems_currents_fetchable_ocean():
    plan = plan_fetch(FakeCurrentsSpec(), lambda key: False, _resolve_cmems)
    assert plan.fetchable
    assert plan.source == "cmems-currents"
    assert plan.kind == "ok"


def test_plan_fetch_great_lakes_currents_refused():
    spec = FakeCurrentsSpec(region_key="lake-superior")
    plan = plan_fetch(spec, lambda key: True, _resolve_oscar)
    assert not plan.fetchable
    assert plan.kind == "no_adapter"
    assert "Great Lakes" in plan.reason or "lake" in plan.reason.lower()


def test_plan_fetch_oscar_wrong_variable_refused():
    plan = plan_fetch(FakeCurrentsSpec(variable="sst"),
                      lambda key: True, _resolve_oscar)
    assert not plan.fetchable
    assert plan.kind == "bad_variable"


def test_plan_fetch_oisst_rejects_currents():
    def resolve_mur(spec):
        return "mur"
    plan = plan_fetch(FakeCurrentsSpec(), lambda key: True, resolve_mur)
    assert not plan.fetchable
    assert plan.kind == "bad_variable"


def test_source_labels_for_currents():
    assert pipeline.SOURCE_LABELS["oscar"] == "NASA PODAAC OSCAR v2.0"
    assert "CMEMS" in pipeline.SOURCE_LABELS["cmems-currents"]


def test_currents_in_supported_variables():
    assert "currents" in pipeline.SUPPORTED_VARIABLES


# --- _field_to_dict -----------------------------------------------------------


def test_field_to_dict_computes_speed_from_u_v():
    d = _field_to_dict(FakeCurrentField())
    assert d["values"].shape == (2, 3, 3)
    assert d["values"][0, 0, 1] == pytest.approx(5.0)  # 3-4-5 triangle
    assert np.isnan(d["values"][0, 0, 0])              # masked -> NaN
    assert d["times"] == ["2024-01-06", "2024-01-07"]


def test_field_to_dict_no_quiver_half_implementation():
    d = _field_to_dict(FakeCurrentField())
    assert "u" not in d and "v" not in d  # only the scalar speed grid


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_oscar_branch(tmp_path):
    calls = []
    peers, seen = make_currents_peers(calls, source="oscar")
    result = run_pipeline(FakeCurrentsSpec(), peers, str(tmp_path))

    assert "fetch_oscar" in calls
    assert seen["bbox"] == (-81.0, 25.0, -55.0, 43.0)
    assert seen["start"] == "2024-01-01"
    assert seen["end"] == "2024-01-31"
    assert seen["stride_days"] == pipeline.DEFAULT_STRIDE_DAYS
    # no lake series for currents
    assert seen["render_series"] is None
    # scalar speed reached the renderer
    assert "values" in seen["render_field_keys"]
    # provenance + result record the oscar source
    assert result.source == "oscar"
    assert result.provenance["source"] == "oscar"
    assert (result.provenance["fetch"]["oscar"]["combined_sha256"]
            == "oscarfakesha")
    assert result.n_frames == 2


def test_run_pipeline_cmems_currents_branch(tmp_path):
    calls = []
    peers, seen = make_currents_peers(calls, source="cmems-currents")
    result = run_pipeline(FakeCurrentsSpec(), peers, str(tmp_path))

    assert "fetch_cmems_currents" in calls
    assert result.source == "cmems-currents"
    assert "cmems-currents" in result.provenance["fetch"]


def test_run_pipeline_currents_missing_adapter(tmp_path):
    calls = []
    peers, _ = make_currents_peers(calls, with_fetch=False)
    with pytest.raises(UnfetchableRegionError, match="0.5.0"):
        run_pipeline(FakeCurrentsSpec(), peers, str(tmp_path))


def test_run_pipeline_currents_fetch_failure_wrapped(tmp_path):
    calls = []
    peers, _ = make_currents_peers(calls, fail_at="fetch_oscar")
    with pytest.raises(RuntimeError, match="Currents fetch failed"):
        run_pipeline(FakeCurrentsSpec(), peers, str(tmp_path))


def test_run_pipeline_great_lakes_currents_refused(tmp_path):
    calls = []
    peers, _ = make_currents_peers(calls)
    with pytest.raises(UnfetchableRegionError):
        run_pipeline(FakeCurrentsSpec(region_key="lake-superior"), peers,
                     str(tmp_path))


def test_plan_fetch_great_lakes_currents_refusal_is_unfetchable():
    calls = []
    peers, _ = make_currents_peers(calls)
    with pytest.raises(UnfetchableRegionError, match="[Ll]ake"):
        run_pipeline(FakeCurrentsSpec(region_key="lake-superior"), peers,
                     "/tmp/nope")


# --- wire_peers ---------------------------------------------------------------


def test_wire_peers_exposes_currents_fetchers_lazily(monkeypatch):
    import importlib
    from studio import peers as peers_mod

    real_import = importlib.import_module

    def fake_import(name, *a, **k):
        if name == "currents.currents_global":
            mod = types.SimpleNamespace(
                fetch_oscar=lambda *a, **k: "oscar",
                fetch_cmems_currents=lambda *a, **k: "cmems")
            return mod
        if name in ("currents.glsea",):
            return types.SimpleNamespace(
                fetch_glsea_sst=lambda *a, **k: None,
                fetch_glsea_lake_averages=lambda *a, **k: None,
                GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
                GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0)
        if name in ("currents.sst_global", "currents.era5"):
            raise ImportError(f"No module named {name!r} (simulated old peer)")
        if name == "viz.sources":
            raise ImportError("No module named 'viz.sources' (simulated)")
        return real_import(name, *a, **k)

    statuses = {
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
    monkeypatch.setattr(importlib, "import_module", fake_import)
    # currents.glsea / currents.sst_global / currents.era5 go through the
    # same fake: give them harmless stand-ins.
    ns = peers_mod.wire_peers(statuses)
    assert callable(ns.fetch_oscar)
    assert callable(ns.fetch_cmems_currents)
    assert ns.fetch_oscar() == "oscar"
    assert ns.fetch_cmems_currents() == "cmems"


def test_wire_peers_currents_missing_on_old_survey_currents(monkeypatch):
    import importlib
    from studio import peers as peers_mod

    real_import = importlib.import_module

    def fake_import(name, *a, **k):
        if name == "currents.currents_global":
            raise ImportError("No module named 'currents.currents_global'")
        if name in ("currents.glsea",):
            return types.SimpleNamespace(
                fetch_glsea_sst=lambda *a, **k: None,
                fetch_glsea_lake_averages=lambda *a, **k: None,
                GLSEA_LON_MIN=-93.0, GLSEA_LAT_MIN=41.0,
                GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0)
        if name in ("currents.sst_global", "currents.era5"):
            raise ImportError(f"No module named {name!r} (simulated old peer)")
        if name == "viz.sources":
            raise ImportError("No module named 'viz.sources' (simulated)")
        return real_import(name, *a, **k)

    statuses = {
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
    monkeypatch.setattr(importlib, "import_module", fake_import)
    ns = peers_mod.wire_peers(statuses)
    # old survey-currents (< 0.5.0): fetchers degrade to None; the pipeline
    # raises the honest upgrade message at fetch time.
    assert ns.fetch_oscar is None
    assert ns.fetch_cmems_currents is None
