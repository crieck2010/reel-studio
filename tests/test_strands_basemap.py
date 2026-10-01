"""Basemap styles + strand controls in run_pipeline + the Aesthetics-step UI (v0.17.0).

Covers the capability checks: signature inspection decides whether the
survey-viz peer supports basemap=/strand_count=/strand_linewidth=,
PeerTooOldError carries the upgrade command, explicit choices ride in
render_viz_kwargs (which the frame-batch fingerprint already covers —
proven by key-change tests), and defaults pass nothing so older peers
keep working untouched. The Streamlit UI section is tested against a
stubbed ``streamlit`` module — no Streamlit runtime needed.
No network, no real peers.
"""

from __future__ import annotations

import importlib
import os
import sys
import types

import numpy as np
import pytest

from studio import pipeline
from studio import caching  # noqa: E402  (cachex itself stays lazy inside)


# ---------------------------------------------------------------------------
# Fake peers
# ---------------------------------------------------------------------------

class FakeField:
    def __init__(self):
        self.times = ["2026-06-01T12:00:00+00:00"]
        self.lats = np.array([41.5, 46.2])
        self.lons = np.array([-88.2, -85.8])
        self.sst = np.ma.masked_array(np.full((1, 2, 2), 18.0), mask=False)
        self.provenance = {}


class FakeSeries:
    def __init__(self):
        self.dates = ["2026-06-01"]
        self.temps = [18.0]
        self.provenance = {}


class FakeSpec:
    def __init__(self):
        self.title = "t"
        self.region_key = "lake-michigan"
        self.bbox = (-88.2, 41.5, -85.8, 46.2)
        self.variable = "sst"
        self.start = "2026-06-01"
        self.end = "2026-06-30"

    def to_dict(self):
        return {"title": self.title}


def _write_frames(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    p = os.path.join(out_dir, "frame_0001.png")
    with open(p, "wb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
    manifest = os.path.join(out_dir, "manifest.json")
    with open(manifest, "w") as fh:
        fh.write("{}")
    return [p], manifest


def make_peers(*, strands_kw=False):
    """Fake peers; ``strands_kw`` mimics survey-viz >= 0.24.0."""
    seen = {}

    def is_fetchable(key):
        return True

    def fetch_sst(bbox, start, end, stride_days=30):
        return FakeField()

    def fetch_averages(lake, start, end):
        return FakeSeries()

    if strands_kw:
        def render_viz(spec, field, series, out_dir, preset=None,
                       rotation=None, watermark=None, subtitle=None,
                       encoding_line=True, place_labels=None,
                       max_labels=None, min_population=None,
                       basemap=None, strand_count=None,
                       strand_linewidth=None):
            seen.update(preset=preset, basemap=basemap,
                        strand_count=strand_count,
                        strand_linewidth=strand_linewidth)
            return _write_frames(out_dir)
    else:
        def render_viz(spec, field, series, out_dir):
            return _write_frames(out_dir)

    def render_video(source, out_path, preset="reel", title="",
                     burn_timestamps_=False):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".json",
                                     fps=30.0, n_frames=1)

    ns = types.SimpleNamespace(
        is_fetchable=is_fetchable,
        fetch_sst=fetch_sst,
        fetch_averages=fetch_averages,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-180.0, -90.0, 180.0, 90.0),
    )
    return ns, seen


# ---------------------------------------------------------------------------
# Pipeline plumbing
# ---------------------------------------------------------------------------

def test_basemap_and_strands_forwarded(tmp_path):
    fake_peers, seen = make_peers(strands_kw=True)
    pipeline.run_pipeline(
        FakeSpec(), fake_peers, str(tmp_path),
        aesthetic_preset="dark_strands", basemap="subtle_land",
        strand_count=5000, strand_linewidth=2.0)
    assert seen["preset"] == "dark_strands"
    assert seen["basemap"] == "subtle_land"
    assert seen["strand_count"] == 5000
    assert seen["strand_linewidth"] == 2.0


def test_strand_defaults_not_forwarded_to_old_peer(tmp_path):
    # No basemap/strand options set: old peers keep working, nothing
    # is passed.
    fake_peers, _ = make_peers(strands_kw=False)
    result = pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    assert result.video_path.endswith(".mp4")


def test_old_peer_with_explicit_basemap_raises(tmp_path):
    old_peers, _ = make_peers(strands_kw=False)
    with pytest.raises(pipeline.PeerTooOldError, match="0.24.0"):
        pipeline.run_pipeline(FakeSpec(), old_peers, str(tmp_path),
                              basemap="void_black")


def test_old_peer_with_strand_count_raises(tmp_path):
    old_peers, _ = make_peers(strands_kw=False)
    with pytest.raises(pipeline.PeerTooOldError,
                       match="basemap styles and strand controls"):
        pipeline.run_pipeline(FakeSpec(), old_peers, str(tmp_path),
                              strand_count=5000)


def test_old_peer_with_strand_linewidth_raises(tmp_path):
    old_peers, _ = make_peers(strands_kw=False)
    with pytest.raises(pipeline.PeerTooOldError, match="0.24.0"):
        pipeline.run_pipeline(FakeSpec(), old_peers, str(tmp_path),
                              strand_linewidth=2.0)


def test_new_peer_partial_strand_options(tmp_path):
    # Only strand_count set: only it is forwarded, the rest stay peer
    # defaults.
    fake_peers, seen = make_peers(strands_kw=True)
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                          aesthetic_preset="dark_strands",
                          strand_count=1200)
    assert seen["strand_count"] == 1200
    assert seen["basemap"] is None
    assert seen["strand_linewidth"] is None


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def _field():
    return {"times": ["2026-06-01"], "lats": [41.5], "lons": [-88.2],
            "values": np.full((1, 1, 1), 18.0), "variable": "sst"}


