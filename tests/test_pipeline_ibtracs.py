"""IBTrACS storm-tracks flow-through (no network, no peers needed).

Covers: plan_fetch routing for source "ibtracs" (fetchable in any
region, variable "storm-tracks" only — other variables are
bad_variable), the run_pipeline ibtracs branch
(fetch_ibtracs(bbox, start, end, storm_name=...), series=None,
provenance under fetch["ibtracs"]), the StormField going to
render_viz as its to_dict() form (never through _field_to_dict),
storm_rank="strongest" + storm_top_n ranking via
rank_by_intensity(), the ERA5 context attachment ("with the wind
field" -> overlay_grids dicts; failed/unavailable context degrades
gracefully), the missing-adapter upgrade message
(survey-currents>=0.11.0), fetch-failure wrapping, the
SUPPORTED_VARIABLES/SOURCE_LABELS registry, and wire_peers exposing
fetch_ibtracs.
"""

from __future__ import annotations

import datetime as dt
import os
import sys
import types
from dataclasses import dataclass, field as dc_field

import numpy as np
import pytest

from studio import peers, pipeline
from studio.pipeline import (
    UnfetchableRegionError,
    _fetch_storm_context,
    plan_fetch,
    run_pipeline,
)


# --- fakes --------------------------------------------------------------------


@dataclass
class FakeStormTrack:
    name: str
    max_wind_kt: float


@dataclass
class FakeStormField:
    """StormField-shaped: dataclass + rank_by_intensity + to_dict."""

    tracks: list = dc_field(default_factory=list)
    provenance: dict = dc_field(default_factory=dict)

    def rank_by_intensity(self):
        return sorted(self.tracks, key=lambda t: -t.max_wind_kt)

    def to_dict(self):
        return {
            "storm_tracks": [
                {"name": t.name, "winds": [t.max_wind_kt]}
                for t in self.tracks
            ],
            "bbox": [-100.0, 10.0, -60.0, 40.0],
        }


def _three_tracks():
    return FakeStormField(
        tracks=[
            FakeStormTrack("ALPHA", 45.0),
            FakeStormTrack("BRAVO", 120.0),
            FakeStormTrack("CHARLIE", 75.0),
        ],
        provenance={"source": "IBTrACS v04r01", "url": "https://example/ibtracs.nc"},
    )


class FakeStormSpec:
    def __init__(self, variable="storm-tracks", region_key="gulf-of-mexico",
                 storm_name="", storm_rank="", storm_top_n=None,
                 overlays=()):
        self.title = "Gulf of Mexico — Storm tracks"
        self.region_key = region_key
        self.bbox = (-97.0, 18.0, -82.0, 31.0)
        self.variable = variable
        self.start = dt.date(2024, 8, 1)
        self.end = dt.date(2024, 8, 10)
        self.storm_name = storm_name
        self.storm_rank = storm_rank
        self.storm_top_n = storm_top_n
        self.overlays = overlays

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start.isoformat(), "end": self.end.isoformat(),
                "storm_name": self.storm_name, "storm_rank": self.storm_rank,
                "storm_top_n": self.storm_top_n,
                "overlays": list(self.overlays)}


class FakeAtmo:
    """AtmoField-shaped for ERA5 context: base wind + msl overlay."""

    base_variable = "wind"

    def __init__(self):
        self.times = ["2024-08-01T12:00:00+00:00",
                      "2024-08-02T12:00:00+00:00"]
        self.lats = np.array([18.0, 24.0, 31.0])
        self.lons = np.array([-97.0, -90.0, -82.0])
        self.values = np.full((2, 3, 3), 12.0)          # wind speed (base)
        self.overlay_grids = {"msl": np.full((2, 3, 3), 1010.0)}


