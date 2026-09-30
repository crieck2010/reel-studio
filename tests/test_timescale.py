"""Tests for the v0.14.0 suggested-window wiring (survey-timescales peer).

The control logic lives in the UI-free ``studio/timescale.py``, so most
tests run headless. Streamlit-dependent behavior (the section, the
picker flow) is exercised through a minimal fake ``st`` injected into
the app module — no Streamlit runtime needed.
"""

from __future__ import annotations

import datetime
import os
import sys
import types

import pytest

from studio import peers, timescale


# --- real engine (sibling checkout) -------------------------------------------

_TS_SRC = os.path.join(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))),
    "survey-timescales", "src")
if os.path.isdir(_TS_SRC) and _TS_SRC not in sys.path:
    sys.path.insert(0, _TS_SRC)

try:
    import timescales as _real_ts  # noqa: F401
    HAS_REAL_ENGINE = True
except ImportError:
    HAS_REAL_ENGINE = False

FIXED_TODAY = datetime.date(2026, 9, 30)


def _spec(variable="fire", region_key="california", source=""):
    return types.SimpleNamespace(variable=variable,
                                 region_key=region_key, source=source)


class _FakeSuggestion:
    def __init__(self, start, end, mode, reason):
        self.start = start
        self.end = end
        self.mode = mode
        self.reason = reason


def _fake_ts(**kwargs):
    """A stub timescales module recording its suggest_window calls."""
    calls = []

    def suggest_window(variable, region=None, today=None, **kw):
        calls.append({"variable": variable, "region": region,
                      "today": today, "kwargs": kw})
        return _FakeSuggestion(datetime.date(2026, 5, 1), FIXED_TODAY,
                               "season", "fake reason")

    mod = types.ModuleType("timescales")
    mod.suggest_window = suggest_window
    mod.calls = calls
    for key, value in kwargs.items():
        setattr(mod, key, value)
    return mod


# --- resolve_source ------------------------------------------------------------

def test_resolve_source_pinned_wins():
    seen = []
    spec = _spec(source="imerg")
    assert timescale.resolve_source(
        spec, resolver=lambda s: seen.append(s) or "era5") == "imerg"
    assert seen == []  # resolver never consulted


def test_resolve_source_resolver_fallback():
    assert timescale.resolve_source(
        _spec(), resolver=lambda s: "ERA5") == "era5"


def test_resolve_source_resolver_valueerror_means_none():
    def bad(spec):
        raise ValueError("unknown region")

    assert timescale.resolve_source(_spec(), resolver=bad) is None


def test_resolve_source_nothing_known_is_none():
    assert timescale.resolve_source(_spec()) is None


# --- suggest -------------------------------------------------------------------

def test_suggest_passes_variable_region_today():
    mod = _fake_ts()
    spec = _spec(variable="sst", region_key="gulf-of-mexico")
    out = timescale.suggest(spec, today=FIXED_TODAY,
                            timescales_module=mod)
    assert out.mode == "season"
    (call,) = mod.calls
    assert call["variable"] == "sst"
    assert call["region"] == "gulf-of-mexico"
    assert call["today"] == FIXED_TODAY
    assert "source" not in call["kwargs"]  # engine does its own fallback


def test_suggest_passes_source_for_tp_disambiguation():
    mod = _fake_ts()
    spec = _spec(variable="tp", region_key="", source="imerg")
    timescale.suggest(spec, today=FIXED_TODAY, timescales_module=mod)
    (call,) = mod.calls
    assert call["kwargs"]["source"] == "imerg"


def test_suggest_uses_resolved_source_when_not_pinned():
    mod = _fake_ts()
    spec = _spec(variable="tp")
    timescale.suggest(spec, today=FIXED_TODAY, timescales_module=mod,
                      resolver=lambda s: "era5")
    (call,) = mod.calls
    assert call["kwargs"]["source"] == "era5"


def test_suggest_too_old_peer_raises_runtimeerror():
    with pytest.raises(RuntimeError, match="no suggest_window"):
        timescale.suggest(_spec(), today=FIXED_TODAY,
                          timescales_module=types.ModuleType("timescales"))


# --- apply_window / override_with_pickers / picker_defaults --------------------

