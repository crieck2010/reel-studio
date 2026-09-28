"""Batch queue: generate multiple reels unattended.

UI-free orchestration over :func:`studio.pipeline.run_pipeline`. Each
job gets an isolated output directory, its own settings snapshot, and
an independent status — one failed job is recorded and the queue keeps
going, so completed reels are never lost.
"""

from __future__ import annotations

import os
import re
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from studio import pipeline

#: Statuses a :class:`BatchJob` moves through. ``skipped`` is for jobs
#: with an empty description (nothing to parse).
JOB_STATUSES = ("queued", "running", "done", "failed", "skipped")

#: Settings keys :func:`run_batch` forwards to
#: :func:`studio.pipeline.run_pipeline` (everything else in a job's
#: ``settings`` dict is ignored, so the UI can stash display hints).
JOB_SETTING_KEYS = (
    "motion", "audio_path", "story_captions", "cmap",
    "stride_days", "stride_hours", "platform",
)


def _slugify(text: str, limit: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return (slug or "reel")[:limit].rstrip("-") or "reel"


@dataclass
class BatchJob:
    """One reel in the queue.

    ``settings`` is a snapshot of the run settings at enqueue time
    (motion/audio/captions/cmap/platform/strides) — later UI changes do not
    retroactively alter queued jobs.
    """

    description: str
    settings: Dict[str, Any] = field(default_factory=dict)
    index: int = 0
    status: str = "queued"
    error: str = ""
    detail: str = ""          # full traceback when failed
    out_dir: str = ""
    result: Optional[Any] = None  # pipeline.RunResult when done


def run_batch(
    jobs: List[BatchJob],
    peers: Any,
    out_root: str,
    parse_fn: Callable[[str], Any],
    progress: Optional[Callable[[int, int, float, str], None]] = None,
) -> List[BatchJob]:
    """Run ``jobs`` sequentially against ``peers``.

    Args:
        jobs: the queue; each job is updated in place (status, error,
            out_dir, result) and also returned in the list.
        peers: namespace for :func:`studio.pipeline.run_pipeline`
            (real wired peers or test doubles).
        out_root: parent directory; each job gets
            ``job-<nn>-<slug>/`` underneath it.
        parse_fn: ``description -> VizSpec`` (raises on unparseable).
        progress: optional ``(job_index, n_jobs, frac, message)``
            callback.

    A job that raises — at parse or at any pipeline stage — is marked
    ``failed`` with its error recorded and the queue continues with
    the next job. Completed results stay on disk in their own
    directories.
    """
    n_jobs = len(jobs)

    def report(idx: int, frac: float, message: str) -> None:
        if progress is not None:
            progress(idx, n_jobs, frac, message)

    os.makedirs(out_root, exist_ok=True)
    for idx, job in enumerate(jobs):
        job.index = idx
        if not job.description or not job.description.strip():
            job.status = "skipped"
            job.error = "empty description — nothing to parse"
            continue
        job.status = "running"
        slug = _slugify(job.description)
        job.out_dir = os.path.join(out_root, f"job-{idx + 1:02d}-{slug}")
        report(idx, 0.0, f"Job {idx + 1}/{n_jobs}: parsing…")
        try:
            spec = parse_fn(job.description)

            def _job_progress(frac: float, message: str,
                              _idx: int = idx) -> None:
                report(_idx, frac, f"Job {_idx + 1}/{n_jobs}: {message}")

            kwargs = {k: job.settings[k] for k in JOB_SETTING_KEYS
                      if k in job.settings}
            job.result = pipeline.run_pipeline(
                spec, peers, job.out_dir,
                progress=_job_progress, **kwargs)
        except pipeline.UnfetchableRegionError as exc:
            job.status = "failed"
            job.error = f"Not fetchable: {exc}"
            job.detail = traceback.format_exc()
            report(idx, 1.0, f"Job {idx + 1}/{n_jobs}: not fetchable")
        except pipeline.UnsupportedVariableError as exc:
            job.status = "failed"
            job.error = f"Unsupported variable: {exc}"
            job.detail = traceback.format_exc()
            report(idx, 1.0, f"Job {idx + 1}/{n_jobs}: unsupported variable")
        except pipeline.PeerTooOldError as exc:
            job.status = "failed"
            job.error = str(exc)
            job.detail = traceback.format_exc()
            report(idx, 1.0, f"Job {idx + 1}/{n_jobs}: peer too old")
        except Exception as exc:  # noqa: BLE001 - one bad job never kills the queue
            job.status = "failed"
            job.error = f"{type(exc).__name__}: {exc}"
            job.detail = traceback.format_exc()
            report(idx, 1.0, f"Job {idx + 1}/{n_jobs}: failed")
        else:
            job.status = "done"
            job.error = ""
            report(idx, 1.0, f"Job {idx + 1}/{n_jobs}: done")
    return jobs
