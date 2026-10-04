# Changelog

All notable changes to reel-studio. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [0.21.0] - 2026-10-04

### Added
- **Surface preset wiring (survey-viz 0.28.0)** — `run_pipeline` now
  accepts `surface_contours` (default `True`), `surface_contour_levels`
  (`None` = auto), `surface_shadow` (`True`), `surface_smoothing`
  (`0.0` = off), and `surface_scale` (`"linear"` / `"log"`), plus
  `counter` (a `{"stat", "unit", "label"}` dict passed through
  verbatim; viz validates it and records it not-applied off the
  Surface preset) and `headline_beats` (a list of
  `(fraction_or_iso_timestamp, text)` pairs). The `"surface"` preset
  itself flows through the existing `aesthetic_preset` parameter.
  Every new kwarg is forwarded only when the installed viz supports
  it (signature inspection via the existing `_supports_kw` pattern):
  older peers keep working, unsupported values are dropped, and the
  drop is recorded under `provenance["render"]["surface" | "counter"
  | "headline_beats"]`. At all-defaults nothing is forwarded and no
  provenance keys appear — defaults-off output is byte-identical to
  v0.20.1.
- **`headline_beats="auto"`** — drafts beats from the fetched field via
  the optional survey-narrate peer (`story_facts` +
  `headline_beats`, reusing the run's already-computed story facts
  when available). These are **drafts for human approval/editing —
  they restate computed facts only**. On any failure (peer missing,
  facts malformed, viz too old) the run proceeds with no beats and
  records `status: "fallback"` / `fallback: true` in provenance;
  beats are never fabricated.
- **App UI (Step 3 · Aesthetics)** — when the Surface preset is
  picked: Surface contours / Surface shadow toggles, Surface
  smoothing slider (0.0 default), Surface scale select (linear/log),
  and a Contour levels text field (comma-separated floats, empty =
  auto). A **Counter & headline beats** block adds the Counter toggle
  (off by default) with stat picker (sum/mean/max) and unit/label
  fields, a multiline headline-beats field
  (`fraction|text`, e.g. `0.0|Opening headline`), and an
  **Auto-draft headline beats** checkbox with an inline note that auto
  beats are survey-narrate drafts for review/editing. New headless-safe
  parsers `parse_headline_beats_text` / `parse_contour_levels_text`
  raise `ValueError` naming the bad line/entry; the Run step warns and
  falls back to `None` instead of crashing. Peers older than
  survey-viz 0.28.0 show the upgrade hint and the new controls stay
  inert.

### Fixed
- Version drift: `app.py` `APP_VERSION` was stuck at `0.17.0`; it now
  matches `studio.__version__` (`0.21.0`). There is no `pyproject.toml`
  in this repo (install is via `requirements.txt` + editable peers);
  the only version strings are `studio/__init__.py` and `app.py`.

### Tests
- New `tests/test_pipeline_surface.py` (11 tests: pass-through of
  every new kwarg, counter verbatim, explicit beats, defaults-off
  byte-identity on strict and new peers, old-peer drop+record,
  auto-beats with/without/broken narrate) and
  `tests/test_surface_ui.py` (20 tests: beat/level parsers incl.
  blank lines, malformed lines, out-of-range fractions; surface and
  counter/headline sections on new and old peers via a Streamlit
  stub). Dev tree: 558 passed, with the same 3 failed + 12 errors
  that pre-exist on v0.20.1 (publish-schedule peer missing from the
  test venv; two updater `.bat` assertions) — no new failures.

## [0.20.1] - 2026-10-03

### Fixed
- `Update reel-studio.bat` no longer hangs on an interactive
  "Deletion of directory .git\objects\... failed, try again? (y/n)"
  prompt. On Windows, `git pull`'s automatic `gc` can block forever
  when OneDrive or Defender holds a lock on `.git` files. All pulls in
  the updater now run with `git -c gc.auto=0`, keeping the update
  fully non-interactive. (Housekeeping can still be run by hand with
  `git gc` whenever needed.)