def test_apply_window_sets_iso_strings():
    spec = {"start": "2020-01-01", "end": "2020-12-31", "variable": "sst"}
    out = timescale.apply_window(spec, datetime.date(2026, 5, 1),
                                 datetime.date(2026, 9, 30))
    assert out["start"] == "2026-05-01"
    assert out["end"] == "2026-09-30"
    assert spec["start"] == "2020-01-01"  # input not mutated
    assert out["variable"] == "sst"  # everything else carried over


def test_apply_window_rejects_start_after_end():
    with pytest.raises(ValueError, match="after end"):
        timescale.apply_window({}, datetime.date(2026, 9, 30),
                               datetime.date(2026, 5, 1))


def test_override_with_pickers_no_keys_leaves_spec_unchanged():
    spec = {"start": "2020-01-01", "end": "2020-12-31"}
    out = timescale.override_with_pickers(spec, {})
    assert out == spec
    assert out is not spec


def test_override_with_pickers_applies_user_dates():
    spec = {"start": "2020-01-01", "end": "2020-12-31"}
    values = {timescale.START_KEY: datetime.date(2021, 3, 1),
              timescale.END_KEY: datetime.date(2021, 6, 30)}
    out = timescale.override_with_pickers(spec, values)
    assert (out["start"], out["end"]) == ("2021-03-01", "2021-06-30")
    assert spec["start"] == "2020-01-01"  # original untouched


def test_override_with_pickers_user_edit_wins_after_suggestion():
    # Simulate the UI flow: engine suggests, user then edits the end.
    spec = {"start": "2020-01-01", "end": "2020-12-31"}
    suggested = timescale.apply_window(spec, "2026-05-01", "2026-09-30")
    values = {timescale.START_KEY: datetime.date(2026, 5, 1),
              timescale.END_KEY: datetime.date(2026, 10, 15)}  # user edit
    out = timescale.override_with_pickers(suggested, values)
    assert (out["start"], out["end"]) == ("2026-05-01", "2026-10-15")


def test_override_with_pickers_start_after_end_raises():
    values = {timescale.START_KEY: datetime.date(2026, 9, 30),
              timescale.END_KEY: datetime.date(2026, 5, 1)}
    with pytest.raises(ValueError, match="Time window pickers"):
        timescale.override_with_pickers({"start": "x", "end": "y"}, values)


def test_override_with_pickers_disabled_is_noop():
    values = {timescale.START_KEY: datetime.date(2021, 3, 1)}
    spec = {"start": "2020-01-01", "end": "2020-12-31"}
    assert timescale.override_with_pickers(
        spec, values, enabled=False) == spec


def test_picker_defaults_from_spec():
    start, end = timescale.picker_defaults(
        {"start": "2020-01-01", "end": "2020-12-31"})
    assert (start, end) == (datetime.date(2020, 1, 1),
                            datetime.date(2020, 12, 31))


def test_picker_defaults_fall_back_to_today():
    start, end = timescale.picker_defaults(
        {}, today=datetime.date(2026, 9, 30))
    assert (start, end) == (datetime.date(2026, 9, 30),
                            datetime.date(2026, 9, 30))


def test_describe_formats_mode_and_reason():
    sug = _FakeSuggestion(datetime.date(2026, 5, 1), FIXED_TODAY,
                          "season", "aligned to California fire season")
    assert timescale.describe(sug) == (
        "season — aligned to California fire season")


# --- real engine integration (fixed today) --------------------------------------

@pytest.mark.skipif(not HAS_REAL_ENGINE,
                    reason="survey-timescales checkout not available")
def test_real_engine_fire_california_season_window():
    sug = _real_ts.suggest_window("fire", region="california",
                                  today=FIXED_TODAY)
    assert sug.mode == "season"
    assert (sug.start, sug.end) == (datetime.date(2026, 5, 1),
                                    datetime.date(2026, 9, 30))
    assert "fire season" in sug.reason.lower()


@pytest.mark.skipif(not HAS_REAL_ENGINE,
                    reason="survey-timescales checkout not available")
