"""Narrative story facts attached to RunResult (survey-narrate peer).

Covers: run_pipeline attaches result.story with status "ok", real facts
and a non-empty caption when the survey-narrate peer is importable; when
the peer is missing the render still succeeds and story reports
"unavailable". No network; the fetch and renderer are stubbed.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np
import pytest

from studio.pipeline import run_pipeline


class FakeWindField:
    """narrate-compatible wind field: grids u10/v10 + air_temperature."""

    def __init__(self, nt=2, ny=4, nx=5):
        rng = np.random.default_rng(11)
        self.grids = {
            "u10": rng.normal(8, 2, (nt, ny, nx)),
            "v10": rng.normal(1, 2, (nt, ny, nx)),
        }
        self.air_temperature = rng.normal(60, 5, (nt, ny, nx))
        self.temperature_unit = "°F"
        self.times = [f"2026-09-{29 + i:02d}T00:00:00+00:00"
                      for i in range(nt)]
        self.lats = np.linspace(25.0, 50.0, ny)
        self.lons = np.linspace(-130.0, -65.0, nx)
        self.provenance = {"source": "test", "sha256": "x"}

    def to_dict(self):
        return {
            "grids": {k: v.tolist() for k, v in self.grids.items()},
            "air_temperature": self.air_temperature.tolist(),
            "temperature_unit": self.temperature_unit,
            "times": list(self.times),
            "lats": self.lats.tolist(),
            "lons": self.lons.tolist(),
            "provenance": dict(self.provenance),
        }


class FakeSpec:
    def __init__(self):
        self.title = "Winds"
        self.region_key = "north-america"
        self.bbox = (-130.0, 25.0, -65.0, 50.0)
        self.variable = "wind"
        self.source = "gfs-wind"
        self.overlays = ()
        self.start = "2026-09-29"
        self.end = "2026-09-30"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "source": self.source, "start": self.start,
                "end": self.end}


def make_peers():
    def is_fetchable(key):
        return False

    def resolve_source(spec):
        return "gfs-wind"

    def fetch_gfs_wind(bbox, start, end, stride_days=1, **kw):
        return FakeWindField()

    def render_viz(spec, field, series, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "frame_0001.png")
        with open(p, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        manifest = os.path.join(out_dir, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{}")
        return [p], manifest

    def render_video(source, out_path, preset="reel", **kwargs):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".prov",
                                     fps=30, n_frames=1)

    ns = {
        "is_fetchable": is_fetchable,
        "resolve_source": resolve_source,
        "fetch_gfs_wind": fetch_gfs_wind,
        "render_viz": render_viz,
        "render_video": render_video,
        "glsea_bounds": (-180.0, -90.0, 180.0, 90.0),
    }
    return types.SimpleNamespace(**ns)


def test_run_pipeline_attaches_story(tmp_path):
    pytest.importorskip("narrate")
    result = run_pipeline(FakeSpec(), make_peers(), str(tmp_path))
    story = result.story
    assert story["status"] == "ok"
    assert isinstance(story["caption"], str) and story["caption"].strip()
    facts = story["facts"]
    assert facts["mean_speed_m_s"] > 0
    assert facts["peak"]["value_m_s"] >= facts["mean_speed_m_s"]
    # provenance manifest carries the story too
    assert result.provenance["story"]["status"] == "ok"


def test_run_pipeline_story_unavailable_without_peer(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "narrate", None)
    result = run_pipeline(FakeSpec(), make_peers(), str(tmp_path))
    assert result.story["status"] == "unavailable"
    # the render itself still succeeded
    assert result.n_frames == 1
    assert os.path.exists(result.video_path)


def test_run_pipeline_region_name_overrides_region_key(tmp_path):
    """region_name kwarg replaces the slug in narrative facts/captions."""
    pytest.importorskip("narrate")
    result = run_pipeline(FakeSpec(), make_peers(), str(tmp_path),
                          region_name="North America")
    story = result.story
    assert story["status"] == "ok"
    assert story["facts"]["region_name"] == "North America"
    assert "North America" in story["caption"]
    assert "north-america" not in story["caption"]
