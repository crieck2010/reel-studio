"""UI parsing + surface-section tests (v0.21.0).

Tests the headless-safe parsers behind the Aesthetics step
(``fraction|text`` headline beats, comma-separated contour levels) and
the surface/counter/headline sections against a stubbed ``streamlit``
module — no Streamlit runtime needed.
"""

from __future__ import annotations

import importlib
import os
import sys
import types

import pytest


class _StubStreamlit:
    """Records calls; widget values come from a script dict."""

    def __init__(self, script):
        self.script = script
        self.session_state = {}
        self.calls = []

    def _val(self, label, default):
        self.calls.append(label)
        if label in self.script:
            v = self.script[label]
            return v() if callable(v) else v
        return default

    def subheader(self, text): self.calls.append(("subheader", text))
    def info(self, text): self.calls.append(("info", text))
    def success(self, text): self.calls.append(("success", text))
    def error(self, text): self.calls.append(("error", text))
    def warning(self, text): self.calls.append(("warning", text))
    def write(self, text): self.calls.append(("write", text))
    def markdown(self, text, unsafe_allow_html=False):
        self.calls.append(("markdown", text))
    def caption(self, text): self.calls.append(("caption", text))
    def image(self, data, caption=None): pass
    def json(self, obj): pass
    def divider(self): pass
    def title(self, text): pass
    def set_page_config(self, **kw): pass
    def progress(self, *a, **k): pass
    def status(self, *a, **k): pass
    def rerun(self): self.calls.append(("rerun",))
    def expander(self, label, expanded=False):
        self.calls.append(("expander", label))

        class _Expander:
            def __enter__(self): return None
            def __exit__(self, *exc): return False

        return _Expander()
    def file_uploader(self, label, type=None, key=None):
        return self._val(label, None)
    def text_area(self, label, value="", height=None, help=None,
                  key=None):
        return self._val(label, value)
    def text_input(self, label, value="", key=None, help=None):
        return self._val(label, value)
    def selectbox(self, label, options, format_func=None, index=0,
                  key=None, help=None, disabled=False):
        if disabled:
            return options[index]
        return self._val(label, options[index])
    def radio(self, label, options, index=0, key=None, help=None,
              horizontal=False):
        return self._val(label, options[index])
    def slider(self, label, min_value=None, max_value=None, value=None,
               step=None, key=None, help=None):
        return self._val(label, value)
    def number_input(self, label, min_value=None, max_value=None,
                     value=None, step=None, key=None, help=None):
        return self._val(label, value)
    def checkbox(self, label, value=False, key=None, help=None):
        return self._val(label, value)
    def button(self, label, type="secondary", help=None, key=None,
               disabled=False):
        return bool(self._val(label, False))
    def columns(self, spec):
        class _Col:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def __getattr__(self, name):
                return lambda *a, **k: None
        return [_Col() for _ in range(len(spec))]


@pytest.fixture()
def app_with_stub(monkeypatch):
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)

    def make(script):
        stub = _StubStreamlit(script)
        monkeypatch.setitem(sys.modules, "streamlit", stub)
        sys.modules.pop("app", None)
        app_mod = importlib.import_module("app")
        return app_mod, stub
    return make


def _status(version="0.28.0"):
    return types.SimpleNamespace(
        installed=True,
        module=types.SimpleNamespace(__version__=version),
        repo="survey-viz",
        pip_command="pip install git+https://github.com/crieck2010/survey-viz")


# ---------------------------------------------------------------------------
# Headline-beats parser
# ---------------------------------------------------------------------------

def test_parse_beats_valid(app_with_stub):
    app_mod, _ = app_with_stub({})
    text = "0.0|Opening headline\n0.6|The north pulls ahead\n1.0|Final state"
    assert app_mod.parse_headline_beats_text(text) == [
        (0.0, "Opening headline"),
        (0.6, "The north pulls ahead"),
        (1.0, "Final state"),
    ]


def test_parse_beats_blank_lines_ignored(app_with_stub):
    app_mod, _ = app_with_stub({})
    text = "\n0.0|Start\n\n   \n0.5|Middle\n\n"
    assert app_mod.parse_headline_beats_text(text) == [
        (0.0, "Start"), (0.5, "Middle")]


def test_parse_beats_empty_is_none(app_with_stub):
    app_mod, _ = app_with_stub({})
    assert app_mod.parse_headline_beats_text("") is None
    assert app_mod.parse_headline_beats_text("   \n  ") is None
    assert app_mod.parse_headline_beats_text(None) is None


def test_parse_beats_text_with_pipe_kept(app_with_stub):
    app_mod, _ = app_with_stub({})
    assert app_mod.parse_headline_beats_text("0.25|North | South") == [
        (0.25, "North | South")]


@pytest.mark.parametrize("bad", [
    "Opening headline",          # missing |
    "abc|Some text",             # non-float fraction
    "1.5|Too late",              # out of range
    "-0.1|Too early",            # out of range
    "0.5|",                      # empty text
    "0.5|   ",                   # whitespace-only text
])
def test_parse_beats_malformed_raises(app_with_stub, bad):
    app_mod, _ = app_with_stub({})
    with pytest.raises(ValueError):
        app_mod.parse_headline_beats_text(bad)


