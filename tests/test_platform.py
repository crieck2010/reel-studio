"""Platform aspect ratios + safe zones (survey-layout interop).

Covers: canvas capability detection, quake/standard flavor selection,
PeerTooOldError upgrade guidance (old viz, missing layout), legacy
fallback never passing canvas=, batch setting snapshots, provenance
recording, peer registration, and the safe-zone description helpers.
"""

from __future__ import annotations

import os
import sys
import types

import numpy as np
import pytest

from studio import batch, peers, pipeline


# --- fake peers ---------------------------------------------------------------

class FakeField:
    def __init__(self):
        self.times = ["2026-06-01T12:00:00+00:00", "2026-07-01T12:00:00+00:00"]
        self.lats = np.array([41.5, 46.2])
        self.lons = np.array([-88.2, -85.8])
        self.sst = np.ma.masked_array(np.full((2, 2, 2), 18.0), mask=False)
        self.provenance = {"synthetic": True}


class FakeSeries:
    def __init__(self):
        self.dates = ["2026-06-01", "2026-07-01"]
        self.temps = [12.5, 18.0]
        self.provenance = {"synthetic": True}


class FakeSpec:
    def __init__(self, variable="sst"):
        self.title = "t"
        self.region_key = "lake-michigan"
        self.bbox = (-88.2, 41.5, -85.8, 46.2)
        self.variable = variable
        self.start = "2026-06-01"
        self.end = "2026-07-31"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable}


def _render_viz_with_canvas(calls):
    """Fake render_viz that accepts canvas= and records it."""
    def render_viz(spec, field, series, out_dir, **kwargs):
        calls["canvas"] = kwargs.get("canvas")
        calls["kwargs"] = kwargs
        os.makedirs(out_dir, exist_ok=True)
        frames = []
        for i in range(2):
            p = os.path.join(out_dir, f"frame_{i+1:04d}.png")
            with open(p, "wb") as fh:
                fh.write(b"\x89PNG\r\n\x1a\n")
            frames.append(p)
        manifest = os.path.join(out_dir, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{}")
        return frames, manifest
    return render_viz


def _render_viz_legacy():
    """Fake render_viz WITHOUT canvas= (survey-viz < 0.18.0)."""
    def render_viz(spec, field, series, out_dir):
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "frame_0001.png")
        with open(p, "wb") as fh:
            fh.write(b"\x89PNG\r\n\x1a\n")
        manifest = os.path.join(out_dir, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{}")
        return [p], manifest
    return render_viz


def _fake_layout(calls):
    """Fake survey-layout peer: records (platform, flavor)."""
    def to_viz_canvas(platform, flavor="standard"):
        calls["platform"] = platform
        calls["flavor"] = flavor
        return {"platform": platform, "flavor": flavor,
                "width": 1080, "height": 1920,
                "regions": {"title": [0, 0.9, 1, 0.1],
                            "map": [0, 0.4, 1, 0.5]},
                "unsafe": [[0.0, 0.0, 1.0, 0.1875]]}
    return types.SimpleNamespace(to_viz_canvas=to_viz_canvas)


def make_peers(calls, render_viz=None, with_layout=True):
    def render_video(source, out_path, preset="reel", **kw):
        calls["encode_preset"] = preset
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".provenance.json",
                                     fps=30.0, n_frames=2)
    ns = types.SimpleNamespace(
        is_fetchable=lambda key: True,
        fetch_sst=lambda bbox, start, end, stride_days=30: FakeField(),
        fetch_averages=lambda lake, start, end: FakeSeries(),
        render_viz=(render_viz or _render_viz_with_canvas(calls)),
        render_video=render_video,
        glsea_bounds=(-92.4, 38.8, -75.8, 50.6),
    )
    if with_layout:
        ns.to_viz_canvas = _fake_layout(calls).to_viz_canvas
        ns.layout_pip = "pip install git+https://github.com/crieck2010/survey-layout.git"
    return ns


# --- tests --------------------------------------------------------------------

def test_platform_builds_canvas_and_records_provenance(tmp_path):
    calls = {}
    result = pipeline.run_pipeline(
        FakeSpec(), make_peers(calls), str(tmp_path), platform="tiktok")
    assert calls["platform"] == "tiktok"
    assert calls["flavor"] == "standard"
    assert calls["canvas"]["width"] == 1080
    assert result.platform == "tiktok"
    render = result.provenance["render"]
    assert render["platform"] == "tiktok"
    assert render["platform_flavor"] == "standard"
    assert render["canvas"] == {"width": 1080, "height": 1920,
                                "regions": ["map", "title"]}


def test_platform_quake_flavor_for_earthquakes(tmp_path, monkeypatch):
    calls = {}
    plan = pipeline.FetchPlan(fetchable=True, reason="", lake="",
                              region_key="lake-erie", variable="earthquakes",
                              kind="ok", source="glsea")
    monkeypatch.setattr(pipeline, "plan_fetch", lambda *a, **k: plan)
    result = pipeline.run_pipeline(
        FakeSpec(variable="earthquakes"), make_peers(calls),
        str(tmp_path), platform="square")
    assert calls["flavor"] == "quake"
    assert result.provenance["render"]["platform_flavor"] == "quake"


