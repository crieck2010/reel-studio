# Changelog

All notable changes to reel-studio. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.8.0] - 2026-09-28

### Added
- **Smarter render caching** (new survey-cache peer, optional seventh
  engine): the pipeline now fingerprints its render inputs (spec dict,
  SHA-256 digests of the fetched arrays, render kwargs, platform
  canvas, style preset, survey-viz version) and reuses cached frame
  batches instead of re-rendering — then fingerprints the encode inputs
  (frame content keys, preset, title, motion, audio content hash,
  survey-animate version) and reuses cached MP4s instead of re-running
  ffmpeg. New satellite data means new array bytes, which means an
  honest cache miss and a fresh render; the fetch itself always
  re-runs. Stored artifacts are content-addressed, so an identical PNG
  in two reels is stored once.
- `studio/caching.py`: the consumer-side bridge — key builders
  (`frame_batch_key`, `video_key`), numpy-aware input digesting,
  store/restore helpers for frame batches and videos, cache location
  (`~/.reel-studio/cache`, `REEL_STUDIO_CACHE_DIR` override),
  `REEL_STUDIO_CACHE=0` kill switch. `run_pipeline(..., cache=None)`
  accepts the cache; batch runs and scheduled jobs use it
  automatically. The Run step shows a Render-cache expander (toggle,
  live usage stats, clear button, install hint); the result step and
  provenance report which stages hit.
- `docs/CACHING.md`: key design, invalidation discipline, storage
  layout, honest limits (local disk only, single-writer assumption,
  version-keyed invalidation).
- `Update reel-studio.bat` now upgrades all seven peers and prints all
  seven installed versions.
- Without the survey-cache peer every run renders and encodes from
  scratch — same optional-peer pattern as the other engines.

## [0.7.0] - 2026-09-28

### Added
- **Scheduled generation** (new survey-schedule peer, optional sixth
  engine): step 6, **Scheduled generation**, turns the reel factory on
  autopilot. A job = a description + a snapshot of the current run
  settings (motion, audio, story captions, colormap, platform, style
  preset) + a schedule — `cron:<5 fields>`, `every:<n>s|m|h|d|w`, or
  `once:<ISO datetime>` — with a plain-words preview before creation,
  per-job timezone, missed-run policy (`skip`: one ledger entry, never
  backfilled; `run-once`: execute once at the next tick), and
  in-run retries. Jobs live as JSON files in `~/.reel-studio/jobs`
  (`REEL_STUDIO_JOBS_DIR` overrides); the Jobs list shows each next
  run with enable/disable and delete; Recent runs tails the ledger.
- `studio/scheduler.py`: the UI-free bridge — `execute_reel_job`
  (parses the description exactly like an interactive run, maps
  settings through the batch `JOB_SETTING_KEYS` contract, runs
  `run_pipeline` into the runner's directory, returns
  `video_path`/`n_frames` for the ledger) and the ticker entrypoint
  `python -m studio.scheduler run-due` (one tick, then exit; wire to
  Task Scheduler on Windows or cron on Linux, every minute). Honest
  limit, stated in the UI and docs: the PC must be on at run time.
- `Update reel-studio.bat` now upgrades all six peers and prints all
  six installed versions.
- Without the survey-schedule peer the Schedule step shows the exact
  install command and one-off/batch generation keep working — same
  optional-peer pattern as survey-layout/survey-style.

## [0.6.0] - 2026-09-28

### Added
- **Reusable style presets** (new survey-style peer, optional fifth
  engine): the Aesthetics step opens with a style-preset picker —
  Reel Dark, Reel Light, Midnight Ocean, Field Notes, Storm Chaser,
  Creator Brand Kit — suggesting the preset that fits the parsed
  variable. **Apply preset** fills the style, colormap, title, footer
  caption, and underlay controls in one click (the Creator kit asks
  for the channel name used in its `©` footer); every control stays
  tweakable afterwards. The preset name flows through
  `run_pipeline(style_preset=...)`, batch job settings snapshots,
  and provenance (`render.style_preset`); manual edits afterwards are
  recorded as hand-tuned (`style_preset: null`), and the result step
  shows the preset or "hand-tuned". Without the survey-style peer the
  picker is replaced by the install command and the manual controls
  keep working — same optional-peer pattern as survey-layout.