def test_parse_beats_error_names_line(app_with_stub):
    app_mod, _ = app_with_stub({})
    with pytest.raises(ValueError, match="line 2"):
        app_mod.parse_headline_beats_text("0.0|Fine\nnope")


# ---------------------------------------------------------------------------
# Contour-levels parser
# ---------------------------------------------------------------------------

def test_parse_levels_valid(app_with_stub):
    app_mod, _ = app_with_stub({})
    assert app_mod.parse_contour_levels_text("0, 5, 10.5") == [0.0, 5.0, 10.5]
    assert app_mod.parse_contour_levels_text("-2,3") == [-2.0, 3.0]


def test_parse_levels_empty_is_none(app_with_stub):
    app_mod, _ = app_with_stub({})
    assert app_mod.parse_contour_levels_text("") is None
    assert app_mod.parse_contour_levels_text(None) is None


def test_parse_levels_malformed_raises(app_with_stub):
    app_mod, _ = app_with_stub({})
    with pytest.raises(ValueError):
        app_mod.parse_contour_levels_text("1, two, 3")


# ---------------------------------------------------------------------------
# Surface / counter / headline sections
# ---------------------------------------------------------------------------

def test_surface_section_defaults_when_preset_not_surface(app_with_stub):
    app_mod, stub = app_with_stub({})
    stub.session_state["aesthetic_preset"] = "dark_glow"
    app_mod._surface_section(_status(), rev=1)
    assert stub.session_state["aes_surface_contours"] is True
    assert stub.session_state["aes_surface_shadow"] is True
    assert stub.session_state["aes_surface_smoothing"] == 0.0
    assert stub.session_state["aes_surface_scale"] == "linear"
    assert stub.session_state["aes_surface_contour_levels_text"] == ""


def test_surface_section_controls_when_surface(app_with_stub):
    script = {
        "Surface contours": False,
        "Surface shadow": False,
        "Surface smoothing": 2.5,
        "Surface scale": "log",
        "Contour levels (comma-separated, empty = auto)": "0, 5, 10",
    }
    app_mod, stub = app_with_stub(script)
    stub.session_state["aesthetic_preset"] = "surface"
    app_mod._surface_section(_status(), rev=1)
    assert stub.session_state["aes_surface_contours"] is False
    assert stub.session_state["aes_surface_shadow"] is False
    assert stub.session_state["aes_surface_smoothing"] == 2.5
    assert stub.session_state["aes_surface_scale"] == "log"
    assert stub.session_state["aes_surface_contour_levels_text"] == "0, 5, 10"


def test_surface_section_old_peer_inert(app_with_stub):
    app_mod, stub = app_with_stub({})
    stub.session_state["aesthetic_preset"] = "surface"
    app_mod._surface_section(_status("0.27.0"), rev=1)
    assert stub.session_state["aes_surface_contours"] is True
    assert stub.session_state["aes_surface_smoothing"] == 0.0
    assert stub.session_state["aes_surface_scale"] == "linear"
    assert any(isinstance(c, tuple) and c[0] == "info" and "0.28.0" in c[1]
               for c in stub.calls)


def test_counter_headline_section_defaults_off(app_with_stub):
    app_mod, stub = app_with_stub({})
    app_mod._counter_headline_section(_status(), rev=1)
    assert stub.session_state["aes_counter_enabled"] is False
    assert stub.session_state["aes_headline_beats_text"] == ""
    assert stub.session_state["aes_headline_beats_auto"] is False


def test_counter_headline_section_values(app_with_stub):
    script = {
        "Counter": True,
        "Counter stat": "mean",
        "Counter unit": "km²",
        "Counter label": "Mean coverage",
        "Headline beats (one per line, fraction|text)":
            "0.0|Opening headline\n0.6|The north pulls ahead",
        "Auto-draft headline beats": True,
    }
    app_mod, stub = app_with_stub(script)
    app_mod._counter_headline_section(_status(), rev=1)
    assert stub.session_state["aes_counter_enabled"] is True
    assert stub.session_state["aes_counter_stat"] == "mean"
    assert stub.session_state["aes_counter_unit"] == "km²"
    assert stub.session_state["aes_counter_label"] == "Mean coverage"
    assert stub.session_state["aes_headline_beats_auto"] is True
    # the auto note (human-approval drafts) is shown inline
    assert any(isinstance(c, tuple) and c[0] == "caption"
               and "drafts from survey-narrate" in c[1]
               for c in stub.calls)


def test_counter_headline_old_peer_inert(app_with_stub):
    app_mod, stub = app_with_stub({})
    app_mod._counter_headline_section(_status("0.24.0"), rev=1)
    assert stub.session_state["aes_counter_enabled"] is False
    assert stub.session_state["aes_headline_beats_auto"] is False
