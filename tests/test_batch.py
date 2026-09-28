"""Batch queue: sequential jobs, per-job isolation, failure containment.

Fake peers + a fake parse function; no network, no real peers.
"""

from __future__ import annotations

import os
import types

import pytest

from studio import batch, pipeline


class Boom(Exception):
    pass


def _fake_peers(fail_descriptions=()):
    def _result(out_dir):
        os.makedirs(out_dir, exist_ok=True)  # the real pipeline makes this
        video = os.path.join(out_dir, "reel.mp4")
        with open(video, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(
            video_path=video, sidecar_path=video + ".json",
            frames_dir=os.path.join(out_dir, "frames"),
            manifest_path=os.path.join(out_dir, "frames", "manifest.json"),
            n_frames=3, spec={}, lake="", source="",
            provenance={"encode": {}, "render": {}})

    real_run = pipeline.run_pipeline

    def run_pipeline(spec, peers, out_dir, **kwargs):
        if spec.description in fail_descriptions:
            raise Boom(f"simulated failure for {spec.description!r}")
        return _result(out_dir)

    peers = types.SimpleNamespace()
    return peers, run_pipeline


def _parse_fn(text):
    if text == "this does not parse":
        raise ValueError("nope")
    return types.SimpleNamespace(description=text)


def _run(jobs, peers, run_pipeline, tmp_path, **kw):
    orig = pipeline.run_pipeline
    pipeline.run_pipeline = run_pipeline
    try:
        return batch.run_batch(
            jobs, peers, str(tmp_path), _parse_fn, **kw)
    finally:
        pipeline.run_pipeline = orig


# --- behavior ------------------------------------------------------------------

def test_three_jobs_all_succeed_with_isolated_dirs(tmp_path):
    peers, fake_run = _fake_peers()
    jobs = [batch.BatchJob(description=d)
            for d in ("lake erie sst", "gulf chlorophyll", "firms amazon")]
    out = _run(jobs, peers, fake_run, tmp_path)
    assert [j.status for j in out] == ["done", "done", "done"]
    dirs = [j.out_dir for j in out]
    assert len(set(dirs)) == 3
    for d in dirs:
        assert os.path.isfile(os.path.join(d, "reel.mp4"))
    assert dirs[0].endswith("job-01-lake-erie-sst")


def test_failed_job_does_not_lose_completed_ones(tmp_path):
    peers, fake_run = _fake_peers(fail_descriptions=("job two",))
    jobs = [batch.BatchJob(description=d)
            for d in ("job one", "job two", "job three")]
    out = _run(jobs, peers, fake_run, tmp_path)
    assert [j.status for j in out] == ["done", "failed", "done"]
    assert "simulated failure" in out[1].error
    assert out[1].detail  # traceback kept for the UI
    # completed reels survived on disk
    assert os.path.isfile(os.path.join(out[0].out_dir, "reel.mp4"))
    assert os.path.isfile(os.path.join(out[2].out_dir, "reel.mp4"))
    assert out[0].result is not None and out[2].result is not None
    assert out[1].result is None


def test_unparseable_description_is_a_failed_job(tmp_path):
    peers, fake_run = _fake_peers()
    jobs = [batch.BatchJob(description="good job"),
            batch.BatchJob(description="this does not parse")]
    out = _run(jobs, peers, fake_run, tmp_path)
    assert [j.status for j in out] == ["done", "failed"]
    assert out[1].error  # parse error message recorded


def test_empty_description_is_skipped(tmp_path):
    peers, fake_run = _fake_peers()
    jobs = [batch.BatchJob(description="  "),
            batch.BatchJob(description="real job")]
    out = _run(jobs, peers, fake_run, tmp_path)
    assert [j.status for j in out] == ["skipped", "done"]


def test_job_settings_forwarded_to_pipeline(tmp_path):
    seen = {}

    def run_pipeline(spec, peers, out_dir, **kwargs):
        seen.update(kwargs)
        os.makedirs(out_dir, exist_ok=True)
        video = os.path.join(out_dir, "reel.mp4")
        with open(video, "wb") as fh:
            fh.write(b"x")
        return types.SimpleNamespace(
            video_path=video, sidecar_path="", frames_dir="",
            manifest_path="", n_frames=1, spec={}, lake="", source="",
            provenance={})

    peers = types.SimpleNamespace()
    settings = {"motion": {"zoom": "in"}, "audio_path": "/tmp/a.mp3",
                "story_captions": True, "cmap": "viridis",
                "stride_days": 7, "display_hint": "ignored"}
    orig = pipeline.run_pipeline
    pipeline.run_pipeline = run_pipeline
    try:
        batch.run_batch([batch.BatchJob(description="d", settings=settings)],
                        peers, str(tmp_path), _parse_fn)
    finally:
        pipeline.run_pipeline = orig
    assert seen["motion"] == {"zoom": "in"}
    assert seen["audio_path"] == "/tmp/a.mp3"
    assert seen["story_captions"] is True
    assert seen["cmap"] == "viridis"
    assert seen["stride_days"] == 7
    assert "display_hint" not in seen  # not a pipeline kwarg


def test_progress_callback_reports_per_job(tmp_path):
    peers, fake_run = _fake_peers()
    events = []
    jobs = [batch.BatchJob(description="a"), batch.BatchJob(description="b")]
    _run(jobs, peers, fake_run, tmp_path,
         progress=lambda i, n, f, m: events.append((i, n, f)))
    assert events[0][:2] == (0, 2)
    assert events[-1][:2] == (1, 2)
    assert events[-1][2] == 1.0


def test_slugify_handles_weird_descriptions():
    assert batch._slugify("Lake Erie SST (2020–2024)!") == \
        "lake-erie-sst-2020-2024"
    assert batch._slugify("   ") == "reel"
    assert batch._slugify("a" * 100) == "a" * 40
