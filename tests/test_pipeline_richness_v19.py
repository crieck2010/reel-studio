"""mapped.earth richness final leg (v0.19.0): bivariate strand encoding,
GFS forecast-hour steps, and NOAA OFS THREDDS currents.

No network, no real peers. Covers:
- run_pipeline(..., bivariate=...): forwarded into render_viz kwargs
  only when not None (mirrors the landmask pattern); None passes
  nothing; an old survey-viz peer (no bivariate kwarg) raises the
  honest PeerTooOldError naming >= 0.26.0; the frame-batch fingerprint
  invalidates on bivariate choices.
- The Aesthetics-step UI bivariate checkbox (stubbed streamlit):
  checked (default) leaves the peer default (None), unchecked records
  False.
- run_pipeline(..., forecast_hours=...): validated up front
  (non-empty tuple of ints 0..120, else ValueError), forwarded to the
  gfs-wind fetch call only when set and only for source 'gfs-wind'
  (other sources raise ValueError instead of silently ignoring it),
  and an old fetch fn without the kwarg raises PeerTooOldError
  naming >= 0.17.0.
- source 'ofs-thredds': plan_fetch routing (currents only, any
  region — the ofs_code pin must cover the bbox), the missing-adapter
  refusal naming survey-currents>=0.18.0, the missing-ofs_code
  ValueError naming the known codes (no silent global default),
  fetch-failure wrapping, derived-product ineligibility, and a
  render-path run with a faked adapter (no network).
"""

from __future__ import annotations

import importlib
import os
import sys
import types

import numpy as np
import pytest

from studio import caching  # noqa: F401  (cachex itself stays lazy inside)
from studio import pipeline
from studio.pipeline import (
    PeerTooOldError,
    UnfetchableRegionError,
    plan_fetch,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

def _write_frames(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    frames = []
    for i in range(2):
        p = os.path.join(out_dir, f"frame_{i + 1:04d}.png")
        with open(p, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        frames.append(p)
    manifest = os.path.join(out_dir, "manifest.json")
    with open(manifest, "w") as fh:
        fh.write("{}")
    return frames, manifest


class FakeStrandsSpec:
    def __init__(self):
        self.title = "t"
        self.region_key = "north-america"
        self.bbox = (-130.0, 25.0, -65.0, 50.0)
        self.variable = "wind"
        self.source = "gfs-wind"
        self.start = "2026-09-29"
        self.end = "2026-09-30"

    def to_dict(self):
        return {"title": self.title}


def _make_strands_peers(*, bivariate_kw=False):
    """Fake peers; bivariate_kw mimics survey-viz >= 0.26.0."""
    seen = {}

    def is_fetchable(key):
        return True

    def fetch_sst(bbox, start, end, stride_days=30):
        raise AssertionError("fetch_sst should not be called")

    def fetch_averages(lake, start, end):
        raise AssertionError("fetch_averages should not be called")

    def fetch_gfs_wind(bbox, start, end, stride_days=1):
        return {"times": ["2026-09-29T00:00:00+00:00"],
                "grids": {"u10": [[1.0]], "v10": [[0.5]]},
                "air_temperature": [[70.0]]}

    def resolve_source(spec):
        return "gfs-wind"

    if bivariate_kw:
        def render_viz(spec, field, series, out_dir, preset=None,
                       basemap=None, strand_count=None,
                       strand_linewidth=None, landmask=None,
                       bivariate=None):
            seen.update(bivariate=bivariate, basemap=basemap)
            return _write_frames(out_dir)
    else:
        def render_viz(spec, field, series, out_dir, preset=None,
                       basemap=None, strand_count=None,
                       strand_linewidth=None, landmask=None):
            seen.update(basemap=basemap)
            return _write_frames(out_dir)

    def render_video(source, out_path, preset="reel", **kwargs):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".json",
                                     fps=30.0, n_frames=2)

    return (types.SimpleNamespace(
        is_fetchable=is_fetchable,
        resolve_source=resolve_source,
        fetch_sst=fetch_sst,
        fetch_averages=fetch_averages,
        fetch_gfs_wind=fetch_gfs_wind,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-180.0, -90.0, 180.0, 90.0),
    ), seen)


def _make_gfs_peers_with_hours(seen, *, hours_kw=True, fail=False):
    """Fake gfs-wind peers recording the fetch call shape."""
    calls = []

    def is_fetchable(key):
        return False  # region check must NOT gate gfs-wind

    def resolve_source(spec):
        return "gfs-wind"

    def _fetch(bbox, start, end, stride_days=1):
        seen["bbox"] = tuple(bbox)
        seen["start"], seen["end"] = start, end
        seen["stride_days"] = stride_days
        if fail:
            raise ConnectionError("simulated NOMADS outage")
        return {"times": ["2026-09-29T00:00:00+00:00"],
                "grids": {"u10": [[1.0]], "v10": [[0.5]]},
                "air_temperature": [[70.0]],
                "provenance": {"source": "nomads", "sha256": "x"}}

    if hours_kw:
        def fetch_gfs_wind(bbox, start, end, stride_days=1,
                           forecast_hours=(0,)):
            seen["forecast_hours"] = tuple(forecast_hours)
            return _fetch(bbox, start, end, stride_days=stride_days)
    else:
        def fetch_gfs_wind(bbox, start, end, stride_days=1):
            return _fetch(bbox, start, end, stride_days=stride_days)

    def render_viz(spec, field, series, out_dir, **kwargs):
        return _write_frames(out_dir)

    def render_video(source, out_path, preset="reel", **kwargs):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".json",
                                     fps=30.0, n_frames=2)

    return types.SimpleNamespace(
        is_fetchable=is_fetchable,
        resolve_source=resolve_source,
        fetch_gfs_wind=fetch_gfs_wind,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-180.0, -90.0, 180.0, 90.0),
    )