## [0.20.0] - 2026-10-01

### Added
- **Autopilot (automatic first-pass)** orchestration in
  `run_pipeline(..., autopilot=False, autopilot_candidates=None,
  autopilot_qa_policy="drop")`, driven by the survey-autopilot engine
  (v0.1.0, required — fail fast, never silently degrade):
  1. **Phenomenon-aware stride before the fetch** — `(spec.variable,
     source)` maps to a phenomenon (`wind`/`gfs-wind` → synoptic,
     `current`/`currents`/`ofs-thredds`/`oscar` → tide,
     `sst`/`seaice`/`sea-ice` → seasonal,
     `quake`/`comcat`/`hurricane`/`ibtracs`/`fires`/`firms` → event,
     `precip`/`imerg` → synoptic); the recommendation overrides the
     caller's `stride_days`/`stride_hours` (original vs applied
     recorded), and `forecast_hours` for `source="gfs-wind"` only.
     Precedence: `autopilot.stride.recommend_stride`, then
     `timescales.recommend_stride`, else `RuntimeError` with the
     install hint. Unknown pairs keep the caller's strides and record
     `{"via": "default", "reason": "no phenomenon mapping"}`.
  2. **QA gate after the fetch** — the fetched dict is checked for
     all-NaN / out-of-plausible-range timesteps and temporal gaps,
     then repaired per `autopilot_qa_policy` (`"drop"` /
     `"interpolate"` / `"fail"`; anything else is a `ValueError`,
     `"fail"` lets the engine's `QAError` propagate). Works on the
     `grids`/`times` dict form and on the normalized `values`/`times`
     form (wrapped as a single grid, unwrapped after repair);
     non-gridded fields (storm tracks, earthquake events,
     streamgages) skip the gate with a recorded reason. Every QA
     issue/action lands in `provenance["autopilot"]["qa"]`.
  3. **Honest render knobs** — `robust_scale=True`,
     `strand_count="auto"`, `salience_labels=True` (survey-viz >=
     0.27.0), each guarded by signature inspection so older peers
     skip with a record instead of breaking.
  4. **Thumbnail composition search** — one first-timestep thumbnail
     per candidate over the default `dark_strands`/`dark_flow` ×
     `None`/`"auto"` × `void_black`/`subtle_land` grid (overridable
     via `autopilot_candidates`), scored by the engine's
     `search_composition`; the winner's preset/rotation/basemap feed
     the full render, overriding manual choices at render time. A
     failed candidate is recorded as `{"error": ...}` and excluded
     from ranking; if every candidate fails, the last error is
     raised.
  5. **Provenance** — `provenance["autopilot"]` records phenomenon,
     recommended vs applied stride, QA issues/actions, render knobs,
     and the composition winner + full score table (JSON-safe).
  `autopilot=False` (default) is byte-identical to 0.19.2: no new
  kwargs are passed and the provenance carries no `"autopilot"` key.
- **"Autopilot (automatic first-pass)" checkbox** (Step 3 ·
  Aesthetics, default OFF). Manual preset/rotation/basemap widgets
  stay enabled — autopilot overrides them at render time.
- `tests/test_pipeline_autopilot.py`: 7 tests (stubbed engine, no
  network) covering the byte-identical default, full orchestration
  order, QA action recording, the fail-fast `RuntimeError`, bad
  policy `ValueError`, `QAError` propagation on `"fail"`, and the
  non-gridded skip path.

## [0.19.2] - 2026-10-01

### Added
- `run_pipeline(..., region_name=None)`: human-readable region label for
  the narrative facts/captions (e.g. `"North America"`). Defaults to
  `spec.region_key` when unset, so existing callers are unaffected.

## [0.19.1] - 2026-10-01

### Added
- **Narrative story facts on `RunResult.story`** (survey-narrate peer,
  optional). `run_pipeline` computes `narrate.story_facts(field)` after
  the fetch and attaches `{"status": "ok", "facts", "caption"}` to the
  result and the run manifest/provenance; when the peer is missing it
  records `{"status": "unavailable", ...}` and the render proceeds
  untouched. Lets callers (e.g. the daily reel email) quote
  data-derived narrative stats — peak speed with location/time, means,
  temperature ranges — in the mapped.earth caption style.

## [0.19.0] - 2026-10-01

### Added
- **`bivariate` pipeline kwarg + "Bivariate encoding (brightness =
  speed)" checkbox** (survey-viz >= 0.26.0). `run_pipeline(bivariate=...)`
  forwards into `render_viz` only when not None, mirroring the
  `landmask` pattern exactly; `bivariate=None` (default) leaves the
  peer default and passes nothing, so older peers keep working
  untouched. An explicit choice on a survey-viz peer older than 0.26.0
  raises the honest `PeerTooOldError` upgrade message. Because it
  lands in `render_viz_kwargs`, the choice is part of the frame-batch
  fingerprint and invalidates the cache. Step 3 (Aesthetics) gains the
  checkbox in the Basemap & strands group next to "Clip strands to
  land", default checked: checked leaves the peer default (nothing
  forwarded — old peers untouched), unchecked forwards
  `bivariate=False` to restore the flat single-variable look.
