"""Autopilot orchestration in run_pipeline (survey-autopilot engine).

Covers: autopilot=False is byte-identical to v0.19.2 (no "autopilot"
provenance key, no new render_viz kwargs, caller's strides pass
through); autopilot=True with a stubbed `autopilot` module drives the
five steps in order (stride before fetch, QA gate after fetch, render
knobs, composition search, provenance); autopilot=True with neither
autopilot nor timescales importable raises RuntimeError; a bad
autopilot_qa_policy raises ValueError; policy="fail" lets the engine's
QAError propagate; non-gridded fields skip the QA gate with a record.
No network; the fetch and renderer are stubbed.
"""

from __future__ import annotations

import json
import os
import sys
import types

import numpy as np
import pytest

from studio.pipeline import run_pipeline


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

class FakeSpec:
    def __init__(self, variable="wind", source="gfs-wind"):
        self.title = "Winds"
        self.region_key = "north-america"
        self.bbox = (-130.0, 25.0, -65.0, 50.0)
        self.variable = variable
        self.source = source
        self.overlays = ()
        self.start = "2026-09-29"
        self.end = "2026-09-30"

    def to_dict(self):
        return {"title": self.title, "region_key": self.region_key,
                "bbox": list(self.bbox), "variable": self.variable,
                "source": self.source, "start": self.start,
                "end": self.end}


def make_field(nt=4, ny=4, nx=5, bad_first_step=False):
    """gfs-wind-shaped dict: grids u10/v10/t2m (m/s, m/s, degC)."""
    rng = np.random.default_rng(7)
    u10 = rng.normal(8, 2, (nt, ny, nx))
    v10 = rng.normal(1, 2, (nt, ny, nx))
    t2m = rng.normal(20, 3, (nt, ny, nx))
    if bad_first_step:
        u10[0] = np.nan  # all-NaN timestep -> QA issue
    return {
        "grids": {"u10": u10.tolist(), "v10": v10.tolist(),
                  "t2m": t2m.tolist()},
        "air_temperature": (t2m * 9 / 5 + 32).tolist(),
        "temperature_unit": "°F",
        "times": [f"2026-09-{29 + i:02d}T00:00:00+00:00"
                  for i in range(nt)],
        "lats": np.linspace(25.0, 50.0, ny).tolist(),
        "lons": np.linspace(-130.0, -65.0, nx).tolist(),
        "provenance": {"source": "test", "sha256": "x"},
    }


def _write_png(path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(3, 2))
    ax.imshow(np.random.default_rng(3).random((30, 40, 3)))
    ax.axis("off")
    fig.savefig(path, dpi=30, bbox_inches="tight", pad_inches=0)
    plt.close(fig)


