"""Offline demo test: the 3 descriptions print parsed specs, no network.

Skipped when survey-viz is not installed.
"""

from __future__ import annotations

import datetime as _dt
import io

import pytest

pytest.importorskip("viz", reason="survey-viz not installed")

from studio import demo  # noqa: E402


def test_demo_prints_three_specs():
    buf = io.StringIO()
    rc = demo.main(today=_dt.date(2026, 9, 26), out=buf)
    assert rc == 0
    text = buf.getvalue()
    assert text.count("parsed VizSpec:") == 3
    assert '"region_key": "lake-superior"' in text
    assert '"region_key": "lake-michigan"' in text
    assert '"region_key": "gulf-of-mexico"' in text


def test_demo_shows_unfetchable_path():
    buf = io.StringIO()
    demo.main(today=_dt.date(2026, 9, 26), out=buf)
    text = buf.getvalue()
    assert "NOT FETCHABLE" in text
    assert "no fetch adapter" in text
    assert "FETCHABLE" in text  # the two Great-Lakes examples


def test_demo_missing_viz_returns_2(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "viz":
            raise ImportError("no viz")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    buf = io.StringIO()
    rc = demo.main(out=buf)
    assert rc == 2
    assert "survey-viz" in buf.getvalue()