- **`forecast_hours` pipeline kwarg** for `source="gfs-wind"`
  (survey-currents >= 0.17.0): pass `forecast_hours=(0, 6)` to add
  forecast-hour steps per sampled day, so the reel steps through the
  forecast horizon within each day. Validated fail-fast as a
  non-empty tuple/list of ints in 0..120 (`ValueError` otherwise) and
  forwarded to `fetch_gfs_wind` only when set — `None` passes
  nothing, so older peers keep working untouched. A non-None value
  with any other source raises `ValueError` instead of being
  silently ignored (other sources have no forecast-hour axis; OFS
  nowcast/forecast selection lives in the peer). Pipeline/CLI level
  only — no Streamlit control. An old fetch fn without the kwarg
  raises the honest `PeerTooOldError` naming >= 0.17.0.
- **`source="ofs-thredds"` routing** (survey-viz >= 0.26.0,
  survey-currents >= 0.18.0): NOAA OFS surface currents via the
  keyless CO-OPS THREDDS OPeNDAP subset — `plan_fetch` routes it for
  `variable="currents"` in any region; the execute branch calls
  `fetch_ofs_thredds(ofs_code, bbox, start, end)`. The OFS model pin
  is explicit and required: `run_pipeline(ofs_code=...)` with one of
  the known codes (`SSCOFS`, `CBOFS`, `WCOFS`, `NGOFS2`, `GOMOFS`,
  `DBOFS`, `SFBOFS`, `LEOFS`, `LMHOFS`, `LOOFS`, `LSOFS`, `CIOFS`) —
  there is no global default (each model covers a fixed coastal
  region), so a missing pin raises `ValueError` and a bbox outside
  the pinned model's domain fails honestly at fetch time. The field
  is `CurrentField`-compatible and feeds survey-viz's currents
  render path unchanged (scalar speed via `_field_to_dict`, like
  OSCAR/CMEMS). `wire_peers` exposes `fetch_ofs_thredds` (None when
  the peer is older than 0.18.0 — the pipeline then raises the honest
  upgrade message). 28 new tests in
  `tests/test_pipeline_richness_v19.py`.

### Changed
- README's peer-versions table pins survey-viz v0.26.0 and
  survey-currents v0.18.0; the supported-sources table gains NOAA OFS
  (15 sources total); docs/INTEROP.md gains the bivariate, OFS
  THREDDS, and forecast_hours contracts.

### Honest limitations
- Bivariate gain × strand alpha-ramp interaction is inherited from
  survey-viz 0.26.0: at very low speeds the brightness encoding can
  push strands toward invisible — the pipeline cannot tune it.
- `forecast_hours` cost: each extra hour multiplies the fetch (~110
  KB per 0.25° GFS step for a North-America subregion, ~2.4 MB
  worst-case full-globe fallback) and the frame count — the caller
  owns the frame budget.
