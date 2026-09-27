# reel-studio interop: peer contracts

reel-studio integrates three peer engines. All contracts below were
verified against the peers' **actual code** (survey-currents v0.2.0,
survey-viz v0.1.0, survey-animate v0.1.0), not guesses. reel-studio never
hard-imports peers and never imports peer internals beyond the entry
points listed here.

## survey-viz v0.1.0 (`viz`)

| Entry point | Signature | reel-studio use |
|---|---|---|
| `parse_description` | `(text, title=None, today=None) -> VizSpec`; raises `UnparseableDescription` | Step 1 parsing |
| `VizSpec` | dataclass; `to_dict()` / `from_dict()`; validated on construction | the spec contract; session state stores the dict |
| `is_fetchable` | `(region_key) -> bool`; True for gazetteer regions the installed peers can serve (13 sources as of survey-viz 0.15.0) | fetch planning |
| `get_region` | `(key) -> dict \| None` | (available; currently unused) |
| `render_viz` | `(spec, field, series=None, out_dir="frames", layout="reel-vertical", style=None, underlay=None, cmap=None) -> (frames, manifest_path)` | Step 3 frame rendering; `cmap` needs survey-viz ≥ 0.15.0 (validated override, continuous maps only — categorical renderers keep fixed colors); `run_pipeline` only passes `cmap` when set, so older peers keep working |

`field`/`series` are **duck-typed**: reel-studio passes the documented
dict forms — `{"times", "lats", "lons", "values"}` and
`{"dates", "values"}` — never peer objects. This keeps the boundary
explicit and testable with fakes.

## survey-currents v0.2.0 (`currents.glsea`)

| Entry point | Signature | reel-studio use |
|---|---|---|
| `fetch_glsea_sst` | `(bbox, start, end, stride_days=30) -> GlseaField`; `bbox=(lon_min, lat_min, lon_max, lat_max)`; lazy `netCDF4`; every fetch carries provenance | SST grid fetch |
| `fetch_glsea_lake_averages` | `(lake, start, end) -> LakeSeries`; `lake` in `{"superior","michigan","huron","erie","ontario"}`; stdlib csv | lake-average series fetch |
| `GlseaField.synthetic` | `(...)` classmethod | offline e2e test fixture |

