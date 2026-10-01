"""Place labels in run_pipeline + the Aesthetics-step UI (v0.16.0).

Covers the capability checks: signature inspection decides whether the
survey-viz peer supports place_labels=/max_labels=/min_population=,
PeerTooOldError carries the upgrade command, explicit label choices
ride in render_viz_kwargs (which the frame-batch fingerprint already
covers — proven by key-change tests), and defaults pass nothing so
older peers keep working untouched. The Streamlit UI section is tested
against a stubbed ``streamlit`` module — no Streamlit runtime needed.
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


def make_peers(*, label_kw=False):
    """Fake peers; ``label_kw`` mimics survey-viz >= 0.23.0."""
    seen = {}

    def is_fetchable(key):
        return True

    def fetch_sst(bbox, start, end, stride_days=30):
        return FakeField()

    def fetch_averages(lake, start, end):
        return FakeSeries()

    if label_kw:
        def render_viz(spec, field, series, out_dir, preset=None,
                       rotation=None, watermark=None, subtitle=None,
                       encoding_line=True, place_labels=None,
                       max_labels=None, min_population=None):
            seen.update(place_labels=place_labels, max_labels=max_labels,
                        min_population=min_population)
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

def test_place_labels_off_forwarded(tmp_path):
    fake_peers, seen = make_peers(label_kw=True)
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                          place_labels=False)
    assert seen["place_labels"] is False
    assert seen["max_labels"] is None
    assert seen["min_population"] is None


def test_place_labels_auto_and_tuning_forwarded(tmp_path):
    fake_peers, seen = make_peers(label_kw=True)
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                          place_labels=True, max_labels=5,
                          min_population=1000)
    assert seen["place_labels"] is True
    assert seen["max_labels"] == 5
    assert seen["min_population"] == 1000


def test_explicit_label_list_forwarded(tmp_path):
    fake_peers, seen = make_peers(label_kw=True)
    labels = [{"x": -87.9, "y": 43.0, "text": "Milwaukee", "priority": 10}]
    pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                          place_labels=labels)
    assert seen["place_labels"] == labels


def test_label_defaults_not_forwarded_to_old_peer(tmp_path):
    # No label choices: old peers keep working, nothing is passed.
    fake_peers, _ = make_peers(label_kw=False)
    result = pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path))
    assert result.video_path.endswith(".mp4")


def test_old_peer_with_explicit_labels_raises(tmp_path):
    fake_peers, _ = make_peers(label_kw=False)
    with pytest.raises(pipeline.PeerTooOldError) as excinfo:
        pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                              place_labels=False)
    msg = str(excinfo.value)
    assert "0.23.0" in msg
    assert "survey-viz" in msg
    assert "git+https://github.com/crieck2010/survey-viz.git" in msg


def test_old_peer_with_tuned_max_labels_raises(tmp_path):
    fake_peers, _ = make_peers(label_kw=False)
    with pytest.raises(pipeline.PeerTooOldError, match="0.23.0"):
        pipeline.run_pipeline(FakeSpec(), fake_peers, str(tmp_path),
                              max_labels=5)


# ---------------------------------------------------------------------------
# Fingerprint invalidation
# ---------------------------------------------------------------------------

def _field():
    return {"times": ["2026-06-01"], "lats": [41.5], "lons": [-88.2],
            "values": np.full((1, 1, 1), 18.0), "variable": "sst"}


def _batch_key(**over):
    pytest.importorskip("cachex")  # fingerprinting needs the peer
    kw = dict(spec_dict={"title": "t", "variable": "sst"},
              render_field=_field(), series_dict=None,
              render_viz_kwargs={}, layout_canvas=None,
              style_preset=None, platform="legacy", viz_version="0.23.0")
    kw.update(over)
    return caching.frame_batch_key(**kw)


def test_fingerprint_changes_with_label_choices():
    base = _batch_key()
    off = _batch_key(render_viz_kwargs={"place_labels": False})
    auto = _batch_key(render_viz_kwargs={"place_labels": True})
    tuned = _batch_key(render_viz_kwargs={"place_labels": True,
                                         "max_labels": 5,
                                         "min_population": 1000})
    custom = _batch_key(render_viz_kwargs={
        "place_labels": [{"x": -87.9, "y": 43.0, "text": "Milwaukee",
                          "priority": 10}]})
    keys = {base, off, auto, tuned, custom}
    assert len(keys) == 5  # every label choice invalidates the cache


def test_fingerprint_stable_for_same_label_choices():
    a = _batch_key(render_viz_kwargs={"place_labels": True,
                                     "max_labels": 5})
    b = _batch_key(render_viz_kwargs={"place_labels": True,
                                     "max_labels": 5})
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
               key=None, help=None):
        return self._val(label, value)

    def number_input(self, label, min_value=None, step=None, value=0,
                     key=None, help=None):
        return self._val(label, value)

    def text_area(self, label, value="", height=None, help=None, key=None):
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


def test_label_section_old_peer_shows_hint(app_with_stub):
    app_mod, stub = app_with_stub({})
    app_mod._place_labels_section(_viz_status("0.22.0"), {}, rev=1)
    infos = [c for c in stub.calls if isinstance(c, tuple) and c[0] == "info"]
    assert any("0.23.0" in text for _, text in infos)
    assert stub.session_state["aes_place_labels"] is None
    assert stub.session_state["aes_max_labels"] is None


def test_label_section_auto_defaults_pass_nothing(app_with_stub):
    app_mod, stub = app_with_stub({"Place labels": "Auto"})
    app_mod._place_labels_section(_viz_status("0.23.0"), {}, rev=1)
    # Peer default (auto on >= 0.23.0): nothing passed, older peers safe.
    assert stub.session_state["aes_place_labels"] is None
    assert stub.session_state["aes_max_labels"] is None
    assert stub.session_state["aes_min_population"] is None


def test_label_section_off(app_with_stub):
    app_mod, stub = app_with_stub({"Place labels": "Off"})
    app_mod._place_labels_section(_viz_status("0.23.0"), {}, rev=1)
    assert stub.session_state["aes_place_labels"] is False


def test_label_section_auto_tuned(app_with_stub):
    app_mod, stub = app_with_stub({"Place labels": "Auto",
                                   "Max labels": 5,
                                   "Min population": 50000})
    app_mod._place_labels_section(_viz_status("0.23.0"), {}, rev=1)
    assert stub.session_state["aes_place_labels"] is True
    assert stub.session_state["aes_max_labels"] == 5
    assert stub.session_state["aes_min_population"] == 50000


def test_label_section_custom_with_disambiguation(app_with_stub, monkeypatch):
    results = {
        "rochester": [
            {"x": -77.6, "y": 43.16, "text": "Rochester", "priority": 60,
             "adm0": "United States of America", "adm1": "New York",
             "kind": "city", "pop": 211328},
            {"x": -92.46, "y": 44.02, "text": "Rochester", "priority": 55,
             "adm0": "United States of America", "adm1": "Minnesota",
             "kind": "city", "pop": 121395},
        ],
    }
    fake_gz = types.SimpleNamespace(
        search=lambda q, limit=5: [dict(r) for r in results[q.lower()]])
    monkeypatch.setitem(sys.modules, "gazetteer", fake_gz)
    # The selectbox stub picks options[0] (New York) for each name.
    app_mod, stub = app_with_stub({"Place labels": "Custom",
                                   "Place names (one per line)":
                                   "Rochester\nNowhereville"})
    app_mod._place_labels_section(_viz_status("0.23.0"), {}, rev=1)
    labels = stub.session_state["aes_place_labels"]
    assert isinstance(labels, list) and len(labels) == 1
    assert labels[0]["text"] == "Rochester"
    assert labels[0]["adm1"] == "New York"  # disambiguation picked NY
    assert labels[0]["x"] == pytest.approx(-77.6)
    warnings = [c for c in stub.calls
                if isinstance(c, tuple) and c[0] == "warning"]
    assert any("Nowhereville" in text for _, text in warnings)


def test_label_section_custom_without_peer(app_with_stub, monkeypatch):
    monkeypatch.setitem(sys.modules, "gazetteer", None)
    app_mod, stub = app_with_stub({"Place labels": "Custom"})
    app_mod._place_labels_section(_viz_status("0.23.0"), {}, rev=1)
    infos = [c for c in stub.calls if isinstance(c, tuple) and c[0] == "info"]
    assert any("survey-gazetteer" in text for _, text in infos)
    assert stub.session_state["aes_place_labels"] is None