- THREDDS keeps only ~31 days of OFS output, and `ofs-thredds` is
  honestly ineligible for derived climatology products for that
  reason; a mismatched `ofs_code` pin returns nothing for bboxes
  outside its fixed coastal domain.

## [0.18.1] - 2026-10-01

### Added
- **`landmask` pipeline kwarg + "Clip strands to land" checkbox**
  (survey-viz >= 0.25.2). `run_pipeline(landmask=...)` forwards into
  `render_viz` only when not None, mirroring the `strand_count` /
  `strand_linewidth` pattern; an explicit choice on a survey-viz peer
  older than 0.25.2 raises the honest `PeerTooOldError` upgrade
  message. Step 3 (Aesthetics) gains the checkbox next to the strand
  sliders, default checked: checked leaves the peer default (clipped,
  nothing forwarded — older peers keep working untouched), unchecked
  forwards `landmask=False` to draw strands everywhere.

## [0.18.0] - 2026-10-01

### Added
- **`source="gfs-wind"` routing** (survey-viz >= 0.25.0,
  survey-currents >= 0.16.0): NOAA GFS 10-m winds via the keyless
  NOMADS GRIB filter — fetchable in any region, daily 00z f000
  analysis, 0.25° grid, with the 2-m air temperature coloring the
  strands (the warming.watch convention). Only serves
  `variable="wind"`; anything else is an honest `bad_variable` plan.
  `wire_peers` exposes `fetch_gfs_wind` (None when the peer is older
  than 0.16.0 — the pipeline then raises the honest upgrade message).
  Derived climatology products are honestly ineligible for this
  source: NOMADS keeps only ~10 days of GFS, so a multi-year baseline
  is impossible.
- The returned `GfsWindField.to_dict()` (grids `u10`/`v10` + °F
  `air_temperature`) is handed straight to `render_viz`, so the
  survey-viz `dark_strands` preset renders GFS winds with zero
  renderer changes.
- 11 new tests in `tests/test_pipeline_gfswind.py`, including a real
  survey-viz `dark_strands` render of a synthetic GFS field proving
  real PNG frames (not just plumbing).
- `studio.__version__` bumped to 0.18.0 (was stale at 0.10.0 since
  v0.1.0 — now maintained per release).

### Changed
- README's peer-versions table pins survey-currents v0.16.0; the
  supported-sources table and docs/INTEROP.md gain the GFS wind
  contract (14 sources total).

## [0.17.0] - 2026-10-01

### Added
- **Basemap styles + strand controls** (survey-viz >= 0.24.0,
  survey-aesthetics v0.2.0). Step 3 (Aesthetics) gains a **Basemap &
  strands** group inside the preset section, every choice
  user-overridable per the standing maximum-control rule:
  - **Preset picker** gains **Dark strands** — advected particle
    trails on black for currents/wind (the warming.watch look);
    variable/preset mismatch warns in the UI and fails fast at run
    time, same as the other presets.
  - **Basemap style**: Preset default / **Void black** / **No
    basemap** / **Subtle land** — how land is drawn under the data.
  - **Strand controls** (shown for Dark strands): **Strand count**
    slider (500–10000, default 3000) and **Strand line width**
    slider (0.5–3.0 pt, default 1.4).
  - Sliders at their defaults pass nothing — the survey-viz defaults
    apply — so older peers keep working untouched and the choice stays
    version-keyed in the cache fingerprint.
- `run_pipeline()` accepts `basemap`, `strand_count`, and
  `strand_linewidth`; explicit choices ride in `render_viz_kwargs`, so
  the survey-cache frame-batch fingerprint already covers them
  (style/strand changes invalidate the cache — proven by new
  fingerprint tests). Older survey-viz peers raise `PeerTooOldError`
  with the upgrade command instead of a traceback; defaults pass
  nothing, so legacy runs are untouched. The new controls stay inert
  with an upgrade hint on survey-viz < 0.24.0.

## [0.16.0] - 2026-09-30