- `studio/styling.py`: UI-free preset helpers (names, labels,
  suggestion, `{channel}` detection, apply) taking the peer module as
  an argument so tests never hard-import it.
- `Update reel-studio.bat` now upgrades all five peers and prints all
  five installed versions.

## [0.5.0] - 2026-09-28

### Added
- **Platform aspect ratios + safe zones** (new step 2, "Platform"):
  pick the target platform — TikTok, Instagram Reels, YouTube Shorts,
  X portrait, square, widescreen — and frame size plus the
  title/map/chart/caption/footer regions follow the platform's aspect
  ratio and measured safe zones via the new survey-layout peer
  (`layout.to_viz_canvas`, consumed by survey-viz >= 0.18.0's
  `render_viz(canvas=...)`). A schematic shades the platform's
  interface chrome (top navigation/status, right action rail, bottom
  captions/channel/progress) in red with plain-words labels; the chrome
  measurements are documented as community-measured approximations,
  not official platform specs. Earthquake reels automatically use the
  `quake` layout flavor (largest-events ranking panel); everything else
  uses `standard`. Needs survey-layout installed and survey-viz >=
  0.18.0 — otherwise `PeerTooOldError` with the exact install/upgrade
  command; without the layout peer the platform step offers only the
  legacy 1080×1920 layout and claims no safe-zone support.
- `platform` flows through `run_pipeline()`, `RunResult`, batch job
  settings snapshots, and provenance (`render.platform`,
  `render.platform_flavor`, `render.canvas` with width/height/regions,
  `encode.preset`); the encode preset follows the canvas (portrait →
  survey-animate `"reel"`, square → `"square"`, landscape → `"wide"`;
  frames are aspect-fit, never stretched); the result step shows
  platform + dimensions.
- `Update reel-studio.bat` now upgrades all four peer engines
  (survey-layout added).

## [0.4.0] - 2026-09-28

