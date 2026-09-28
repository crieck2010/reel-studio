"""Headless tests for the v0.4.0 app helpers (no Streamlit runtime needed)."""

from __future__ import annotations

import json
import os
import sys
import types

import pytest


@pytest.fixture(scope="module")
def app_module():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    try:
        import streamlit  # noqa: F401
    except ImportError:
        sys.modules["streamlit"] = types.ModuleType("streamlit")
    sys.modules.pop("app", None)
    import app as app_mod
    return app_mod


def _status(module=None, installed=True, repo="survey-viz"):
    return types.SimpleNamespace(
        installed=installed, module=module, repo=repo,
        pip_command="pip install x")


# --- _peer_version_tuple / _version_ok ----------------------------------------

def test_version_tuple_parses(app_module):
    m = types.SimpleNamespace(__version__="0.17.0")
    assert app_module._peer_version_tuple(_status(m)) == (0, 17, 0)


def test_version_tuple_missing_module_is_zero(app_module):
    assert app_module._peer_version_tuple(_status(None, installed=False)) == (0, 0, 0)


def test_version_ok_compares(app_module):
    m = types.SimpleNamespace(__version__="0.16.0")
    assert app_module._version_ok(_status(m), (0, 16, 0)) is True
    assert app_module._version_ok(_status(m), (0, 17, 0)) is False


def test_upgrade_hint_names_repo_and_command(app_module):
    hint = app_module._upgrade_hint(_status(None), "0.17.0", "Story captions")
    assert "0.17.0" in hint and "pip install x" in hint


# --- _caption_events ------------------------------------------------------------

def test_caption_events_reads_real_manifest_shape(app_module, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({
        "render": {"story_captions": [
            {"frame_start": 0, "frame_end": 1, "text": "Peak a"},
            {"frame_start": 2, "frame_end": 4, "text": "Trend b"},
        ]}}))
    events = app_module._caption_events(str(manifest))
    assert [e["text"] for e in events] == ["Peak a", "Trend b"]


def test_caption_events_missing_or_bad_manifest(app_module, tmp_path):
    assert app_module._caption_events(None) == []
    assert app_module._caption_events(str(tmp_path / "nope.json")) == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert app_module._caption_events(str(bad)) == []
    nodata = tmp_path / "empty.json"
    nodata.write_text(json.dumps({"render": {}}))
    assert app_module._caption_events(str(nodata)) == []


# --- motion option vocabularies -------------------------------------------------

def test_motion_vocabularies_match_peers(app_module):
    assert set(app_module.ZOOM_MODES) == {"off", "in", "out"}
    assert set(app_module.PAN_DIRECTIONS) == {
        "off", "up", "down", "left", "right",
        "up-left", "up-right", "down-left", "down-right"}
    assert set(app_module.PAN_LABELS) == set(app_module.PAN_DIRECTIONS)
