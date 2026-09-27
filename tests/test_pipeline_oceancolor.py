"""Ocean-color flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "oceancolor" (fetchable in any
region, variable "ocean-color" only — other variables are
bad_variable), the run_pipeline oceancolor branch
(fetch_oceancolor(bbox, start, end, cadence=..., sensor="modis-aqua"),
times normalized to date strings for render_viz, series=None,
provenance under fetch["oceancolor"]), the best-effort context
fetching for "currents" (OSCAR preferred, CMEMS fallback) and "sst"
(OISST preferred, MUR fallback) recorded under
fetch["oceancolor_context"], graceful degradation when context peers
are missing or fail, the missing-adapter upgrade message
(survey-currents>=0.14.0), fetch-failure wrapping, the SOURCE_LABELS
registry, and wire_peers exposing fetch_oceancolor.
"""

from __future__ import annotations

import datetime as dt
import os
import types

import numpy as np
import pytest

from studio import peers, pipeline
from studio.pipeline import (
    UnfetchableRegionError,
    _fetch_oceancolor_context,
    plan_fetch,
    run_pipeline,
)


# --- fakes --------------------------------------------------------------------


class FakeOceanColorField:
    """OceanColorField-shaped: to_dict + provenance, ISO-datetime times."""

    def __init__(self, n_times=3):
        self.times = [f"2024-0{m}-01T00:00:00+00:00"
                      for m in range(1, n_times + 1)]
        self.lats = np.linspace(20.0, 40.0, 6)
        self.lons = np.linspace(-80.0, -60.0, 8)
        rng = np.random.default_rng(12)
        self.values = 0.3 * 10.0 ** rng.normal(0, 0.4,
                                               (n_times, 6, 8))
        self.provenance = {
            "source": "NOAA CoastWatch ERDDAP",
            "dataset_id": "erdMH1chlamday_R2022SQ",
            "sensor": "modis-aqua",
            "cadence": "monthly",
            "units": "mg/m^3",
        }

    def to_dict(self):
        return {
            "times": list(self.times),
            "lats": [float(x) for x in self.lats],
            "lons": [float(x) for x in self.lons],
            "values": self.values.tolist(),
            "units": "mg/m^3",
            "source": "coastwatch",
            "sensor": "modis-aqua",
            "cadence": "monthly",
            "provenance": dict(self.provenance),
        }


class FakeContextField:
    def __init__(self, n_times=2):
        self.times = [dt.date(2024, m, 1) for m in range(1, n_times + 1)]
        self.provenance = {"source": "fake-context"}


class FakeOceanSpec:
    def __init__(self, variable="ocean-color", cadence="monthly",
                 context=(), region_key="north-atlantic"):
        self.title = "t"
        self.region_key = region_key
        self.bbox = (-80.0, 20.0, -60.0, 40.0)
        self.variable = variable
        self.start = dt.date(2024, 1, 1)
        self.end = dt.date(2024, 3, 1)
        self.cadence = cadence
        self.source = "oceancolor"
        self.source_reason = "test pin"
        self.context = tuple(context)
        self.overlays = ()

    def to_dict(self):
        return {"variable": self.variable, "context": list(self.context)}


