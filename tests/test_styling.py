"""Style presets (survey-style interop) — UI-free tests.

Exercises :mod:`studio.styling` against the real survey-style peer
(installed in the test venv), plus the pipeline/batch plumbing for
``style_preset``. No streamlit, no network.
"""

from __future__ import annotations

import pytest

import style as style_peer
from studio import batch, styling


def _spec(variable="sst"):
    return {
        "title": "Lake Michigan warmth",
        "region_key": "lake-michigan",
        "variable": variable,
        "start": "2026-07-01",
        "end": "2026-07-31",
        "style": "reel-dark",
        "caption": None,
        "underlay": False,
    }


# --- studio.styling -----------------------------------------------------------

def test_preset_names_lists_builtins():
    names = styling.preset_names(style_peer)
    assert names == ["creator", "field-notes", "midnight-ocean",
                     "reel-dark", "reel-light", "storm-chaser"]


def test_preset_label_includes_description():
    label = styling.preset_label(style_peer, "midnight-ocean")
    assert label.startswith("Midnight Ocean — ")


def test_suggested_preset_per_variable():
    assert styling.suggested_preset(style_peer, "sst") == "midnight-ocean"
    assert styling.suggested_preset(style_peer, "wind") == "storm-chaser"
    assert styling.suggested_preset(style_peer, "nope") == "reel-dark"
    assert styling.suggested_preset(style_peer, None) == "reel-dark"


def test_preset_needs_channel_only_for_creator():
    assert styling.preset_needs_channel(style_peer, "creator") is True
    assert styling.preset_needs_channel(style_peer, "reel-dark") is False
    assert styling.preset_needs_channel(style_peer, "nope") is False


def test_apply_preset_rewrites_spec_and_cmap():
    new_spec, cmap = styling.apply_preset(
        style_peer, _spec(), "midnight-ocean")
    assert new_spec["style"] == "reel-dark"
    assert new_spec["caption"] == "Sea Surface Temperature · Lake Michigan"
    assert new_spec["underlay"] is True
    assert cmap == "inferno"


def test_apply_preset_does_not_mutate_input():
    spec = _spec()
    styling.apply_preset(style_peer, spec, "storm-chaser")
    assert spec["caption"] is None
    assert spec["underlay"] is False


def test_apply_preset_creator_needs_channel():
    with pytest.raises(ValueError, match="channel"):
        styling.apply_preset(style_peer, _spec(), "creator")
    new_spec, _ = styling.apply_preset(
        style_peer, _spec(), "creator", channel="  My Channel  ")
    assert new_spec["caption"] == "© My Channel"


def test_apply_preset_unknown_name():
    with pytest.raises(ValueError, match="did you mean"):
        styling.apply_preset(style_peer, _spec(), "midnight-oceen")


def test_styling_survives_missing_peer_gracefully():
    class Broken:
        def suggest_style(self, v):
            raise RuntimeError("boom")
    assert styling.suggested_preset(Broken(), "sst") == "reel-dark"


# --- pipeline / batch plumbing ------------------------------------------------

def test_job_setting_keys_include_style_preset():
    assert "style_preset" in batch.JOB_SETTING_KEYS


def test_run_pipeline_signature_accepts_style_preset():
    import inspect
    from studio import pipeline
    assert "style_preset" in inspect.signature(pipeline.run_pipeline).parameters
