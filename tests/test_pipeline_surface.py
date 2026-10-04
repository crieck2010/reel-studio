"""Surface preset / counter / headline-beats plumbing (v0.21.0).

Covers the survey-viz 0.28.0 pass-through in ``run_pipeline``:
non-default surface kwargs, counter, and explicit headline beats reach
the render call only when the peer supports them (``_supports_kw``
signature inspection); unsupported kwargs are dropped and recorded in
provenance, never raised. ``headline_beats="auto"`` drafts beats via
the survey-narrate peer and falls back honestly when it is missing.
Defaults-off runs are byte-identical to pre-0.21.0 behavior: no new
kwargs, no new provenance keys. Fake peers throughout — no network.
"""

from __future__ import annotations

import os
import sys
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


def make_peers(*, new_viz=True):
    """Fake peers; ``new_viz`` mimics survey-viz >= 0.28.0 kwargs."""
    seen = {}

    def is_fetchable(key):
        return True

    def fetch_sst(bbox, start, end, stride_days=30):
        return FakeField()

    def fetch_averages(lake, start, end):
        return FakeSeries()

    if new_viz:
        def render_viz(spec, field, series, out_dir, preset=None,
                       **kwargs):
            seen.update(preset=preset)
            seen.update(kwargs)
            seen["kwargs"] = dict(kwargs)
            return _write_frames(out_dir)
    else:
        # Old peer: no new kwargs, no **kwargs catch-all.
        def render_viz(spec, field, series, out_dir):
            seen["kwargs"] = {}
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


NEW_KWARGS = ("surface_contours", "surface_contour_levels",
              "surface_shadow", "surface_smoothing", "surface_scale",
              "counter", "headline_beats")


# ---------------------------------------------------------------------------
# Pass-through
# ---------------------------------------------------------------------------

def test_surface_kwargs_forwarded(tmp_path):
    peers, seen = make_peers(new_viz=True)
    pipeline.run_pipeline(
        FakeSpec(), peers, str(tmp_path), aesthetic_preset="surface",
        surface_contours=False,
        surface_contour_levels=[0.0, 5.0, 10.0],
        surface_shadow=False, surface_smoothing=1.5,
        surface_scale="log")
    assert seen["preset"] == "surface"
    assert seen["surface_contours"] is False
    assert seen["surface_contour_levels"] == [0.0, 5.0, 10.0]
    assert seen["surface_shadow"] is False
    assert seen["surface_smoothing"] == 1.5
    assert seen["surface_scale"] == "log"


def test_surface_preset_accepted_with_defaults(tmp_path):
    # "surface" flows through the existing aesthetic_preset path.
    peers, seen = make_peers(new_viz=True)
    result = pipeline.run_pipeline(FakeSpec(), peers, str(tmp_path),
                                   aesthetic_preset="surface")
    assert seen["preset"] == "surface"
    assert result.video_path.endswith(".mp4")


def test_surface_defaults_not_forwarded(tmp_path):
    # At peer defaults nothing extra rides along (smaller cache key,
    # older peers untouched) even on the surface preset.
    peers, seen = make_peers(new_viz=True)
    pipeline.run_pipeline(FakeSpec(), peers, str(tmp_path),
                          aesthetic_preset="surface")
    for key in NEW_KWARGS:
        assert key not in seen["kwargs"]


def test_counter_forwarded_verbatim(tmp_path):
    peers, seen = make_peers(new_viz=True)
    counter = {"stat": "sum", "unit": "km²", "label": "Total area"}
    result = pipeline.run_pipeline(
        FakeSpec(), peers, str(tmp_path), aesthetic_preset="surface",
        counter=counter)
    assert seen["counter"] == counter
    rec = result.provenance["render"]["counter"]
    assert rec["applied"] is True
    assert rec["dropped"] is False


def test_headline_beats_explicit_forwarded(tmp_path):
    peers, seen = make_peers(new_viz=True)
    beats = [(0.0, "Opening headline"), (0.6, "The north pulls ahead")]
    result = pipeline.run_pipeline(
        FakeSpec(), peers, str(tmp_path), aesthetic_preset="surface",
        headline_beats=beats)
    assert seen["headline_beats"] == beats
    rec = result.provenance["render"]["headline_beats"]
    assert rec["mode"] == "explicit"
    assert rec["applied"] is True


# ---------------------------------------------------------------------------
# Defaults-off byte-identity
# ---------------------------------------------------------------------------

