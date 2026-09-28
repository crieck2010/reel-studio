"""Scheduled generation bridge — UI-free tests.

Exercises :mod:`studio.scheduler` against the real survey-schedule
peer (sibling checkout via conftest), with stubbed pipeline wiring.
No streamlit, no network.
"""

from __future__ import annotations

import os
import types

import pytest

schedx = pytest.importorskip("schedx")

from studio import batch, peers, pipeline, scheduler


def _schedx_status(module=schedx):
    return {"survey-schedule": peers.PeerStatus(
        repo="survey-schedule", module_name="schedx", module=module,
        pip_command="pip install git+https://github.com/crieck2010/survey-schedule.git",
        needed_for="scheduling")}


def _job(**over):
    base = dict(name="weekly-sst",
                description="weekly sea surface temperature of Lake Michigan",
                schedule="cron:0 6 * * mon",
                settings={"platform": "tiktok",
                          "style_preset": "midnight-ocean",
                          "bogus_key": "ignored"},
                timezone="UTC")
    base.update(over)
    return schedx.Job(**base)


# --- jobs dir / peer plumbing -------------------------------------------------

def test_default_jobs_dir_env_override(monkeypatch, tmp_path):
    monkeypatch.setenv(scheduler.JOBS_DIR_ENV, str(tmp_path))
    assert scheduler.default_jobs_dir() == os.path.abspath(str(tmp_path))


def test_default_jobs_dir_fallback(monkeypatch):
    monkeypatch.delenv(scheduler.JOBS_DIR_ENV, raising=False)
    assert scheduler.default_jobs_dir().endswith(
        os.path.join(".reel-studio", "jobs"))


def test_require_schedx_missing_names_fix():
    with pytest.raises(peers.MissingPeerError) as excinfo:
        scheduler.require_schedx({})
    assert "survey-schedule.git" in str(excinfo.value)


def test_require_schedx_returns_module():
    assert scheduler.require_schedx(_schedx_status()) is schedx


def test_describe_schedule_real_peer():
    text = scheduler.describe_schedule(_schedx_status(), "cron:0 6 * * mon")
    assert "06:00" in text and "Mon" in text


def test_next_run_iso_real_peer():
    import datetime as dt
    now = dt.datetime(2026, 9, 27, 12, 0, tzinfo=dt.timezone.utc)  # a Sunday
    iso = scheduler.next_run_iso(_schedx_status(), _job(), now=now)
    assert iso.startswith("2026-09-28T06:00")


def test_next_run_iso_invalid_schedule_returns_none():
    class Stub:
        name = "x"
        schedule = "not-a-schedule"
    assert scheduler.next_run_iso(_schedx_status(), Stub()) is None


# --- settings -> pipeline kwargs ----------------------------------------------

def test_job_to_pipeline_kwargs_filters_known_keys():
    kwargs = scheduler.job_to_pipeline_kwargs(_job())
    assert kwargs == {"platform": "tiktok",
                      "style_preset": "midnight-ocean"}
    assert "bogus_key" not in kwargs


def test_job_to_pipeline_kwargs_passes_strides_when_present():
    job = _job(settings={"stride_days": 7, "stride_hours": 24})
    assert scheduler.job_to_pipeline_kwargs(job) == {
        "stride_days": 7, "stride_hours": 24}
    assert set(scheduler.SCHEDULER_SETTING_KEYS) == set(batch.JOB_SETTING_KEYS)


# --- execute_reel_job -----------------------------------------------------------

class _FakeResult:
    video_path = "/tmp/runs/out.mp4"
    n_frames = 42


def _stub_pipeline(monkeypatch, capture):
    def fake_wire(statuses):
        return types.SimpleNamespace(
            parse_description=lambda text: f"spec({text})",
            parse_error=ValueError)

    def fake_parse_with_fallback(text, parse_fn, parse_exc, assist_fn=None):
        return parse_fn(text), False

    def fake_run_pipeline(spec, wired, out_dir, **kwargs):
        capture["spec"] = spec
        capture["kwargs"] = kwargs
        capture["out_dir"] = out_dir
        return _FakeResult()

    monkeypatch.setattr(scheduler.peers, "wire_peers", fake_wire)
    monkeypatch.setattr(scheduler.pipeline, "parse_with_fallback",
                        fake_parse_with_fallback)
    monkeypatch.setattr(scheduler.pipeline, "run_pipeline",
                        fake_run_pipeline)


