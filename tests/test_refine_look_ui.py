"""Functional tests for the v0.3.0 app features.

* ``app._refine_section`` — plain-language refinements (needs
  survey-viz >= 0.16.0 ``viz.refine_spec``).
* ``app._copy_look_section`` — copy-a-reel's-look (needs
  ``viz.suggest_aesthetic`` / ``viz.fetch_image_bytes`` /
  ``viz.analyze_image``).

Runs against a stubbed ``streamlit`` module — no Streamlit runtime.
"""

from __future__ import annotations

import importlib
import os
import sys
import types

import pytest


# ---------------------------------------------------------------------------
# Streamlit stub (mirrors tests/test_aesthetics_ui.py, extended)
# ---------------------------------------------------------------------------

class _StubStreamlit:
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
    def image(self, data, caption=None):
        self.calls.append(("image", caption))
    def json(self, obj): self.calls.append(("json", obj))
    def divider(self): pass
    def title(self, text): pass
    def caption(self, text): pass
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
    def number_input(self, label, min_value=None, step=None, value=0,
                     key=None, help=None):
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
               key=None, help=None):
        return self._val(label, value)
    def checkbox(self, label, value=False, key=None, help=None):
        return self._val(label, value)
    def columns(self, spec):
        class _Col:
            def __enter__(self): return None
            def __exit__(self, *exc): return False
        return [_Col() for _ in spec]
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

class _FakeUnparseable(ValueError):
    pass


class _FakeAestheticError(ValueError):
    pass


class _FakeChange:
    def __init__(self, field, old, new, reason):
        self.field, self.old, self.new, self.reason = field, old, new, reason


class _FakeRefineResult:
    def __init__(self, spec_dict, cmap, applied, unparsed):
        self._spec_dict = spec_dict
        self.cmap = cmap
        self.applied = applied
        self.unparsed = unparsed

    @property
    def spec(self):
        return types.SimpleNamespace(to_dict=lambda: dict(self._spec_dict))


class _FakeProfile:
    def __init__(self, style="reel-dark", colormap="inferno"):
        self._d = {
            "style": style,
            "colormap": colormap,
            "palette": ["#96281e", "#111111"],
            "brightness": 0.2,
            "dominant_hue": 5.0,
            "source": "upload",
            "notes": ["mean brightness 0.20 -> dark style"],
        }

    def to_dict(self):
        return dict(self._d)


def _make_viz(monkeypatch, **overrides):
    """Fake survey-viz >= 0.16.0 module."""
    viz = types.SimpleNamespace()
    viz.UnparseableDescription = _FakeUnparseable
    viz.AestheticError = _FakeAestheticError
    viz.CURATED_CMAPS = ("viridis", "inferno", "Blues")

    class _VizSpec:
        @classmethod
        def from_dict(cls, d):
            return types.SimpleNamespace(to_dict=lambda: dict(d))

    viz.VizSpec = _VizSpec

    def refine_spec(spec, text):
        if "blorp" in text:
            raise _FakeUnparseable("Could not understand 'blorp'")
        return _FakeRefineResult(
            {"title": "Refined", "style": "reel-dark",
             "variable": "sst", "caption": None},
            "inferno" if "colormap" in text else None,
            [_FakeChange("title", "Original", "Refined", "instruction set it")],
            ["note: nothing else matched"] if "colormap" not in text else [])

    viz.refine_spec = refine_spec
    viz.fetch_image_bytes = lambda url: b"fake-image-bytes"
    viz.analyze_image = lambda data, source="upload": _FakeProfile()
    viz.suggest_aesthetic = lambda source: _FakeProfile()
    for key, value in overrides.items():
        if value is None:
            delattr(viz, key)
        else:
            setattr(viz, key, value)
    return viz


def _statuses(viz):
    def status(module):
        return types.SimpleNamespace(
            installed=module is not None, module=module,
            pip_command="pip install git+https://github.com/crieck2010/x.git",
            needed_for="tests")
    return {"survey-viz": status(viz),
            "survey-currents": status(None),
            "survey-animate": status(None)}


_BASE_SPEC = {"title": "Original", "style": "reel-dark", "variable": "sst",
              "caption": None}


# ---------------------------------------------------------------------------
# Refine section
# ---------------------------------------------------------------------------

def test_refine_applies_changes(app_with_stub, monkeypatch):
    viz = _make_viz(monkeypatch)
    app_mod, stub = app_with_stub(
        {"Apply refinements": True, "Changes": "use the inferno colormap"})
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    app_mod._run_step(_statuses(viz))
    assert stub.session_state["spec_dict"]["title"] == "Refined"
    assert stub.session_state["cmap"] == "inferno"
    assert any(c[0] == "success" for c in stub.calls if isinstance(c, tuple))


