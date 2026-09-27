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
| [survey-viz](https://github.com/crieck2010/survey-viz) | Description parser + reel frame renderer | v0.14.1 |
| [survey-animate](https://github.com/crieck2010/survey-animate) | Frames → MP4 encoder (resolves ffmpeg) | v0.1.0 |
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
```

The app **launches with any peer missing** — the Engine-status panel shows exactly which
`pip install` command fixes it. Parsing needs survey-viz, fetching needs
survey-currents, encoding needs survey-animate.

## Run

```bash
streamlit run app.py
```

Your browser opens to `http://localhost:8501` (paste it manually if it doesn't). The
3-step flow:

1. **Describe** — type a description, press **Parse**. The deterministic parser turns it
   into a `VizSpec`, shown as JSON for confirmation: region, bbox, variable, pinned
   source, the reason that source was chosen, dates, title.
2. **Run** — press **Run**. The pipeline fetches the data (progress bar + status
   messages), renders the frames, and encodes the MP4. Anything the stack can't answer
   gets an honest message naming what's missing — no crash.
3. **Take the reel** — embedded video player, **Download MP4** button, and a
   *Provenance* expander with the exact fetch URLs, SHA-256 hashes, and the full spec
   (every frame is reproducible from what's listed there).

Fully offline demo (no network, no key, no ffmpeg):

```bash
python -m studio.demo
```

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

* **Engine status** — three badges (survey-viz / survey-currents / survey-animate),
  green when installed, amber with the exact install command when missing.
* **Step 1** — text area with an example description, **Parse** button, then the parsed
  `VizSpec` rendered as JSON (title, `region_key`, `bbox`, `variable`, pinned `source`
  with its selection reason, `start`/`end`, `cadence`).
* **Step 2** — **Run** button, progress bar with live status messages ("Fetching
  NOAA GLSEA sea-surface-temperature grid…", "Rendering reel frames…", "Encoding
  MP4…").
* **Step 3** — embedded video player, **Download MP4** button, and a *Provenance*
  expander with fetch URLs, SHA-256 digests, and the spec.

## Docs

* [docs/APP.md](docs/APP.md) — user guide (the 3 steps, LLM assist, troubleshooting)
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the code is organized
* [docs/INTEROP.md](docs/INTEROP.md) — peer contracts + the interop quirks discovered
  during the 13-source build program (read before extending)
* [CHANGELOG.md](CHANGELOG.md)

## Tests

```bash
pytest tests/
```

The suite covers the UI-free pipeline (`studio/pipeline.py`), peer wiring with graceful
degradation, source selection, provenance, and the offline demo. Fresh-clone verified
against the released peers: 226 passed.

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
