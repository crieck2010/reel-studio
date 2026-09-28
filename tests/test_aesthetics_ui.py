"""Functional tests for the app's Aesthetics step (v0.2.0).

Runs ``app._aesthetics_step`` against a stubbed ``streamlit`` module —
no Streamlit runtime needed.
"""

from __future__ import annotations

import importlib
import os
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Streamlit stub
# ---------------------------------------------------------------------------

class _StubStreamlit:
    """Records calls; widget values come from a script dict."""

    def __init__(self, script):
        self.script = script          # label -> value / callable
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
    def image(self, data, caption=None):
        self.calls.append(("image", caption))
    def json(self, obj): self.calls.append(("json", obj))
    def divider(self): pass
    def title(self, text): pass
    def caption(self, text): pass
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
    def text_area(self, label, value="", height=None, help=None, key=None):
        return self._val(label, value)
    def text_input(self, label, value="", key=None, help=None):
        return self._val(label, value)
    def selectbox(self, label, options, format_func=None, index=0,
                  key=None, help=None, disabled=False):
        if disabled:
            return options[index]
        return self._val(label, options[index])
    def checkbox(self, label, value=False, key=None, help=None):
        return self._val(label, value)
    def button(self, label, type="secondary", help=None, key=None):
        return bool(self._val(label, False))


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


# ---------------------------------------------------------------------------
# Fake peers
# ---------------------------------------------------------------------------

class _NewVizSpec:
    """survey-viz >= 0.15.0 shape: caption field + CURATED_CMAPS."""

    def __init__(self, title="", style="reel-dark", underlay=True,
                 caption=None, **kw):
        if not isinstance(title, str):
            raise TypeError("title must be str")
        if caption is not None and not isinstance(caption, str):
            raise TypeError("caption must be str or None")
        self.__dict__.update(title=title, style=style,
                             underlay=underlay, caption=caption, **kw)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


class _OldVizSpec:
    """survey-viz < 0.15.0 shape: no caption field, no CURATED_CMAPS."""

    def __init__(self, title="", style="reel-dark", underlay=True, **kw):
        if "caption" in kw:
            raise TypeError("unexpected keyword 'caption'")
        self.__dict__.update(title=title, style=style,
                             underlay=underlay, **kw)

    @classmethod
    def from_dict(cls, d):
        return cls(**d)


def _statuses(viz_module):
    return {"survey-viz": types.SimpleNamespace(
        installed=True, module=viz_module,
        pip_command="pip install git+https://github.com/crieck2010/survey-viz")}


def _spec_dict(**over):
    d = dict(title="Original title", region_key="lake-superior",
             bbox=[-92.5, 46.0, -84.5, 48.8], variable="sst",
             style="reel-dark", underlay=True, caption=None,
             start="2026-01-01", end="2026-03-31", cadence="monthly")
    d.update(over)
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_spec_dict_shows_info(app_with_stub):
    app_mod, stub = app_with_stub({})
    app_mod._aesthetics_step(_statuses(types.SimpleNamespace(
        CURATED_CMAPS=["viridis"], VizSpec=_NewVizSpec)))
    infos = [c[1] for c in stub.calls
             if isinstance(c, tuple) and c[0] == "info"]
    assert "Parse a description first (step 1)." in infos


def test_apply_writes_validated_spec(app_with_stub):
    viz = types.SimpleNamespace(CURATED_CMAPS=["viridis", "inferno"],
                                VizSpec=_NewVizSpec)
    script = {
        "Style": "light",
        "Title": "My custom title",
        "Footer caption (optional)": "  my channel  ",
        "Basemap underlay (GEBCO tint + Natural Earth coastlines)": False,
        "Colormap": "inferno",
        "Apply aesthetics": True,
    }
    app_mod, stub = app_with_stub(script)
    stub.session_state["spec_dict"] = _spec_dict()
    app_mod._aesthetics_step(_statuses(viz))
    new = stub.session_state["spec_dict"]
    assert new["title"] == "My custom title"
    assert new["style"] == "light"
    assert new["caption"] == "my channel"      # stripped
    assert new["underlay"] is False
    assert stub.session_state["cmap"] == "inferno"
    assert any(c[0] == "success" for c in stub.calls
               if isinstance(c, tuple))


def test_caption_cleared_normalizes_to_none(app_with_stub):
    viz = types.SimpleNamespace(CURATED_CMAPS=["viridis"],
                                VizSpec=_NewVizSpec)
    script = {"Footer caption (optional)": "   ", "Apply aesthetics": True}
    app_mod, stub = app_with_stub(script)
    stub.session_state["spec_dict"] = _spec_dict(caption="old")
    app_mod._aesthetics_step(_statuses(viz))
    assert stub.session_state["spec_dict"]["caption"] is None


def test_categorical_variable_disables_colormap(app_with_stub):
    viz = types.SimpleNamespace(CURATED_CMAPS=["viridis", "inferno"],
                                VizSpec=_NewVizSpec)
    app_mod, stub = app_with_stub({"Apply aesthetics": False})
    stub.session_state["spec_dict"] = _spec_dict(variable="earthquakes")
    stub.session_state["cmap"] = "inferno"  # stale from an earlier spec
    app_mod._aesthetics_step(_statuses(viz))
    assert stub.session_state["cmap"] is None
    assert any(isinstance(c, tuple) and c[0] == "info" and
               "does not apply" in c[1] for c in stub.calls)


def test_old_peer_degrades_gracefully(app_with_stub):
    """survey-viz < 0.15.0: no colormap picker, caption dropped, no crash."""
    viz = types.SimpleNamespace(VizSpec=_OldVizSpec)  # no CURATED_CMAPS
    script = {"Footer caption (optional)": "hello", "Apply aesthetics": True}
    app_mod, stub = app_with_stub(script)
    stub.session_state["spec_dict"] = _spec_dict()
    app_mod._aesthetics_step(_statuses(viz))  # must not raise
    new = stub.session_state["spec_dict"]
    assert "caption" not in new
    assert stub.session_state["cmap"] is None
    assert any(isinstance(c, tuple) and c[0] == "info" and
               "≥ 0.15.0" in c[1] for c in stub.calls)