def test_refine_unparseable_shows_error(app_with_stub, monkeypatch):
    viz = _make_viz(monkeypatch)
    app_mod, stub = app_with_stub(
        {"Apply refinements": True, "Changes": "blorp"})
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    app_mod._run_step(_statuses(viz))
    assert stub.session_state["spec_dict"] == _BASE_SPEC  # unchanged
    assert any(c[0] == "error" for c in stub.calls if isinstance(c, tuple))


def test_refine_gated_on_old_peer(app_with_stub, monkeypatch):
    viz = _make_viz(monkeypatch, refine_spec=None)  # < 0.16.0
    app_mod, stub = app_with_stub({"Apply refinements": True})
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    app_mod._run_step(_statuses(viz))
    infos = [c[1] for c in stub.calls
             if isinstance(c, tuple) and c[0] == "info"]
    assert any("0.16.0" in text for text in infos)
    assert stub.session_state["spec_dict"] == _BASE_SPEC


# ---------------------------------------------------------------------------
# Copy-look section
# ---------------------------------------------------------------------------

def _aesthetics_script(**extra):
    script = {"Apply aesthetics": False}
    script.update(extra)
    return script


def test_copy_look_upload_and_apply(app_with_stub, monkeypatch):
    viz = _make_viz(monkeypatch)
    fake_file = types.SimpleNamespace(read=lambda: b"png-bytes")
    app_mod, stub = app_with_stub(_aesthetics_script(**{
        "…or upload a screenshot": fake_file,
        "Analyze look": True,
        "Apply this look": True,
    }))
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    status = _statuses(viz)["survey-viz"]
    app_mod._aesthetics_step(_statuses(viz))
    profile = stub.session_state.get("look_profile")
    assert profile is not None
    assert profile["colormap"] == "inferno"
    assert stub.session_state["spec_dict"]["style"] == "reel-dark"
    assert stub.session_state["cmap"] == "inferno"
    assert ("rerun",) in stub.calls
    assert any(c[0] == "image" for c in stub.calls if isinstance(c, tuple))


def test_copy_look_url_path(app_with_stub, monkeypatch):
    viz = _make_viz(monkeypatch)
    app_mod, stub = app_with_stub(_aesthetics_script(**{
        "Reel URL": "https://social.example.com/reel/1",
        "Analyze look": True,
    }))
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    app_mod._aesthetics_step(_statuses(viz))
    assert stub.session_state.get("look_profile") is not None


def test_copy_look_fetch_error(app_with_stub, monkeypatch):
    def boom(url):
        raise _FakeAestheticError("no og:image found")
    viz = _make_viz(monkeypatch, fetch_image_bytes=boom)
    app_mod, stub = app_with_stub(_aesthetics_script(**{
        "Reel URL": "https://social.example.com/reel/1",
        "Analyze look": True,
    }))
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    app_mod._aesthetics_step(_statuses(viz))
    assert stub.session_state.get("look_profile") is None
    assert any(c[0] == "error" for c in stub.calls if isinstance(c, tuple))


def test_copy_look_gated_on_old_peer(app_with_stub, monkeypatch):
    viz = _make_viz(monkeypatch, suggest_aesthetic=None,
                    fetch_image_bytes=None, analyze_image=None)
    app_mod, stub = app_with_stub(_aesthetics_script())
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    app_mod._aesthetics_step(_statuses(viz))
    infos = [c[1] for c in stub.calls
             if isinstance(c, tuple) and c[0] == "info"]
    assert any("0.16.0" in text for text in infos)


# ---------------------------------------------------------------------------
# Real survey-viz integration (skipped when the peer is not installed)
# ---------------------------------------------------------------------------

def test_copy_look_real_viz_end_to_end(app_with_stub, monkeypatch):
    real_viz = pytest.importorskip("viz")
    if not hasattr(real_viz, "analyze_image"):  # < 0.16.0
        pytest.skip("needs survey-viz >= 0.16.0")
    import io
    import numpy as np
    from PIL import Image

    # dark, red-dominant image -> expect dark style + warm colormap
    arr = np.zeros((64, 64, 3), dtype=np.uint8)
    arr[..., 0] = 170
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    fake_file = types.SimpleNamespace(read=lambda: buf.getvalue())

    app_mod, stub = app_with_stub(_aesthetics_script(**{
        "…or upload a screenshot": fake_file,
        "Analyze look": True,
        "Apply this look": True,
    }))
    stub.session_state["spec_dict"] = dict(_BASE_SPEC)
    app_mod._aesthetics_step(_statuses(real_viz))

    profile = stub.session_state.get("look_profile")
    assert profile is not None
    assert profile["style"] == "reel-dark"
    assert profile["colormap"] in real_viz.CURATED_CMAPS
    assert stub.session_state["spec_dict"]["style"] == "reel-dark"
    assert stub.session_state["cmap"] == profile["colormap"]
