"""Scheduled generation bridge: schedx jobs -> the reel pipeline.

This module is the reel-studio side of the survey-schedule engine
(the optional sixth peer). It translates a :class:`schedx.Job`
(description + settings snapshot + schedule) into a headless
:func:`studio.pipeline.run_pipeline` call, and provides the ticker
entrypoint::

    python -m studio.scheduler run-due [--jobs-dir DIR] [--out-root DIR]

``schedx`` is imported lazily so the rest of reel-studio works without
the peer installed; call :func:`require_schedx` where a missing peer
must be a clear error instead of a missing UI section.
"""

from __future__ import annotations

import argparse
import os
from typing import Any, Callable, Dict, List, Optional

from . import batch, llm_assist, peers, pipeline

#: Env overrides for the jobs directory and the scheduled-run output root.
JOBS_DIR_ENV = "REEL_STUDIO_JOBS_DIR"
OUT_ROOT_ENV = "REEL_STUDIO_SCHEDULED_OUT"

#: Settings keys a scheduled job carries (same contract as batch jobs).
SCHEDULER_SETTING_KEYS = batch.JOB_SETTING_KEYS


def default_jobs_dir() -> str:
    """Where scheduled-job JSON files live."""
    return os.path.abspath(os.path.expanduser(os.environ.get(
        JOBS_DIR_ENV, os.path.join("~", ".reel-studio", "jobs"))))


def default_out_root() -> str:
    """Where scheduled runs write their per-run directories."""
    return os.path.abspath(os.path.expanduser(os.environ.get(
        OUT_ROOT_ENV, os.path.join("~", ".reel-studio", "scheduled-runs"))))


def schedx_available(statuses: Dict[str, peers.PeerStatus]) -> bool:
    """Whether the optional survey-schedule peer is installed."""
    return bool(statuses.get("survey-schedule")
                and statuses["survey-schedule"].installed)


def require_schedx(statuses: Dict[str, peers.PeerStatus]) -> Any:
    """The imported ``schedx`` module, or a MissingPeerError naming the fix."""
    status = statuses.get("survey-schedule")
    if status is None or not status.installed:
        spec = peers.PEER_SPECS["survey-schedule"]
        raise peers.MissingPeerError("survey-schedule", spec["pip"],
                                     spec["needed_for"])
    return status.module


def describe_schedule(statuses: Dict[str, peers.PeerStatus],
                      expression: str) -> str:
    """Plain-words description of a schedule string (peer required)."""
    return require_schedx(statuses).describe_schedule(expression)


def next_run_iso(statuses: Dict[str, peers.PeerStatus],
                 schedx_job: Any,
                 now: Optional[Any] = None) -> Optional[str]:
    """ISO timestamp of the job's next run, or None when spent/invalid."""
    schedx = require_schedx(statuses)
    try:
        schedule = schedx.parse_schedule(schedx_job.schedule)
        tz = schedx.job_timezone(schedx_job)
    except schedx.ScheduleError:
        return None
    import datetime as _dt
    now = now or _dt.datetime.now(_dt.timezone.utc)
    state = schedx.JobStore(default_jobs_dir()).load_state().get(
        schedx_job.name, {})
    last_run = None
    if state.get("last_run"):
        try:
            last_run = _dt.datetime.fromisoformat(state["last_run"])
        except ValueError:
            last_run = None
    nr = schedx.next_run(schedule, tz, now, last_run)
    return nr.isoformat() if nr else None


def job_to_pipeline_kwargs(schedx_job: Any) -> Dict[str, Any]:
    """Map a job's settings snapshot to run_pipeline kwargs.

    Only the known setting keys pass through (the same contract as
    batch jobs); unknown keys in the JSON are ignored, never crash.
    """
    settings = dict(getattr(schedx_job, "settings", None) or {})
    return {k: settings[k] for k in SCHEDULER_SETTING_KEYS
            if k in settings}