class FakeOfsField:
    """CurrentField-shaped fake (u/v in m/s, temperature in degC)."""

    def __init__(self):
        self.u = np.full((1, 2, 2), 0.3)
        self.v = np.full((1, 2, 2), -0.1)
        self.temperature = np.full((1, 2, 2), 18.0)
        self.lats = np.array([37.0, 38.0])
        self.lons = np.array([-123.0, -122.0])
        self.times = ["2026-09-30T00:00:00+00:00"]
        self.provenance = {"source": "ofs-thredds/SSCOFS",
                           "sha256": "ofsfakesha"}

    def to_dict(self):
        return {"times": list(self.times), "lats": self.lats.tolist(),
                "lons": self.lons.tolist(),
                "values": np.hypot(self.u, self.v).tolist(),
                "provenance": dict(self.provenance)}


class FakeOfsSpec:
    def __init__(self, variable="currents", source="ofs-thredds"):
        self.title = "San Francisco Bay — surface currents"
        self.region_key = "san-francisco-bay"
        self.bbox = (-123.2, 37.0, -122.2, 38.4)
        self.variable = variable
        self.source = source
        self.overlays = ()
        self.start = "2026-09-30"
        self.end = "2026-09-30"

    def to_dict(self):
        return {"title": self.title}


def _make_ofs_peers(seen, *, with_fetch=True, fail=False):
    def is_fetchable(key):
        return False  # region check must NOT gate ofs-thredds

    def resolve_source(spec):
        return "ofs-thredds"

    def render_viz(spec, field, series, out_dir, **kwargs):
        seen["render_field_type"] = type(field).__name__
        seen["render_series"] = series
        return _write_frames(out_dir)

    def render_video(source, out_path, preset="reel", **kwargs):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".json",
                                     fps=30.0, n_frames=1)

    ns = types.SimpleNamespace(
        is_fetchable=is_fetchable,
        resolve_source=resolve_source,
        render_viz=render_viz,
        render_video=render_video,
        glsea_bounds=(-180.0, -90.0, 180.0, 90.0),
        derive=types.SimpleNamespace(),  # present so derived requests
        # reach the source-ineligibility check
    )
    if with_fetch:
        def fetch_ofs_thredds(ofs_code, bbox, start, end,
                              cadence_hours=6, prefer="nowcast",
                              timeout=120.0):
            seen["ofs_code"] = ofs_code
            seen["bbox"] = tuple(bbox)
            seen["start"], seen["end"] = start, end
            if fail:
                raise ConnectionError("simulated THREDDS outage")
            return FakeOfsField()
        ns.fetch_ofs_thredds = fetch_ofs_thredds
    try:  # real VizSpec for the derived_note capability check
        import viz as _viz
        ns.VizSpec = _viz.VizSpec
    except ImportError:
        pass
    return ns


# ---------------------------------------------------------------------------
# bivariate forwarding
# ---------------------------------------------------------------------------

def test_bivariate_false_forwarded(tmp_path):
    fake_peers, seen = _make_strands_peers(bivariate_kw=True)
    run_pipeline(FakeStrandsSpec(), fake_peers, str(tmp_path),
                 aesthetic_preset="dark_strands", bivariate=False)
    assert seen["bivariate"] is False


def test_bivariate_true_forwarded(tmp_path):
    fake_peers, seen = _make_strands_peers(bivariate_kw=True)
    run_pipeline(FakeStrandsSpec(), fake_peers, str(tmp_path),
                 aesthetic_preset="dark_strands", bivariate=True)
    assert seen["bivariate"] is True


