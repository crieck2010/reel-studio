"""Smarter render caching: key design + pipeline cache hit/miss paths.

Uses the fake peers from test_pipeline (no network, no real peers) and
a real ``cachex.Cache`` in a tmp dir for the store/restore round-trips.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np
import pytest

from studio import caching, pipeline
from tests.test_pipeline import FakeSpec, make_fake_peers

cachex = pytest.importorskip("cachex")


def make_cache(tmp_path, **kw):
    return cachex.Cache(str(tmp_path / "cache"), **kw)


# --- key design ---------------------------------------------------------------

def _field():
    return {"times": ["2026-06-01"], "lats": [41.5], "lons": [-88.2],
            "values": np.full((1, 1, 1), 18.0), "variable": "sst"}


def _batch_key(**over):
    kw = dict(spec_dict={"title": "t", "variable": "sst"},
              render_field=_field(), series_dict=None,
              render_viz_kwargs={}, layout_canvas=None,
              style_preset=None, platform="legacy", viz_version="0.18.0")
    kw.update(over)
    return caching.frame_batch_key(**kw)


def test_frame_batch_key_stable():
    assert _batch_key() == _batch_key()


def test_frame_batch_key_sensitive_to_render_inputs():
    base = _batch_key()
    assert _batch_key(render_viz_kwargs={"cmap": "viridis"}) != base
    assert _batch_key(style_preset="midnight-ocean") != base
    assert _batch_key(platform="square") != base
    assert _batch_key(viz_version="0.19.0") != base
    assert _batch_key(spec_dict={"title": "other",
                                 "variable": "sst"}) != base


def test_frame_batch_key_sensitive_to_data_bytes():
    base = _batch_key()
    field = _field()
    field["values"] = np.full((1, 1, 1), 19.0)  # same shape, new data
    assert _batch_key(render_field=field) != base


def test_frame_batch_key_sensitive_to_aesthetic_preset():
    # The preset/rotation/watermark/subtitle ride in render_viz_kwargs,
    # so they invalidate the frame cache like any other render input.
    base = _batch_key()
    assert _batch_key(
        render_viz_kwargs={"preset": "dark_glow"}) != base
    assert _batch_key(
        render_viz_kwargs={"preset": "dark_glow",
                           "rotation": "auto"}) != _batch_key(
        render_viz_kwargs={"preset": "dark_glow"})
    assert _batch_key(
        render_viz_kwargs={"preset": "paper_prism",
                           "watermark": "handle"}) != base
    assert _batch_key(
        render_viz_kwargs={"preset": "paper_prism",
                           "subtitle": "Custom"}) != base
    assert _batch_key(
        render_viz_kwargs={"preset": "dark_flow",
                           "encoding_line": False}) != base


def test_frame_batch_key_ignores_array_identity():
    # Two distinct arrays with identical bytes key identically.
    assert _batch_key() == _batch_key(render_field=_field())


def test_video_key_stable_and_sensitive():
    keys = ["a" * 64, "b" * 64]
    k1 = caching.video_key(keys, "reel", "title", {}, animate_version="1.0")
    assert k1 == caching.video_key(keys, "reel", "title", {},
                                   animate_version="1.0")
    assert caching.video_key(keys, "square", "title", {},
                             animate_version="1.0") != k1
    assert caching.video_key(keys, "reel", "title",
                             {"motion": {"zoom": "in"}},
                             animate_version="1.0") != k1
    assert caching.video_key(keys, "reel", "title", {},
                             audio_content_key="c" * 64,
                             animate_version="1.0") != k1
    assert caching.video_key(["c" * 64], "reel", "title", {},
                             animate_version="1.0") != k1


# --- store / restore round-trips --------------------------------------------

def _write_png(path, payload=b"\x89PNG\r\n\x1a\n"):
    with open(path, "wb") as fh:
        fh.write(payload)


def test_store_restore_frame_batch(tmp_path):
    cache = make_cache(tmp_path)
    frames_dir = tmp_path / "frames"
    frames_dir.mkdir()
    paths = []
    for i in range(2):
        p = frames_dir / f"frame_{i + 1:04d}.png"
        _write_png(p, b"PNG%d" % i)
        paths.append(str(p))
    manifest = frames_dir / "manifest.json"
    manifest.write_text('{"n": 2}')
    record = caching.store_frame_batch(cache, "bk", paths, str(manifest))
    assert record["n_frames"] == 2

    out = tmp_path / "restored"
    frames, manifest_path, content_keys = caching.restore_frame_batch(
        cache, "bk", str(out))
    assert len(frames) == 2
    assert [open(p, "rb").read() for p in frames] == [b"PNG0", b"PNG1"]
    assert open(manifest_path).read() == '{"n": 2}'
    assert content_keys == record["frames"]


def test_restore_frame_batch_miss_returns_none(tmp_path):
    cache = make_cache(tmp_path)
    assert caching.restore_frame_batch(cache, "nope",
                                       str(tmp_path / "x")) is None
    assert caching.restore_video(cache, "nope",
                                 str(tmp_path / "v.mp4")) is None


def test_store_restore_video(tmp_path):
    cache = make_cache(tmp_path)
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"FAKEMP4")
    meta = {"sidecar_path": "s", "fps": 30.0, "n_frames": 2}
    caching.store_video(cache, "vk", str(video), meta)
    out = tmp_path / "r.mp4"
    got = caching.restore_video(cache, "vk", str(out))
    assert got == meta
    assert out.read_bytes() == b"FAKEMP4"


def test_identical_frames_dedupe(tmp_path):
    cache = make_cache(tmp_path)
    d = tmp_path / "f"
    d.mkdir()
    p1, p2 = str(d / "a.png"), str(d / "b.png")
    _write_png(p1, b"SAME")
    _write_png(p2, b"SAME")
    m = d / "m.json"
    m.write_text("{}")
    r = caching.store_frame_batch(cache, "bk", [p1, p2], str(m))
    assert r["frames"][0] == r["frames"][1]
    assert cache.stats()["entries"] < 4  # frames deduped


# --- pipeline integration -----------------------------------------------------

def _run(spec, fake_peers, out_dir, cache):
    return pipeline.run_pipeline(spec, fake_peers, str(out_dir), cache=cache)


def test_pipeline_caches_and_hits(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)
    cache = make_cache(tmp_path)
    out1, out2 = tmp_path / "r1", tmp_path / "r2"

    res1 = _run(FakeSpec(), fake_peers, out1, cache)
    assert calls == ["is_fetchable", "fetch_sst", "fetch_averages",
                     "render_viz", "render_video"]
    assert res1.provenance["cache"]["enabled"] is True
    assert res1.provenance["cache"]["frame_hit"] is False
    assert res1.provenance["cache"]["video_hit"] is False

    calls.clear()
    res2 = _run(FakeSpec(), fake_peers, out2, cache)
    # Fetch still runs (data freshness is the caller's business); the
    # expensive render + encode steps are skipped on identical inputs.
    assert calls == ["is_fetchable", "fetch_sst", "fetch_averages"]
    assert res2.provenance["cache"]["frame_hit"] is True
    assert res2.provenance["cache"]["video_hit"] is True
    assert os.path.isfile(res2.video_path)
    assert open(res2.video_path, "rb").read() == b"FAKEMP4"
    assert res2.n_frames == 2


def test_pipeline_frame_hit_but_video_miss(tmp_path):
    # New encode input (audio track added) misses the video tag while
    # the frames still hit — audio affects the MP4, never the pixels.
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)
    cache = make_cache(tmp_path)
    _run(FakeSpec(), fake_peers, tmp_path / "r1", cache)
    audio = tmp_path / "track.mp3"
    audio.write_bytes(b"FAKEAUDIO")
    calls.clear()
    res = pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path / "r2"), cache=cache,
        audio_path=str(audio))
    assert "render_viz" not in calls
    assert "render_video" in calls
    assert res.provenance["cache"]["frame_hit"] is True
    assert res.provenance["cache"]["video_hit"] is False


def test_pipeline_cache_disabled_by_default(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)
    res = _run(FakeSpec(), fake_peers, tmp_path / "r", None)
    assert res.provenance["cache"]["enabled"] is False
    assert calls == ["is_fetchable", "fetch_sst", "fetch_averages",
                     "render_viz", "render_video"]


def test_pipeline_new_data_misses_frame_cache(tmp_path):
    calls = []
    fake_peers, _, _ = make_fake_peers(calls)
    cache = make_cache(tmp_path)
    _run(FakeSpec(), fake_peers, tmp_path / "r1", cache)

    # Same description, different fetched bytes -> honest re-render.
    calls2 = []
    peers2, _, _ = make_fake_peers(calls2)

    orig_fetch = peers2.fetch_sst

    def fetch_sst_hot(bbox, start, end, stride_days=30):
        field = orig_fetch(bbox, start, end, stride_days=stride_days)
        field.sst = np.ma.masked_array(np.full((3, 3, 3), 25.0), mask=False)
        return field

    peers2.fetch_sst = fetch_sst_hot
    calls.clear()
    res = _run(FakeSpec(), peers2, tmp_path / "r2", cache)
    assert "render_viz" in calls2 or "render_viz" in calls
    assert res.provenance["cache"]["frame_hit"] is False


# --- helpers ------------------------------------------------------------------

def test_open_cache_none_when_peer_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "cachex", None)
    assert caching.open_cache() is None
    assert caching.cachex_module() is None


def test_open_cache_none_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("REEL_STUDIO_CACHE", "0")
    assert caching.open_cache(str(tmp_path)) is None
    monkeypatch.setenv("REEL_STUDIO_CACHE", "1")
    assert isinstance(caching.open_cache(str(tmp_path)), cachex.Cache)


def test_describe_stats(tmp_path):
    cache = make_cache(tmp_path)
    info = caching.describe_stats(cache)
    assert info["entries"] == 0
    assert info["used_human"].endswith("KiB")
    assert info["hits"] == 0


def test_default_cache_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv("REEL_STUDIO_CACHE_DIR", str(tmp_path / "c"))
    assert caching.default_cache_dir() == str(tmp_path / "c")


# --- batch / scheduler passthrough --------------------------------------------

def test_batch_passes_cache_through(tmp_path):
    from studio import batch
    seen = {}

    def fake_run(spec, peers, out_dir, **kwargs):
        seen.update(kwargs)
        return types.SimpleNamespace(
            video_path=os.path.join(out_dir, "reel.mp4"),
            sidecar_path="", frames_dir="", manifest_path="",
            n_frames=1, spec={}, lake="", source="", provenance={})

    orig = pipeline.run_pipeline
    pipeline.run_pipeline = fake_run
    try:
        jobs = [batch.BatchJob(description="sst of Lake Michigan")]
        batch.run_batch(jobs, types.SimpleNamespace(), str(tmp_path),
                        lambda t: types.SimpleNamespace(description=t),
                        cache="SENTINEL")
    finally:
        pipeline.run_pipeline = orig
    assert seen.get("cache") == "SENTINEL"
    assert jobs[0].status == "done"


def test_scheduler_execute_passes_cache(monkeypatch, tmp_path):
    from studio import peers as peers_mod
    from studio import scheduler
    schedx = pytest.importorskip("schedx")
    capture = {}

    def fake_run_pipeline(spec, wired, out_dir, **kwargs):
        capture.update(kwargs)
        return types.SimpleNamespace(video_path="/tmp/r.mp4", n_frames=1)

    def fake_wire(statuses):
        return types.SimpleNamespace(
            parse_description=lambda text: f"spec({text})",
            parse_error=ValueError)

    monkeypatch.setattr(scheduler.pipeline, "run_pipeline",
                        fake_run_pipeline)
    monkeypatch.setattr(scheduler.peers, "wire_peers", fake_wire)
    monkeypatch.setattr(
        scheduler.pipeline, "parse_with_fallback",
        lambda text, pf, pe, assist_fn=None: (pf(text), False))
    statuses = {"survey-viz": peers_mod.PeerStatus(
        repo="survey-viz", module_name="viz",
        module=types.ModuleType("viz"), pip_command="x", needed_for="y")}
    job = schedx.Job(name="j", description="sst of Lake Michigan",
                     schedule="every:1d", settings={})
    scheduler.execute_reel_job(job, str(tmp_path), statuses)
    assert "cache" in capture  # None when peer missing, Cache when present