def test_defaults_off_no_new_kwargs_no_provenance(tmp_path):
    # Strict old-style peer: render_viz accepts nothing beyond the
    # positional args — any leaked kwarg would TypeError. The run must
    # be exactly the pre-0.21.0 shape.
    peers, seen = make_peers(new_viz=False)
    result = pipeline.run_pipeline(FakeSpec(), peers, str(tmp_path))
    assert seen["kwargs"] == {}
    render_prov = result.provenance["render"]
    assert "surface" not in render_prov
    assert "counter" not in render_prov
    assert "headline_beats" not in render_prov
    with open(result.video_path, "rb") as fh:
        assert fh.read() == b"FAKEMP4"


def test_defaults_off_new_peer_gets_nothing(tmp_path):
    peers, seen = make_peers(new_viz=True)
    result = pipeline.run_pipeline(FakeSpec(), peers, str(tmp_path))
    assert seen["kwargs"] == {}
    render_prov = result.provenance["render"]
    assert "surface" not in render_prov
    assert "counter" not in render_prov
    assert "headline_beats" not in render_prov


# ---------------------------------------------------------------------------
# Old-peer guard: dropped, recorded, never raised
# ---------------------------------------------------------------------------

def test_old_peer_drops_new_kwargs_and_records(tmp_path):
    # No aesthetic_preset here: any preset on an old peer raises
    # PeerTooOldError by pre-existing design. The NEW kwargs are the
    # ones that must drop silently and record.
    peers, seen = make_peers(new_viz=False)
    result = pipeline.run_pipeline(
        FakeSpec(), peers, str(tmp_path),
        surface_contours=False, surface_smoothing=2.0,
        counter={"stat": "max", "unit": "", "label": ""},
        headline_beats=[(0.0, "Hi")])
    assert result.video_path.endswith(".mp4")  # no raise
    render_prov = result.provenance["render"]
    surf = render_prov["surface"]
    assert "surface_contours" in surf["dropped"]
    assert "surface_smoothing" in surf["dropped"]
    assert render_prov["counter"]["applied"] is False
    assert render_prov["counter"]["dropped"] is True
    assert render_prov["headline_beats"]["applied"] is False
    assert render_prov["headline_beats"]["dropped"] is True


# ---------------------------------------------------------------------------
# headline_beats="auto" (survey-narrate peer)
# ---------------------------------------------------------------------------

def _narrate_stub(beats):
    mod = types.ModuleType("narrate")

    def story_facts(field, region_name=""):
        return {"peak_speed": 12.0, "region_name": region_name}

    def headline_beats(facts, *, max_beats=4):
        return list(beats)

    def render_caption(facts, style="email"):
        return "caption"

    mod.story_facts = story_facts
    mod.headline_beats = headline_beats
    mod.render_caption = render_caption
    return mod


def test_headline_beats_auto_with_narrate(tmp_path, monkeypatch):
    beats = [(0.0, "Peak flow 12.0"), (0.5, "Holding steady")]
    monkeypatch.setitem(sys.modules, "narrate", _narrate_stub(beats))
    peers, seen = make_peers(new_viz=True)
    result = pipeline.run_pipeline(
        FakeSpec(), peers, str(tmp_path), aesthetic_preset="surface",
        headline_beats="auto")
    assert seen["headline_beats"] == beats
    rec = result.provenance["render"]["headline_beats"]
    assert rec["mode"] == "auto"
    assert rec["status"] == "ok"
    assert rec["applied"] is True
    assert rec["fallback"] is False


def test_headline_beats_auto_narrate_missing_falls_back(tmp_path,
                                                        monkeypatch):
    # None in sys.modules makes `import narrate` raise ImportError.
    monkeypatch.setitem(sys.modules, "narrate", None)
    peers, seen = make_peers(new_viz=True)
    result = pipeline.run_pipeline(
        FakeSpec(), peers, str(tmp_path), aesthetic_preset="surface",
        headline_beats="auto")
    assert result.video_path.endswith(".mp4")  # render proceeds
    assert "headline_beats" not in seen["kwargs"]  # nothing fabricated
    rec = result.provenance["render"]["headline_beats"]
    assert rec["status"] == "fallback"
    assert rec["fallback"] is True
    assert rec["applied"] is False
    assert rec["beats"] is None


def test_headline_beats_auto_narrate_error_falls_back(tmp_path,
                                                      monkeypatch):
    bad = types.ModuleType("narrate")

    def story_facts(field, region_name=""):
        raise RuntimeError("facts malformed")

    bad.story_facts = story_facts
    bad.headline_beats = lambda facts, *, max_beats=4: []
    bad.render_caption = lambda facts, style="email": ""
    monkeypatch.setitem(sys.modules, "narrate", bad)
    peers, seen = make_peers(new_viz=True)
    result = pipeline.run_pipeline(
        FakeSpec(), peers, str(tmp_path), headline_beats="auto")
    assert "headline_beats" not in seen["kwargs"]
    rec = result.provenance["render"]["headline_beats"]
    assert rec["fallback"] is True
    assert "RuntimeError" in rec["reason"]
