# Changelog

All notable changes to reel-studio. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

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