def make_ocean_peers(calls, seen, fail_at=None, with_fetch=True,
                     with_oscar=True, with_oisst=True,
                     oscar_fails=False, oisst_fails=False):
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
        return False  # region check must NOT gate oceancolor

    @_guard("resolve_source")
    def resolve_source(spec):
        return "oceancolor" if spec.variable == "ocean-color" else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_oceancolor")
        def fetch_oceancolor(bbox, start, end, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["start"], seen["end"] = start, end
            seen["kwargs"] = dict(kwargs)
            return FakeOceanColorField()
        ns["fetch_oceancolor"] = fetch_oceancolor

    if with_oscar:
        @_guard("fetch_oscar")
        def fetch_oscar(bbox, start, end, stride_days=30):
            if oscar_fails:
                raise ConnectionError("simulated OSCAR failure")
            return FakeContextField()
        ns["fetch_oscar"] = fetch_oscar

    if with_oisst:
        @_guard("fetch_oisst")
        def fetch_oisst(bbox, start, end, stride_days=30):
            if oisst_fails:
                raise ConnectionError("simulated OISST failure")
            return FakeContextField()
        ns["fetch_oisst"] = fetch_oisst

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field"] = field
        seen["render_series"] = series
        assert isinstance(field, dict)
        assert "values" in field
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
    return types.SimpleNamespace(**ns)


# --- plan_fetch ---------------------------------------------------------------


def test_plan_fetch_oceancolor_fetchable_any_region():
    plan = plan_fetch(
        FakeOceanSpec(), lambda key: False,
        lambda spec: "oceancolor" if spec.variable == "ocean-color" else "")
    assert plan.fetchable is True
    assert plan.source == "oceancolor"
    assert "CoastWatch" in plan.reason


def test_plan_fetch_oceancolor_bad_variable():
    spec = FakeOceanSpec(variable="sst")
    plan = plan_fetch(spec, lambda key: False, lambda s: "oceancolor")
    assert plan.fetchable is False
    assert plan.kind == "bad_variable"
    assert "ocean-color" in plan.reason


def test_source_labels_registry():
    assert pipeline.SOURCE_LABELS["oceancolor"] == \
        "NOAA CoastWatch Ocean Color"


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_oceancolor_branch(tmp_path):
    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen)
    result = run_pipeline(FakeOceanSpec(), peers_ns, str(tmp_path))
    assert "fetch_oceancolor" in calls
    assert seen["bbox"] == (-80.0, 20.0, -60.0, 40.0)
    assert seen["kwargs"]["cadence"] == "monthly"
    assert seen["kwargs"]["sensor"] == "modis-aqua"
    # ISO-datetime times normalized to date strings for render_viz.
    field = seen["render_field"]
    assert field["times"] == ["2024-01-01", "2024-02-01", "2024-03-01"]
    assert seen["render_series"] is None
    assert result.source == "oceancolor"
    prov = result.provenance["fetch"]
    assert prov["oceancolor"]["dataset_id"] == "erdMH1chlamday_R2022SQ"
    assert prov["oceancolor_context"] == {}
    assert result.n_frames == 1


def test_run_pipeline_oceancolor_daily_cadence(tmp_path):
    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen)
    run_pipeline(FakeOceanSpec(cadence="daily"), peers_ns, str(tmp_path))
    assert seen["kwargs"]["cadence"] == "daily"
    assert seen["kwargs"]["sensor"] == "modis-aqua"


def test_run_pipeline_oceancolor_context_fetched(tmp_path):
    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen)
    result = run_pipeline(
        FakeOceanSpec(context=("currents", "sst")), peers_ns,
        str(tmp_path))
    assert "fetch_oscar" in calls      # currents: OSCAR preferred
    assert "fetch_oisst" in calls      # sst: OISST preferred
    ctx = result.provenance["fetch"]["oceancolor_context"]
    assert ctx["currents"]["fetched"] is True
    assert ctx["currents"]["source"] == "oscar"
    assert ctx["sst"]["fetched"] is True
    assert ctx["sst"]["source"] == "oisst"


def test_run_pipeline_oceancolor_context_degrades_gracefully(tmp_path):
    # Context peers missing or failing must not fail the reel.
    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen, with_oscar=False,
                                with_oisst=False)
    result = run_pipeline(
        FakeOceanSpec(context=("currents", "sst")), peers_ns,
        str(tmp_path))
    ctx = result.provenance["fetch"]["oceancolor_context"]
    assert ctx["currents"]["fetched"] is False
    assert ctx["sst"]["fetched"] is False
    assert result.n_frames == 1

    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen, oscar_fails=True,
                                oisst_fails=True)
    result = run_pipeline(
        FakeOceanSpec(context=("currents", "sst")), peers_ns,
        str(tmp_path))
    ctx = result.provenance["fetch"]["oceancolor_context"]
    assert ctx["currents"]["status"] == "absent"
    assert ctx["sst"]["status"] == "absent"
    assert result.n_frames == 1