### Added
- **Cinematic motion** (new step 3, "Cinematic motion & audio"):
  enable/disable plus full user control — zoom mode (in/out/off) and
  zoom speed, pan direction (8 compass points + off) and pan speed,
  smooth crossfade transitions on/off with crossfade-step count.
  Encoded at video time by survey-animate >= 0.2.0 (`MotionSpec`);
  the data frames are unchanged. Refinement-phase motion intents from
  survey-viz >= 0.17.0 ("add a slow zoom in", "pan left during the
  video", "no camera motion") merge into these settings and each
  applied change is shown with its reason. Older survey-animate peers
  get the upgrade hint, never a crash.
- **Batch queue** (new step 4, `studio/batch.py`): queue several
  descriptions (one per line) and generate them unattended, one after
  another. Each job snapshots your current settings (motion, audio,
  captions, colormap) at enqueue time, gets an isolated
  `job-<nn>-<slug>/` output folder, and carries its own status —
  a failed job is recorded with its error + traceback and the queue
  keeps going, so completed reels are never lost. Per-job video
  preview + download on completion.
- **Audio muxing** (step 3): attach your own audio file (mp3/wav/ogg/
  flac/m4a/aac/opus/wma) — muxed under the reel via survey-animate >=
  0.2.0 (AAC for MP4, trimmed to the video length with `-shortest`).
  Needs survey-animate >= 0.2.0; older peers get the upgrade hint.
  A missing audio file fails fast before the fetch, not after it.
- **Data-driven story captions** (step 2 checkbox + step 6 display):
  `story_captions=True` flows to survey-viz >= 0.17.0's `render_viz`,
  which burns peak/trend captions onto the frames and records the
  events in the frame manifest; the app lists them under the finished
  reel with their frame ranges. Categorical products (storm tracks /
  streamgages / earthquakes) keep their fixed scientific encodings and
  say so instead of captioning. Needs survey-viz >= 0.17.0.
- `pipeline.PeerTooOldError`: capability mismatches now raise a
  dedicated error carrying the exact `pip install --upgrade` command;
  capability detection uses signature inspection (not version
  strings), so test doubles and mislabeled peers behave honestly.
  Provenance records motion settings, the audio path, and the
  captions flag.
- Tests: `tests/test_batch.py` (8), `tests/test_pipeline_features.py`
  (11), `tests/test_app_helpers.py` (7) — 275 non-network tests total.

## [0.3.0] - 2026-09-27

### Added
- **Refine in plain language** (new section in step 3, before the Run
  button): describe the changes you want ("zoom in on the Gulf of
  Mexico", "use a warmer colormap", "title it 'Gulf Heat'", "run it
  from 2015 to 2020", "switch to light mode") and the app applies
  them to the parsed spec via `viz.refine_spec` — showing every
  applied change (old → new + reason) and every unparsed note before
  generation. Needs survey-viz >= 0.16.0; older peers get the upgrade
  hint, never a crash.
- **Copy the look of a reel** (new section in step 2): paste a reel
  URL or upload a screenshot and the app reads the reference's
  *color mood* (overall brightness + dominant hues) via
  `viz.suggest_aesthetic`/`viz.analyze_image`, shows the measured
  palette and the suggested dark/light style + colormap with
  explanatory notes, and applies it on confirmation. Honest by
  design: only the color mood is copied — fonts, layouts, and
  transitions can't be read from a thumbnail; the video itself is
  never downloaded (social platforms keep it behind login walls), so
  a URL falls back to the page's preview thumbnail and a screenshot
  upload is the most reliable input. Categorical products keep their
  fixed scientific colors even when a reference suggests a colormap.
  Needs survey-viz >= 0.16.0; older peers degrade gracefully.
- `tests/test_refine_look_ui.py`: 8 stubbed-Streamlit tests covering
  both flows (apply/unparseable/gated refinements; upload/URL/error/
  gated look-copying) plus a real-integration test against the
  installed survey-viz.

### Fixed
- **`Update reel-studio.bat`**: peer checkouts live *next to*
  reel-studio (siblings under the CODE folder), but the script probed
  `%%P\.git` *inside* reel-studio after `cd`-ing into it — the
  existence check, `git -C`, and `pip install` now use `..\%%P`.
  Without the fix, sibling checkouts were silently ignored and peers
  were always reinstalled from GitHub. Pinned by an updated
  `tests/test_updater.py`.

## [0.2.1] - 2026-09-27

### Added
- **`Update reel-studio.bat`**: one-click Windows updater (double-click,
  no command line). Git-pulls reel-studio, then upgrades the three peer
  engines — from a local checkout when one sits next to the repo,
  otherwise straight from GitHub — and prints the installed peer
  versions for confirmation. Guards against a missing git with the
  `winget install Git.Git` fix. Content-covered by
  `tests/test_updater.py` (the script itself can't execute on Linux).
- README: new "Updating" section; the Run walkthrough updated to the
  4-step flow.

## [0.2.0] - 2026-09-27

### Added
- **Aesthetics step** (new step 2 in the app, between Parse and Run):
  - *Style* picker: Dark (`reel-dark`, default) / Light.
  - *Title* text input, seeded from the parsed title.
  - *Footer caption* (optional): a custom line prepended to the frame
    footer; per-renderer honesty wording (e.g. the earthquake catalog's
    "observed events — not a forecast") is always kept. Needs
    survey-viz >= 0.15.0; older peers degrade gracefully (caption is
    dropped, never a crash).
  - *Basemap underlay* checkbox (GEBCO tint + Natural Earth coastlines).
  - *Colormap* picker: Automatic plus the 30 `viz.CURATED_CMAPS` names
    (needs survey-viz >= 0.15.0, otherwise the app says so and keeps
    the variable default). Applies to continuous data maps only — for
    storm tracks, streamgages, and earthquakes the app explains that
    their fixed scientific colors are kept.
- `pipeline.run_pipeline(..., cmap=None)`: UI-neutral colormap
  passthrough to `peers.render_viz`. `cmap=None` (default) is never
  passed, so older survey-viz peers and existing duck-typed fakes keep
  working untouched.

### Changed
- App steps renumbered: 1 Parse · 2 Aesthetics · 3 Run · 4 Your reel.
- Minimum survey-viz for the new aesthetics controls: v0.15.0
  (peer table in README updated).

## [Unreleased]

### Added
- USGS streamflow flow-through (needs survey-viz 0.12.0 +
  survey-currents 0.13.0): `plan_fetch` routes source `"usgs"` as
  routable in any region for `variable="streamflow"` (a `usgs` pin on
  any other variable is refused as `bad_variable`); `sea-level` stays
  an honest `no_adapter` refusal (satellite altimetry, not GRACE).
  `run_pipeline` calls `fetch_usgs(bbox, start, end)` on the spec bbox
  directly and hands the `GageField` to `render_viz` as its
  `to_dict()` form — never through `_field_to_dict`. A requested
  `"tp"` precipitation overlay is fetched via `_fetch_streamflow_context`
  (IMERG observed daily totals preferred, ERA5 fallback) and attached
  as `overlay_grids` dicts; an unavailable context degrades gracefully
  (the renderer records `"absent"` per overlay). Provenance under
  `fetch["usgs"]`, `series=None`. `wire_peers` exposes `fetch_usgs`
  lazily (honest upgrade message naming `survey-currents>=0.13.0` on
  older peers). Documented in `docs/INTEROP.md`.
- IBTrACS storm-tracks flow-through (needs survey-viz 0.10.0 +
  survey-currents 0.11.0): `plan_fetch` routes source `"ibtracs"` as
  fetchable in any region for `variable="storm-tracks"` (an `ibtracs`
  pin on any other variable is refused as `bad_variable`).
  `run_pipeline` calls
  `fetch_ibtracs(bbox, start, end, storm_name=spec.storm_name or None)`
  on the spec bbox directly, applies `storm_rank="strongest"` +
  `storm_top_n` (default 5) via `StormField.rank_by_intensity()`
  (lifetime maximum sustained wind), and hands the `StormField` to
  `render_viz` as its `to_dict()` form — never through
  `_field_to_dict`. A requested ERA5 overlay ("… with the wind
  field") is fetched via `fetch_era5` and attached as `overlay_grids`
  dicts; an unavailable context degrades gracefully (the renderer
  records `"absent"` per overlay). Provenance under
  `fetch["ibtracs"]`, `series=None`. `wire_peers` exposes
  `fetch_ibtracs` lazily (honest upgrade message naming
  `survey-currents>=0.11.0` on older peers). Documented in
  `docs/INTEROP.md`.
- GEBCO topography/bathymetry flow-through (needs survey-viz 0.9.0 +
  survey-currents 0.10.0): `plan_fetch` routes source `"gebco"` as
  fetchable in any region for `bathymetry`/`elevation` (a `gebco` pin
  on any other variable is refused as `bad_variable`);
  `variable="country-borders"` is refused honestly as `no_adapter`
  before the region fall-through (Natural Earth vectors are a
  cartographic underlay, not a data variable). `run_pipeline` calls
  `fetch_gebco(bbox, resolution=...)` on the spec bbox directly
  (resolution picked from the bbox span, <= ~720 cells per axis),
  renders with `series=None`, and records `fetch["gebco"]`
  provenance. `wire_peers` exposes `fetch_gebco` (optional — honest
  upgrade message on survey-currents < 0.10.0). Documented in
  `docs/INTEROP.md`.
- ERA5 atmosphere flow-through (needs survey-viz 0.3.0 +
  survey-currents 0.4.0): `plan_fetch` routes source `"era5"` as
  fetchable in any region for `wind`/`msl`/`t2m`/`tp`;
  `run_pipeline` fetches `[variable] + overlays` in one CDS call
  (`stride_hours=24` default), carries `overlay_grids` into
  `render_viz` (isobar contours for the storm combination), renders
  with `series=None`, and records `source="era5"` with field
  provenance under `provenance["fetch"]["era5"]`. `wire_peers`
  exposes `fetch_era5` (optional — honest upgrade message on
  survey-currents < 0.4.0). Documented in `docs/INTEROP.md`.
- GPM IMERG precipitation flow-through (needs survey-viz 0.7.0 +
  survey-currents 0.8.0): `plan_fetch` routes source `"imerg"` as
  fetchable in any region for variable `tp` only (other variables are
  `bad_variable`); `run_pipeline` calls
  `fetch_imerg(bbox, start, end, accumulate="daily", run="late",
  stride_days=...)` — relying on the adapter defaults — renders with
  `series=None`, and records field provenance under
  `provenance["fetch"]["imerg"]`. `wire_peers` exposes `fetch_imerg`
  lazily (honest upgrade message naming `survey-currents>=0.8.0` on
  older peers). `_field_to_dict` adapts the `RainField` through the
  generic 3D-`values` path (mm/day totals, NaN for missing) — zero
  renderer changes. Documented in `docs/INTEROP.md`.

## [0.1.0] - 2026-09-26

First release: plain-English description → finished vertical reel MP4, 100% local.

### Added

* `app.py` — Streamlit app (`streamlit run app.py`): engine-status panel,
  Parse step (description → `VizSpec` JSON confirmation), Run step
  (progress bar → embedded player + Download MP4 + provenance expander).
  All peer imports optional with exact `pip install git+https://...` fix
  commands when a peer is missing. Headless-safe import
  (`import app` works with or without streamlit installed).
* `studio/pipeline.py` — UI-free orchestration with injected peer
  callables: `plan_fetch` (fetchability without network),
  `parse_with_fallback` (deterministic parse + optional one-shot LLM
  assist; the original parse error wins on failure), `run_pipeline`
  (GLSEA fetch → frame render → MP4 encode → `RunResult` with full
  provenance).
* `studio/peers.py` — optional peer loading, `MissingPeerError` carrying
  the fix command, `wire_peers()` building the callables namespace.
* `studio/llm_assist.py` — optional `LLM_API_KEY` assist: one
  OpenAI-compatible chat-completions POST via stdlib `urllib` only,
  response validated with `VizSpec.from_dict`; never required.
* `studio/demo.py` — offline demo over 3 descriptions, including the
  honest unfetchable-region path (Gulf of Mexico).
* Honest refusals: unfetchable regions and unsupported variables produce
  clear messages naming the missing adapter instead of crashing.
* Interop handling for two quirks discovered against the real peer code:
  frames **directory** (not the viz manifest) passed to
  `render_video` (animate rejects viz's manifest schema), and
  `GlseaField` ISO-datetime `times` normalized to date-only strings
  (viz's date coercion can't parse them). Plus bbox clamping for
  lake-superior (gazetteer lon_min -92.5 vs GLSEA grid floor
  -92.4199507342304), recorded in provenance.
* Docs: `README.md`, `docs/APP.md` (user guide), `docs/ARCHITECTURE.md`,
  `docs/INTEROP.md` (peer contracts + quirks).
* 74 pytest tests, fully offline: parser integration (real survey-viz),
  VizSpec round-trip, peer degradation, pipeline with fake peers, LLM
  assist with mocked urllib, headless app smoke test, demo assertions,
  and a real-peers end-to-end (synthetic GLSEA field → real render →
  real encode → valid MP4).

### Limitations

* Fetchable regions: the 5 Great Lakes only. Fetchable variable: `sst` only.
* Real fetches need network access to NOAA ERDDAP and `netCDF4`.
