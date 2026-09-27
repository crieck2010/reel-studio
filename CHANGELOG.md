# Changelog

All notable changes to reel-studio. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