def test_run_pipeline_oceancolor_missing_peer(tmp_path):
    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen, with_fetch=False)
    with pytest.raises(UnfetchableRegionError,
                       match="survey-currents>=0.14.0"):
        run_pipeline(FakeOceanSpec(), peers_ns, str(tmp_path))


def test_run_pipeline_oceancolor_fetch_failure_wrapped(tmp_path):
    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen, fail_at="fetch_oceancolor")
    with pytest.raises(RuntimeError, match="Ocean color fetch failed"):
        run_pipeline(FakeOceanSpec(), peers_ns, str(tmp_path))


# --- context helper -----------------------------------------------------------


def test_fetch_oceancolor_context_fallback_order():
    calls, seen = [], {}
    peers_ns = make_ocean_peers(calls, seen, with_oscar=False)
    # currents falls back to CMEMS when OSCAR is missing
    peers_ns.fetch_cmems_currents = lambda *a, **k: FakeContextField()
    out = _fetch_oceancolor_context(
        peers_ns, ["currents"], FakeOceanSpec(), 30)
    assert out["currents"]["source"] == "cmems-currents"

    # sst falls back to MUR when OISST is missing
    peers_ns2 = make_ocean_peers([], {}, with_oisst=False)
    peers_ns2.fetch_mur = lambda *a, **k: FakeContextField()
    out = _fetch_oceancolor_context(peers_ns2, ["sst"], FakeOceanSpec(),
                                    30)
    assert out["sst"]["source"] == "mur"


def test_fetch_oceancolor_context_unknown_name():
    peers_ns = make_ocean_peers([], {})
    out = _fetch_oceancolor_context(peers_ns, ["tp"], FakeOceanSpec(), 30)
    assert out["tp"]["status"] == "absent"


# --- wire_peers ---------------------------------------------------------------


def test_wire_peers_exposes_fetch_oceancolor():
    # Fake the three peers; the currents stand-in exposes the
    # oceancolor submodule so the new import block runs for real.
    class _AnyModule(types.ModuleType):
        def __getattr__(self, name):
            return lambda *a, **k: None

    fake_viz = _AnyModule("viz")
    fake_animate = _AnyModule("animate")
    fake_oceancolor = types.ModuleType("currents.oceancolor")

    def _fetch_oceancolor(*a, **k):
        raise AssertionError("should not be called")

    fake_oceancolor.fetch_oceancolor = _fetch_oceancolor
    fake_currents = types.ModuleType("currents")
    fake_currents.oceancolor = fake_oceancolor

    def fake_import(name, *a, **k):
        if name == "currents.oceancolor":
            return fake_oceancolor
        if name == "currents":
            return fake_currents
        if name.startswith("currents."):
            return _AnyModule(name)
        raise ImportError(f"No module named {name!r} (test double)")

    import importlib
    import unittest.mock as mock
    statuses = {
        repo: peers.PeerStatus(repo=repo, module_name=m, module=mod,
                               pip_command="p",
                               needed_for=peers.PEER_SPECS[repo]["needed_for"])
        for repo, m, mod in (("survey-viz", "viz", fake_viz),
                             ("survey-currents", "currents", fake_currents),
                             ("survey-animate", "animate", fake_animate))
    }
    with mock.patch.object(importlib, "import_module", fake_import):
        ns = peers.wire_peers(statuses)
    assert ns.fetch_oceancolor is _fetch_oceancolor
    assert "fetch_oceancolor" in statuses["survey-currents"].needed_for
