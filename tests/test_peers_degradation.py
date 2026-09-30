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

def test_load_peers_returns_all_statuses():
    statuses = peers.load_peers()
    assert set(statuses) == {"survey-viz", "survey-currents",
                             "survey-animate", "survey-layout",
                             "survey-style", "survey-schedule",
                             "survey-cache", "survey-derive",
                             "survey-publish", "survey-timescales"}
    for repo, status in statuses.items():
        assert isinstance(status, peers.PeerStatus)
        assert status.repo == repo
        assert status.pip_command.startswith(
            "pip install git+https://github.com/crieck2010/")


def test_timescales_peer_spec():
    spec = peers.PEER_SPECS["survey-timescales"]
    assert spec["module"] == "timescales"
    assert spec["pip"] == ("pip install "
                           "git+https://github.com/crieck2010/survey-timescales.git")
    assert "suggest_window" in spec["needed_for"]


def _stub_wire_statuses():
    """Statuses whose first three peers are importable stubs, so
    wire_peers() can run headless for the optional-peer probes."""
    viz = types.ModuleType("viz")
    viz.parse_description = lambda *a, **k: None
    viz.UnparseableDescription = type("UnparseableDescription",
                                      (Exception,), {})
    viz.VizSpec = type("VizSpec", (), {})
    viz.is_fetchable = lambda key: True
    viz.get_region = lambda key: None
    viz.render_viz = lambda *a, **k: None
    currents = types.ModuleType("currents")
    glsea = types.ModuleType("currents.glsea")
    glsea.fetch_glsea_sst = lambda *a, **k: None
    glsea.fetch_glsea_lake_averages = lambda *a, **k: None
    (glsea.GLSEA_LON_MIN, glsea.GLSEA_LAT_MIN,
     glsea.GLSEA_LON_MAX, glsea.GLSEA_LAT_MAX) = (-95.0, 41.0, -76.0, 49.0)
    animate = types.ModuleType("animate")
    animate.render_video = lambda *a, **k: None
    return {
        "survey-viz": peers.PeerStatus(
            repo="survey-viz", module_name="viz", module=viz,
            pip_command="p", needed_for="x"),
        "survey-currents": peers.PeerStatus(
            repo="survey-currents", module_name="currents",
            module=currents, pip_command="p", needed_for="x"),
        "survey-animate": peers.PeerStatus(
            repo="survey-animate", module_name="animate",
            module=animate, pip_command="p", needed_for="x"),
        "survey-timescales": peers.PeerStatus(
            repo="survey-timescales", module_name="timescales",
            module=None, pip_command="p", needed_for="x"),
    }, {"viz": viz, "currents": currents,
        "currents.glsea": glsea, "animate": animate}


def _no_timescales_import(monkeypatch):
    """Make 'timescales' unimportable; everything else imports normally."""
    import importlib as _il
    real_import = _il.import_module

    def fake_import(name, *args, **kwargs):
        if name == "timescales":
            raise ImportError("No module named 'timescales'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(_il, "import_module", fake_import)


def test_wire_peers_exposes_timescales_when_installed(monkeypatch):
    statuses, modules = _stub_wire_statuses()
    fake_ts = types.ModuleType("timescales")
    fake_ts.suggest_window = lambda *a, **k: None
    statuses["survey-timescales"].module = fake_ts
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setitem(sys.modules, "timescales", fake_ts)
    wired = peers.wire_peers(statuses)
    assert wired.suggest_window is fake_ts.suggest_window
    assert wired.timescales_pip.startswith("pip install git+")


def test_wire_peers_timescales_missing_is_none(monkeypatch):
    statuses, modules = _stub_wire_statuses()
    for name, mod in modules.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delitem(sys.modules, "timescales", raising=False)
    _no_timescales_import(monkeypatch)
    wired = peers.wire_peers(statuses)
    assert wired.suggest_window is None
    assert wired.timescales_pip.startswith("pip install git+")


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
