# Derived products: climatological anomaly maps

reel-studio can render a **climatological anomaly map** instead of the raw
variable: each frame minus its day-of-year climatology over a user-chosen
baseline period. The math lives in the
[survey-derive](https://github.com/crieck2010/survey-derive) engine
(NaN-aware NumPy, no network, no plotting); the orchestration lives in
`studio/pipeline.py`; the rendering of the anomaly field reuses
survey-viz ≥ 0.19.0's `<variable>-anomaly` handling. This document is the
methods reference.

## What "anomaly" means here

For each grid cell and each calendar day *d*, the climatology is

```
clim_mean(cell, d) = mean of baseline values within ±window_days of d
clim_std (cell, d) = std  of the same samples
```

The baseline is re-fetched with the **same source adapter** that fetched
the analysis (same grid, same variable semantics), so the climatology
and the analysis always align cell-for-cell. The three products are:

| Product | Formula | Units shown |
|---|---|---|
| `anomaly` | field − clim_mean | native units (e.g. °C) |
| `standardized` | (field − clim_mean) / clim_std | σ (standard deviations) |
| `percent` | 100 · field / clim_mean | % |

### The color scale is shared and symmetric

`derive.suggest_symmetric_limits()` picks one symmetric `[-bound, +bound]`
range from the anomaly values (default 99th percentile of |value|), and
that range is applied to **every frame**. Without this, a renderer would
auto-scale each frame independently and identical anomalies would look
different across the reel — the shared scale is what makes the anomaly
sequence honest. The default colormap is `RdBu_r` (diverging) unless the
user picked a colormap explicitly; an explicit pick wins.

### The chart follows the product (Great Lakes SST)

Great Lakes SST reels carry a lake-average temperature chart. In derived
mode the chart is **replaced** by the per-frame spatial mean of the
anomaly field — so the chart and the map tell the same story, and the
raw temperatures are not shown where they would contradict the frames.

## Controls (all yours, before and after generation)

The Run step's *"Derived product"* expander exposes every control, and
the same config rides along into batch queues and scheduled jobs:

| Control | Default | Meaning |
|---|---|---|
| Enable | off | Reels stay raw-variable until you opt in |
| Product | `anomaly` | `anomaly` / `standardized` / `percent` |
| Baseline start / end | 1991-01-01 / 2020-12-31 | WMO-style 30-year normal by default; any range the source archive covers |
| Baseline stride (days) | 30 | Sample one baseline frame per N days (≈ monthly by default) |
| Day-of-year window (days) | 15 | Climatology for each day uses ±N days around it (0 = exact-day matches) |
| Minimum samples | 10 | Floor on pooled baseline samples per day-of-year and cell; day/cells below it are masked out of standardized anomalies |
| Color-limit quantile | 0.99 | The shared symmetric range covers up to this quantile of |anomaly| — lower it to saturate the extremes sooner |

### Percent of normal shows deviation from 100%

Percent-of-normal is a ratio centered at 100, not a signed quantity
around zero — a symmetric color scale on the raw ratio would saturate
the whole map. The reel renders the **percentage-point deviation from
100%** (0 = normal, +20 = 120% of normal) with the shared symmetric
scale, and the footer says so: *"percent of normal (deviation from
100%) vs 1991–2020 climatology"*.

Programmatic runs pass the same dict to `run_pipeline(..., derived={...})`;
`_normalize_derived` validates it fail-fast (unknown product, reversed or
malformed dates, non-positive stride/window all raise `ValueError` before
any fetch happens).

## Provenance and labeling (nothing hidden)

- The spec's `derived_note` (e.g. *"anomaly vs 1991–2020 climatology"*)
  is burned into the frame **footer** by survey-viz ≥ 0.19.0 and into the
  manifest, so a downloaded reel still says what it is.
- `run_pipeline` records a `derived` provenance block: product, engine
  (`"survey-derive"`), baseline range, stride, window, variable, and the
  symmetric `vmin`/`vmax`.
- The render cache fingerprints the normalized derived config alongside
  the transformed field content, so changing the baseline or product
  can never serve stale frames.

## Eligibility: some sources honestly refuse

Derived products need a gridded scalar field with a meaningful long
baseline. Sources that cannot meet that bar **raise** instead of
producing a misleading map:

| Source | Why it refuses |
|---|---|
| GRACE water storage | The source field *is* already a time-mean-removed anomaly — an anomaly of an anomaly would double-count the removal |
| FIRMS fires | The fetch adapter has no stride parameter, so a multi-decade baseline cannot be sampled the same way as the analysis |
| GEBCO topography | Static — no baseline to compare against |
| IBTrACS storm tracks | Track data, not a gridded scalar field |
| USGS streamgages | Gauge records, not a gridded scalar field |
| USGS earthquakes (ComCat) | Event records, not a gridded scalar field |

Eligible: SST (GLSEA / OISST / MUR), ERA5 atmosphere, OSCAR/CMEMS
currents, NSIDC sea ice, IMERG precipitation, Black Marble night lights,
ocean color.

Two more honest errors you can hit: the baseline fetch failing because
the archive does not cover your range (same credentials and network as
the analysis — e.g. ERA5 needs a CDS account for the baseline too), and
a grid mismatch between the baseline and analysis fetches (the engine
raises rather than interpolate silently).

## Missing pieces stay honest, too

- No survey-derive installed → the expander shows the install command;
  raw reels work untouched.
- survey-viz < 0.19.0 (no `VizSpec.derived_note`) → `PeerTooOldError`
  names the upgrade instead of rendering an unlabeled anomaly.
- The percent-of-normal product shares the symmetric scale: values near
  100% map near zero divergence. It answers "how far from normal", not
  "how much rain" — read the footer, not the hue alone.