### Added
- **Place labels** (survey-viz >= 0.23.0, twelfth peer surface:
  survey-gazetteer). Step 3 (Aesthetics) gains a **Place labels**
  control group inside the preset section — **Off** / **Auto** /
  **Custom**, every choice user-overridable per the standing
  maximum-control rule:
  - **Auto**: max-labels slider + min-population input; the
    survey-gazetteer peer fetches city/town labels for the region once
    per reel (Natural Earth 1:10m populated places). At the default
    slider values nothing is passed — the survey-viz default (auto
    labels when a preset is active) applies, so older peers keep
    working untouched.
  - **Custom**: place names, one per line, resolved via
    `gazetteer.search()` with city + region + country disambiguation
    shown in the UI so the user picks the right "Rochester"; names
    with no match are skipped with a warning, never a crash.
  - **Off**: no labels.
- `run_pipeline()` accepts `place_labels`, `max_labels`, and
  `min_population`; explicit choices ride in `render_viz_kwargs`, so
  the survey-cache frame-batch fingerprint already covers them (label
  changes invalidate the cache — proven by new fingerprint tests).
  Older survey-viz peers raise `PeerTooOldError` with the upgrade
  command instead of a traceback; defaults pass nothing, so legacy
  runs are untouched. The label controls stay inert with an upgrade
  hint when survey-viz < 0.23.0.
- `Update reel-studio.bat` installs survey-gazetteer as the twelfth
  peer (local checkout or GitHub) and prints its installed version.

## [0.15.0] - 2026-09-30

### Added
- **mapped.earth aesthetic presets** (survey-viz >= 0.22.0, eleventh
  peer surface). Step 3 (Aesthetics) gains a preset picker —
  *Dark flow* (LIC current/wind streaks on black), *Dark glow* (event
  glow with bloom on black), *Paper prism* (3D extrusion on warm
  paper) — plus full manual control over every auto choice, per the
  standing maximum-control rule:
  - **Frame rotation**: Off / Auto (optimal for the region bbox —
    elongated regions like Lake Ontario rotate ~90° to maximize zoom;
    the north arrow rotates with the map) / Manual degrees slider.
  - **Subtitle** text field (empty = the automatic time-window label).
  - **Watermark** checkbox + brand-handle field (off by default —
    watermarking stays the user's call).
  - **Encoding honesty line** toggle (default on).
  - Variable/preset compatibility warnings and a platform-canvas
    conflict warning, all fail-fast with plain-words reasons at run
    time.
- `run_pipeline()` accepts `aesthetic_preset`, `rotation`,
  `watermark`, `subtitle`, and `encoding_line`; they ride in
  `render_viz_kwargs`, so the survey-cache frame-batch fingerprint
  already covers them (preset changes invalidate the cache — proven
  by new fingerprint tests). Older survey-viz peers raise
  `PeerTooOldError` with the upgrade command instead of a traceback.

### Notes
- Windows upgrade path is unchanged: double-click
  `Update reel-studio.bat`, then relaunch from the desktop icon.

## [0.14.0] - 2026-09-30

### Added
- **Suggested time window** (survey-timescales, optional tenth peer).
  The Run step gains a *Time window: suggested framing* expander with
  start/end date pickers (defaulting to the spec's parsed dates) plus a
  one-click **Suggest window** button. The engine suggests a `[start,
  end]` framing tuned to the spec's variable and region — season
  alignment (fire season, melt season), trailing event-density windows,
  the full annual cycle, trend horizons — and the framing mode + reason
  string are shown under the button. The suggestion only ever fills the
  two pickers: the user can edit them freely afterwards, and the Run
  button consumes the pickers' values (explicit dates always win —
  user-typed dates stay authoritative). With the peer missing the whole
  control hides behind an explanatory note with the install command —
  never a crash. Details:
  - New UI-free engine-side module `studio/timescale.py` (no Streamlit
    imports): `suggest()` calls `timescales.suggest_window(variable,
    region=region_key, today=..., source=...)`; `resolve_source()`
    passes the spec's pinned source, else the regionally-resolved one
    via `viz.sources.resolve_source` — this disambiguates ERA5 vs
    IMERG for the `tp` variable; `override_with_pickers()` applies
    picker values to the run's spec copy (validated: start-after-end
    is refused with a plain-words warning).
  - Static underlays (bathymetry/elevation) get a "no time window
    applies" note instead of a window (`StaticVariableError` surfaced
    honestly).
  - `Update reel-studio.bat` now upgrades ten peers (adds
    survey-timescales).
  - Peer wiring follows the established optional-peer pattern
    (`PEER_SPECS` entry, `wire_peers` exposes `suggest_window` /
    `timescales_pip`, graceful degradation throughout).