def make_storm_peers(calls, fail_at=None, with_fetch=True,
                     with_era5=True, era5_fails=False):
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
        return False  # region check must NOT gate ibtracs (source routing does)

    @_guard("resolve_source")
    def resolve_source(spec):
        return "ibtracs" if spec.variable == "storm-tracks" else ""

    ns = {"is_fetchable": is_fetchable, "resolve_source": resolve_source}

    if with_fetch:
        @_guard("fetch_ibtracs")
        def fetch_ibtracs(bbox, start, end, storm_name=None, **kwargs):
            seen["bbox"] = tuple(bbox)
            seen["storm_name"] = storm_name
            seen["start"], seen["end"] = start, end
            return _three_tracks()
        ns["fetch_ibtracs"] = fetch_ibtracs

    if with_era5:
        @_guard("fetch_era5")
        def fetch_era5(variables, bbox, start, end, stride_hours=24):
            if era5_fails:
                raise ConnectionError("simulated CDS failure")
            seen["era5_variables"] = list(variables)
            return FakeAtmo()
        ns["fetch_era5"] = fetch_era5

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field"] = field
        seen["render_series"] = series
        # The storm renderer contract: a dict with the "storm_tracks" key,
        # NOT a scalar-grid dict (no "values"/"times" top-level keys).
        assert isinstance(field, dict)
        assert "storm_tracks" in field
        assert "values" not in field
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


def test_plan_fetch_ibtracs_fetchable_any_region():
    plan = plan_fetch(FakeStormSpec(), lambda key: False, lambda s: "ibtracs")
    assert plan.fetchable is True
    assert plan.source == "ibtracs"
    assert "IBTrACS" in plan.reason


def test_plan_fetch_ibtracs_bad_variable():
    plan = plan_fetch(FakeStormSpec(variable="sst"),
                      lambda key: False, lambda s: "ibtracs")
    assert plan.fetchable is False
    assert plan.kind == "bad_variable"
    assert "storm-tracks" in plan.reason


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_ibtracs_fetch_and_render(tmp_path):
    calls = []
    peers_ns, seen = make_storm_peers(calls)
    spec = FakeStormSpec(storm_name="katrina")
    out = run_pipeline(spec, peers_ns, out_dir=str(tmp_path / "reel"))
    assert "fetch_ibtracs" in calls
    assert seen["storm_name"] == "katrina"
    assert seen["bbox"] == (-97.0, 18.0, -82.0, 31.0)
    assert seen["start"] == dt.date(2024, 8, 1)
    assert seen["render_series"] is None
    # render_viz got the track dict (storm_tracks), not a scalar grid.
    assert [t["name"] for t in seen["render_field"]["storm_tracks"]] == \
        ["ALPHA", "BRAVO", "CHARLIE"]
    # provenance rides on the StormField object, not the render dict.
    assert "ibtracs" in out.provenance["fetch"]
    assert out.provenance["fetch"]["ibtracs"]["source"] == "IBTrACS v04r01"
    assert out.source == "ibtracs"


def test_run_pipeline_ibtracs_strongest_ranking(tmp_path):
    calls = []
    peers_ns, seen = make_storm_peers(calls)
    spec = FakeStormSpec(storm_rank="strongest", storm_top_n=2)
    run_pipeline(spec, peers_ns, out_dir=str(tmp_path / "reel"))
    # rank_by_intensity order (descending max wind), top 2 kept.
    assert [t["name"] for t in seen["render_field"]["storm_tracks"]] == \
        ["BRAVO", "CHARLIE"]


def test_run_pipeline_ibtracs_strongest_default_top_n(tmp_path):
    calls = []
    peers_ns, seen = make_storm_peers(calls)
    spec = FakeStormSpec(storm_rank="strongest")  # top_n None -> default 5
    run_pipeline(spec, peers_ns, out_dir=str(tmp_path / "reel"))
    assert [t["name"] for t in seen["render_field"]["storm_tracks"]] == \
        ["BRAVO", "CHARLIE", "ALPHA"]


def test_run_pipeline_ibtracs_no_ranking_keeps_all(tmp_path):
    calls = []
    peers_ns, seen = make_storm_peers(calls)
    run_pipeline(FakeStormSpec(), peers_ns, out_dir=str(tmp_path / "reel"))
    assert len(seen["render_field"]["storm_tracks"]) == 3


def test_run_pipeline_ibtracs_missing_adapter():
    calls = []
    peers_ns, _ = make_storm_peers(calls, with_fetch=False)
    with pytest.raises(UnfetchableRegionError, match="0.11.0"):
        run_pipeline(FakeStormSpec(), peers_ns, out_dir="/tmp/x-ibtracs-nope")


def test_run_pipeline_ibtracs_fetch_failure_wrapped(tmp_path):
    calls = []
    peers_ns, _ = make_storm_peers(calls, fail_at="fetch_ibtracs")
    with pytest.raises(RuntimeError, match="IBTrACS fetch failed"):
        run_pipeline(FakeStormSpec(), peers_ns,
                     out_dir=str(tmp_path / "reel"))


