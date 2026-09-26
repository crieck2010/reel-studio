"""Pipeline orchestration with FAKE peers (no network, no peers needed).

Covers: happy-path ordering, provenance collection, bbox clamping,
fetch-failure wrapping, unfetchable refusal, and the frames-dir (not
manifest-path) interop detail with survey-animate.
"""

from __future__ import annotations

import os
import types

import numpy as np
import pytest

from studio import pipeline


# --- fake peers ---------------------------------------------------------------

class FakeField:
    """GlseaField-shaped: .times/.lats/.lons + 3D .sst + .provenance."""

    def __init__(self, provenance=None):
        self.times = ["2026-06-01T12:00:00+00:00", "2026-07-01T12:00:00+00:00",
                      "2026-08-01T12:00:00+00:00"]
        self.lats = np.array([41.5, 44.0, 46.2])
        self.lons = np.array([-88.2, -87.0, -85.8])
        self.sst = np.ma.masked_array(
            np.full((3, 3, 3), 18.0), mask=False)
        self.provenance = provenance or {
            "url": "https://example.invalid/erddap/griddap/GLSEA_ACSPO_GCS.nc?x",
            "sha256": "fakesha",
        }


class FakeSeries:
    """LakeSeries-shaped: .dates/.temps + .provenance."""

    def __init__(self, provenance=None):
        self.dates = ["2026-06-01", "2026-07-01", "2026-08-01"]
        self.temps = [12.5, 18.0, 21.3]
        self.provenance = provenance or {
            "url": "https://example.invalid/erddap/tabledap/glsea_avgtemps_3.csv?x",
            "sha256": "fakesha2",
        }


class FakeSpec:
    def __init__(self):
        self.title = "Lake Michigan — Surface Water Temperature, 2026"
        self.region_key = "lake-michigan"
        self.bbox = (-88.2, 41.5, -85.8, 46.2)
        self.variable = "sst"
        self.start = "2026-06-01"
        self.end = "2026-08-31"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "start": self.start, "end": self.end}


def make_fake_peers(calls, fail_at=None, expected_lake="michigan"):
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
        return True

    seen_bboxes = []

    @_guard("fetch_sst")
    def fetch_sst(bbox, start, end, stride_days=30):
        seen_bboxes.append(tuple(bbox))
        assert stride_days == 30
        return FakeField()

    @_guard("fetch_averages")
    def fetch_averages(lake, start, end):
        assert lake == expected_lake
        return FakeSeries()

    @_guard("render_viz")
    def render_viz(spec, field, series, out_dir):
        assert isinstance(series, dict) and "values" in series  # adapted form
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

    seen_video_sources = []

    @_guard("render_video")
    def render_video(source, out_path, preset="reel", **kwargs):
        seen_video_sources.append(source)
        assert preset == "reel"
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".provenance.json",
                                     fps=30.0, n_frames=2,
                                     width=1080, height=1920)

    peers = types.SimpleNamespace(
        is_fetchable=is_fetchable,
        fetch_sst=fetch_sst,
        fetch_averages=fetch_averages,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-92.4199507342304, 38.8749871947297,
                      -75.8816402880531, 50.6059751976539),
    )
    return peers, seen_bboxes, seen_video_sources


# --- tests --------------------------------------------------------------------

def test_happy_path_order_and_result(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)
    events = []
    result = pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path),
        progress=lambda frac, msg: events.append((frac, msg)))
    assert calls == ["is_fetchable", "fetch_sst", "fetch_averages",
                     "render_viz", "render_video"]
    assert result.video_path.endswith("reel.mp4")
    assert os.path.isfile(result.video_path)
    assert result.n_frames == 2
    assert result.lake == "michigan"
    assert events[-1] == (1.0, "Done")
    assert [f for f, _ in events] == sorted(f for f, _ in events)


def test_provenance_collected(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)
    result = pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    prov = result.provenance
    assert prov["fetch"]["sst"]["sha256"] == "fakesha"
    assert prov["fetch"]["averages"]["sha256"] == "fakesha2"
    assert "erddap" in prov["fetch"]["sst"]["url"]
    assert prov["encode"]["video_sha256"]
    assert prov["spec"]["region_key"] == "lake-michigan"
    assert prov["render"]["n_frames"] == 2


def test_render_video_gets_frames_dir_not_manifest(tmp_path):
    """Interop detail: animate rejects the viz manifest schema, so the
    frames DIRECTORY must be passed (see docs/INTEROP.md)."""
    calls = []
    fake_peers, _, seen_sources = make_fake_peers(calls)
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    assert len(seen_sources) == 1
    assert os.path.isdir(seen_sources[0])
    assert seen_sources[0].endswith("frames")


def test_bbox_clamped_to_glsea_grid(tmp_path):
    """lake-superior's gazetteer bbox (-92.5) is west of the GLSEA floor;
    the pipeline clamps it instead of letting validate_glsea_bbox blow up."""
    calls = []
    fake_peers, seen_bboxes, _ = make_fake_peers(calls, expected_lake="superior")

    class SuperiorSpec(FakeSpec):
        def __init__(self):
            super().__init__()
            self.region_key = "lake-superior"
            self.bbox = (-92.5, 46.0, -84.5, 48.8)

    result = pipeline.run_pipeline(SuperiorSpec(), fake_peers, str(tmp_path))
    assert len(seen_bboxes) == 1
    assert seen_bboxes[0][0] == pytest.approx(-92.4199507342304)
    assert result.provenance["fetch"]["bbox_clamp_notes"]
    assert "clamped" in result.provenance["fetch"]["bbox_clamp_notes"][0]


def test_bbox_not_clamped_when_inside_grid(tmp_path):
    calls = []
    fake_peers, seen_bboxes, _ = make_fake_peers(calls)
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    assert seen_bboxes[0] == (-88.2, 41.5, -85.8, 46.2)


def test_fetch_failure_wrapped(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls, fail_at="fetch_sst")
    with pytest.raises(RuntimeError, match="SST fetch failed"):
        pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    assert "fetch_averages" not in calls  # stopped at the failing step


def test_averages_failure_wrapped(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls, fail_at="fetch_averages")
    with pytest.raises(RuntimeError, match="Lake-average fetch failed"):
        pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))


def test_unfetchable_region_refuses(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)

    class GulfSpec(FakeSpec):
        def __init__(self):
            super().__init__()
            self.region_key = "gulf-of-mexico"
            self.variable = "chlorophyll"

    def never_fetchable(key):
        return False

    fake_peers.is_fetchable = never_fetchable
    with pytest.raises(pipeline.UnfetchableRegionError, match="no fetch adapter"):
        pipeline.run_pipeline(GulfSpec(), fake_peers, str(tmp_path))
    assert calls == []  # planning only, via the unwrapped stub; no fetch/render/encode


def test_unsupported_variable_refuses(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)

    class CurrentsSpec(FakeSpec):
        def __init__(self):
            super().__init__()
            self.variable = "currents"

    with pytest.raises(pipeline.UnsupportedVariableError, match="currents"):
        pipeline.run_pipeline(CurrentsSpec(), fake_peers, str(tmp_path))
    assert calls == ["is_fetchable"]  # planning only; no fetch/render/encode


def test_progress_optional(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)
    result = pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                                   progress=None)
    assert result.n_frames == 2