`LakeSeries` exposes `.dates`/`.temps` (not viz's `.dates`/`.values`),
so the pipeline adapts it to the dict form explicitly.

## survey-animate v0.1.0 (`animate`)

| Entry point | Signature | reel-studio use |
|---|---|---|
| `render_video` | `(source, out_path, *, preset="reel", fmt=None, fps=None, crf=None, burn_timestamps_=True, ...) -> VideoResult`; `source` = PNG dir **or** manifest path; resolves ffmpeg itself (bundled or system) | Step 2 MP4 encoding |

## Discovered interop quirks (verified, handled in reel-studio)

### 1. Pass the frames DIRECTORY to `render_video`, not the viz manifest

`render_viz` writes a manifest with schema
`"survey-viz.frame-manifest/1.0"`, but survey-animate's manifest reader
only accepts the `"survey-flow.frame-manifest/"` schema prefix — handing
it the viz manifest raises a schema error. reel-studio therefore passes
the **frames directory** (`frames_from_dir` path), which animate accepts
for any PNG directory. `run_pipeline` asserts this in tests
(`test_render_video_gets_frames_dir_not_manifest`).

### 2. `GlseaField.times` are ISO datetimes; viz only parses date-only strings

viz's `_coerce_date` handles `date`/`datetime` objects and date-only ISO
strings, but raises on ISO *datetime* strings like
`"2026-06-01T12:00:00+00:00"` — exactly what `GlseaField.times` holds,
including from a real `fetch_glsea_sst` (ERDDAP `num2date(...).isoformat()`).
Passing a real `GlseaField` straight into `render_viz` fails. reel-studio
adapts via `_field_to_dict()`, normalizing every timestep to
`YYYY-MM-DD` first. (Not fixed in survey-viz: this repo must not touch
peer checkouts.)

### 3. lake-superior's gazetteer bbox is west of the GLSEA grid floor

The survey-viz gazetteer frames Lake Superior at lon_min **-92.5**, but
the GLSEA grid's longitude floor is **-92.4199507342304** — passing the
spec bbox straight to `fetch_glsea_sst` raises `ValueError` from
`validate_glsea_bbox`. reel-studio clamps the fetch bbox into the GLSEA
grid (`_clamp_bbox`, bounds wired from `currents.glsea` constants) and
records the clamping in provenance (`fetch.bbox_clamp_notes`). The other
four lakes' bboxes already fit the grid.

## What "fetchable" means today

* **Regions:** the 5 Great Lakes (`lake-superior`, `lake-michigan`,
  `lake-huron`, `lake-erie`, `lake-ontario`) for SST via GLSEA
  (`viz.is_fetchable`); any region for SST via OISST/MUR (global grids);
  **any region** for the ERA5 atmosphere variables (global 0.25° grid);
  **any non-Great-Lakes region** for surface currents via OSCAR
  v2.0 / CMEMS global physics (global grids).
* **Variables:** `sst` (GLSEA/OISST/MUR), `wind` / `msl` / `t2m` / `tp`
  (ERA5 via the CDS API — needs `survey-currents>=0.4.0`, `cdsapi`,
  `netCDF4`, and a free CDS account), `currents` (OSCAR v2.0 — needs
  `survey-currents>=0.5.0`, `netCDF4`, and a free Earthdata Login — or
  CMEMS global physics — needs `survey-currents>=0.5.0`, the
  `copernicusmarine` toolbox, and a free CMEMS account). Great Lakes
  `currents` stays an honest refusal (no lake-scale current adapter);
  `night-lights` via NASA Black Marble VNP46A2 V002 in any region
  (needs `survey-currents>=0.9.0`, `h5py` via
  `survey-currents[blackmarble]`, and a free Earthdata Login),
  `bathymetry` / `elevation` via GEBCO 2024 in any region (needs
  `survey-currents>=0.10.0`, no account, no heavy deps),
  `storm-tracks` via NOAA IBTrACS v04r01 best tracks in any region
  (needs `survey-currents>=0.11.0` + `survey-viz>=0.10.0`, keyless,
  `netCDF4`),
  `water-storage` via CSR GRACE/GRACE-FO RL06.3 monthly terrestrial
  water storage anomalies in any region (needs
  `survey-currents>=0.12.0` + `survey-viz>=0.11.0`, keyless, land-only,
  `netCDF4`), and
  `power-outage` stays an honest refusal (change detection, not a
  single-epoch map). `country-borders` stays an honest refusal too
  (Natural Earth vectors are a cartographic underlay, not a data
  variable). `sea-level` stays an honest refusal as well (sea level
  is altimetry, not a GRACE map).
* Anything else → `plan_fetch` returns `fetchable=False` with a message
  naming the missing adapter; `run_pipeline` raises
  `UnfetchableRegionError` / `UnsupportedVariableError`; the app shows
  the message instead of crashing.

## Currents contract (survey-viz 0.4.0 / survey-currents 0.5.0)

* `wire_peers` exposes `fetch_oscar` and `fetch_cmems_currents` lazily
  (None when survey-currents < 0.5.0 — the pipeline then raises the
  honest upgrade message naming `survey-currents>=0.5.0`).
* `run_pipeline` calls `fetch(bbox, start, end, stride_days=...)` with
  `series=None` (no lake-average equivalent; the chart panel shows a
  placeholder). Provenance keys are `fetch["oscar"]` or
  `fetch["cmems-currents"]`.
* `plan_fetch` routes `oscar`/`cmems-currents` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.4.0: non-Great-Lakes
  `currents` → `oscar`; high-resolution/ultra/1-km currents wording pins
  `cmems-currents`).
* `_field_to_dict` adapts a survey-currents `CurrentField` by computing
  the scalar current speed `sqrt(u^2+v^2)` (masked cells → NaN) — no
  quiver/streamline/particle rendering here; that is survey-flow's job.
* **Deferred:** a Gulf Stream `currents + SST base` particle-flow overlay
  — current overlay plumbing is contour-only (see survey-viz CHANGELOG
  0.4.0).

## Fires contract (survey-viz 0.5.0 / survey-currents 0.6.0)

* `wire_peers` exposes `fetch_firms` lazily (None when
  survey-currents < 0.6.0 — the pipeline then raises the honest upgrade
  message naming `survey-currents>=0.6.0`).
* `run_pipeline` calls `fetch_firms(bbox, start, end)` (no stride — the
  FIRMS API returns daily detections by construction) with `series=None`
  (no lake-average equivalent; the chart panel shows a placeholder).
  Provenance key is `fetch["firms"]` (the FireField provenance carries
  the MAP_KEY-free retrieval record).