def _headless_assist_fn(statuses: Dict[str, peers.PeerStatus]
                        ) -> Optional[Callable[[str], Any]]:
    """One-shot LLM assist for headless runs, or None when disabled."""
    viz_status = statuses["survey-viz"]
    if not viz_status.installed or not os.environ.get("LLM_API_KEY"):
        return None
    VizSpec = viz_status.module.VizSpec

    def assist(text: str) -> Any:
        spec_dict = llm_assist.assist_from_env(text)
        if spec_dict is None:
            raise llm_assist.LLMAssistError("LLM_API_KEY is not set")
        return VizSpec.from_dict(spec_dict)

    return assist


def execute_reel_job(schedx_job: Any,
                     run_dir: str,
                     statuses: Dict[str, peers.PeerStatus]) -> Dict[str, Any]:
    """schedx executor: run one scheduled job through the reel pipeline.

    Parses ``schedx_job.description`` exactly like an interactive run
    (deterministic parser, one LLM assist attempt only when
    ``LLM_API_KEY`` is set), applies the settings snapshot, and runs
    :func:`studio.pipeline.run_pipeline` into ``run_dir``.

    Returns ``{"video_path": …, "n_frames": …}`` for the ledger's
    ``detail``. Raises on any failure — the schedx runner records it
    as ``failed`` and retries per the job's policy.
    """
    wired = peers.wire_peers(statuses)  # MissingPeerError names the fix
    viz = statuses["survey-viz"].module
    assist_fn = _headless_assist_fn(statuses)

    def parse_fn(text: str) -> Any:
        spec, _used = pipeline.parse_with_fallback(
            text, wired.parse_description, wired.parse_error,
            assist_fn=assist_fn)
        return spec

    if not schedx_job.description or not schedx_job.description.strip():
        raise ValueError("scheduled job has an empty description")
    spec = parse_fn(schedx_job.description)
    result = pipeline.run_pipeline(
        spec, wired, run_dir, **job_to_pipeline_kwargs(schedx_job))
    _ = viz  # the parse path already used it; kept for symmetry
    return {"video_path": result.video_path,
            "n_frames": result.n_frames}


def tick(jobs_dir: Optional[str] = None,
         out_root: Optional[str] = None,
         statuses: Optional[Dict[str, peers.PeerStatus]] = None) -> List[Any]:
    """Run one scheduler tick: execute every due job, return the records."""
    statuses = statuses if statuses is not None else peers.load_peers()
    schedx = require_schedx(statuses)
    store = schedx.JobStore(
        jobs_dir if jobs_dir is not None else default_jobs_dir())

    def executor(job: Any, run_dir: str) -> Dict[str, Any]:
        return execute_reel_job(job, run_dir, statuses)

    runner = schedx.Runner(
        store, executor,
        out_root if out_root is not None else default_out_root())
    return runner.tick()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="studio.scheduler",
        description="Tick reel-studio's scheduled jobs (survey-schedule "
                    "peer). Intended for cron (Linux) or Task Scheduler "
                    "(Windows), every minute.")
    parser.add_argument("--jobs-dir", default=None,
                        help=f"jobs directory (default: ${JOBS_DIR_ENV} or "
                             "~/.reel-studio/jobs)")
    parser.add_argument("--out-root", default=None,
                        help=f"scheduled-run output root (default: "
                             f"${OUT_ROOT_ENV} or "
                             "~/.reel-studio/scheduled-runs)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run-due", help="execute every due job once and exit")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        records = tick(jobs_dir=args.jobs_dir, out_root=args.out_root)
    except peers.MissingPeerError as exc:
        print(f"error: {exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 - the ticker reports, not crashes
        print(f"error: tick failed: {exc}")
        return 1
    if args.command == "run-due":
        if not records:
            print("no jobs due")
        for record in records:
            extra = f": {record.error}" if record.error else ""
            print(f"{record.job}: {record.status} "
                  f"(attempt {record.attempt}){extra}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
