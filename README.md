# reel-studio

**Plain-English description → finished vertical reel (MP4).** 100% local.

Type something like *"surface water temperature on Lake Superior for the past 10 years"*,
confirm the parsed spec, press **Run** — reel-studio picks the best data source from
**13 supported satellite/model/in-situ datasets**, fetches it, renders vertical map
frames, and encodes a 1080×1920 MP4 you can post straight to Instagram, TikTok, or
YouTube Shorts.

The natural-language layer is **deterministic and offline**: a rule-based parser turns
each description into a `VizSpec` that pins an explicit source variable *and* the
inspectable reason it was chosen. No LLM is required at runtime.

## The stack

| Repo | Role | Version tested |
|---|---|---|
| [survey-currents](https://github.com/crieck2010/survey-currents) | Data-fetch engines for all 13 sources | v0.15.0 |
| [survey-viz](https://github.com/crieck2010/survey-viz) | Description parser + reel frame renderer | v0.24.0 |
| [survey-animate](https://github.com/crieck2010/survey-animate) | Frames → MP4 encoder (resolves ffmpeg) | v0.2.0 |
| [survey-layout](https://github.com/crieck2010/survey-layout) | Platform aspect ratios + safe-zone canvases (optional) | v0.1.0 |
| [survey-style](https://github.com/crieck2010/survey-style) | Reusable style presets for the Aesthetics step (optional) | v0.1.0 |
| [survey-schedule](https://github.com/crieck2010/survey-schedule) | Scheduled generation: cron/interval/once jobs + run ledger (optional) | v0.1.0 |
| [survey-cache](https://github.com/crieck2010/survey-cache) | Smarter render caching: content-addressed frame/MP4 reuse (optional) | v0.1.0 |
| [survey-derive](https://github.com/crieck2010/survey-derive) | Climatological anomaly products (optional) | v0.1.0 |
| [survey-publish](https://github.com/crieck2010/survey-publish) | Social-media publishing: per-platform adapters + terminal OAuth connect (optional) | v0.1.0 |
| [survey-timescales](https://github.com/crieck2010/survey-timescales) | Suggested time-window framing: season/cycle/event-density modes + reasons (optional) | v0.1.0 |
| [survey-aesthetics](https://github.com/crieck2010/survey-aesthetics) | mapped.earth preset engine: LIC flow streaks, event glow, 3D prisms, advected strands, basemap styles, editorial furniture (optional) | v0.2.0 |
| [survey-gazetteer](https://github.com/crieck2010/survey-gazetteer) | Place-label gazetteer for the aesthetic preset path (optional) | v0.1.0 |
| **reel-studio** (this repo) | Streamlit web app + UI-free pipeline orchestration | — |

## Supported data sources

Everything below is reachable through the app with plain-English descriptions. Sources
marked **keyless** work with no account; a few optional sources need free accounts
(see [Credentials](#credentials)).

| What you ask for | Fetcher source | Access |
|---|---|---|
| Sea-surface temperature (Great Lakes / global) | NOAA GLSEA; NOAA OISST v2.1; NASA JPL MUR v4.1 | keyless |
| Wind, pressure, air temperature, precipitation (ERA5) | Copernicus ERA5 (CDS) | free CDS account |
| Ocean currents | NASA PODAAC OSCAR v2.0; CMEMS Global Ocean Physics | OSCAR keyless |
| Active fires | NASA FIRMS | keyless (API key optional) |
| Sea-ice concentration | NSIDC Sea Ice Index G02135 v4.0 | keyless |
| Precipitation | NASA GPM IMERG V07 | free Earthdata Login |
| Night lights | NASA Black Marble VNP46A2 | free Earthdata Login |
| Bathymetry / elevation basemap | GEBCO 2024 (+ Natural Earth coastlines) | keyless |
| Hurricane tracks | NOAA IBTrACS v04r01 | keyless |
| Water storage anomalies | CSR GRACE/GRACE-FO RL06.3 | keyless |
| River streamgages | USGS Water Services (NWIS) | keyless |
| Ocean color (chlorophyll-a) | NOAA CoastWatch (MODIS Aqua R2022); NASA OBPG fallback | CoastWatch keyless |
| Earthquakes | USGS ComCat FDSN catalog | keyless |

Every render sits on the GEBCO 2024 terrain tint/hillshade + Natural Earth coastline
underlay (failure-tolerant; recorded in the frame manifest). Combination requests
(e.g. *"groundwater decline vs rainfall in California"*) fetch a primary variable plus
best-effort context overlays, with graceful `absent` status when unavailable.

Requests with no matching adapter — e.g. *"sea level"* (satellite altimetry), *"country
borders"*, *"power outage"* (change detection) — are refused honestly with an
explanation, never rendered as a wrong dataset.

## Install

Requirements: Python 3.10+, `git`, and `ffmpeg` (for MP4 encoding —
`winget install ffmpeg` / `brew install ffmpeg` / `sudo apt install ffmpeg`).

```bash
git clone https://github.com/crieck2010/reel-studio.git
cd reel-studio
pip install -r requirements.txt

# Peer engines (separate repos, install once):
pip install git+https://github.com/crieck2010/survey-viz.git
pip install git+https://github.com/crieck2010/survey-currents.git
pip install git+https://github.com/crieck2010/survey-animate.git
pip install git+https://github.com/crieck2010/survey-layout.git   # optional: platform layouts
pip install git+https://github.com/crieck2010/survey-style.git    # optional: style presets
pip install git+https://github.com/crieck2010/survey-schedule.git # optional: scheduled generation
pip install git+https://github.com/crieck2010/survey-cache.git    # optional: render caching
pip install git+https://github.com/crieck2010/survey-derive.git   # optional: derived anomaly products
pip install git+https://github.com/crieck2010/survey-publish.git  # optional: social publishing
pip install git+https://github.com/crieck2010/survey-timescales.git  # optional: suggested time windows
```

The app **launches with any peer missing** — the Engine-status panel shows exactly which
`pip install` command fixes it. Parsing needs survey-viz, fetching needs
survey-currents, encoding needs survey-animate; survey-layout unlocks the platform
aspect ratios, survey-style unlocks one-click style presets, survey-schedule unlocks
the Schedule step (without it, only the legacy 1080×1920 layout is offered);
survey-cache unlocks the render cache (without it, every run renders and
encodes from scratch); survey-derive unlocks derived anomaly products;
survey-publish unlocks the Publish step (without it, reels stay local);
survey-timescales unlocks the Suggested window control in the Run step
(without it, the dates stay exactly as parsed).

## Run

```bash
streamlit run app.py
```

Your browser opens to `http://localhost:8501` (paste it manually if it doesn't). The
7-step flow:

1. **Describe** — type a description, press **Parse**. The deterministic parser turns it
   into a `VizSpec`, shown as JSON for confirmation: region, bbox, variable, pinned
   source, the reason that source was chosen, dates, title.
2. **Platform** — pick the target platform (TikTok, Instagram Reels, YouTube Shorts,
   X portrait, square, widescreen). Frame size and the title/map/chart/caption/footer
   regions follow the platform's aspect ratio and measured safe zones, shown as a
   schematic with the red interface-chrome zones labeled (needs survey-layout and
   survey-viz ≥ 0.18.0; without them the legacy 1080×1920 layout is used and no
   safe-zone support is claimed).
3. **Aesthetics** — pick Dark/Light style, edit the title, add an optional footer
   caption, toggle the basemap underlay, and choose a colormap (continuous data maps
   only) — or paste a reel URL / upload a screenshot to copy its color mood.
   **mapped.earth presets** — Dark flow (LIC current/wind streaks), Dark glow
   (event glow with bloom), Paper prism (3D extrusion) — with frame rotation
   (auto/manual), subtitle, watermark, honesty-line controls, and **place
   labels** (Off / Auto — gazetteer cities/towns, once per reel — / Custom
   with name search and disambiguation; needs survey-viz ≥ 0.23.0 and the
   survey-gazetteer peer).
   Press **Apply aesthetics** to store the spec that will run.
4. **Cinematic motion & audio** — set zoom mode/speed, pan direction/speed, and
   crossfade smoothing; optionally attach your own audio file to mux under the reel
   (needs survey-animate ≥ 0.2.0).
5. **Batch queue** — add several descriptions (one per line), each snapshotting your
   current settings (motion, audio, captions, colormap, platform), and generate them
   unattended, one after another. A failed job never loses completed ones.
6. **Run** — optionally **Refine in plain language** first ("zoom in on the Gulf of
   Mexico and use a warmer colormap"), then press **Run**. The pipeline fetches the
   data (progress bar + status messages), renders the frames, and encodes the MP4.
   Anything the stack can't answer gets an honest message naming what's missing —
   no crash.
7. **Take the reel** — embedded video player, **Download MP4** button, story-caption
   listing when captions were generated, platform + dimensions, and a
   *Provenance* expander with the exact fetch URLs, SHA-256 hashes, and the full spec
   (every frame is reproducible from what's listed there).

## Updating

No command line needed: double-click **`Update reel-studio.bat`** in the repo folder.
It git-pulls reel-studio, upgrades the ten peer engines (survey-viz, survey-currents,
survey-animate, survey-layout, survey-style, survey-schedule, survey-cache, survey-derive,
survey-publish, survey-timescales) — from a local checkout when one sits next to the repo, otherwise
straight from GitHub — and prints the installed versions for confirmation. Run it any
time a new release is announced, then launch with the **Reel Studio** desktop icon.

Fully offline demo (no network, no key, no ffmpeg):

```bash
python -m studio.demo
```

## Publishing

Step 9 of the app publishes the finished reel to social platforms via the
optional ninth engine, [survey-publish](https://github.com/crieck2010/survey-publish).
All publishing logic lives in that engine; the app only collects your approval,
builds the requests, and records the results.

* **Platforms:** YouTube, Instagram, Facebook, TikTok — each with its own
  adapter and stored credentials.
* **Prerequisites:** each platform needs its own app/API credentials
  (API key / OAuth client), set up per that platform's
  `docs/SETUP_<PLATFORM>.md` in the survey-publish repo.
* **Connect flow:** connect each platform once from a terminal —
  `survey-publish connect <platform>` (`survey-publish status` shows what's
  connected). OAuth needs a browser, so connect is deliberately terminal-based;
  the app never asks for tokens. Step 9 then shows a per-platform
  connected/not-connected panel, with the exact connect command for any
  platform that isn't connected yet.
* **Approval model:** edit the title, caption, and hashtags (prefilled from the
  reel), then press **Approve & Publish**. The app publishes only to platforms
  whose adapter is connected — an unconnected platform is never published to.
  Approval is in-app only in this version: email click-to-approve was evaluated
  and deliberately excluded (mail-scanner link prefetching would fire approvals
  on its own, and it would need public hosting).
* **Record:** every approval is appended to the run's `manifest.json` under a
  `"publication"` section (`approved_at`, per-platform `ok`/`url_or_id`/`error`,
  title, caption, hashtags).
* **Honest limits:** the reel publishes as-is — audio was already muxed by
  survey-animate upstream. Platform availability is whatever adapters
  survey-publish ships; the app shows its documented set and defers to the
  engine's registry.

See [docs/PUBLISHING.md](docs/PUBLISHING.md) for the full reference.

## Example descriptions

```
"surface water temperature on Lake Superior for the past 10 years"
"sea surface temperature oscillation in the Gulf Stream region, 2015 to 2020"
"wildfires in California last summer"
"Arctic sea ice extent for March 2024"
"rainfall over the Gulf of Mexico during hurricane season 2024"
"city lights in Texas at night, 2020 to 2024"
"Hurricane Katrina's track across the Atlantic"
"groundwater decline in California over the past 5 years"
"river discharge in California over the past 3 months"
"chlorophyll bloom in the North Atlantic for March 2024"
"earthquakes in Japan over the past 10 years"
```

## Credentials

The parser and most keyless sources need nothing. Optional sources that need free
accounts:

| Source | What's needed |
|---|---|
| ERA5 (Copernicus CDS) | free CDS account + `cdsapi` config |
| GPM IMERG / Black Marble / OBPG ocean color | free NASA Earthdata Login |
| CMEMS currents / ocean physics | free Copernicus Marine account |

Missing credentials surface as `CredentialsMissing` with setup instructions — never as a
crash or a silent omission. The core reel experience is fully usable with zero accounts.

## Optional: LLM assist

The parser is deterministic and offline. If `LLM_API_KEY` is set **and** the parser
fails, reel-studio makes **one** assist attempt: it POSTs the description plus a
VizSpec schema summary to an OpenAI-compatible chat-completions endpoint
(`LLM_BASE_URL`, default `https://api.openai.com/v1`; model from `LLM_MODEL`, default
`gpt-4o-mini`) using only stdlib `urllib` — no extra dependencies. The returned JSON
is validated with `VizSpec.from_dict`; on failure you see the **original** parse error.
The app works **fully** without the key — it is never required. See
[docs/APP.md](docs/APP.md).

## What you'll see

* **Engine status** — nine badges (survey-viz / survey-currents / survey-animate / survey-layout / survey-style / survey-schedule / survey-cache / survey-derive / survey-publish),
  green when installed, amber with the exact install command when missing.
* **Step 1** — text area with an example description, **Parse** button, then the parsed
  `VizSpec` rendered as JSON (title, `region_key`, `bbox`, `variable`, pinned `source`
  with its selection reason, `start`/`end`, `cadence`).
* **Step 2 — Platform** — target-platform picker (TikTok, Instagram Reels,
  YouTube Shorts, X portrait, square, widescreen): aspect ratio, dimensions,
  and a safe-zone schematic with the platform's interface chrome (top
  navigation/status, right action rail, bottom captions/channel/progress)
  shaded red and labeled in plain words. Measurements are community-measured
  approximations, not official specs. Needs survey-layout + survey-viz ≥
  0.18.0; without them only the legacy 1080×1920 layout is offered.
* **Step 3 — Aesthetics** — style picker (Dark/Light), title and optional
  footer-caption inputs, basemap-underlay checkbox, and a colormap picker
  (Automatic + 30 curated names; continuous data maps only — categorical
  products like earthquakes keep their fixed scientific colors). **Apply
  aesthetics** validates the tweaks against the installed survey-viz and
  shows the spec that will run. **Copy the look of a reel** — paste a reel
  URL or upload a screenshot to copy its color mood (dark/light style +
  colormap suggestion). **Data-driven story captions** — burn
  peak/trend captions onto the frames (needs survey-viz ≥ 0.17.0).
  **mapped.earth presets** (needs survey-viz ≥ 0.22.0) — Dark flow, Dark
  glow, Paper prism: chrome-free frames, editorial typography, custom
  legends, fixed reel-wide scales. Rotation (Off/Auto/Manual degrees),
  subtitle (empty = auto time-window label), watermark (off by default),
  and honesty-line toggle are all user-overridable; incompatible
  variable/preset and preset/platform-canvas combinations warn or fail
  fast with plain-words reasons. The preset is part of the render
  fingerprint, so changing it invalidates the frame cache.
* **Step 4 — Cinematic motion & audio** — enable camera motion and set
  zoom mode/speed, pan direction/speed, and crossfade smoothing; attach
  your own audio file to mux under the reel (needs survey-animate ≥
  0.2.0).
* **Step 5 — Batch queue** — add several descriptions (one per line),
  each snapshotting your current settings (motion, audio, captions,
  colormap, platform), and generate them unattended, one after another.
  A failed job never loses completed ones.
* **Step 6 — Scheduled generation** — turn the reel factory on autopilot: a job
  reuses a description plus your current settings (motion, audio, captions,
  colormap, platform, style preset) on a `cron:`, `every:`, or `once:` schedule.
  Jobs live as JSON files in `~/.reel-studio/jobs`; a ticker
  (`python -m studio.scheduler run-due`, every minute via Task Scheduler or cron)
  runs due jobs and every attempt lands in the ledger. Needs survey-schedule;
  the PC must be on at run time — a missed run is skipped, never backfilled.
* **Render cache** — with the survey-cache peer installed, the Run step shows a
  cache panel: identical re-runs reuse cached frames and the cached MP4 instead
  of re-rendering (cache keys fingerprint the spec, the fetched-data bytes, the
  render/encode settings, and the peer versions, so new satellite data always
  re-renders honestly). Toggle per run, inspect usage, clear on demand.
* **Step 7** — **Derived product: climatological anomaly** (optional) — render
  the anomaly instead of the raw variable (anomaly / standardized anomaly /
  percent of normal vs a user-chosen baseline period, default 1991–2020),
  then **Refine in plain language** ("zoom in on the Gulf of Mexico
  and use a warmer colormap", "add a slow zoom in during the video"),
  a **Time window** expander with start/end pickers plus a one-click
  **Suggest window** control (with the optional survey-timescales
  engine: the engine suggests the window for this variable + region and
  says why — your edits always win), then the **Run** button, progress
  bar with live status messages
  ("Fetching NOAA GLSEA sea-surface-temperature grid…", "Rendering
  reel frames…", "Encoding MP4…").
* **Step 8** — embedded video player, **Download MP4** button, story-caption
  listing when captions were generated, platform + dimensions, and a *Provenance* expander with
  fetch URLs, SHA-256 digests, motion/audio/platform settings, and the spec.
* **Step 9** — Publish: per-platform connection panel, editable title/caption/hashtags,
  and **Approve & Publish** to every connected platform (YouTube, Instagram, Facebook,
  TikTok), with each approval recorded in the run manifest.

## Docs

* [docs/APP.md](docs/APP.md) — user guide (the 9 steps, LLM assist, troubleshooting)
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the code is organized
* [docs/INTEROP.md](docs/INTEROP.md) — peer contracts + the interop quirks discovered
  during the 13-source build program (read before extending)
* [docs/DERIVED.md](docs/DERIVED.md) — derived climatological anomaly
  products: the science, the controls, eligibility rules, and honest limits
* [docs/PUBLISHING.md](docs/PUBLISHING.md) — the Publish step: per-platform
  account prerequisites, terminal connect flow, and the in-app approval model
* [CHANGELOG.md](CHANGELOG.md)

## Tests

```bash
pytest tests/
```

The suite covers the UI-free pipeline (`studio/pipeline.py`), the batch
queue (`studio/batch.py`), peer wiring with graceful degradation, source
selection, provenance, and the offline demo. Fresh-clone verified
against the released peers: 290 passed.

## Manual smoke check

```bash
streamlit run app.py   # app launches; Engine-status shows all three peers
# Parse the default description -> spec JSON appears
# Run -> progress bar -> video player + Download MP4 + provenance expander
```

## Limitations

* Some sources are US-only (USGS streamgages) or coverage-limited by their nature —
  requests outside coverage get an honest empty result, never fabricated data.
* Gap-prone products (GRACE months, ocean color cloud cover, fire seasons) render
  explicit "no observation" frames instead of being interpolated.
* Certain derived asks are refused honestly: sea level (no altimetry adapter), power
  outages / change detection, country borders as a data layer, forecast seismic hazard
  (the catalog is observed events only).
* First runs are slow (data downloads); re-runs hit the local cache.
* `streamlit run` needs the peers installed; `python -m studio.demo` and the test
  suite degrade gracefully without them.

## License

MIT — see [LICENSE](LICENSE).