* `plan_fetch` routes `firms` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.5.0: any region,
  variable `fire` → `firms`). `burn-scar` is refused honestly here:
  FIRMS is active-fire *detections* only; the refusal names survey-burn
  as the future imagery adapter instead of misrouting to detections.
* `_field_to_dict` adapts a survey-currents `FireField` via its
  `to_density_grid()` (daily fire-count grids in the render dict shape)
  — zero renderer changes; per-detection point markers are deliberately
  deferred to a future renderer version, not half-plumbed.

## Sea-ice contract (survey-viz 0.6.0 / survey-currents 0.7.0)

* `wire_peers` exposes `fetch_nsidc` lazily (None when
  survey-currents < 0.7.0 — the pipeline then raises the honest upgrade
  message naming `survey-currents>=0.7.0`).
* `run_pipeline` calls `fetch_nsidc(bbox, start, end,
  stride_days=DEFAULT_STRIDE_DAYS)` on the spec bbox directly (the
  polar grids are hemispheric — nothing to clamp) with `series=None`
  (no lake-average equivalent; the chart panel shows a placeholder).
  Provenance key is `fetch["nsidc"]` (per-file URLs + SHA-256 +
  reprojection method). The archive is keyless HTTPS, so the fetch
  failure message names connectivity / date range instead of
  credentials.
* `plan_fetch` routes `nsidc` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.6.0: the polar
  regions `arctic-ocean` / `southern-ocean`, variable `sea-ice` →
  `nsidc`). `sea-ice` with no resolved source (a non-polar region) is
  refused honestly — the product has no mid-latitude ice domain.
  `land-ice` (glaciers / ice sheets / icebergs) is refused honestly
  too: it parses but is never routed to NSIDC (different physical
  product, no adapter yet).
* `_field_to_dict` adapts a survey-currents `IceField` through the
  generic 3D-`values` path — percent concentration with NaN for
  land/missing — zero renderer changes.

## IMERG precipitation contract (survey-viz 0.7.0 / survey-currents 0.8.0)

* `wire_peers` exposes `fetch_imerg` lazily (None when
  survey-currents < 0.8.0 — the pipeline then raises the honest upgrade
  message naming `survey-currents>=0.8.0`).
* `run_pipeline` calls `fetch_imerg(bbox, start, end,
  accumulate="daily", run="late", stride_days=DEFAULT_STRIDE_DAYS)` on
  the spec bbox directly (the 0.1° IMERG grid is global — nothing to
  clamp) with `series=None` (no lake-average equivalent; the chart
  panel shows a placeholder). Provenance key is `fetch["imerg"]`
  (per-file URLs + SHA-256 + run + accumulation mode +
  per-day `{"expected": 48, "retrieved": n}` coverage). The archive
  needs a free Earthdata Login, so the fetch failure message names
  `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` (or `~/.netrc`) and the
  `survey-currents[imerg]` (h5py) extra.
* `plan_fetch` routes `imerg` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.7.0: any region,
  variable `tp` → `imerg` for explicit requests and recent / observed /
  event wording; long-record wording pins `era5` instead — see
  survey-viz docs/PARSER.md §6a). An `imerg` pin on any other variable
  is refused as `bad_variable`.
* `_field_to_dict` adapts a survey-currents `RainField` through the
  generic 3D-`values` path — mm/day daily totals (or mm/hr rates for
  `accumulate="native"`) with NaN for missing — zero renderer changes.

## Black Marble night-lights contract (survey-viz 0.8.0 / survey-currents 0.9.0)

* `wire_peers` exposes `fetch_blackmarble` lazily (None when
  survey-currents < 0.9.0 — the pipeline then raises the honest upgrade
  message naming `survey-currents>=0.9.0`).
* `run_pipeline` calls `fetch_blackmarble(bbox, start, end,
  product="daily", stride_days=DEFAULT_STRIDE_DAYS)` on the spec bbox
  directly (the VNP46A2 10°×10° tiles are global — nothing to clamp)
  with `series=None` (no lake-average equivalent; the chart panel
  shows a placeholder). Provenance key is `fetch["blackmarble"]`
  (per-file URLs + SHA-256 + tile list + per-day
  `{"expected": n, "retrieved": m}` + skipped tiles/days). The LAADS
  archive needs a free Earthdata Login, so the fetch failure message
  names `EARTHDATA_USERNAME`/`EARTHDATA_PASSWORD` (or `~/.netrc`) and
  the `survey-currents[blackmarble]` (h5py) extra.
