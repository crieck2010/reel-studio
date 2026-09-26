"""VizSpec round-trip + validation, against REAL survey-viz code.

Skipped when survey-viz is not installed.
"""

from __future__ import annotations

import datetime as _dt

import pytest

viz = pytest.importorskip("viz", reason="survey-viz not installed")
VizSpec = viz.VizSpec


def make_spec(**overrides):
    kwargs = dict(
        title="Lake Superior — Surface Water Temperature, 2016–2026",
        region_key="lake-superior",
        bbox=(-92.5, 46.0, -84.5, 48.8),
        variable="sst",
        start=_dt.date(2016, 9, 26),
        end=_dt.date(2026, 9, 26),
        cadence="monthly",
    )
    kwargs.update(overrides)
    return VizSpec(**kwargs)


def test_round_trip_dict_equal():
    spec = make_spec()
    rebuilt = VizSpec.from_dict(spec.to_dict())
    assert rebuilt.to_dict() == spec.to_dict()


def test_round_trip_preserves_types():
    rebuilt = VizSpec.from_dict(make_spec().to_dict())
    assert isinstance(rebuilt.start, _dt.date)
    assert isinstance(rebuilt.bbox, tuple)
    assert rebuilt.bbox == (-92.5, 46.0, -84.5, 48.8)


def test_from_dict_accepts_iso_strings():
    data = make_spec().to_dict()
    assert isinstance(data["start"], str)
    rebuilt = VizSpec.from_dict(data)
    assert rebuilt.start == _dt.date(2016, 9, 26)


def test_from_dict_rejects_non_dict():
    with pytest.raises(TypeError):
        VizSpec.from_dict(["not", "a", "dict"])


def test_start_after_end_rejected():
    with pytest.raises(ValueError, match="must not be after"):
        make_spec(start=_dt.date(2026, 1, 1), end=_dt.date(2020, 1, 1))


def test_bad_bbox_rejected():
    with pytest.raises(ValueError):
        make_spec(bbox=(-84.5, 46.0, -92.5, 48.8))  # lon_min > lon_max


def test_bad_layout_rejected():
    with pytest.raises(ValueError):
        make_spec(layout="wide")


def test_bad_style_rejected():
    with pytest.raises(ValueError):
        make_spec(style="neon")


def test_bad_cadence_rejected():
    with pytest.raises(ValueError):
        make_spec(cadence="hourly")


def test_empty_title_rejected():
    with pytest.raises(ValueError):
        make_spec(title="   ")


def test_json_serializable():
    import json

    assert json.loads(json.dumps(make_spec().to_dict())) == make_spec().to_dict()