def make_peers(calls, field=None, render_kwargs=None):
    """Stub peers recording (name, detail) into `calls`."""
    field = make_field() if field is None else field
    render_kwargs = {} if render_kwargs is None else render_kwargs

    def is_fetchable(key):
        return False

    def resolve_source(spec):
        return "gfs-wind"

    def fetch_gfs_wind(bbox, start, end, stride_days=1, **kw):
        calls.append(("fetch", {"stride_days": stride_days, **kw}))
        return field

    def render_viz(spec, field, series, out_dir, **kwargs):
        calls.append(("render_viz", {"out_dir": out_dir,
                                     "kwargs": dict(kwargs)}))
        os.makedirs(out_dir, exist_ok=True)
        p = os.path.join(out_dir, "frame_0001.png")
        _write_png(p)
        manifest = os.path.join(out_dir, "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("{}")
        return [p], manifest

    def render_video(source, out_path, preset="reel", **kwargs):
        with open(out_path, "wb") as fh:
            fh.write(b"FAKEMP4")
        return types.SimpleNamespace(video_path=out_path,
                                     sidecar_path=out_path + ".prov",
                                     fps=30, n_frames=1)

    ns = {
        "is_fetchable": is_fetchable,
        "resolve_source": resolve_source,
        "fetch_gfs_wind": fetch_gfs_wind,
        "render_viz": render_viz,
        "render_video": render_video,
        "glsea_bounds": (-180.0, -90.0, 180.0, 90.0),
    }
    ns.update(render_kwargs)
    return types.SimpleNamespace(**ns)


# --------------------------------------------------------------------------
# Stubbed survey-autopilot engine (real function signatures)
# --------------------------------------------------------------------------

class FakeQAError(Exception):
    """Stub for autopilot.qa.QAError."""


class FakeQAReport:
    def __init__(self, ok, issues, n_timesteps, checked_vars):
        self.ok = ok
        self.issues = issues
        self.actions = []
        self.n_timesteps = n_timesteps
        self.checked_vars = checked_vars


_STRIDE_TABLE = {
    "synoptic": {"stride_days": 1, "stride_hours": 6,
                 "forecast_hours": (0, 6, 12, 18), "reason": "synoptic"},
    "tide": {"stride_days": 1, "stride_hours": 3,
             "forecast_hours": None, "reason": "tide"},
    "seasonal": {"stride_days": 7, "stride_hours": 0,
                 "forecast_hours": None, "reason": "seasonal"},
    "event": {"stride_days": 1, "stride_hours": 0,
              "forecast_hours": None, "reason": "event",
              "per_event": True},
}


def make_autopilot_stub(calls, issues=()):
    """Build a stub `autopilot` package honoring the real signatures."""
    pkg = types.ModuleType("autopilot")

    stride_mod = types.ModuleType("autopilot.stride")

    def recommend_stride(phenomenon, source=None):
        calls.append(("stride", (phenomenon, source)))
        out = {"phenomenon": phenomenon, "source": source}
        out.update(_STRIDE_TABLE[phenomenon])
        return out

    stride_mod.recommend_stride = recommend_stride

    qa_mod = types.ModuleType("autopilot.qa")
    qa_mod.QAError = FakeQAError

    def qa_field(field, plausible=None, expected_cadence_hours=None,
                 outlier_fraction_threshold=0.01):
        calls.append(("qa", None))
        grids = field["grids"]
        n = len(list(field["times"]))
        return FakeQAReport(ok=not issues, issues=list(issues),
                            n_timesteps=n,
                            checked_vars=sorted(grids))

    def repair(report, field, policy="drop"):
        calls.append(("repair", policy))
        if policy not in ("drop", "interpolate", "fail"):
            raise ValueError(f"unknown repair policy {policy!r}")
        if policy == "fail" and report.issues:
            raise FakeQAError("QA failed with issues; refusing to repair")
        out = {
            "grids": {k: np.asarray(v, dtype=float)
                      for k, v in field["grids"].items()},
            "times": np.asarray(list(field["times"])),
            "lats": (np.asarray(field["lats"], dtype=float)
                     if field.get("lats") is not None else None),
            "lons": (np.asarray(field["lons"], dtype=float)
                     if field.get("lons") is not None else None),
        }
        for issue in report.issues:
            report.actions.append(
                {"action": f"{policy}d_timestep",
                 "index": issue["index"],
                 "reason": issue["type"]})
        return out, report

    qa_mod.qa_field = qa_field
    qa_mod.repair = repair

    compose_mod = types.ModuleType("autopilot.compose")

    def search_composition(render_thumbnail, candidates, weights=None,
                           label_boxes=None):
        calls.append(("compose", len(candidates)))
        scored = []
        for i, cand in enumerate(candidates):
            thumb = render_thumbnail(cand["preset"], cand["rotation"],
                                     cand["basemap"])
            calls.append(("thumbnail", cand["preset"]))
            scored.append({"candidate": dict(cand), "coverage": 0.9,
                           "contrast": 0.8 - 0.05 * i,
                           "label_legibility": 0.5,
                           "total": 0.9 - 0.01 * i})
        scored.sort(key=lambda d: d["total"], reverse=True)
        return {"winner": scored[0]["candidate"], "scores": scored,
                "weights": {"coverage": 0.4}, "n_candidates": len(scored)}

    compose_mod.search_composition = search_composition

    pkg.stride = stride_mod
    pkg.qa = qa_mod
    pkg.compose = compose_mod
    return pkg


@pytest.fixture
def autopilot_stub(monkeypatch):
    calls: list = []
    pkg = make_autopilot_stub(calls)
    monkeypatch.setitem(sys.modules, "autopilot", pkg)
    monkeypatch.setitem(sys.modules, "autopilot.stride", pkg.stride)
    monkeypatch.setitem(sys.modules, "autopilot.qa", pkg.qa)
    monkeypatch.setitem(sys.modules, "autopilot.compose", pkg.compose)
    return calls, pkg


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

def test_autopilot_false_is_byte_identical(tmp_path):
    """autopilot=False: no provenance key, no new kwargs, strides pass."""
    calls: list = []
    peers = make_peers(calls)
    result = run_pipeline(FakeSpec(), peers, str(tmp_path),
                          stride_days=30, stride_hours=24)
    assert "autopilot" not in result.provenance
    # the full render got no kwargs at all (the stub would TypeError
    # on any unexpected kwarg, and the old set is unchanged)
    renders = [kw for name, kw in calls if name == "render_viz"]
    assert len(renders) == 1
    assert renders[0]["kwargs"] == {}
    # caller's stride reached the fetch untouched
    fetch = [kw for name, kw in calls if name == "fetch"][0]
    assert fetch["stride_days"] == 30
    assert result.n_frames == 1


def test_autopilot_true_full_orchestration(tmp_path, autopilot_stub):
    """Stubbed engine: stride -> fetch -> QA -> knobs -> search -> render."""
    calls, _pkg = autopilot_stub
    peers = make_peers(calls)
    result = run_pipeline(FakeSpec(), peers, str(tmp_path),
                          stride_days=30, stride_hours=24,
                          autopilot=True)
    auto = result.provenance["autopilot"]
    assert auto["enabled"] is True

    # -- orchestration order: stride before fetch, QA after fetch,
    # -- composition thumbnails before the full render
    order = [name for name, _ in calls]
    assert order.index("stride") < order.index("fetch")
    assert order.index("fetch") < order.index("qa")
    assert order.index("qa") < order.index("repair")
    assert order.index("repair") < order.index("compose")
    thumbs = [i for i, n in enumerate(order) if n == "thumbnail"]
    full_idx = max(i for i, n in enumerate(order) if n == "render_viz")
    assert thumbs and max(thumbs) < full_idx
    # the full render is the LAST render_viz call (thumbnails first)
    renders = [kw for name, kw in calls if name == "render_viz"]
    full_kw = renders[-1]["kwargs"]
    assert "autopilot-thumbs" not in renders[-1]["out_dir"]

    # -- stride: phenomenon synoptic, caller's 30d overridden to 1d
    assert auto["phenomenon"] == "synoptic"
    assert auto["stride"]["via"] == "survey-autopilot"
    assert auto["stride"]["original"]["stride_days"] == 30
    assert auto["stride"]["applied"]["stride_days"] == 1
    fetch = [kw for name, kw in calls if name == "fetch"][0]
    assert fetch["stride_days"] == 1
    # GFS-only forecast_hours accepted from the recommendation
    assert fetch["forecast_hours"] == (0, 6, 12, 18)

    # -- QA gate ran on the fetched field
    assert auto["qa"]["policy"] == "drop"
    assert auto["qa"]["ok"] is True
    assert auto["qa"]["issues"] == []
    assert [n for n, _ in calls].count("repair") == 1

    # -- render knobs applied (stub peer accepts **kwargs)
    assert auto["render_knobs"]["robust_scale"] == {"applied": True}
    assert auto["render_knobs"]["strand_count"] == {"applied": "auto"}
    assert auto["render_knobs"]["salience_labels"] == {"applied": True}
    assert full_kw["robust_scale"] is True
    assert full_kw["strand_count"] == "auto"
    assert full_kw["salience_labels"] is True

    # -- composition: default 2x2x2 grid, winner fed to the full render
    comp = auto["composition"]
    assert comp["n_candidates"] == 8
    assert len(comp["scores"]) == 8
    assert comp["winner"]["preset"] == "dark_strands"
    assert full_kw["preset"] == comp["winner"]["preset"]
    assert full_kw["basemap"] == comp["winner"]["basemap"]
    # no thumbnail errors recorded
    assert all("error" not in s for s in comp["scores"])

    # -- provenance is JSON-serializable
    json.dumps(result.provenance["autopilot"])
    assert result.n_frames == 1


def test_autopilot_true_records_qa_actions(tmp_path, autopilot_stub):
    """QA issues are repaired and every action lands in provenance."""
    calls, pkg = autopilot_stub
    # re-stub with one all-NaN issue present
    issue = {"type": "all_nan", "var": "u10", "index": 0,
             "time": "2026-09-29T00:00:00+00:00"}

    def qa_field(field, plausible=None, expected_cadence_hours=None,
                 outlier_fraction_threshold=0.01):
        calls.append(("qa", None))
        return FakeQAReport(ok=False, issues=[issue],
                            n_timesteps=len(list(field["times"])),
                            checked_vars=sorted(field["grids"]))

    pkg.qa.qa_field = qa_field
    peers = make_peers(calls, field=make_field(bad_first_step=True))
    result = run_pipeline(FakeSpec(), peers, str(tmp_path),
                          autopilot=True)
    qa_rec = result.provenance["autopilot"]["qa"]
    assert qa_rec["status"] == "repaired"
    assert qa_rec["ok"] is False
    assert qa_rec["issues"] == [issue]
    assert len(qa_rec["actions"]) == 1
    assert qa_rec["actions"][0]["index"] == 0
    json.dumps(qa_rec)


def test_autopilot_true_without_engine_raises(tmp_path, monkeypatch):
    """Neither autopilot nor timescales importable -> RuntimeError."""
    monkeypatch.setitem(sys.modules, "autopilot", None)
    monkeypatch.setitem(sys.modules, "timescales", None)
    peers = make_peers([])
    with pytest.raises(RuntimeError, match="survey-autopilot"):
        run_pipeline(FakeSpec(), peers, str(tmp_path), autopilot=True)


def test_autopilot_bad_qa_policy_raises(tmp_path, autopilot_stub):
    peers = make_peers([])
    with pytest.raises(ValueError, match="autopilot_qa_policy"):
        run_pipeline(FakeSpec(), peers, str(tmp_path), autopilot=True,
                     autopilot_qa_policy="nuke")


def test_autopilot_fail_policy_propagates_qaerror(tmp_path, autopilot_stub):
    """policy='fail' with QA issues -> the engine's QAError escapes."""
    calls, pkg = autopilot_stub
    issue = {"type": "all_nan", "var": "u10", "index": 0,
             "time": "2026-09-29T00:00:00+00:00"}

    def qa_field(field, plausible=None, expected_cadence_hours=None,
                 outlier_fraction_threshold=0.01):
        return FakeQAReport(ok=False, issues=[issue],
                            n_timesteps=len(list(field["times"])),
                            checked_vars=sorted(field["grids"]))

    pkg.qa.qa_field = qa_field
    peers = make_peers(calls, field=make_field(bad_first_step=True))
    with pytest.raises(FakeQAError):
        run_pipeline(FakeSpec(), peers, str(tmp_path), autopilot=True,
                     autopilot_qa_policy="fail")


def test_autopilot_qa_skips_non_gridded_field(tmp_path, autopilot_stub,
                                              monkeypatch):
    """Storm-track dict (no grids/values) skips the QA gate, still runs."""
    calls, _pkg = autopilot_stub
    storm_field = {
        "tracks": [{"name": "TEST", "points": [[-60.0, 20.0]]}],
        "times": ["2026-09-29T00:00:00+00:00"],
        "provenance": {"source": "test"},
    }

    def resolve_source(spec):
        return "ibtracs"

    def fetch_ibtracs(bbox, start, end, **kw):
        calls.append(("fetch", kw))
        return storm_field

    peers = make_peers(calls)
    peers.resolve_source = resolve_source
    peers.fetch_ibtracs = fetch_ibtracs
    del peers.fetch_gfs_wind

    result = run_pipeline(FakeSpec(variable="storm-tracks",
                                   source="ibtracs"),
                          peers, str(tmp_path), autopilot=True)
    qa_rec = result.provenance["autopilot"]["qa"]
    assert qa_rec["status"] == "skipped"
    assert "grids" in qa_rec["reason"]
    assert qa_rec["issues"] == [] and qa_rec["actions"] == []
    # stride still applied (event phenomenon); render still happened
    assert result.provenance["autopilot"]["phenomenon"] == "event"
    assert result.n_frames == 1