* `plan_fetch` routes `blackmarble` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.8.0: any region,
  `variable="night-lights"` always pins `blackmarble`). A `blackmarble`
  pin on any other variable is refused as `bad_variable`.
  `variable="power-outage"` is refused honestly as `no_adapter` before
  the region fall-through: outage mapping is temporal change detection
  across two or more epochs, and a single daily Black Marble map cannot
  show it — the refusal names the real reason, not the legacy-path
  message.
* `_field_to_dict` adapts a survey-currents `LightsField` through the
  generic 3D-`values` path — nW/cm²/sr radiance with NaN for
  unlit/missing — zero renderer changes.

## GEBCO topography contract (survey-viz 0.9.0 / survey-currents 0.10.0)

* `wire_peers` exposes `fetch_gebco` lazily (None when
  survey-currents < 0.10.0 — the pipeline then raises the honest
  upgrade message naming `survey-currents>=0.10.0`).
* `run_pipeline` calls `fetch_gebco(bbox, resolution=...)` on the spec
  bbox directly (the GEBCO grid is global — nothing to clamp), picking
  the resolution from the bbox span so the downsampled grid stays <=
  ~720 cells per axis; `series=None` (static compilation — no time
  axis; the chart panel shows a placeholder). Provenance key is
  `fetch["gebco"]` (SHA-256 sidecars + intersecting 90° tile list +
  structural timestamp `2024-01-01T00:00:00Z` = release year). The
  first fetch in a new region downloads the intersecting 90° tile
  entries (~500 MB each, cached afterwards); the fetch failure
  message says so and names the cache re-download behaviour.
* `plan_fetch` routes `gebco` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.9.0: any region,
  `variable="bathymetry"`/`"elevation"` always pins `gebco`). A
  `gebco` pin on any other variable is refused as `bad_variable`.
  `variable="country-borders"` is refused honestly as `no_adapter`
  before the region fall-through: Natural Earth vectors are a
  cartographic underlay, not a data variable — the refusal names the
  real reason, not the legacy-path message.
* `_field_to_dict` adapts a survey-currents `TopoField` through the
  generic 3D-`values` path — metres, positive up — zero renderer
  changes.

## IBTrACS storm-tracks contract (survey-viz 0.10.0 / survey-currents 0.11.0)

* `wire_peers` exposes `fetch_ibtracs` (None when survey-currents <
  0.11.0 — the pipeline then raises the honest upgrade message).
* `plan_fetch` routes `ibtracs` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.10.0: any region,
  `variable="storm-tracks"` always pins `ibtracs`). An `ibtracs` pin
  on any other variable is refused as `bad_variable`.
* `run_pipeline` calls
  `fetch_ibtracs(bbox, start, end, storm_name=spec.storm_name or None)`
  — the keyless v04r01 NetCDF (1980–present by default) is global, so
  nothing is clamped. Ranking (`storm_rank="strongest"` +
  `storm_top_n`, default 5) is applied here via
  `StormField.rank_by_intensity()` — lifetime maximum sustained wind
  (kt), the documented aggregation rule.
* The `StormField` goes to `render_viz` as its `to_dict()` form
  (`"storm_tracks"` key) — **never** through `_field_to_dict`, which
  only understands scalar 3-D grids. Provenance rides on the field
  object (`fetch["ibtracs"]`); `series=None`.
* A requested ERA5 overlay ("… with the wind field") is fetched via
  `fetch_era5` and attached as `overlay_grids` dicts
  (`{"times", "lats", "lons", "grid"}` per overlay) under the track
  dict — survey-viz >= 0.10.0 draws the contours under the tracks.
  Context is best-effort: a missing peer, missing CDS credentials, or
  a failed download degrades gracefully (the renderer records
  `"absent"` per overlay in the manifest) instead of failing the
  reel.

## GRACE water-storage contract (survey-viz 0.11.0 / survey-currents 0.12.0)

* `wire_peers` exposes `fetch_grace` (None when survey-currents <
  0.12.0 — the pipeline then raises the honest upgrade message).
* `plan_fetch` routes `grace` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.11.0: any region,
  `variable="water-storage"` always pins `grace`). A `grace` pin on
  any other variable is refused as `bad_variable`; `sea-level`
  (satellite altimetry, not terrestrial water storage) is refused
  honestly as `no_adapter` — never answered with a GRACE map.