def test_bivariate_none_not_forwarded(tmp_path):
    # Default: nothing passed, so older peers keep working untouched
    # (the fake peer's own default None is what arrives).
    fake_peers, seen = _make_strands_peers(bivariate_kw=True)
    run_pipeline(FakeStrandsSpec(), fake_peers, str(tmp_path),
                 aesthetic_preset="dark_strands")
    assert seen["bivariate"] is None


def test_peer_without_bivariate_kw_raises(tmp_path):
    # survey-viz >= 0.24.0 (basemap/strand kwargs exist) but < 0.26.0:
    # explicit bivariate -> honest PeerTooOldError.
    old_peers, _ = _make_strands_peers(bivariate_kw=False)
    with pytest.raises(PeerTooOldError, match="0.26.0"):
        run_pipeline(FakeStrandsSpec(), old_peers, str(tmp_path),
                     aesthetic_preset="dark_strands", bivariate=False)


def _field():
    return {"times": ["2026-06-01"], "lats": [41.5], "lons": [-88.2],
            "values": np.full((1, 1, 1), 18.0), "variable": "wind"}


def _batch_key(**over):
    # survey-cache is a workspace sibling (conftest only wires the six
    # peers it needs, not cachex).
    workspace = os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    cache_src = os.path.join(workspace, "survey-cache", "src")
    if os.path.isdir(cache_src) and cache_src not in sys.path:
        sys.path.insert(0, cache_src)
    pytest.importorskip("cachex")  # fingerprinting needs the peer
    kw = dict(spec_dict={"title": "t", "variable": "wind"},
              render_field=_field(), series_dict=None,
              render_viz_kwargs={}, layout_canvas=None,
              style_preset=None, platform="legacy", viz_version="0.26.0")
    kw.update(over)
    return caching.frame_batch_key(**kw)


def test_fingerprint_changes_with_bivariate_choices():
    base = _batch_key(render_viz_kwargs={"preset": "dark_strands"})
    flat = _batch_key(render_viz_kwargs={"preset": "dark_strands",
                                        "bivariate": False})
    rich = _batch_key(render_viz_kwargs={"preset": "dark_strands",
                                        "bivariate": True})
    assert len({base, flat, rich}) == 3  # each choice invalidates


# ---------------------------------------------------------------------------
# forecast_hours
# ---------------------------------------------------------------------------

def test_forecast_hours_forwarded_to_gfs_fetch(tmp_path):
    seen = {}
    peers = _make_gfs_peers_with_hours(seen, hours_kw=True)
    run_pipeline(FakeStrandsSpec(), peers, str(tmp_path),
                 forecast_hours=(0, 6))
    assert seen["forecast_hours"] == (0, 6)
    # the existing call shape is untouched
    assert seen["bbox"] == (-130.0, 25.0, -65.0, 50.0)
    assert seen["start"] == "2026-09-29"
    assert seen["end"] == "2026-09-30"
    assert seen["stride_days"] == pipeline.DEFAULT_STRIDE_DAYS


def test_forecast_hours_none_not_passed(tmp_path):
    seen = {}
    peers = _make_gfs_peers_with_hours(seen, hours_kw=True)
    run_pipeline(FakeStrandsSpec(), peers, str(tmp_path))
    assert seen["forecast_hours"] == (0,)  # the peer's own default


@pytest.mark.parametrize("bad", [
    (),                    # empty
    (130,),                # above the GFS horizon
    (-1,),                 # below zero
    (0, "six"),            # non-int member
    ("0", 6),              # string member
    (True, 6),             # bool is not an hour
    "06",                  # not a tuple/list at all
    6,                     # scalar, not a tuple
])
def test_forecast_hours_invalid_raise_valueerror(tmp_path, bad):
    seen = {}
    peers = _make_gfs_peers_with_hours(seen, hours_kw=True)
    with pytest.raises(ValueError, match="forecast_hours"):
        run_pipeline(FakeStrandsSpec(), peers, str(tmp_path),
                     forecast_hours=bad)


def test_forecast_hours_non_gfs_source_refused(tmp_path):
    # forecast_hours is refused for non-GFS sources instead of being
    # silently ignored. ofs-thredds is the honest example: its
    # nowcast/forecast selection lives in the peer.
    seen = {}
    peers = _make_ofs_peers(seen)
    with pytest.raises(ValueError, match="only supported for source"):
        run_pipeline(FakeOfsSpec(), peers, str(tmp_path),
                     ofs_code="SSCOFS", forecast_hours=(0, 6))


def test_forecast_hours_old_fetch_fn_raises(tmp_path):
    # survey-currents < 0.17.0's fetch_gfs_wind has no forecast_hours
    # kwarg -> honest PeerTooOldError naming the upgrade.
    seen = {}
    peers = _make_gfs_peers_with_hours(seen, hours_kw=False)
    with pytest.raises(PeerTooOldError, match="0.17.0"):
        run_pipeline(FakeStrandsSpec(), peers, str(tmp_path),
                     forecast_hours=(0, 6))


