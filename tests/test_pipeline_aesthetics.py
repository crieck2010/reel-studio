"""Aesthetic preset plumbing in run_pipeline (fake peers).

Covers the v0.15.0 capability checks: signature inspection decides
whether the survey-viz peer supports preset=/rotation=/watermark=,
PeerTooOldError carries the upgrade command, and the new options ride
in render_viz_kwargs (which the frame-batch fingerprint already
covers). No network, no real peers.
"""

from __future__ import annotations

import os
import types

import numpy as np
import pytest

from studio import pipeline


class FakeField:
    def __init__(self):
        self.times = ["2026-06-01T12:00:00+00:00"]
        self.lats = np.array([41.5, 46.2])
        self.lons = np.array([-88.2, -85.8])
        self.sst = np.ma.masked_array(np.full((1, 2, 2), 18.0), mask=False)
        self.provenance = {}


class FakeSeries:
    def __init__(self):
        self.dates = ["2026-06-01"]
        self.temps = [18.0]
        self.provenance = {}


class FakeSpec:
    def __init__(self):
        self.title = "t"
        self.region_key = "lake-michigan"
        self.bbox = (-88.2, 41.5, -85.8, 46.2)
        self.variable = "sst"
        self.start = "2026-06-01"
        self.end = "2026-06-30"

    def to_dict(self):
        return {"title": self.title}


def _write_frames(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "frame_0001.png")
    with open(p, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    manifest = os.path.join(out_dir, "manifest.json")
    with open(manifest, "w") as fh:
        fh.write("{}")
    return [p], manifest


def make_peers(*, preset_kw=False):
    """Fake peers; ``preset_kw`` mimics survey-viz >= 0.22.0."""
    seen = {}

    def is_fetchable(key):
        return True

    def fetch_sst(bbox, start, end, stride_days=30):
        return FakeField()

    def fetch_averages(lake, start, end):
        return FakeSeries()

    if preset_kw:
        def render_viz(spec, field, series, out_dir, preset=None,
                       rotation=None, watermark=None, subtitle=None,
                       encoding_line=True):
            seen.update(preset=preset, rotation=rotation,
                        watermark=watermark, subtitle=subtitle,
                        encoding_line=encoding_line)
            return _write_frames(out_dir)
    else:
        def render_viz(spec, field, series, out_dir):
            return _write_frames(out_dir)

    def render_video(source, out_path, preset="reel", title="",
                     burn_timestamps_=False):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".json",
                                     fps=30.0, n_frames=1)

    ns = types.SimpleNamespace(
        is_fetchable=is_fetchable,
        fetch_sst=fetch_sst,
        fetch_averages=fetch_averages,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-180.0, -90.0, 180.0, 90.0),
    )
    return ns, seen


def test_preset_forwarded_to_render_viz(tmp_path):
    fake_peers, seen = make_peers(preset_kw=True)
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                          aesthetic_preset="dark_glow")
    assert seen["preset"] == "dark_glow"
    assert seen["rotation"] is None
    assert seen["watermark"] is None
    assert seen["subtitle"] is None
    assert seen["encoding_line"] is True


def test_rotation_watermark_subtitle_forwarded(tmp_path):
    fake_peers, seen = make_peers(preset_kw=True)
    pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path),
        aesthetic_preset="paper_prism", rotation="auto",
        watermark="my_handle", subtitle="Custom window")
    assert seen["preset"] == "paper_prism"
    assert seen["rotation"] == "auto"
    assert seen["watermark"] == "my_handle"
    assert seen["subtitle"] == "Custom window"


def test_encoding_line_false_forwarded(tmp_path):
    fake_peers, seen = make_peers(preset_kw=True)
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                          aesthetic_preset="dark_flow",
                          encoding_line=False)
    assert seen["encoding_line"] is False


def test_preset_defaults_not_forwarded_to_old_peer(tmp_path):
    # No preset options set: old peers keep working, nothing is passed.
    fake_peers, _ = make_peers(preset_kw=False)
    result = pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    assert result.video_path.endswith(".mp4")


def test_preset_old_peer_raises_upgrade_error(tmp_path):
    old_peers, _ = make_peers(preset_kw=False)
    with pytest.raises(pipeline.PeerTooOldError, match="0.22.0"):
        pipeline.run_pipeline(FakeSpec(), old_peers,
                              str(tmp_path), aesthetic_preset="dark_glow")


def test_rotation_alone_triggers_capability_check(tmp_path):
    # rotation without a preset still needs the preset-capable peer.
    old_peers, _ = make_peers(preset_kw=False)
    with pytest.raises(pipeline.PeerTooOldError, match="aesthetic presets"):
        pipeline.run_pipeline(FakeSpec(), old_peers, str(tmp_path),
                              rotation="auto")
