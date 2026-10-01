"""reel-studio: plain-English description -> finished vertical reel (MP4).

UI-free orchestration lives in :mod:`studio.pipeline`; the Streamlit UI
is ``app.py`` at the repo root. Peers (survey-viz, survey-currents,
survey-animate, survey-layout, survey-style, survey-schedule,
survey-cache, survey-derive, survey-publish) are optional and never
hard-imported.
"""

__version__ = "0.18.0"

__all__ = ["__version__"]