def test_real_engine_tp_source_disambiguation():
    for source in ("imerg", "era5"):
        sug = _real_ts.suggest_window("tp", region=None,
                                      today=FIXED_TODAY, source=source)
        assert sug.start <= sug.end
        assert sug.mode


@pytest.mark.skipif(not HAS_REAL_ENGINE,
                    reason="survey-timescales checkout not available")
def test_real_engine_static_variable_raises():
    with pytest.raises(_real_ts.StaticVariableError):
        _real_ts.suggest_window("bathymetry", region="gulf-of-mexico",
                                today=FIXED_TODAY)


@pytest.mark.skipif(not HAS_REAL_ENGINE,
                    reason="survey-timescales checkout not available")
def test_real_engine_through_timescale_suggest():
    spec = _spec(variable="fire", region_key="california")
    sug = timescale.suggest(spec, today=FIXED_TODAY,
                            timescales_module=_real_ts)
    assert sug.mode == "season"
    assert "fire season" in sug.reason.lower()


# --- UI layer: fake Streamlit ----------------------------------------------------

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


class _Ctx:
    def __init__(self, fake):
        self.fake = fake

    def __enter__(self):
        return self.fake

    def __exit__(self, *args):
        return False


class _Col:
    def __init__(self, fake):
        self.fake = fake

    def date_input(self, label, value=None, key=None, help=None):
        self.fake.calls.append(("date_input", label, value, key))
        return self.fake.session_state.get(key, value)

    def __getattr__(self, name):
        return getattr(self.fake, name)


class FakeSt:
    """Records widget calls; session_state is a plain dict."""

    def __init__(self):
        self.calls = []
        self.session_state = {}
        self._buttons = {}

    def expander(self, *args, **kwargs):
        self.calls.append(("expander", args, kwargs))
        return _Ctx(self)

    def columns(self, n):
        return [_Col(self) for _ in range(n)]

    def button(self, label, key=None, **kwargs):
        self.calls.append(("button", label, key))
        return self._buttons.get(key or label, False)

    def __getattr__(self, name):
        def _record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
        return _record


def _ts_statuses(installed, module=None):
    ts = peers.PeerStatus(
        repo="survey-timescales", module_name="timescales",
        module=module,
        pip_command="pip install "
                    "git+https://github.com/crieck2010/survey-timescales.git",
        needed_for="suggested windows")
    viz = peers.PeerStatus(repo="survey-viz", module_name="viz",
                           module=None, pip_command="p", needed_for="x")
    return {"survey-timescales": ts, "survey-viz": viz}


def _widget_calls(fake, name):
    return [c for c in fake.calls if c[0] == name]


def test_section_peer_missing_shows_note_never_crashes(app_module,
                                                       monkeypatch):
    fake = FakeSt()
    fake.session_state["spec_dict"] = {"start": "2020-01-01",
                                       "end": "2020-12-31",
                                       "variable": "sst",
                                       "region_key": "gulf-of-mexico"}
    monkeypatch.setattr(app_module, "st", fake)
    app_module._timescale_section(_ts_statuses(installed=False))
    infos = _widget_calls(fake, "info")
    assert infos, "expected the explanatory note"
    assert "survey-timescales.git" in str(infos[0])
    assert _widget_calls(fake, "date_input") == []
    assert _widget_calls(fake, "button") == []


def test_section_peer_installed_renders_pickers_and_button(app_module,
                                                           monkeypatch):
    fake = FakeSt()
    fake.session_state["spec_dict"] = {"start": "2020-01-01",
                                       "end": "2020-12-31",
                                       "variable": "sst",
                                       "region_key": "gulf-of-mexico"}
    monkeypatch.setattr(app_module, "st", fake)
    app_module._timescale_section(_ts_statuses(installed=True,
                                              module=_fake_ts()))
    pickers = _widget_calls(fake, "date_input")
    assert [c[3] for c in pickers] == [timescale.START_KEY,
                                       timescale.END_KEY]
    assert [c[2] for c in pickers] == [datetime.date(2020, 1, 1),
                                       datetime.date(2020, 12, 31)]
    buttons = _widget_calls(fake, "button")
    assert any("Suggest window" in str(c[1]) for c in buttons)


