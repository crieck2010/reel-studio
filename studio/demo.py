"""Offline demo: parse 3 example descriptions, print their VizSpecs.

No network, no LLM key, no ffmpeg needed — this only exercises the
deterministic survey-viz parser plus reel-studio's fetch planning. The
third example (Gulf of Mexico) demonstrates the honest unfetchable-region
path: it parses fine, but has no fetch adapter yet.

Usage:  python -m studio.demo
"""

from __future__ import annotations

import datetime as _dt
import json
import sys
from typing import Optional, TextIO

DEMO_DESCRIPTIONS = [
    "surface water temperature oscillation on Lake Superior for the past 10 years",
    "Lake Michigan water temperature last summer",
    "chlorophyll in the Gulf of Mexico 2020 to 2022",
]

#: Fixed reference date so the demo output is deterministic.
DEMO_TODAY = _dt.date(2026, 9, 26)


def main(today: Optional[_dt.date] = None,
         out: Optional[TextIO] = None) -> int:
    """Run the demo; returns 0 on success, 2 when survey-viz is missing."""
    out = out or sys.stdout
    today = today or DEMO_TODAY
    try:
        from viz import is_fetchable, parse_description
    except ImportError:
        out.write(
            "demo needs survey-viz, which is not installed.\n"
            "Install it with:\n"
            "    pip install git+https://github.com/crieck2010/survey-viz.git\n"
        )
        return 2

    from studio.pipeline import plan_fetch

    def emit(text: str) -> None:
        out.write(text + "\n")

    emit("reel-studio demo — 3 descriptions, deterministic parser, no network")
    emit("=" * 72)
    for i, text in enumerate(DEMO_DESCRIPTIONS, 1):
        emit(f"\n[{i}] {text!r}")
        try:
            spec = parse_description(text, today=today)
        except Exception as exc:  # UnparseableDescription
            emit(f"    parse FAILED: {exc}")
            continue
        emit("    parsed VizSpec:")
        for line in json.dumps(spec.to_dict(), indent=2).splitlines():
            emit("      " + line)
        plan = plan_fetch(spec, is_fetchable)
        if plan.fetchable:
            emit(f"    fetch plan: FETCHABLE — {plan.reason}")
            emit(f"    lake-averages key: {plan.lake!r}")
        else:
            emit("    fetch plan: NOT FETCHABLE (honest path, no crash)")
            emit(f"    reason: {plan.reason}")
    emit("\n" + "=" * 72)
    emit("done — run the Streamlit app for the full pipeline: streamlit run app.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
