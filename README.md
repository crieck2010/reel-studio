# reel-studio 🎬

**Plain-English description → finished vertical reel (MP4).** 100% local.

Type something like *"surface water temperature oscillation on Lake Superior
for the past 10 years"*, confirm the parsed spec, press **Run** — reel-studio
fetches NOAA GLSEA satellite sea-surface temperature, renders vertical
map frames, and encodes a 1080×1920 MP4 you can post straight to
Instagram/TikTok/YouTube Shorts.

This is the studio layer of the mapped.earth-style reel factory:

| Repo | Role |
|---|---|
| [survey-currents](https://github.com/crieck2010/survey-currents) | GLSEA SST + lake-average fetch engine |
| [survey-viz](https://github.com/crieck2010/survey-viz) | description parser + reel frame renderer |
| [survey-animate](https://github.com/crieck2010/survey-animate) | frames → MP4 encoder (resolves ffmpeg) |
| **reel-studio** (this repo) | Streamlit app + UI-free pipeline orchestration |

## Install

```bash
git clone https://github.com/crieck2010/reel-studio.git
cd reel-studio
pip install -r requirements.txt

# Peer engines (separate repos, install once):
pip install git+https://github.com/crieck2010/survey-viz.git
pip install git+https://github.com/crieck2010/survey-currents.git
pip install git+https://github.com/crieck2010/survey-animate.git
```

The app **launches with any peer missing** — the Engine-status panel shows
exactly which `pip install` command fixes it. Parsing needs survey-viz,
fetching needs survey-currents, encoding needs survey-animate.

## Run

```bash
streamlit run app.py
```

The 3-step flow:

1. **Describe** — type a description, press **Parse**. The deterministic
   survey-viz parser turns it into a `VizSpec`, shown as JSON for
   confirmation (region, bbox, variable, dates, title).
2. **Run** — press **Run**. If the region is one of the 5 fetchable Great
   Lakes and the variable is `sst`, the pipeline fetches GLSEA data
   (progress bar + status), renders frames, and encodes the MP4. Anything
   else gets an honest message naming what's missing — no crash.
3. **Take the reel** — embedded `st.video` player, **Download MP4** button,
   and a provenance expander (fetch URLs, SHA-256 hashes, full spec JSON).

Offline demo (no network, no key, no ffmpeg):

```bash
python -m studio.demo
```

## Optional: LLM assist

The parser is deterministic and offline. If `LLM_API_KEY` is set **and**
the parser fails, reel-studio makes **one** assist attempt: it POSTs the
description plus a VizSpec schema summary to an OpenAI-compatible
chat-completions endpoint (`LLM_BASE_URL`, default
`https://api.openai.com/v1`; model from `LLM_MODEL`, default
`gpt-4o-mini`) using only stdlib `urllib` — no extra dependencies. The
returned JSON is validated with `VizSpec.from_dict`; on failure you see
the **original** parse error. The app works **fully** without the key —
it is never required. See [docs/APP.md](docs/APP.md).

## Screenshots (what you'll see)

* **Engine status** — three badges (survey-viz / survey-currents /
  survey-animate), green when installed, amber with the exact install
  command when missing.
* **Step 1** — text area with an example description, **Parse** button,
  then the parsed `VizSpec` rendered as JSON (title, `region_key`,
  `bbox`, `variable`, `start`/`end`, `cadence`).
* **Step 2** — **Run** button, progress bar with live status messages
  ("Fetching GLSEA sea-surface-temperature grid…", "Rendering reel
  frames…", "Encoding MP4…").
* **Step 3** — embedded video player, **Download MP4** button, and a
  *Provenance* expander with fetch URLs, SHA-256 digests, and the spec.

## Docs

* [docs/APP.md](docs/APP.md) — user guide (the 3 steps, LLM assist, troubleshooting)
* [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — how the code is organized
* [docs/INTEROP.md](docs/INTEROP.md) — peer contracts + the two interop
  quirks this build discovered (read before extending)
* [CHANGELOG.md](CHANGELOG.md)

## Manual smoke check

```bash
streamlit run app.py   # app launches; Engine-status shows all three peers
# Parse the default description -> spec JSON appears
# Run -> progress bar -> video player + Download MP4 + provenance expander
```

## Limitations (v0.1.0)

* Fetchable **regions**: the 5 Great Lakes only (Superior, Michigan, Huron,
  Erie, Ontario). Everything else parses fine but reports "no fetch
  adapter yet" — honestly, not a crash.
* Fetchable **variable**: `sst` (surface water temperature) only.
* GLSEA SST grid is clipped to the lakes region; the lake-superior
  gazetteer bbox starts slightly west of the grid floor, so the fetch
  bbox is clamped (recorded in provenance).
* Real fetches need network access to NOAA ERDDAP and `netCDF4`.
* `streamlit run` needs the peers installed; `python -m studio.demo` and
  the test suite degrade gracefully without them.

## License

MIT — see [LICENSE](LICENSE).