def test_apply_suggestion_fills_pickers_and_stores_reason(app_module,
                                                          monkeypatch):
    fake = FakeSt()
    fake.session_state["spec_dict"] = {"start": "2020-01-01",
                                       "end": "2020-12-31",
                                       "variable": "fire",
                                       "region_key": "california",
                                       "source": ""}
    monkeypatch.setattr(app_module, "st", fake)
    app_module._apply_timescale_suggestion(
        _ts_statuses(installed=True, module=_fake_ts()))
    assert fake.session_state[timescale.START_KEY] == datetime.date(2026, 5, 1)
    assert fake.session_state[timescale.END_KEY] == FIXED_TODAY
    note = fake.session_state[timescale.SUGGESTION_KEY]
    assert note["mode"] == "season"
    assert note["reason"] == "fake reason"


def test_section_shows_reason_after_suggestion(app_module, monkeypatch):
    fake = FakeSt()
    fake.session_state["spec_dict"] = {"start": "2020-01-01",
                                       "end": "2020-12-31",
                                       "variable": "fire",
                                       "region_key": "california"}
    fake.session_state[timescale.SUGGESTION_KEY] = {
        "start": "2026-05-01", "end": "2026-09-30",
        "mode": "season", "reason": "fake reason"}
    monkeypatch.setattr(app_module, "st", fake)
    app_module._timescale_section(_ts_statuses(installed=True,
                                              module=_fake_ts()))
    text = str(fake.calls)
    assert "fake reason" in text
    assert "season" in text


def test_apply_suggestion_static_variable_warns(app_module, monkeypatch):
    class StaticVariableError(Exception):
        pass

    mod = _fake_ts(StaticVariableError=StaticVariableError)

    def boom(variable, region=None, today=None, **kw):
        raise StaticVariableError("no timeframe window applies")

    mod.suggest_window = boom
    fake = FakeSt()
    fake.session_state["spec_dict"] = {"start": "2020-01-01",
                                       "end": "2020-12-31",
                                       "variable": "bathymetry",
                                       "region_key": "gulf-of-mexico",
                                       "source": ""}
    monkeypatch.setattr(app_module, "st", fake)
    app_module._apply_timescale_suggestion(
        _ts_statuses(installed=True, module=mod))
    kind, message = fake.session_state[timescale.ERROR_KEY]
    assert kind == "warning"
    assert "No time window applies" in message
    assert timescale.START_KEY not in fake.session_state


def test_apply_suggestion_engine_error_is_honest(app_module, monkeypatch):
    mod = _fake_ts()

    def boom(variable, region=None, today=None, **kw):
        raise RuntimeError("registry exploded")

    mod.suggest_window = boom
    fake = FakeSt()
    fake.session_state["spec_dict"] = {"start": "2020-01-01",
                                       "end": "2020-12-31",
                                       "variable": "sst",
                                       "region_key": "gulf-of-mexico",
                                       "source": ""}
    monkeypatch.setattr(app_module, "st", fake)
    app_module._apply_timescale_suggestion(
        _ts_statuses(installed=True, module=mod))
    kind, message = fake.session_state[timescale.ERROR_KEY]
    assert kind == "error"
    assert "Could not suggest a window" in message
    assert timescale.START_KEY not in fake.session_state


def test_section_renders_error_from_session_state(app_module, monkeypatch):
    fake = FakeSt()
    fake.session_state["spec_dict"] = {"start": "2020-01-01",
                                       "end": "2020-12-31",
                                       "variable": "bathymetry",
                                       "region_key": "gulf-of-mexico"}
    fake.session_state[timescale.ERROR_KEY] = (
        "warning", "No time window applies to 'bathymetry'")
    monkeypatch.setattr(app_module, "st", fake)
    app_module._timescale_section(_ts_statuses(installed=True,
                                              module=_fake_ts()))
    warnings = _widget_calls(fake, "warning")
    assert warnings, "expected the stored warning to render"
    assert "No time window applies" in str(warnings[0])


def test_timescale_enabled_helper(app_module):
    assert app_module._timescale_enabled(
        _ts_statuses(installed=True, module=object())) is True
    assert app_module._timescale_enabled(
        _ts_statuses(installed=False)) is False
    assert app_module._timescale_enabled({}) is False
