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
| `is_fetchable` | `(region_key) -> bool`; True only for the 5 Great Lakes | fetch planning |
| `get_region` | `(key) -> dict \| None` | (available; currently unused) |
| `render_viz` | `(spec, field, series=None, out_dir="frames", layout="reel-vertical", style=None) -> (frames, manifest_path)` | Step 2 frame rendering |

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
  **any region** for the ERA5 atmosphere variables (global 0.25° grid).
* **Variables:** `sst` (GLSEA/OISST/MUR), `wind` / `msl` / `t2m` / `tp`
  (ERA5 via the CDS API — needs `survey-currents>=0.4.0`, `cdsapi`,
  `netCDF4`, and a free CDS account).
* Anything else → `plan_fetch` returns `fetchable=False` with a message
  naming the missing adapter; `run_pipeline` raises
  `UnfetchableRegionError` / `UnsupportedVariableError`; the app shows
  the message instead of crashing.

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
