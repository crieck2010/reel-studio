# reel-studio architecture

## Layout

```
reel-studio/
├── app.py                 # Streamlit UI only (no engine code)
├── studio/
│   ├── __init__.py        # version
│   ├── peers.py           # optional peer imports + MissingPeerError + wiring
│   ├── pipeline.py        # UI-free orchestration: parse/plan/fetch/render/encode
│   ├── batch.py           # batch queue: sequential jobs, isolated dirs, failure containment
│   ├── llm_assist.py      # optional OpenAI-compatible assist (stdlib urllib)
│   └── demo.py            # offline 3-description demo
├── tests/                 # 290 pytest tests, fully offline
├── requirements.txt       # streamlit, numpy, matplotlib, netCDF4 (+ pytest)
└── docs/
    ├── APP.md             # user guide
    ├── ARCHITECTURE.md    # this file
    └── INTEROP.md         # peer contracts + discovered quirks
```

## Design principles

1. **Peers are optional, never hard imports.** `studio/peers.py` tries
   each import and records availability; `MissingPeerError` carries the
   exact `pip install git+https://...` fix. The app launches with any
   subset missing.
2. **The pipeline is UI-free.** `studio/pipeline.py` takes injected
   callables (`peers.wire_peers()` builds them from the real modules),
   so orchestration is testable with fake peers — no Streamlit, no
   network.
3. **Honest refusals, not crashes.** `plan_fetch()` decides fetchability
   without touching the network; unfetchable regions and unsupported
   variables become `UnfetchableRegionError` /
   `UnsupportedVariableError` with messages naming the missing adapter.
4. **Adapt at the boundary.** reel-studio never imports peer internals
   beyond their documented entry points, and it adapts peer shapes
   (dict forms for viz's duck-typed inputs) rather than relying on
   attribute coincidence — see the two quirks in docs/INTEROP.md.
5. **Provenance everywhere.** Every run collects fetch URLs, SHA-256
   digests, the spec JSON, frame/manifest paths, and encoder details
   into one provenance dict, shown in the app's expander.

## Module responsibilities

* **`studio/peers.py`** — `PEER_SPECS` (repo → module/pip-command/purpose),
  `load_peers()` (never raises), `require_peer()` (raises
  `MissingPeerError`), `wire_peers()` (builds the callables namespace:
  parse, is_fetchable, fetch_sst, fetch_averages, render_viz,
  render_video, plus `glsea_bounds` for bbox clamping; `layout`,
  `to_viz_canvas`, `get_platform`, `list_platforms`, `layout_pip` for
  the optional survey-layout peer — all None when it is missing, and
  the pipeline raises `PeerTooOldError` with the install command only
  on actual use).
* **`studio/pipeline.py`** — `FetchPlan`/`plan_fetch()`
  (fetchability without network), `region_to_lake()`,
  `parse_with_fallback()` (deterministic parse + optional one-shot LLM
  assist, original error wins), `run_pipeline()` (fetch → adapt →
  render → encode → `RunResult`), `_field_to_dict()`/`_series_to_dict()`
  (peer-shape adapters), `_clamp_bbox()` (GLSEA grid fit).
* **`studio/llm_assist.py`** — `assist_fetch_spec_dict()` (one POST,
  injectable `urlopen` for tests), `assist_from_env()` (reads
  `LLM_API_KEY`/`LLM_BASE_URL`/`LLM_MODEL`; returns None when unset),
  `SPEC_SCHEMA_SUMMARY` (the contract sent to the model).
* **`studio/demo.py`** — offline demo over the 3 canonical descriptions.
* **`app.py`** — Streamlit only: peer-status panel, Parse step (spec
  JSON), Run step (progress bar → video + download + provenance
  expander), LLM-assist wiring. Headless-safe: `main()` runs only under
  a real Streamlit runtime (`_streamlit_is_running()`), so
  `import app` works with or without streamlit installed.

## Data flow

```
description text
  → parse_description (viz) → VizSpec ──→ st.json (confirmation)
  → plan_fetch (fetchable? lake?) ──→ honest refusal | continue
  → fetch_glsea_sst (currents) → GlseaField ──┐
  → fetch_glsea_lake_averages (currents) → LakeSeries ──┤ adapt to viz dict forms
  → render_viz (viz) → frames/ + manifest.json ──→ frames DIR ──→ render_video (animate)
  → reel.mp4 + .provenance.json + RunResult.provenance
```

The platform step (survey-layout) feeds the render stage: `run_pipeline`
builds `layout.to_viz_canvas(platform, flavor=...)` (flavor `quake` for
earthquakes, `standard` otherwise) and passes it as
`render_viz(..., canvas=...)`, so frame dimensions and the
title/map/chart/caption/footer regions follow the platform's aspect
ratio and safe zones. `platform=None`/`"legacy"` passes no canvas —
older survey-viz peers (no `canvas` keyword) keep working untouched,
and the layout peer is never *required*.

## Test strategy

74 tests, all offline: parser integration against real viz (3 demo
descriptions incl. the unfetchable path), VizSpec round-trip, peer
degradation with stubbed modules, pipeline orchestration with fake
peers (ordering, provenance, bbox clamp, failure wrapping, honest
refusals), LLM assist with mocked urllib (success + every failure
mode), headless `import app` smoke test (stubbed streamlit), demo
output assertions, and a real-peers end-to-end (synthetic GLSEA field →
real render → real encode → valid MP4).
