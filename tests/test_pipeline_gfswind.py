"""GFS 10-m wind source routing (no network, no peers needed).

Covers: plan_fetch routing for source "gfs-wind" (fetchable in any
region, wind-only), the run_pipeline gfs-wind branch (bbox/start/end/
stride_days call shape, the field's to_dict() form reaching render_viz
with grids + air_temperature for the dark_strands wind path), the
missing-adapter refusal, fetch-failure wrapping with the NOMADS hint,
derived-product ineligibility (10-day retention), and a REAL
survey-viz dark_strands render of a synthetic GfsWindField proving the
viz wind path accepts the returned field.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np
import pytest

from studio import pipeline
from studio.pipeline import (
    UnfetchableRegionError,
    UnsupportedVariableError,
    plan_fetch,
    run_pipeline,
)


# --- fakes --------------------------------------------------------------------


class FakeGfsField:
    """GfsWindField-shaped: to_dict() with grids + air_temperature."""

    def __init__(self, nt=2, ny=4, nx=5):
        rng = np.random.default_rng(7)
        self._u = rng.normal(5, 2, (nt, ny, nx))
        self._v = rng.normal(0, 2, (nt, ny, nx))
        self._t2m = rng.normal(20, 3, (nt, ny, nx))  # degC
        self.times = [f"2026-09-{29 + i:02d}T00:00:00+00:00" for i in range(nt)]
        self.lats = np.linspace(25.0, 50.0, ny)
        self.lons = np.linspace(-130.0, -65.0, nx)
        self.provenance = {
            "source": "nomads",
            "dataset": "NOAA GFS 0.25-degree analysis (f000)",
            "sha256": "gfsfakesha",
            "n_requests": nt,
        }

    def to_dict(self):
        return {
            "grids": {
                "u10": self._u.tolist(),
                "v10": self._v.tolist(),
                "t2m": self._t2m.tolist(),
            },
            "air_temperature": (self._t2m * 9.0 / 5.0 + 32.0).tolist(),
            "temperature_unit": "°F",
            "values": np.hypot(self._u, self._v).tolist(),
            "times": list(self.times),
            "lats": self.lats.tolist(),
            "lons": self.lons.tolist(),
            "cycle": "00",
            "provenance": dict(self.provenance),
        }


class FakeGfsSpec:
    def __init__(self, variable="wind", source="gfs-wind"):
        self.title = "North America — 10-m Wind, 2026"
        self.region_key = "north-america"
        self.bbox = (-130.0, 25.0, -65.0, 50.0)
        self.variable = variable
        self.source = source
        self.overlays = ()
        self.start = "2026-09-29"
        self.end = "2026-09-30"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "source": self.source, "start": self.start,
                "end": self.end}


def _resolve_gfs(spec):
    return "gfs-wind"


def make_gfs_peers(calls, fail_at=None, with_fetch=True):
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
        return False  # region check must NOT gate gfs-wind (global grid)

    @_guard("resolve_source")
    def resolve_source(spec):
        return _resolve_gfs(spec)

    if with_fetch:
        @_guard("fetch_gfs_wind")
        def fetch_gfs_wind(bbox, start, end, stride_days=1):
            seen["bbox"] = tuple(bbox)
            seen["start"], seen["end"] = start, end
            seen["stride_days"] = stride_days
            return FakeGfsField()

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        seen["render_field_keys"] = sorted(field.keys())
        seen["render_series"] = series
        seen["has_air_temperature"] = "air_temperature" in field
        seen["has_u10"] = "u10" in field.get("grids", {})
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
        # derive stub: present so derived requests reach the
        # source-ineligibility check (gfs-wind is ineligible).
        "derive": types.SimpleNamespace(),
    }
    if with_fetch:
        ns["fetch_gfs_wind"] = fetch_gfs_wind
    try:  # real VizSpec for the derived_note capability check
        import viz as _viz
        ns["VizSpec"] = _viz.VizSpec
    except ImportError:
        pass
    return types.SimpleNamespace(**ns), seen


# --- plan_fetch ---------------------------------------------------------------


def test_plan_fetch_gfs_wind_fetchable_any_region():
    plan = plan_fetch(FakeGfsSpec(), lambda key: False, _resolve_gfs)
    assert plan.fetchable
    assert plan.source == "gfs-wind"
    assert plan.variable == "wind"
    assert plan.kind == "ok"
    assert "GFS" in plan.reason


def test_plan_fetch_gfs_wind_wrong_variable_refused():
    plan = plan_fetch(FakeGfsSpec(variable="sst"), lambda key: True,
                      _resolve_gfs)
    assert not plan.fetchable
    assert plan.kind == "bad_variable"
    assert "gfs-wind" in plan.reason


def test_plan_fetch_gfs_wind_label_known():
    assert (pipeline.SOURCE_LABELS["gfs-wind"]
            == "NOAA GFS 10m winds (NOMADS), keyless")


# --- run_pipeline -------------------------------------------------------------


def test_run_pipeline_gfs_wind_branch(tmp_path):
    calls = []
    peers, seen = make_gfs_peers(calls)
    result = run_pipeline(FakeGfsSpec(), peers, str(tmp_path))

    assert "fetch_gfs_wind" in calls
    # fetch(bbox, start, end, stride_days=...) call shape
    assert seen["bbox"] == (-130.0, 25.0, -65.0, 50.0)
    assert seen["start"] == "2026-09-29"
    assert seen["end"] == "2026-09-30"
    assert seen["stride_days"] == 30  # DEFAULT_STRIDE_DAYS
    # the to_dict() form reaches the renderer with the wind-path keys
    assert seen["has_air_temperature"]
    assert seen["has_u10"]
    assert seen["render_series"] is None
    # provenance + result record the gfs-wind source
    assert result.source == "gfs-wind"
    assert result.provenance["source"] == "gfs-wind"
    assert result.provenance["fetch"]["gfs-wind"]["sha256"] == "gfsfakesha"
    assert result.n_frames == 2


def test_run_pipeline_gfs_wind_custom_stride(tmp_path):
    calls = []
    peers, seen = make_gfs_peers(calls)
    run_pipeline(FakeGfsSpec(), peers, str(tmp_path), stride_days=1)
    assert seen["stride_days"] == 1


def test_run_pipeline_gfs_wind_missing_adapter_refused(tmp_path):
    calls = []
    peers, _ = make_gfs_peers(calls, with_fetch=False)
    with pytest.raises(UnfetchableRegionError,
                       match=r"survey-currents>=0\.16\.0"):
        run_pipeline(FakeGfsSpec(), peers, str(tmp_path))


def test_run_pipeline_gfs_wind_fetch_failure_wrapped(tmp_path):
    calls = []
    peers, _ = make_gfs_peers(calls, fail_at="fetch_gfs_wind")
    with pytest.raises(RuntimeError, match="10 days"):
        run_pipeline(FakeGfsSpec(), peers, str(tmp_path))


def test_run_pipeline_gfs_wind_bad_variable_refused(tmp_path):
    calls = []
    peers, _ = make_gfs_peers(calls)
    with pytest.raises(UnsupportedVariableError):
        run_pipeline(FakeGfsSpec(variable="sst"), peers, str(tmp_path))


def test_run_pipeline_gfs_wind_derived_refused_honestly(tmp_path):
    calls = []
    peers, _ = make_gfs_peers(calls)
    with pytest.raises(ValueError, match="10 days"):
        run_pipeline(FakeGfsSpec(), peers, str(tmp_path),
                     derived={"product": "anomaly",
                              "baseline_start": "2000-01-01",
                              "baseline_end": "2020-12-31",
                              "baseline_stride_days": 30,
                              "window_days": 15,
                              "min_samples": 10,
                              "symmetric_quantile": 0.98})


# --- peers wiring -------------------------------------------------------------


def test_wire_peers_exposes_fetch_gfs_wind():
    from studio import peers as peers_mod
    statuses = peers_mod.load_peers()
    for repo in ("survey-viz", "survey-currents", "survey-animate"):
        if statuses[repo].module is None:
            pytest.skip(f"{repo} peer not importable in this env")
    ns = peers_mod.wire_peers(statuses)
    import currents.gfs_wind as gw
    assert ns.fetch_gfs_wind is gw.fetch_gfs_wind


# --- real dark_strands render -------------------------------------------------


def _need_flow():
    flow_src = os.path.join(os.path.dirname(os.path.dirname(
        os.path.dirname(os.path.abspath(__file__)))),
        "survey-flow", "src")
    if os.path.isdir(flow_src) and flow_src not in sys.path:
        sys.path.insert(0, flow_src)
    try:
        import flow.advect  # noqa: F401
    except ImportError:
        pytest.skip("survey-flow peer not available (dark_strands needs it)")


def test_dark_strands_renders_gfs_wind_field(tmp_path):
    """REAL survey-viz dark_strands render of a GfsWindField.to_dict().

    Proves the viz wind variable path accepts exactly what the
    gfs-wind pipeline branch hands render_viz: grids["u10"]/["v10"]
    plus air_temperature (°F) for the strand coloring.
    """
    _need_flow()
    import viz
    from currents.gfs_wind import GfsWindField

    field = GfsWindField.synthetic(
        nt=2, ny=12, nx=16, lats=(25.0, 50.0), lons=(-130.0, -65.0),
        start="2026-09-29T00:00:00+00:00", seed=11)
    render_dict = field.to_dict()
    # The pipeline branch hands render_viz this exact dict form.
    assert "air_temperature" in render_dict
    assert render_dict["temperature_unit"] == "°F"
    assert sorted(render_dict["grids"]) == ["t2m", "u10", "v10"]

    spec = viz.VizSpec(
        title="North America — 10-m Wind",
        region_key="north-america",
        bbox=(-130.0, 25.0, -65.0, 50.0),
        variable="wind",
        source="gfs-wind",
        start="2026-09-29", end="2026-09-30",
        cadence="daily")
    out_dir = str(tmp_path / "frames")
    frames, manifest = viz.render_viz(
        spec, render_dict, None, out_dir=out_dir,
        preset="dark_strands", strand_count=60, strand_linewidth=1.2)
    assert len(frames) == 2
    for f in frames:
        assert os.path.getsize(f) > 1000  # real PNG content, not a stub
    assert os.path.exists(manifest)
