"""Parser integration: the 3 demo descriptions against REAL survey-viz code.

Fully offline (deterministic parser + gazetteer only, no network, no LLM).
Skipped when survey-viz is not installed.
"""

from __future__ import annotations

import datetime as _dt

import pytest

viz = pytest.importorskip("viz", reason="survey-viz not installed")

from studio.pipeline import (FetchPlan, plan_fetch, region_to_lake,  # noqa: E402
                             run_pipeline)

TODAY = _dt.date(2026, 9, 26)

DESCRIPTIONS = [
    "surface water temperature oscillation on Lake Superior for the past 10 years",
    "Lake Michigan water temperature last summer",
    "sea level in the Gulf of Mexico 2020 to 2022",
]


def parse(text):
    return viz.parse_description(text, today=TODAY)


# --- description 1: Lake Superior, 10 years ---------------------------------

def test_demo1_region_and_variable():
    spec = parse(DESCRIPTIONS[0])
    assert spec.region_key == "lake-superior"
    assert spec.variable == "sst"


def test_demo1_dates():
    spec = parse(DESCRIPTIONS[0])
    assert spec.start == _dt.date(2016, 9, 26)
    assert spec.end == TODAY


def test_demo1_fetchable():
    spec = parse(DESCRIPTIONS[0])
    assert viz.is_fetchable(spec.region_key) is True
    plan = plan_fetch(spec, viz.is_fetchable)
    assert plan.fetchable is True
    assert plan.kind == "ok"
    assert plan.lake == "superior"


# --- description 2: Lake Michigan, last summer ------------------------------

def test_demo2_region_and_variable():
    spec = parse(DESCRIPTIONS[1])
    assert spec.region_key == "lake-michigan"
    assert spec.variable == "sst"


def test_demo2_dates_last_summer():
    spec = parse(DESCRIPTIONS[1])
    assert (spec.start, spec.end) == (_dt.date(2026, 6, 1), _dt.date(2026, 8, 31))


def test_demo2_fetchable():
    spec = parse(DESCRIPTIONS[1])
    assert viz.is_fetchable(spec.region_key) is True
    assert plan_fetch(spec, viz.is_fetchable).lake == "michigan"


# --- description 3: Gulf of Mexico — the honest unfetchable path -------------

def test_demo3_parses_but_not_fetchable():
    spec = parse(DESCRIPTIONS[2])
    assert spec.region_key == "gulf-of-mexico"
    assert spec.variable == "sea-level"
    assert (spec.start, spec.end) == (_dt.date(2020, 1, 1), _dt.date(2022, 12, 31))
    assert viz.is_fetchable(spec.region_key) is False


def test_demo3_plan_names_missing_adapter():
    spec = parse(DESCRIPTIONS[2])
    plan = plan_fetch(spec, viz.is_fetchable)
    assert isinstance(plan, FetchPlan)
    assert plan.fetchable is False
    assert plan.kind == "no_adapter"
    assert "no fetch adapter" in plan.reason
    assert plan.region_key == "gulf-of-mexico"


def test_demo3_run_pipeline_refuses_without_network():
    """run_pipeline must refuse BEFORE any fetch callable is invoked."""
    import types

    spec = parse(DESCRIPTIONS[2])
    calls = []

    def _boom(*a, **k):
        calls.append((a, k))
        raise AssertionError("fetch must not be attempted")

    fake_peers = types.SimpleNamespace(
        is_fetchable=viz.is_fetchable,
        fetch_sst=_boom,
        fetch_averages=_boom,
        render_viz=_boom,
        render_video=_boom,
        glsea_bounds=(-92.42, 38.87, -75.88, 50.61),
    )
    from studio.pipeline import UnfetchableRegionError
    with pytest.raises(UnfetchableRegionError, match="no fetch adapter"):
        run_pipeline(spec, fake_peers, "/tmp/reel-studio-never-created")
    assert calls == []


# --- region -> lake mapping --------------------------------------------------

@pytest.mark.parametrize("region_key,lake", [
    ("lake-superior", "superior"),
    ("lake-michigan", "michigan"),
    ("lake-huron", "huron"),
    ("lake-erie", "erie"),
    ("lake-ontario", "ontario"),
    ("gulf-of-mexico", None),
    ("", None),
])
def test_region_to_lake(region_key, lake):
    assert region_to_lake(region_key) == lake


def test_unsupported_variable_plan():
    spec = parse("Lake Superior currents over the past 5 years")
    assert spec.variable == "currents"
    plan = plan_fetch(spec, viz.is_fetchable)
    assert plan.fetchable is False
    assert plan.kind == "bad_variable"
    assert "currents" in plan.reason
