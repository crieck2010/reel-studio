"""Graceful degradation: peers missing, wiring errors, honest refusals.

These tests never import the peers; they exercise reel-studio's own
degradation paths with stubbed modules.
"""

from __future__ import annotations

import importlib
import sys
import types

import pytest

from studio import peers, pipeline


# --- load_peers ---------------------------------------------------------------

def test_load_peers_returns_five_statuses():
    statuses = peers.load_peers()
    assert set(statuses) == {"survey-viz", "survey-currents",
                             "survey-animate", "survey-layout",
                             "survey-style", "survey-schedule"}
    for repo, status in statuses.items():
        assert isinstance(status, peers.PeerStatus)
        assert status.repo == repo
        assert status.pip_command.startswith(
            "pip install git+https://github.com/crieck2010/")


def test_missing_peer_error_names_pip_command():
    statuses = {
        "survey-viz": peers.PeerStatus(
            repo="survey-viz", module_name="viz", module=None,
            pip_command="pip install git+https://github.com/crieck2010/survey-viz.git",
            needed_for="parsing"),
    }
    with pytest.raises(peers.MissingPeerError) as excinfo:
        peers.require_peer(statuses, "survey-viz")
    assert "git+https://github.com/crieck2010/survey-viz.git" in str(excinfo.value)
    assert excinfo.value.pip_command.startswith("pip install git+")


def test_require_peer_returns_stubbed_module(monkeypatch):
    fake = types.ModuleType("viz")
    monkeypatch.setitem(sys.modules, "viz", fake)
    statuses = peers.load_peers()
    assert statuses["survey-viz"].installed is True
    assert peers.require_peer(statuses, "survey-viz") is fake


def test_wire_peers_missing_viz_first(monkeypatch):
    statuses = {
        repo: peers.PeerStatus(repo=repo, module_name=m, module=None,
                               pip_command=f"pip install git+https://github.com/crieck2010/{repo}.git",
                               needed_for="x")
        for repo, m in (("survey-viz", "viz"), ("survey-currents", "currents"),
                        ("survey-animate", "animate"))
    }
    with pytest.raises(peers.MissingPeerError, match="survey-viz"):
        peers.wire_peers(statuses)


def test_wire_peers_missing_second_names_it(monkeypatch):
    fake_viz = types.ModuleType("viz")
    statuses = {
        "survey-viz": peers.PeerStatus(repo="survey-viz", module_name="viz",
                                       module=fake_viz, pip_command="p", needed_for="x"),
        "survey-currents": peers.PeerStatus(repo="survey-currents", module_name="currents",
                                            module=None, pip_command="p", needed_for="x"),
        "survey-animate": peers.PeerStatus(repo="survey-animate", module_name="animate",
                                           module=None, pip_command="p", needed_for="x"),
    }
    with pytest.raises(peers.MissingPeerError, match="survey-currents"):
        peers.wire_peers(statuses)


# --- plan_fetch with stubbed is_fetchable --------------------------------------

def _stub_spec(region_key="gulf-of-mexico", variable="chlorophyll"):
    return types.SimpleNamespace(region_key=region_key, variable=variable)


def test_plan_fetch_no_adapter_stubbed():
    plan = pipeline.plan_fetch(_stub_spec(), lambda key: False)
    assert plan.fetchable is False
    assert plan.kind == "no_adapter"
    assert "gulf-of-mexico" in plan.reason


def test_plan_fetch_bad_variable_stubbed():
    plan = pipeline.plan_fetch(
        _stub_spec(region_key="lake-erie", variable="currents"),
        lambda key: True)
    assert plan.fetchable is False
    assert plan.kind == "bad_variable"
    assert "currents" in plan.reason


def test_plan_fetch_ok_stubbed():
    plan = pipeline.plan_fetch(
        _stub_spec(region_key="lake-erie", variable="sst"), lambda key: True)
    assert plan.fetchable is True
    assert plan.kind == "ok"
    assert plan.lake == "erie"


# --- parse_with_fallback -------------------------------------------------------

class _ParseError(Exception):
    pass


def test_parse_with_fallback_no_assist_reraises():
    def parse_fn(text):
        raise _ParseError("nope")

    with pytest.raises(_ParseError, match="nope"):
        pipeline.parse_with_fallback(text="x", parse_fn=parse_fn,
                                     parse_exc=_ParseError)


def test_parse_with_fallback_success_no_assist():
    spec, used = pipeline.parse_with_fallback(
        text="x", parse_fn=lambda t: "SPEC", parse_exc=_ParseError)
    assert (spec, used) == ("SPEC", False)


def test_parse_with_fallback_assist_success():
    def parse_fn(text):
        raise _ParseError("deterministic failed")

    spec, used = pipeline.parse_with_fallback(
        text="x", parse_fn=parse_fn, parse_exc=_ParseError,
        assist_fn=lambda t: "ASSISTED")
    assert (spec, used) == ("ASSISTED", True)


def test_parse_with_fallback_assist_failure_reraises_original():
    def parse_fn(text):
        raise _ParseError("original deterministic failure")

    def assist_fn(text):
        raise RuntimeError("llm blew up")

    with pytest.raises(_ParseError, match="original deterministic failure"):
        pipeline.parse_with_fallback(
            text="x", parse_fn=parse_fn, parse_exc=_ParseError,
            assist_fn=assist_fn)
