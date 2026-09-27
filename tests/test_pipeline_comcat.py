"""ComCat earthquake flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "comcat" (fetchable in any
region, variable "earthquakes" only — other variables are
bad_variable), the run_pipeline comcat branch
(fetch_earthquakes(bbox, start, end) positionally, the QuakeField
dict passed straight to render_viz under the "events" key,
series=None, provenance under fetch["comcat"]), the missing-adapter
upgrade message (survey-currents>=0.15.0), fetch-failure wrapping,
the SOURCE_LABELS registry, and wire_peers exposing
fetch_earthquakes.
"""

from __future__ import annotations

import datetime as dt
import os
import types

import pytest

from studio import peers, pipeline
from studio.pipeline import (
    UnfetchableRegionError,
    plan_fetch,
    run_pipeline,
)


# --- fakes --------------------------------------------------------------------


class FakeQuakeField:
    """QuakeField-shaped: to_dict + provenance."""

    def __init__(self):
        self.provenance = {
            "source": "usgs",
            "product": "USGS Earthquake Catalog (ComCat), FDSN event service",
            "n_events": 2,
        }

    def to_dict(self):
        return {
            "bbox": [-125.0, 32.0, -114.0, 42.0],
            "start": "2024-01-01",
            "end": "2024-01-31",
            "min_magnitude": 0.0,
            "event_type": None,
            "source": "usgs",
            "events": [
                {"event_id": "us1", "time": "2024-01-05T12:00:00+00:00",
                 "lat": 35.0, "lon": -120.0, "depth_km": 10.0,
                 "magnitude": 5.2, "mag_type": "mww",
                 "place": "10 km W of Testville",
                 "event_type": "earthquake"},
                {"event_id": "us2", "time": "2024-01-06T03:00:00+00:00",
                 "lat": 36.0, "lon": -121.0, "depth_km": None,
                 "magnitude": None, "mag_type": None,
                 "place": "5 km E of Faketon",
                 "event_type": "quarry blast"},
            ],
            "provenance": dict(self.provenance),
        }


class FakeQuakeSpec:
    def __init__(self, variable="earthquakes",
                 region_key="california"):
        self.title = "t"
        self.region_key = region_key
        self.bbox = (-125.0, 32.0, -114.0, 42.0)
        self.variable = variable
        self.start = dt.date(2024, 1, 1)
        self.end = dt.date(2024, 1, 31)
        self.cadence = "daily"
        self.source = "comcat"
        self.source_reason = "test pin"
        self.context = ()
        self.overlays = ()

    def to_dict(self):
        return {"variable": self.variable}


def make_quake_peers(calls, seen, fail_at=None, with_fetch=True):
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
        return False  # region check must NOT gate comcat

    @_guard("resolve_source")
    def resolve_source(spec):
        return "comcat" if spec.variable == "earthquakes" else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_earthquakes")
        def fetch_earthquakes(bbox, start, end, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["start"], seen["end"] = start, end
            seen["kwargs"] = dict(kwargs)
            return FakeQuakeField()
        ns["fetch_earthquakes"] = fetch_earthquakes

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field"] = field
        seen["render_series"] = series
        assert isinstance(field, dict)
        assert "events" in field
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


def test_plan_fetch_comcat_fetchable_any_region():
    plan = plan_fetch(
        FakeQuakeSpec(), lambda key: False,
        lambda spec: "comcat" if spec.variable == "earthquakes" else "")
    assert plan.fetchable is True
    assert plan.source == "comcat"
    assert "observed" in plan.reason


def test_plan_fetch_comcat_bad_variable():
    spec = FakeQuakeSpec(variable="sst")
    plan = plan_fetch(spec, lambda key: False, lambda s: "comcat")
    assert plan.fetchable is False
    assert plan.kind == "bad_variable"
    assert "earthquakes" in plan.reason


def test_source_labels_registry():
    assert pipeline.SOURCE_LABELS["comcat"] == \
        "USGS Earthquake Catalog (ComCat)"


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_comcat_branch(tmp_path):
    calls, seen = [], {}
    peers_ns = make_quake_peers(calls, seen)
    result = run_pipeline(FakeQuakeSpec(), peers_ns, str(tmp_path))
    assert "fetch_earthquakes" in calls
    assert seen["bbox"] == (-125.0, 32.0, -114.0, 42.0)
    assert seen["start"] == dt.date(2024, 1, 1)
    assert seen["end"] == dt.date(2024, 1, 31)
    field = seen["render_field"]
    assert len(field["events"]) == 2
    assert seen["render_series"] is None
    assert result.source == "comcat"
    prov = result.provenance["fetch"]
    assert prov["comcat"]["n_events"] == 2
    assert result.n_frames == 1


def test_run_pipeline_comcat_missing_peer(tmp_path):
    calls, seen = [], {}
    peers_ns = make_quake_peers(calls, seen, with_fetch=False)
    with pytest.raises(UnfetchableRegionError,
                       match="survey-currents>=0.15.0"):
        run_pipeline(FakeQuakeSpec(), peers_ns, str(tmp_path))


def test_run_pipeline_comcat_fetch_failure_wrapped(tmp_path):
    calls, seen = [], {}
    peers_ns = make_quake_peers(calls, seen, fail_at="fetch_earthquakes")
    with pytest.raises(RuntimeError, match="ComCat earthquake fetch failed"):
        run_pipeline(FakeQuakeSpec(), peers_ns, str(tmp_path))


def test_wire_peers_exposes_fetch_earthquakes(monkeypatch):
    # wire_peers must tolerate a survey-currents without the
    # earthquakes submodule (older peers simply lack the adapter).
    fake_viz = types.SimpleNamespace(
        parse_description=lambda *a, **k: None,
        UnparseableDescription=Exception,
        VizSpec=object,
        is_fetchable=lambda *a, **k: False,
        get_region=lambda *a, **k: None,
        render_viz=lambda *a, **k: None)
    fake_animate = types.SimpleNamespace(
        render_video=lambda *a, **k: None)
    statuses = {
        "survey-viz": types.SimpleNamespace(module=fake_viz),
        "survey-currents": types.SimpleNamespace(module=object()),
        "survey-animate": types.SimpleNamespace(module=fake_animate),
    }
    real_import = peers.importlib.import_module

    def fake_import(name):
        if name == "currents.earthquakes":
            raise ImportError("no earthquakes module")
        if name == "currents.glsea":
            return types.SimpleNamespace(
                fetch_glsea_sst=lambda *a, **k: None,
                fetch_glsea_lake_averages=lambda *a, **k: None,
                GLSEA_LON_MIN=-95.0, GLSEA_LAT_MIN=41.0,
                GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0)
        if name.startswith("currents."):
            raise ImportError(f"no {name}")
        if name == "viz.sources":
            raise ImportError("no viz.sources")
        return real_import(name)

    monkeypatch.setattr(peers.importlib, "import_module", fake_import)
    ns = peers.wire_peers(statuses)
    assert ns.fetch_earthquakes is None
