"""Motion / audio / story-caption plumbing in run_pipeline (fake peers).

Covers the v0.4.0 capability checks: signature inspection decides
whether a peer supports a feature, and PeerTooOldError carries the
upgrade command instead of a traceback. No network, no real peers.
"""

from __future__ import annotations

import os
import types

import numpy as np
import pytest

from studio import pipeline


# --- fakes -------------------------------------------------------------------

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


class FakeMotionSpec:
    """Stands in for survey-animate's MotionSpec: records kwargs."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs


def make_peers(*, motion_kw=False, audio_kw=False, captions_kw=False,
               with_motionspec=False):
    """Fake peers with explicit (inspectable) signatures.

    ``motion_kw``/``audio_kw``/``captions_kw`` add the corresponding
    keyword to the fake render callables — mimicking new peers.
    Without them the fakes mimic old peers (no **kwargs either, so
    the signature check is strict).
    """
    seen = {}

    def is_fetchable(key):
        return True

    def fetch_sst(bbox, start, end, stride_days=30):
        return FakeField()

    def fetch_averages(lake, start, end):
        return FakeSeries()

    if captions_kw:
        def render_viz(spec, field, series, out_dir, story_captions=False):
            seen["story_captions"] = story_captions
            return _write_frames(out_dir)
    else:
        def render_viz(spec, field, series, out_dir):
            return _write_frames(out_dir)

    params = []
    if motion_kw:
        params.append("motion")
    if audio_kw:
        params.append("audio_path")

    def render_video(source, out_path, preset="reel", title="",
                     burn_timestamps_=False, **kwargs):
        # **kwargs present only to keep the base fake flexible; the
        # strict old-peer fakes are built separately below.
        for name in params:
            seen[name] = kwargs.get(name)
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
    if with_motionspec:
        ns.MotionSpec = FakeMotionSpec
    return ns, seen


def make_strict_old_peers():
    """Old peers: render_video/render_viz have NO new kwargs and NO **kwargs."""

    def is_fetchable(key):
        return True

    def fetch_sst(bbox, start, end, stride_days=30):
        return FakeField()

    def fetch_averages(lake, start, end):
        return FakeSeries()

    def render_viz(spec, field, series, out_dir):
        return _write_frames(out_dir)

    def render_video(source, out_path, preset="reel", title="",
                     burn_timestamps_=False):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".json",
                                     fps=30.0, n_frames=1)

    return types.SimpleNamespace(
        is_fetchable=is_fetchable,
        fetch_sst=fetch_sst,
        fetch_averages=fetch_averages,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-180.0, -90.0, 180.0, 90.0),
    )


def _write_frames(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "frame_0001.png")
    with open(p, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    manifest = os.path.join(out_dir, "manifest.json")
    with open(manifest, "w") as fh:
        fh.write("{}")
    return [p], manifest


# --- motion ------------------------------------------------------------------

def test_motion_forwarded_as_motionspec(tmp_path):
    fake_peers, seen = make_peers(motion_kw=True, with_motionspec=True)
    motion = {"zoom": "in", "zoom_speed": 0.5, "pan": "right",
              "pan_speed": 0.3, "smooth": True, "smooth_steps": 3}
    result = pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path), motion=motion)
    assert isinstance(seen["motion"], FakeMotionSpec)
    assert seen["motion"].kwargs == motion
    assert result.provenance["encode"]["motion"] == motion


def test_motion_empty_dict_not_forwarded(tmp_path):
    # {} / None keep old peers working: nothing is passed.
    fake_peers, _ = make_peers()
    result = pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path), motion={})
    assert result.provenance["encode"]["motion"] is None


def test_motion_unknown_key_fails_fast(tmp_path):
    fake_peers, _ = make_peers(motion_kw=True, with_motionspec=True)
    with pytest.raises(ValueError, match="unknown motion settings"):
        pipeline.run_pipeline(
            FakeSpec(), fake_peers, str(tmp_path),
            motion={"zoom": "in", "spin": 2})


def test_motion_old_peer_raises_upgrade_error(tmp_path):
    old = make_strict_old_peers()
    with pytest.raises(pipeline.PeerTooOldError) as excinfo:
        pipeline.run_pipeline(
            FakeSpec(), old, str(tmp_path), motion={"zoom": "in"})
    assert "survey-animate" in str(excinfo.value)
    assert "0.2.0" in str(excinfo.value)
    assert excinfo.value.pip_command.startswith("pip install --upgrade")


def test_motion_missing_motionspec_class_raises(tmp_path):
    # Peer accepts the kwarg but exposes no MotionSpec (should not happen
    # in practice; still an honest error, not a crash deep inside).
    fake_peers, _ = make_peers(motion_kw=True, with_motionspec=False)
    with pytest.raises(pipeline.PeerTooOldError):
        pipeline.run_pipeline(
            FakeSpec(), fake_peers, str(tmp_path), motion={"zoom": "in"})


# --- audio -------------------------------------------------------------------

def test_audio_path_forwarded(tmp_path):
    fake_peers, seen = make_peers(audio_kw=True)
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"FAKEAUDIO")
    pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path), audio_path=str(audio))
    assert seen["audio_path"] == str(audio)


def test_audio_missing_file_fails_fast(tmp_path):
    fake_peers, _ = make_peers(audio_kw=True)
    with pytest.raises(ValueError, match="does not exist"):
        pipeline.run_pipeline(
            FakeSpec(), fake_peers, str(tmp_path),
            audio_path=str(tmp_path / "nope.mp3"))


def test_audio_old_peer_raises_upgrade_error(tmp_path):
    old = make_strict_old_peers()
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"FAKEAUDIO")
    with pytest.raises(pipeline.PeerTooOldError) as excinfo:
        pipeline.run_pipeline(
            FakeSpec(), old, str(tmp_path), audio_path=str(audio))
    assert "survey-animate" in str(excinfo.value)
    assert "0.2.0" in str(excinfo.value)


# --- story captions ------------------------------------------------------------

def test_story_captions_forwarded(tmp_path):
    fake_peers, seen = make_peers(captions_kw=True)
    result = pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path), story_captions=True)
    assert seen["story_captions"] is True
    assert result.provenance["render"]["story_captions"] is True


def test_story_captions_default_off(tmp_path):
    fake_peers, seen = make_peers(captions_kw=True)
    result = pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    assert seen["story_captions"] is False
    assert result.provenance["render"]["story_captions"] is False


def test_story_captions_old_peer_raises_upgrade_error(tmp_path):
    old = make_strict_old_peers()
    with pytest.raises(pipeline.PeerTooOldError) as excinfo:
        pipeline.run_pipeline(
            FakeSpec(), old, str(tmp_path), story_captions=True)
    assert "survey-viz" in str(excinfo.value)
    assert "0.17.0" in str(excinfo.value)


# --- provenance ----------------------------------------------------------------

def test_provenance_records_audio_and_defaults(tmp_path):
    fake_peers, _ = make_peers(audio_kw=True)
    audio = tmp_path / "track.wav"
    audio.write_bytes(b"FAKEAUDIO")
    result = pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path), audio_path=str(audio))
    assert result.provenance["encode"]["audio_path"] == str(audio)
    assert result.provenance["encode"]["motion"] is None

    result2 = pipeline.run_pipeline(FakeSpec(), fake_peers,
                                    str(tmp_path / "second"))
    assert result2.provenance["encode"]["audio_path"] is None