def test_execute_reel_job_maps_settings(monkeypatch, tmp_path):
    capture = {}
    _stub_pipeline(monkeypatch, capture)
    statuses = {"survey-viz": peers.PeerStatus(
        repo="survey-viz", module_name="viz", module=types.ModuleType("viz"),
        pip_command="x", needed_for="y")}
    detail = scheduler.execute_reel_job(
        _job(), str(tmp_path), statuses)
    assert detail == {"video_path": "/tmp/runs/out.mp4", "n_frames": 42}
    assert capture["kwargs"] == {"platform": "tiktok",
                                 "style_preset": "midnight-ocean"}
    assert capture["out_dir"] == str(tmp_path)
    assert "Lake Michigan" in capture["spec"]


def test_execute_reel_job_empty_description_raises(monkeypatch, tmp_path):
    capture = {}
    _stub_pipeline(monkeypatch, capture)
    # schedx.Job validation rejects the empty description up front —
    # execute_reel_job never sees it.
    with pytest.raises(schedx.ScheduleError, match="non-empty"):
        _job(description="  ")


def test_execute_reel_job_missing_viz_peer_raises(monkeypatch, tmp_path):
    def fake_wire(statuses):
        raise peers.MissingPeerError(
            "survey-viz", "pip install x", "parsing")
    monkeypatch.setattr(scheduler.peers, "wire_peers", fake_wire)
    with pytest.raises(peers.MissingPeerError):
        scheduler.execute_reel_job(_job(), str(tmp_path), {})


# --- tick ---------------------------------------------------------------------

def test_tick_runs_due_once_job_and_ledgers(tmp_path, monkeypatch):
    jobs_dir = tmp_path / "jobs"
    out_root = tmp_path / "runs"
    store = schedx.JobStore(str(jobs_dir))
    store.save_job(_job(name="once-reel", schedule="once:2000-01-01T00:00"))
    statuses = _schedx_status()
    calls = []

    def fake_execute(job, run_dir, statuses):
        calls.append((job.name, run_dir))
        os.makedirs(run_dir, exist_ok=True)
        return {"video_path": "v.mp4", "n_frames": 5}

    monkeypatch.setattr(scheduler, "execute_reel_job", fake_execute)
    records = scheduler.tick(jobs_dir=str(jobs_dir),
                             out_root=str(out_root),
                             statuses=statuses)
    assert len(records) == 1
    assert records[0].status == "ok"
    assert records[0].detail["video_path"] == "v.mp4"
    assert calls[0][0] == "once-reel"
    # Ledger persisted; second tick has nothing due.
    assert scheduler.tick(jobs_dir=str(jobs_dir),
                          out_root=str(out_root),
                          statuses=statuses) == []
    probe = schedx.Runner(store, schedx.null_executor, str(out_root))
    assert len(probe.ledger_tail()) == 1


def test_tick_no_jobs_due_returns_empty(tmp_path):
    statuses = _schedx_status()
    assert scheduler.tick(jobs_dir=str(tmp_path / "jobs"),
                          out_root=str(tmp_path / "runs"),
                          statuses=statuses) == []


def test_main_run_due_no_jobs(tmp_path, monkeypatch, capsys):
    jobs = tmp_path / "jobs"
    jobs.mkdir()
    monkeypatch.setenv(scheduler.JOBS_DIR_ENV, str(jobs))
    # statuses resolve via load_peers; the peer is importable here.
    rc = scheduler.main(["--jobs-dir", str(jobs),
                         "--out-root", str(tmp_path / "runs"),
                         "run-due"])
    assert rc == 0
    assert "no jobs due" in capsys.readouterr().out
