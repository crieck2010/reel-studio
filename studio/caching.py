"""Render-result caching for reel-studio, backed by the survey-cache peer.

This module is the *consumer-side* half of feature 4 ("smarter render
caching"). The storage engine itself lives in the ``survey-cache`` repo
(import name ``cachex``): a content-addressed, stdlib-only artifact
store. This module owns the Reel Studio-specific part — **what goes
into a cache key** — plus thin store/restore helpers for frame batches
and encoded videos.

Key design (the "smart" part):

* Frame batches are keyed by a fingerprint of *everything that can
  change the pixels*: the parsed spec dict, a digest of the fetched
  field (array bytes + shape + dtype, never the whole array in the
  key), the series dict, the exact ``render_viz`` kwargs, the platform
  canvas, the style preset, and the survey-viz distribution version.
  Re-running the same description against the same fetched data hits;
  new satellite data (different array bytes) misses honestly.
* Encoded videos are keyed by the frame *content* keys plus the encode
  kwargs (preset, title, motion, audio content hash) and the
  survey-animate version. A cache hit skips ffmpeg entirely.
* Stored artifacts are content-addressed (SHA-256 of the bytes), so an
  identical PNG appearing in two different reels is stored once; the
  semantic tag (``reel-studio/frames/<batch-key>``) just points at the
  content.

Everything here degrades gracefully: :func:`open_cache` returns
``None`` when the peer is missing, and the pipeline treats ``None``
as "caching disabled".
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Dict, List, Optional, Tuple

#: Tag namespaces inside the shared cache.
FRAMES_TAG_PREFIX = "reel-studio/frames/"
VIDEO_TAG_PREFIX = "reel-studio/video/"

#: Env var overriding the cache directory.
CACHE_DIR_ENV = "REEL_STUDIO_CACHE_DIR"

#: Env var disabling the cache entirely (``"0"``/``"false"``/``"no"``).
CACHE_ENABLE_ENV = "REEL_STUDIO_CACHE"

#: Default on-disk budget for the render cache (20 GiB).
DEFAULT_MAX_BYTES = 20 * 1024 ** 3


def cachex_module() -> Optional[Any]:
    """Import the survey-cache peer, or None when it is not installed."""
    try:
        import cachex  # type: ignore
    except ImportError:
        return None
    return cachex


def default_cache_dir() -> str:
    """Filesystem home of the render cache."""
    override = os.environ.get(CACHE_DIR_ENV)
    if override:
        return os.path.expanduser(override)
    return os.path.join(os.path.expanduser("~"), ".reel-studio", "cache")


def cache_enabled() -> bool:
    """False only when the user explicitly disabled caching via env."""
    return os.environ.get(CACHE_ENABLE_ENV, "1").strip().lower() not in (
        "0", "false", "no", "off")


def open_cache(root: Optional[str] = None,
               max_bytes: int = DEFAULT_MAX_BYTES) -> Optional[Any]:
    """Open the shared render cache, or None when unavailable/disabled.

    Returns None when the survey-cache peer is not installed or when
    caching was disabled via ``REEL_STUDIO_CACHE=0``. Never raises for
    a missing peer — the pipeline simply runs uncached.
    """
    if not cache_enabled():
        return None
    cachex = cachex_module()
    if cachex is None:
        return None
    return cachex.Cache(root or default_cache_dir(), max_bytes=max_bytes)


def _dist_version(dist_name: str) -> str:
    """Installed distribution version, or ``"unknown"`` (never raises)."""
    try:
        from importlib.metadata import version, PackageNotFoundError
        try:
            return version(dist_name)
        except PackageNotFoundError:
            return "unknown"
    except Exception:  # pragma: no cover - importlib.metadata is stdlib
        return "unknown"


def _digest_value(value: Any) -> Any:
    """Recursively replace numpy arrays with content digests.

    Arrays can be gigabytes; the key carries their SHA-256, shape and
    dtype instead of their bytes, so equal data keys equal and any
    changed sample misses.
    """
    try:
        import numpy as np
    except ImportError:  # pragma: no cover - numpy is a hard dep here
        np = None  # type: ignore
    if np is not None and isinstance(value, (np.ndarray, np.generic)):
        arr = value if isinstance(value, np.ndarray) else None
        if arr is None:  # numpy scalar
            return {"__npscalar__": str(value), "dtype": str(value.dtype)}
        h = hashlib.sha256()
        h.update(str(arr.dtype).encode("utf-8"))
        h.update(str(arr.shape).encode("utf-8"))
        h.update(arr.tobytes(order="C"))
        return {"__ndarray__": h.hexdigest(), "shape": list(arr.shape),
                "dtype": str(arr.dtype)}
    if isinstance(value, dict):
        return {str(k): _digest_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_digest_value(v) for v in value]
    return value


def frame_batch_key(spec_dict: Dict[str, Any],
                    render_field: Dict[str, Any],
                    series_dict: Optional[Dict[str, Any]],
                    render_viz_kwargs: Dict[str, Any],
                    layout_canvas: Optional[Dict[str, Any]],
                    style_preset: Optional[str],
                    platform: Optional[str],
                    viz_version: Optional[str] = None,
                    derived: Optional[Dict[str, Any]] = None) -> str:
    """Fingerprint every input that can change the rendered pixels.

    ``derived`` is the normalized derived-product config (product,
    baseline range, window, stride) when a climatological anomaly
    product was requested, else ``None``. It is keyed explicitly even
    though the derived settings also surface through ``spec_dict``
    (``<base>-anomaly`` variable, symmetric limits, baseline note) and
    the transformed field digest: the config is the scientific identity
    of the product, and an explicit key keeps cache behavior auditable
    if the spec mutation ever moves.
    """
    cachex = cachex_module()
    if cachex is None:  # pragma: no cover - keys are only built when cached
        raise RuntimeError("survey-cache peer is not installed")
    payload = {
        "kind": "reel-studio/frame-batch",
        "spec": spec_dict,
        "field": _digest_value(render_field),
        "series": _digest_value(series_dict),
        "render_viz_kwargs": _digest_value(render_viz_kwargs),
        "canvas": _digest_value(layout_canvas),
        "style_preset": style_preset,
        "platform": platform or "legacy",
        "viz_version": viz_version or _dist_version("survey-viz"),
        "derived": _digest_value(derived),
    }
    return cachex.fingerprint(payload)


def video_key(frame_content_keys: List[str],
              encode_preset: str,
              title: str,
              render_video_kwargs: Dict[str, Any],
              audio_content_key: Optional[str] = None,
              animate_version: Optional[str] = None) -> str:
    """Fingerprint every input that can change the encoded MP4."""
    cachex = cachex_module()
    if cachex is None:  # pragma: no cover - keys are only built when cached
        raise RuntimeError("survey-cache peer is not installed")
    payload = {
        "kind": "reel-studio/video",
        "frames": list(frame_content_keys),
        "preset": encode_preset,
        "title": title or "",
        "render_video_kwargs": _digest_value(render_video_kwargs),
        "audio_content_key": audio_content_key,
        "animate_version": animate_version or _dist_version("survey-animate"),
    }
    return cachex.fingerprint(payload)


def frames_tag(batch_key: str) -> str:
    """Cache tag name for a frame batch."""
    return FRAMES_TAG_PREFIX + batch_key


def video_tag(vkey: str) -> str:
    """Cache tag name for an encoded video."""
    return VIDEO_TAG_PREFIX + vkey


def store_frame_batch(cache: Any, batch_key: str,
                      frame_paths: List[str],
                      manifest_path: str) -> Dict[str, Any]:
    """Store a freshly rendered frame batch; return the batch record.

    Each PNG is stored once by content hash (identical frames across
    reels dedupe for free); the tag points at a small JSON record with
    the per-frame content keys, basenames, and the manifest bytes.
    """
    with open(manifest_path, "rb") as fh:
        manifest_key = cache.put_bytes(fh.read(), meta={"kind": "manifest"})
    content_keys: List[str] = []
    basenames: List[str] = []
    for path in frame_paths:
        content_keys.append(cache.put_file(
            path, meta={"kind": "frame", "batch": batch_key}))
        basenames.append(os.path.basename(path))
    record = {"kind": "frame-batch", "batch_key": batch_key,
              "frames": content_keys, "basenames": basenames,
              "manifest_key": manifest_key, "n_frames": len(frame_paths)}
    record_key = cache.put_bytes(
        json.dumps(record, sort_keys=True).encode("utf-8"),
        meta={"kind": "frame-batch-record"})
    cache.tag(frames_tag(batch_key), record_key,
              meta={"n_frames": len(frame_paths)})
    return record


def restore_frame_batch(cache: Any, batch_key: str,
                        frames_dir: str) -> Optional[Tuple[List[str], str, List[str]]]:
    """Materialize a cached frame batch into ``frames_dir``.

    Returns ``(frame_paths, manifest_path, content_keys)`` on a hit,
    else None. A corrupt/partial entry is treated as a miss (the bad
    bytes are quarantined by the cache itself).
    """
    record_key = cache.resolve(frames_tag(batch_key))
    if record_key is None:
        return None
    raw = cache.get_bytes(record_key)
    if raw is None:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
        content_keys = list(record["frames"])
        basenames = list(record["basenames"])
        manifest_key = str(record["manifest_key"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    os.makedirs(frames_dir, exist_ok=True)
    frame_paths: List[str] = []
    for content_key, basename in zip(content_keys, basenames):
        dest = os.path.join(frames_dir, basename)
        try:
            cache.materialize(content_key, dest)
        except KeyError:
            return None  # evicted mid-read: honest miss
        frame_paths.append(dest)
    manifest_dest = os.path.join(frames_dir, "manifest.json")
    try:
        cache.materialize(manifest_key, manifest_dest)
    except KeyError:
        return None
    return frame_paths, manifest_dest, content_keys


def store_video(cache: Any, vkey: str, video_path: str,
                result_meta: Dict[str, Any]) -> Dict[str, Any]:
    """Store a freshly encoded MP4; return the video record."""
    content_key = cache.put_file(video_path, meta={"kind": "video"})
    record = {"kind": "video", "video_key": vkey,
              "content_key": content_key, "meta": dict(result_meta)}
    record_key = cache.put_bytes(
        json.dumps(record, sort_keys=True).encode("utf-8"),
        meta={"kind": "video-record"})
    cache.tag(video_tag(vkey), record_key)
    return record


def restore_video(cache: Any, vkey: str,
                  video_path: str) -> Optional[Dict[str, Any]]:
    """Materialize a cached MP4 to ``video_path``; return its meta or None."""
    record_key = cache.resolve(video_tag(vkey))
    if record_key is None:
        return None
    raw = cache.get_bytes(record_key)
    if raw is None:
        return None
    try:
        record = json.loads(raw.decode("utf-8"))
        content_key = str(record["content_key"])
        meta = dict(record["meta"])
    except (ValueError, KeyError, TypeError, AttributeError):
        return None
    os.makedirs(os.path.dirname(os.path.abspath(video_path)), exist_ok=True)
    try:
        cache.materialize(content_key, video_path)
    except KeyError:
        return None
    return meta


def describe_stats(cache: Any) -> Dict[str, Any]:
    """Human-friendly subset of ``cache.stats()`` for the UI."""
    stats = cache.stats()
    used = float(stats.get("bytes_used", 0))
    maximum = float(stats.get("max_bytes", 0) or 0)
    if used >= 1024 ** 3:
        used_s = f"{used / 1024 ** 3:.2f} GiB"
    elif used >= 1024 ** 2:
        used_s = f"{used / 1024 ** 2:.1f} MiB"
    else:
        used_s = f"{used / 1024:.0f} KiB"
    pct = (100.0 * used / maximum) if maximum else 0.0
    return {"entries": stats.get("entries", 0),
            "tags": stats.get("tags", 0),
            "used_human": used_s,
            "used_pct": round(pct, 1),
            "hits": stats.get("hits", 0),
            "misses": stats.get("misses", 0)}