# ---------------------------------------------------------------------------
# ofs-thredds plan_fetch
# ---------------------------------------------------------------------------

def _resolve_ofs(spec):
    return "ofs-thredds"


def test_plan_fetch_ofs_thredds_fetchable_any_region():
    plan = plan_fetch(FakeOfsSpec(), lambda key: False, _resolve_ofs)
    assert plan.fetchable
    assert plan.source == "ofs-thredds"
    assert plan.variable == "currents"
    assert plan.kind == "ok"
    assert "31 days" in plan.reason or "31-day" in plan.reason


def test_plan_fetch_ofs_thredds_wrong_variable_refused():
    plan = plan_fetch(FakeOfsSpec(variable="wind"), lambda key: True,
                      _resolve_ofs)
    assert not plan.fetchable
    assert plan.kind == "bad_variable"
    assert "ofs-thredds" in plan.reason


def test_plan_fetch_ofs_thredds_label_known():
    assert (pipeline.SOURCE_LABELS["ofs-thredds"]
            == "NOAA OFS surface currents (CO-OPS THREDDS), keyless")


def test_run_pipeline_ofs_thredds_render_path(tmp_path):
    seen = {}
    peers = _make_ofs_peers(seen)
    result = run_pipeline(FakeOfsSpec(), peers, str(tmp_path),
                          ofs_code="SSCOFS")
    assert seen["ofs_code"] == "SSCOFS"
    assert seen["bbox"] == (-123.2, 37.0, -122.2, 38.4)
    assert seen["start"] == "2026-09-30"
    assert seen["end"] == "2026-09-30"
    # the raw CurrentField-shaped field reaches the renderer (the
    # pipeline adapts it via _field_to_dict, like the OSCAR branch)
    assert seen["render_field_type"] == "dict"
    assert seen["render_series"] is None
    assert result.source == "ofs-thredds"
    assert result.provenance["source"] == "ofs-thredds"
    assert result.video_path.endswith(".mp4")


def test_run_pipeline_ofs_thredds_missing_adapter_refused(tmp_path):
    peers = _make_ofs_peers({}, with_fetch=False)
    with pytest.raises(UnfetchableRegionError, match="0.18.0"):
        run_pipeline(FakeOfsSpec(), peers, str(tmp_path),
                     ofs_code="SSCOFS")


def test_run_pipeline_ofs_thredds_missing_ofs_code_refused(tmp_path):
    # No silent global default: the model pin is required explicitly.
    seen = {}
    peers = _make_ofs_peers(seen)
    with pytest.raises(ValueError, match="explicit OFS model pin"):
        run_pipeline(FakeOfsSpec(), peers, str(tmp_path))
    assert "ofs_code" not in seen  # no fetch was attempted


def test_run_pipeline_ofs_thredds_fetch_failure_wrapped(tmp_path):
    peers = _make_ofs_peers({}, fail=True)
    with pytest.raises(RuntimeError, match="THREDDS"):
        run_pipeline(FakeOfsSpec(), peers, str(tmp_path),
                     ofs_code="SSCOFS")


def test_run_pipeline_ofs_thredds_derived_refused(tmp_path):
    seen = {}
    peers = _make_ofs_peers(seen)
    with pytest.raises(ValueError, match="31 days"):
        run_pipeline(FakeOfsSpec(), peers, str(tmp_path),
                     ofs_code="SSCOFS",
                     derived={"product": "anomaly",
                              "baseline_start": "1991-01-01",
                              "baseline_end": "2020-12-31"})


# ---------------------------------------------------------------------------
# Aesthetics-step UI: bivariate checkbox (stubbed streamlit)
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
        self.calls.append((text, unsafe_allow_html))
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


def test_bivariate_checkbox_checked_passes_nothing(app_with_stub):
    # Default (checked): the peer default stands, nothing forwarded.
    app_mod, stub = app_with_stub({})
    stub.session_state["aesthetic_preset"] = "dark_strands"
    app_mod._basemap_strands_section(_viz_status("0.26.0"), rev=1)
    assert "Bivariate encoding (brightness = speed)" in stub.calls
    assert stub.session_state["aes_bivariate"] is None


def test_bivariate_checkbox_unchecked_records_false(app_with_stub):
    app_mod, stub = app_with_stub(
        {"Bivariate encoding (brightness = speed)": False})
    stub.session_state["aesthetic_preset"] = "dark_strands"
    app_mod._basemap_strands_section(_viz_status("0.26.0"), rev=1)
    assert stub.session_state["aes_bivariate"] is False


def test_bivariate_reset_when_strands_unavailable(app_with_stub):
    app_mod, stub = app_with_stub({})
    app_mod._basemap_strands_section(_viz_status("0.23.0"), rev=1)
    assert stub.session_state["aes_bivariate"] is None