## [0.13.0] - 2026-09-29

### Added
- Minimum data duration passthrough: `run_pipeline(...,
  min_duration_s=10.0)` forwards into survey-animate >= 0.3.0's
  `render_video` (each data frame repeats
  `k = ceil(ceil(min_duration_s * fps) / n)` times; applied after
  blending, before the title card). Only forwarded when > 0, so older
  peers keep working (PeerTooOldError otherwise); fingerprinted into
  the encode-cache key via `render_video_kwargs`. Pairs with
  `motion={"blending": True, "smooth_steps": N}` for graceful
  fade-in of new data points on short reels.

## [0.12.0] - 2026-09-29

### Added
- Optional title card: `run_pipeline(..., title_card=False)` skips the
  survey-animate full-screen cover page. The effective title passed to
  both `render_video` and the encode-cache key is `""` (falsy, so no
  card renders), keeping card-less and carded reels from colliding in
  the cache. Default `True` — no behavior change for existing callers.
  The in-frame title burned by survey-viz into each frame header is
  unaffected either way. Built for the daily scheduled reels, where a
  60-frame cover page can dwarf a handful of data frames.

## [0.11.0] - 2026-09-28

### Added
- **Publish step: schedule for later** (survey-publish ≥ 0.2.0 queue
  engine). The Publish step gains a per-reel **Publish mode** radio —
  *Publish now* keeps the v0.10.0 approve-and-post behavior unchanged,
  and *Schedule for later* queues the reel instead of posting it: three
  preset slots from the engine's `DEFAULT_SLOTS` (**Morning 08:30**,
  **Midday 12:30**, **Evening 18:30**, local time — a slot that already
  passed today rolls to tomorrow) plus a custom date + time
  (`st.date_input` defaulting to today + `HH:MM` text field), all
  resolved through the engine's `parse_schedule_time`. The resolved
  local time is shown back before enqueueing, the platform multiselect
  defaults to the connected platforms, and **Queue for scheduled
  publish** enqueues via `QueueStore`, shows the item id + scheduled
  time, and records it in the run manifest under a new
  `"queued_publication"` section
  (`studio.pipeline.record_queued_publication()`).
- A **Publish queue** expander in the same step: queued/publishing
  items (title, scheduled local time, platforms, status) with per-item
  **Cancel** and **Reschedule** (new-time input) controls for queued
  items, plus a **History** view of published/failed/canceled items.
  Failed items show `last_error` and a **Requeue failed platforms**
  button that re-enqueues **only** the platforms missing from
  `published_urls` — successes are never double-posted
  (partial-failure rule).
- An onboarding hint (`st.info`) in the step: scheduled publishes only
  fire while the PC is on and awake with the 15-minute tick job
  running, with a link to the Task Scheduler setup in
  `survey-publish`'s `docs/SCHEDULING.md`. Unconnected platforms are
  skipped with a warning when queueing, mirroring the publish-now rule
  that nothing ever posts to an unconnected platform.
- survey-publish peer note (`studio/peers.py`) now names the ≥ 0.2.0
  queue engine (`publish.QueuedItem` / `QueueStore` /
  `parse_schedule_time` / `DEFAULT_SLOTS`, fired by
  `survey-publish tick`); [docs/PUBLISHING.md](docs/PUBLISHING.md)
  gains a *Scheduling for later* section.