def test_platform_selects_encode_preset(tmp_path):
    # Fake layout canvases with explicit dims (the real e2e check below
    # verifies actual MP4 dims with ffprobe).
    def canvas_for(platform, flavor="standard"):
        dims = {"tiktok": (1080, 1920), "x-portrait": (1080, 1350),
                "square": (1080, 1080), "widescreen": (1920, 1080)}
        w, h = dims[platform]
        return {"platform": platform, "flavor": flavor, "width": w,
                "height": h, "regions": {}, "unsafe": []}

    for platform, preset in (("tiktok", "reel"), ("x-portrait", "reel"),
                             ("square", "square"), ("widescreen", "wide"),
                             (None, "reel"), ("legacy", "reel")):
        calls = {}
        fake = make_peers(calls)
        fake.to_viz_canvas = canvas_for
        result = pipeline.run_pipeline(
            FakeSpec(), fake, str(tmp_path), platform=platform)
        assert calls["encode_preset"] == preset, platform
        assert result.provenance["encode"]["preset"] == preset


def test_platform_none_and_legacy_never_pass_canvas(tmp_path):
    for plat in (None, "legacy"):
        calls = {}
        result = pipeline.run_pipeline(
            FakeSpec(), make_peers(calls), str(tmp_path), platform=plat)
        assert "canvas" not in calls["kwargs"]
        assert result.platform == "legacy"
        assert result.provenance["render"]["platform"] == "legacy"
        assert result.provenance["render"]["canvas"] is None


def test_platform_old_viz_raises_peer_too_old(tmp_path):
    calls = {}
    fake = make_peers(calls, render_viz=_render_viz_legacy())
    with pytest.raises(pipeline.PeerTooOldError) as excinfo:
        pipeline.run_pipeline(FakeSpec(), fake, str(tmp_path),
                              platform="tiktok")
    assert "survey-viz" in str(excinfo.value)
    assert "0.18.0" in str(excinfo.value)


def test_platform_missing_layout_raises_with_install_command(tmp_path):
    calls = {}
    fake = make_peers(calls, with_layout=False)
    with pytest.raises(pipeline.PeerTooOldError) as excinfo:
        pipeline.run_pipeline(FakeSpec(), fake, str(tmp_path),
                              platform="tiktok")
    assert "survey-layout" in str(excinfo.value)
    assert "github.com/crieck2010/survey-layout" in str(excinfo.value)


def test_platform_unknown_name_surfaces_layout_error(tmp_path):
    calls = {}

    def bad_canvas(platform, flavor="standard"):
        raise ValueError(f"unknown platform {platform!r}")

    fake = make_peers(calls)
    fake.to_viz_canvas = bad_canvas
    with pytest.raises(ValueError, match="unknown platform"):
        pipeline.run_pipeline(FakeSpec(), fake, str(tmp_path),
                              platform="myspace")


def test_batch_job_setting_keys_include_platform():
    assert "platform" in batch.JOB_SETTING_KEYS


def test_batch_forwards_platform_setting(tmp_path):
    calls = {}
    fake = make_peers(calls)
    jobs = [batch.BatchJob(description="Lake Michigan SST last summer",
                           settings={"platform": "square"})]
    out = batch.run_batch(jobs, fake, str(tmp_path),
                          parse_fn=lambda text: FakeSpec())
    assert out[0].status == "done"
    assert calls["platform"] == "square"
    assert out[0].result.platform == "square"


def test_peers_registers_survey_layout():
    assert "survey-layout" in peers.PEER_SPECS
    spec = peers.PEER_SPECS["survey-layout"]
    assert spec["module"] == "layout"
    assert "survey-layout.git" in spec["pip"]


def test_load_peers_reports_layout_status():
    statuses = peers.load_peers()
    assert "survey-layout" in statuses
    assert isinstance(statuses["survey-layout"].installed, bool)


def test_wire_peers_layout_wiring_matches_installation():
    # wire_peers trusts the real import (not the status record): layout
    # callables are present exactly when the module imports.
    statuses = peers.load_peers()
    layout_installed = statuses["survey-layout"].installed
    try:
        wired = peers.wire_peers(statuses)
    except peers.MissingPeerError:
        pytest.skip("core peers missing in this env")
    assert (wired.to_viz_canvas is not None) == layout_installed
    assert (wired.layout is not None) == layout_installed
    assert "survey-layout.git" in wired.layout_pip


# --- safe-zone helpers (pure; app import is headless-safe) ---------------------

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


def test_describe_unsafe_names_chrome_by_position(app_module):
    top = app_module._describe_unsafe((0.0, 0.922, 1.0, 0.078))
    assert "navigation" in top or "status" in top
    rail = app_module._describe_unsafe((0.856, 0.25, 0.144, 0.333))
    assert "action rail" in rail
    bottom = app_module._describe_unsafe((0.0, 0.0, 1.0, 0.1875))
    assert "captions" in bottom


def test_safe_zone_schematic_scales_with_aspect(app_module):
    plat = types.SimpleNamespace(
        width=1080, height=1920,
        unsafe=[(0.0, 0.922, 1.0, 0.078), (0.0, 0.0, 1.0, 0.1875)])
    html = app_module._safe_zone_schematic(plat)
    assert "height:356px" in html  # round(200 * 1920/1080)
    assert html.count("rgba(255,80,80") == 4  # 2 bands × bg + border
    wide = types.SimpleNamespace(width=1920, height=1080, unsafe=[])
    html_wide = app_module._safe_zone_schematic(wide)
    assert "height:112px" in html_wide  # 200 * 1080/1920 = 112.5 → 112


def test_current_run_settings_snapshots_platform(app_module):
    st = sys.modules["streamlit"]
    st.session_state = {"platform": "widescreen"}
    try:
        settings = app_module._current_run_settings()
    finally:
        st.session_state = {}
    assert settings["platform"] == "widescreen"