# --- ERA5 context ---------------------------------------------------------------


def test_fetch_storm_context_wind_base():
    peers_ns, seen = make_storm_peers([])
    ctx = _fetch_storm_context(peers_ns, ["wind"], FakeStormSpec(),
                               stride_hours=24)
    assert set(ctx) == {"wind"}
    entry = ctx["wind"]
    assert entry["times"] == ["2024-08-01", "2024-08-02"]
    assert entry["grid"].shape == (2, 3, 3)
    assert seen["era5_variables"] == ["wind"]


def test_fetch_storm_context_msl_overlay():
    peers_ns, _ = make_storm_peers([])
    ctx = _fetch_storm_context(peers_ns, ["msl"], FakeStormSpec(),
                               stride_hours=24)
    assert set(ctx) == {"msl"}
    assert float(ctx["msl"]["grid"][0, 0, 0]) == 1010.0


def test_fetch_storm_context_graceful_on_failure():
    peers_ns, _ = make_storm_peers([], era5_fails=True)
    assert _fetch_storm_context(peers_ns, ["wind"], FakeStormSpec(),
                                stride_hours=24) == {}


def test_fetch_storm_context_graceful_without_peer():
    peers_ns, _ = make_storm_peers([], with_era5=False)
    assert _fetch_storm_context(peers_ns, ["wind"], FakeStormSpec(),
                                stride_hours=24) == {}


def test_run_pipeline_ibtracs_with_wind_context(tmp_path):
    calls = []
    peers_ns, seen = make_storm_peers(calls)
    spec = FakeStormSpec(overlays=("wind",))
    run_pipeline(spec, peers_ns, out_dir=str(tmp_path / "reel"))
    assert "fetch_era5" in calls
    field = seen["render_field"]
    assert "overlay_grids" in field
    assert set(field["overlay_grids"]) == {"wind"}
    assert field["overlay_grids"]["wind"]["grid"].shape == (2, 3, 3)


def test_run_pipeline_ibtracs_context_failure_still_renders(tmp_path):
    calls = []
    peers_ns, seen = make_storm_peers(calls, era5_fails=True)
    spec = FakeStormSpec(overlays=("wind",))
    run_pipeline(spec, peers_ns, out_dir=str(tmp_path / "reel"))
    # Graceful: no overlay_grids attached, tracks still rendered.
    assert "overlay_grids" not in seen["render_field"]
    assert len(seen["render_field"]["storm_tracks"]) == 3


# --- registry + wiring ----------------------------------------------------------


def test_supported_variables_and_source_labels():
    assert "storm-tracks" in pipeline.SUPPORTED_VARIABLES
    assert pipeline.SOURCE_LABELS["ibtracs"] == "NOAA IBTrACS v04r01"


def _stub_module(name, **attrs):
    mod = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


def test_wire_peers_exposes_fetch_ibtracs(monkeypatch):
    viz = _stub_module(
        "viz",
        parse_description=lambda *a, **k: None,
        UnparseableDescription=type("UnparseableDescription", (Exception,), {}),
        VizSpec=type("VizSpec", (), {}),
        is_fetchable=lambda key: False,
        get_region=lambda key: None,
        render_viz=lambda *a, **k: ([], ""),
    )
    glsea = _stub_module(
        "currents.glsea",
        fetch_glsea_sst=lambda *a, **k: None,
        fetch_glsea_lake_averages=lambda *a, **k: None,
        GLSEA_LON_MIN=-92.0, GLSEA_LAT_MIN=41.0,
        GLSEA_LON_MAX=-76.0, GLSEA_LAT_MAX=49.0,
    )
    currents = _stub_module("currents", glsea=glsea)
    storms = _stub_module("currents.storms",
                          fetch_ibtracs=lambda *a, **k: None)
    animate = _stub_module("animate", render_video=lambda *a, **k: None)
    for name, mod in (("viz", viz), ("currents", currents),
                      ("currents.glsea", glsea),
                      ("currents.storms", storms),
                      ("animate", animate)):
        monkeypatch.setitem(sys.modules, name, mod)
    statuses = peers.load_peers()
    assert statuses["survey-currents"].installed is True
    ns = peers.wire_peers(statuses)
    assert callable(ns.fetch_ibtracs)
    assert ns.fetch_ibtracs is storms.fetch_ibtracs
