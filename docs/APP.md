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

## The 7-step flow

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

### Step 2 — Platform

Pick where the reel will be posted — TikTok, Instagram Reels,
YouTube Shorts, X portrait, square, or widescreen. The frame size and
the title/map/chart/caption/footer regions follow the platform's
aspect ratio and measured safe zones, shown as a schematic with the
platform's interface chrome (top navigation/status, right action
rail, bottom captions/channel/progress) shaded red and labeled in
plain words. The measurements are community-measured
approximations, not official platform specs. Earthquake reels get the
`quake` layout flavor (largest-events ranking panel) automatically;
everything else gets `standard`. Needs survey-layout + survey-viz ≥
0.18.0; without them the step offers only the legacy 1080×1920
layout and claims no safe-zone support.

### Step 3 — Aesthetics

Style the reel before it runs:

* **Style** — Dark (`reel-dark`, the default) or Light.
* **Title** — burned into the top of every frame; seeded from the
  parsed title.
* **Footer caption** (optional) — a custom line prepended to the frame
  footer, e.g. your channel name. Per-renderer honesty wording is
  always kept (the earthquake catalog's "observed events — not a
  forecast" can never be erased by a caption).
* **Basemap underlay** — GEBCO tint + Natural Earth coastlines beneath
  the data map; uncheck for a flat background.
* **Colormap** — Automatic (the variable default) or any of the 30
  curated colormaps. Applies to continuous data maps only; storm
  tracks, streamgages, and earthquakes keep their fixed scientific
  colors, and the app tells you so. Needs survey-viz ≥ 0.15.0 — with
  an older peer the app says so and keeps the default.
* **Data-driven story captions** — burn data-driven captions onto the
  frames: peak values ("Peak sea surface temperature: 16.2°C regional
  mean") and significant trends, describing only what the reel shows.
  Needs survey-viz ≥ 0.17.0; categorical products keep their fixed
  scientific encodings and skip captions honestly.

Press **Apply aesthetics** to validate the tweaks and store the spec
that will run (the colormap applies immediately, without the button).

**Copy the look of a reel** — paste a reel link (Instagram, TikTok,
YouTube, …) or upload a screenshot, press **Analyze look**, and the
app reads the reference's *color mood*: overall brightness (→ Dark or
Light style) and dominant hues (→ the closest of the 30 curated
colormaps). It shows the measured palette and the suggested style +
colormap with explanatory notes before you **Apply this look**.
Honest limits, stated in the UI: only the color mood is copied —
fonts, layouts, and transitions can't be read from a thumbnail; the
video itself is never downloaded (social platforms keep it behind
login walls), so a URL falls back to the page's preview thumbnail and
a screenshot upload is the most reliable input; categorical products
(earthquakes, streamgages, storm tracks) keep their fixed scientific
colors even when a reference suggests a colormap. Needs
survey-viz ≥ 0.16.0 — with an older peer the app says so and does
nothing.

### Step 4 — Cinematic motion & audio

**Cinematic camera motion** (optional, needs survey-animate ≥ 0.2.0):
check *Enable cinematic camera motion*, then set zoom (in/out/off)
and zoom speed, pan direction (8 compass points + off) and pan speed,
and smooth crossfade transitions on/off with a step count. **Save
motion settings** stores them for the next run. Plain-language motion
instructions in step 5 ("add a slow zoom in") merge into these
settings and each change is shown.

**Audio track** (optional, needs survey-animate ≥ 0.2.0): upload your
own audio file — it is muxed under the reel and trimmed to the video
length (AAC for MP4). Use audio you own or have the rights to. Remove
it any time with **Remove audio**.

### Step 5 — Batch queue

Add several descriptions (one per line) and press **Add to queue**:
each becomes a job that snapshots your current settings (motion,
audio, captions, colormap). **Run batch** generates them one after
another, unattended — each job gets an isolated `job-<nn>-<slug>/`
folder and its own status. A failed job is recorded with its error
and the queue continues, so completed reels are never lost. Queued
jobs can be removed with ✕; finished jobs cleared with **Clear
finished**.

### Step 6 — Run the pipeline

**Refine in plain language** (optional) — before running, describe the
changes you want: "zoom in on the Gulf of Mexico", "use a warmer
colormap", "title it 'Gulf Heat'", "run it from 2015 to 2020",
"switch to light mode", "hide the basemap". The app applies them to
the parsed spec via `viz.refine_spec` and shows every applied change
(old → new + reason) plus anything it couldn't understand — then you
press **Run** to regenerate. Only the things you mention change;
automatic titles follow region/variable/time changes, custom titles
are never touched. Needs survey-viz ≥ 0.16.0 — with an older peer
the app says so and does nothing.

Press **Run — fetch, render, encode**. With a progress bar and live
status messages, the pipeline:

1. **Fetches** — the spec's variable for the spec's bbox (30-day stride
   for daily sources) plus a context series where the adapter provides
   one, both with provenance (source URL, SHA-256, retrieval time).
   Fetchable regions/variables are whatever the installed survey-viz +
   survey-currents support (13 sources as of survey-viz 0.15.0 /
   survey-currents 0.15.2).
2. **Renders** — survey-viz renders one PNG per frame (title block,
   map panel, burned-in timestamp, time-series panel with a playhead)
   using the aesthetics from step 3, sized to the platform from step
   2 — or the historical 1080×1920 frames when no platform is chosen.
3. **Encodes** — survey-animate encodes the frames to `reel.mp4`
   (H.264, 30 fps, `reel` preset) with a title card and a
   `.provenance.json` sidecar.

If the region isn't fetchable or the variable isn't supported, you get
an **honest message naming what's missing** — the app never crashes
on these paths.

### Step 7 — Take the reel

* Embedded video player (`st.video`)
* **Download MP4** button
* **Story captions** listing (when captions were generated): each
  caption with its frame range
* **Platform** line: platform + dimensions (or the legacy layout)
* **Provenance** expander: fetch URLs, SHA-256 digests, motion/audio/
  platform settings, the full spec JSON, frame/manifest paths,
  encoder details

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