* `run_pipeline` calls
  `fetch_grace(bbox, start, end)` — the keyless CSR RL06.3 NetCDF
  (2002–present, 0.25° output grid) plus the separate land mask are
  global, so nothing is clamped. Months with no solution (e.g. the
  2017-07 … 2018-05 inter-mission gap) are all-NaN frames — never
  interpolated.
* The `WaterField` goes to `render_viz` as its `to_dict()` form
  (`"values"` key, monthly cm-LWE anomaly maps) — **never** through
  `_field_to_dict`, which only understands scalar 3-D grids.
  Provenance rides on the field object (`fetch["grace"]`: product,
  solution+mask URLs, SHA-256 digests, anomaly baseline, gap months);
  `series=None`.
* A requested `"tp"` precipitation overlay ("… vs rainfall") is
  fetched via `fetch_era5(["tp"], bbox, start, end, stride_hours=24)`
  by `_fetch_water_context`, grouped into monthly means, and
  bilinearly resampled onto the GRACE grid
  (`_bilinear_resample`) before being attached as an `overlay_grids`
  dict — survey-viz >= 0.11.0 draws the contours over the anomaly
  map. The resampled precipitation is approximate context (monthly
  means of daily snapshots, not a true accumulation), and the whole
  overlay is best-effort: a missing peer, missing CDS credentials, or
  a failed download degrades gracefully (the renderer records
  `"absent"` in the manifest) instead of failing the reel.

## USGS streamflow contract (survey-viz 0.12.0 / survey-currents 0.13.0)

* `wire_peers` exposes `fetch_usgs` (None when survey-currents <
  0.13.0 — the pipeline then raises the honest upgrade message).
* `plan_fetch` routes `usgs` (the source comes from
  `viz.sources.resolve_source` on survey-viz >= 0.12.0: any region,
  `variable="streamflow"` always pins `usgs`). A `usgs` pin on
  any other variable is refused as `bad_variable`. NWIS coverage is
  US-only, so a bbox outside USGS coverage returns an honest empty
  field — survey-viz >= 0.12.0 renders an explicit no-gages message,
  never fabricated data.
* `run_pipeline` calls `fetch_usgs(bbox, start, end)` — the keyless
  NWIS water services (site inventory + daily values JSON), cached
  7 days with SHA-256 verification in survey-currents.
* The `GageField` goes to `render_viz` as its `to_dict()` form
  (`"gage_records"` key: gage markers + hydrograph panel) — **never**
  through `_field_to_dict`, which only understands scalar 3-D grids.
  Provenance rides on the field object (`fetch["usgs"]`: NWIS URLs,
  retrieval time, parameters, site counts, cache state);
  `series=None`.
* A requested `"tp"` precipitation overlay ("… with rainfall" /
  "Flooding after heavy rainfall") is fetched by
  `_fetch_streamflow_context`: `fetch_imerg(bbox, start, end,
  accumulate="daily", run="late", stride_days=...)` is preferred
  (observed), with `fetch_era5(["tp"], …)` as the fallback —
  survey-viz >= 0.12.0 draws the contours under the gage markers.
  Best-effort: a missing peer, missing credentials, or a failed
  download degrades gracefully (the renderer records `"absent"` in
  the manifest) instead of failing the reel.

## ERA5 contract (survey-viz 0.3.0 / survey-currents 0.4.0)

* `wire_peers` exposes `fetch_era5` (None when survey-currents < 0.4.0 —
  the pipeline then raises the honest upgrade message).
* `run_pipeline` calls
  `fetch_era5([spec.variable] + list(spec.overlays), bbox, start, end,
  stride_hours=...)` — one CDS call carries the base variable **and**
  any contour overlays (e.g. `["wind", "msl"]` for the storm
  combination). `stride_hours=24` by default (daily 12:00 UTC).
* The field is adapted with `_field_to_dict`, which carries
  `overlay_grids` through (dropping them would silently lose a requested
  overlay); `render_viz` draws the contours from survey-viz 0.3.0.
* ERA5 renders with `series=None` (no lake-average equivalent —
  placeholder chart panel, same as global SST).
* Provenance: `RunResult.source == "era5"`, field provenance under
  `provenance["fetch"]["era5"]` (SST adapters keep `"sst"`).

## Adding a new adapter (future)

1. Add the region to survey-viz's gazetteer (`data/regions.yaml`) and a
   fetch function in the appropriate engine.
2. Extend `studio/pipeline.py`: `FETCHABLE_REGION_TO_LAKE` (or a new
   mapping), `SUPPORTED_VARIABLES`, and the `plan_fetch` branches.
3. Document the new contract here.
