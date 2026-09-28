# Render caching

Reel Studio can skip work it has already done. With the optional
[survey-cache](https://github.com/crieck2010/survey-cache) peer
installed (`pip install git+https://github.com/crieck2010/survey-cache.git`),
the pipeline reuses **rendered frame batches** and **encoded MP4s**
instead of re-rendering identical reels.

## What is cached, and when it hits

Two levels, checked in order on every run (`run_pipeline(..., cache=...)`):

1. **Frame batch** — after fetching, the pipeline fingerprints *everything
   that can change the pixels* and looks the batch up:
   - the parsed spec dict (description, bbox, variable, dates, title, …)
   - digests of the fetched arrays (SHA-256 of the bytes + shape + dtype —
     the key never embeds gigabytes of data)
   - the exact `render_viz` kwargs (colormap, story captions, …)
   - the platform canvas, the style preset, the platform name
   - the installed survey-viz version

   A hit materializes the PNGs into the run's `frames/` directory and
   `render_viz` is never called. **New satellite data = different array
   bytes = honest miss and a fresh render.** The fetch itself always
   re-runs: the cache answers "given this data, are the frames already
   drawn?", never "is the data fresh?".

2. **Encoded video** — keyed by the frame *content* keys (SHA-256 of each
   PNG) plus the encode inputs: preset, title, motion, the audio file's
   content hash, and the survey-animate version. A hit materializes the
   MP4 and ffmpeg is never run.

Because stored artifacts are content-addressed, an identical PNG or MP4
appearing in two different reels is stored **once**; the semantic tags
(`reel-studio/frames/<batch-key>`, `reel-studio/video/<video-key>`) just
point at the content.

## Where it lives

- Default directory: `~/.reel-studio/cache`
  (override with `REEL_STUDIO_CACHE_DIR`; the scheduled-run ticker honors
  the same variable).
- Default budget: **20 GiB**, LRU-evicted (least-recently-used first) with
  optional TTL support in the engine. Inspect with
  `python -m cachex --root ~/.reel-studio/cache stats`, prune, verify, or
  clear from the same CLI.
- Disable entirely with `REEL_STUDIO_CACHE=0` (the app also has a
  per-run "Use render cache" checkbox).

## In the app

The Run step shows a **Render cache** expander: install hint when the
peer is missing; otherwise the toggle, live usage (objects, tags, bytes,
hit/miss counters), the cache location, and a **Clear render cache**
button. After a run, the result step reports "frames from cache ·
video freshly encoded" (or whichever combination applied), and the
provenance JSON records the full cache report (`enabled`, `frame_hit`,
`video_hit`, tag names).

Batch runs and scheduled jobs use the same cache automatically — a
daily scheduled job over unchanged inputs becomes nearly free.

## Key-building lives here, storage lives there

Deliberate split, mirroring the other peers:

- `survey-cache` (`cachex`) knows **nothing** about reels: bytes in,
  bytes out, content keys, tags, eviction. Any future tool (a fetch
  cache in survey-currents, a tile cache, …) can use the same engine.
- `studio/caching.py` knows what a reel *is*: it builds the key
  payloads (`frame_batch_key`, `video_key`), digests numpy arrays, and
  stores/restores frame batches and videos through the engine.

## Honest limits

- The cache is **local disk**: no network sync, no shared cache between
  machines. Two PCs render independently.
- The engine assumes a **single writer** per cache directory (atomic
  file ops keep concurrent readers safe; don't run two Studio instances
  sharing one cache dir — in practice each machine has its own).
- Cache keys include peer *versions*, so upgrading survey-viz or
  survey-animate intentionally invalidates old entries (they age out via
  LRU rather than being deleted up front).
- A corrupted entry (hash mismatch on read) is quarantined and treated
  as a miss — the run re-renders instead of failing.
