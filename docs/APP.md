# reel-studio user guide

## What it is

reel-studio turns a plain-English description of a Great-Lakes water
visualization into a finished vertical (1080×1920) MP4 reel. It is a
thin studio layer over three engines:

* **survey-viz** — deterministic description parser (`parse_description`
  → `VizSpec`) and reel frame renderer (`render_viz`)
* **survey-currents** — NOAA GLSEA sea-surface-temperature fetch
  (`fetch_glsea_sst`, `fetch_glsea_lake_averages`)
* **survey-animate** — frame → MP4 encoder (`render_video`, resolves
  ffmpeg itself)

## The 3-step flow

### Step 1 — Describe and parse

Type a description such as:

* `surface water temperature oscillation on Lake Superior for the past 10 years`
* `Lake Michigan water temperature last summer`
* `chlorophyll in the Gulf of Mexico 2020 to 2022`

Press **Parse**. The app calls survey-viz's deterministic, offline
parser (no AI, no network) and shows the resulting `VizSpec` as JSON —
`title`, `region_key`, `bbox`, `variable`, `start`/`end`, `cadence` —
for confirmation. If the parser can't handle the description you get a
clear error with examples that do parse.

### Step 2 — Run the pipeline

Press **Run — fetch, render, encode**. With a progress bar and live
status messages, the pipeline:

1. **Fetches** — GLSEA daily SST for the spec's bbox (30-day stride)
   plus the lake-average temperature series, both with provenance
   (source URL, SHA-256, retrieval time). Fetchable today: the 5 Great
   Lakes, variable `sst` only.
2. **Renders** — survey-viz renders one 1080×1920 PNG per month
   (title block, map panel with fixed colormap, burned-in timestamp,
   time-series panel with a playhead).
3. **Encodes** — survey-animate encodes the frames to `reel.mp4`
   (H.264, 30 fps, `reel` preset) with a title card and a
   `.provenance.json` sidecar.

If the region isn't fetchable (e.g. Gulf of Mexico) or the variable
isn't supported (e.g. `currents`), you get an **honest message naming
what's missing** — the app never crashes on these paths.

### Step 3 — Take the reel

* Embedded video player (`st.video`)
* **Download MP4** button
* **Provenance** expander: fetch URLs, SHA-256 digests, the full spec
  JSON, frame/manifest paths, encoder details

## The optional LLM assist

Set `LLM_API_KEY` (and optionally `LLM_BASE_URL`, default
`https://api.openai.com/v1`; `LLM_MODEL`, default `gpt-4o-mini`) before
launching:

```bash
LLM_API_KEY=sk-... streamlit run app.py
```

Behavior:

* The deterministic parser always runs **first**.
* Only if it raises `UnparseableDescription` does the app make **one**
  assist attempt: a chat-completions POST (stdlib `urllib` only) with the
  description plus a VizSpec schema summary.
* The returned JSON is validated with `VizSpec.from_dict`. If validation
  fails, you see the **original** parse error — the LLM output is never
  trusted blindly.
* The parsed-spec screen tells you when the assist was used, so you can
  double-check before running.

The app works **fully** without the key; it is never required.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Engine badge amber, "missing" | Run the shown `pip install git+https://github.com/crieck2010/<repo>.git` |
| `SST fetch failed` | Network to NOAA ERDDAP unreachable, or `netCDF4` missing (`pip install netCDF4`) |
| Parse error on a good-looking description | Check the examples in the error; or set `LLM_API_KEY` for one assist attempt |
| `Nothing to fetch` warning | Region/variable has no adapter yet — the message says exactly which |
| Video plays in-app but download is empty | Re-run; the temp working dir is per-session |

## Offline demo

```bash
python -m studio.demo
```

Parses the 3 example descriptions with a fixed reference date and
prints each `VizSpec` plus the fetch plan — including the honest
unfetchable-region path for the Gulf of Mexico. No network, no key, no
ffmpeg needed. Requires survey-viz (install command shown otherwise).