### Limitations
- Queue times are the PC's local time and the queue only fires while
  the PC is on/awake with the tick job running — scheduled reels are
  not a fire-and-forget cloud service (this is the honest
  self-hosted model; see the scheduling docs).

## [0.10.0] - 2026-09-28

### Added
- **Publish step (step 9)** wired to the new survey-publish engine
  (optional ninth peer): after generation, review the finished reel in
  `st.video`, see a per-platform connection panel (YouTube, Instagram,
  Facebook, TikTok) with the account label when the adapter provides
  one, and get an expander with the exact
  `survey-publish connect <platform>` terminal command plus a link to
  that platform's `docs/SETUP_<PLATFORM>.md` in the survey-publish repo
  for any platform not connected yet (OAuth needs a browser, so connect
  is deliberately terminal-based). Edit the title, caption, and
  hashtags (prefilled from the reel's spec; hashtags default to
  `reelstudio, dataviz, remotesensing, earthobservation`), then press
  **Approve & Publish**: the app builds one `PublishRequest` per
  connected platform, calls the adapter, and shows per-platform results
  (post URL/id on success, error text on failure). Unconnected
  platforms are never published to — skipped, not failed.
- Every approval is recorded in the run's `manifest.json` under a
  `"publication"` section (`approved_at`, per-platform `ok`/`url_or_id`/
  `error`, title, caption, hashtags) via the new
  `studio.pipeline.record_publication()` helper, which appends to the
  existing manifest written during the Run step (tolerating a missing
  or corrupt manifest instead of crashing).
- Approval is in-app only in this version: email click-to-approve was
  evaluated and deliberately excluded (mail-scanner link-prefetch
  hazard, needs public hosting). The reel publishes as-is —
  survey-animate already muxed the audio track upstream.
- survey-publish added to the peer registry (`studio/peers.py`), the
  Engine-status panel, `Update reel-studio.bat`, the README stack
  table + install list, and the new [docs/PUBLISHING.md](docs/PUBLISHING.md)
  reference. Without the peer installed, step 9 shows the install hint
  and reels stay local.

## [0.9.0] - 2026-09-28

### Added
- **Derived climatological anomaly products** (new survey-derive peer,
  optional eighth engine): the Run step gains a *Derived product*
  expander — render the anomaly instead of the raw variable. Three
  products (`anomaly`, `standardized`, `percent of normal`), all vs a
  day-of-year climatology over a user-chosen baseline period (default
  1991–2020): baseline start/end dates, baseline sampling stride,
  climatology day-window, minimum baseline samples per day-of-year,
  and the color-limit quantile are all user-controlled, before
  generation and carried into batch queues and scheduled jobs. The baseline is
  re-fetched with the same source adapter as the analysis (same grid,
  same variable semantics); the renderer applies one shared symmetric
  color scale to every frame and defaults to the `RdBu_r` diverging
  colormap unless the user picked one. survey-viz ≥ 0.19.0 renders
  `<variable>-anomaly` specs, burns the baseline note into the frame
  footer, and records it in the manifest. The Great Lakes SST chart is
  replaced by per-frame spatial-mean anomalies so chart and map agree.
  `run_pipeline(..., derived={...})` for programmatic runs; the render
  cache fingerprints the normalized derived config so a changed baseline
  can never serve stale frames. See [docs/DERIVED.md](docs/DERIVED.md)
  for the methods reference.
- Sources that cannot honestly produce an anomaly **refuse** instead of
  rendering a misleading map: GRACE (already an anomaly), FIRMS (no
  baseline stride on its adapter), GEBCO (static), storm tracks,
  streamgages, and earthquakes. Missing survey-derive shows the install
  command; survey-viz < 0.19.0 gets a named `PeerTooOldError` instead of
  an unlabeled anomaly.

### Changed
- `studio/caching.py`: `frame_batch_key()` takes an optional `derived`
  config and folds it into the frame-batch fingerprint.
- Engine status panel now tracks eight peers (survey-derive added).

### Fixed
- None.

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