def _batch_key(**over):
    pytest.importorskip("cachex")  # fingerprinting needs the peer
    kw = dict(spec_dict={"title": "t", "variable": "sst"},
              render_field=_field(), series_dict=None,
              render_viz_kwargs={}, layout_canvas=None,
              style_preset=None, platform="legacy", viz_version="0.24.0")
    kw.update(over)
    return caching.frame_batch_key(**kw)


def test_fingerprint_changes_with_basemap_choices():
    base = _batch_key()
    void = _batch_key(render_viz_kwargs={"basemap": "void_black"})
    none_ = _batch_key(render_viz_kwargs={"basemap": "no_basemap"})
    subtle = _batch_key(render_viz_kwargs={"basemap": "subtle_land"})
    keys = {base, void, none_, subtle}
    assert len(keys) == 4  # every basemap choice invalidates the cache


def test_fingerprint_changes_with_strand_choices():
    base = _batch_key(render_viz_kwargs={"preset": "dark_strands"})
    more = _batch_key(render_viz_kwargs={"preset": "dark_strands",
                                         "strand_count": 8000})
    wider = _batch_key(render_viz_kwargs={"preset": "dark_strands",
                                          "strand_linewidth": 2.5})
    keys = {base, more, wider}
    assert len(keys) == 3


def test_fingerprint_stable_for_same_strand_choices():
    a = _batch_key(render_viz_kwargs={"preset": "dark_strands",
                                     "basemap": "void_black",
                                     "strand_count": 5000})
    b = _batch_key(render_viz_kwargs={"preset": "dark_strands",
                                     "basemap": "void_black",
                                     "strand_count": 5000})
    assert a == b


# ---------------------------------------------------------------------------
# Aesthetics-step UI (stubbed streamlit)
# ---------------------------------------------------------------------------

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
    def divider(self): pass

    def radio(self, label, options, index=0, key=None, help=None,
              horizontal=False):
        return self._val(label, options[index])

    def slider(self, label, min_value=None, max_value=None, value=None,
               key=None, help=None, step=None):
        return self._val(label, value)

    def number_input(self, label, min_value=None, step=None, value=0,
                     key=None, help=None):
        return self._val(label, value)

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


def _viz_status(version):
    viz = types.SimpleNamespace(__version__=version)
    return types.SimpleNamespace(
        installed=True, module=viz, repo="survey-viz",
        pip_command="pip install git+https://github.com/crieck2010/survey-viz")


def test_strands_section_old_peer_shows_hint(app_with_stub):
    app_mod, stub = app_with_stub({})
    app_mod._basemap_strands_section(_viz_status("0.23.0"), rev=1)
    infos = [c for c in stub.calls if isinstance(c, tuple) and c[0] == "info"]
    assert any("0.24.0" in text for _, text in infos)
    assert stub.session_state["aes_basemap"] is None
    assert stub.session_state["aes_strand_count"] is None
    assert stub.session_state["aes_strand_linewidth"] is None


def test_basemap_choice_recorded(app_with_stub):
    app_mod, stub = app_with_stub({"Basemap style": "subtle_land"})
    stub.session_state["aesthetic_preset"] = "dark_glow"
    app_mod._basemap_strands_section(_viz_status("0.24.0"), rev=1)
    assert stub.session_state["aes_basemap"] == "subtle_land"
    # Not a strands preset: strand controls stay off.
    assert stub.session_state["aes_strand_count"] is None
    assert stub.session_state["aes_strand_linewidth"] is None


def test_basemap_default_passes_nothing(app_with_stub):
    app_mod, stub = app_with_stub({})
    stub.session_state["aesthetic_preset"] = "dark_glow"
    app_mod._basemap_strands_section(_viz_status("0.24.0"), rev=1)
    assert stub.session_state["aes_basemap"] is None


def test_strand_sliders_defaults_pass_nothing(app_with_stub):
    app_mod, stub = app_with_stub({})
    stub.session_state["aesthetic_preset"] = "dark_strands"
    app_mod._basemap_strands_section(_viz_status("0.24.0"), rev=1)
    assert stub.session_state["aes_strand_count"] is None
    assert stub.session_state["aes_strand_linewidth"] is None


def test_strand_sliders_tuned_values_recorded(app_with_stub):
    app_mod, stub = app_with_stub({"Strand count": 8000,
                                   "Strand line width": 2.5})
    stub.session_state["aesthetic_preset"] = "dark_strands"
    app_mod._basemap_strands_section(_viz_status("0.24.0"), rev=1)
    assert stub.session_state["aes_strand_count"] == 8000
    assert stub.session_state["aes_strand_linewidth"] == 2.5


def test_preset_picker_offers_dark_strands(app_with_stub):
    # The preset selectbox options come from the peer; the label map
    # must cover dark_strands.
    viz = types.SimpleNamespace(
        __version__="0.24.0",
        AESTHETIC_PRESETS=("dark_flow", "dark_glow", "paper_prism",
                           "dark_strands"),
        PRESET_VARIABLES={"dark_strands": ("currents", "wind")})
    status = types.SimpleNamespace(
        installed=True, module=viz, repo="survey-viz",
        pip_command="pip install git+https://github.com/crieck2010/survey-viz")
    app_mod, stub = app_with_stub({"Aesthetic preset": "dark_strands"})
    app_mod._aesthetic_preset_section(
        status, {"variable": "sst"}, rev=1)
    assert stub.session_state["aesthetic_preset"] == "dark_strands"
    # Strand sliders ran (defaults pass nothing).
    assert stub.session_state["aes_strand_count"] is None
