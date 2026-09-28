"""End-to-end OFFLINE test with the REAL peers (no network).

parse_description -> plan_fetch -> synthetic GlseaField ->
render_viz -> render_video (frames dir) -> MP4 + sidecar.

This is the test that caught two genuine interop quirks during the
build (documented in docs/INTEROP.md):
  1. viz's manifest schema is rejected by animate's reader, so the
     frames DIRECTORY (not the manifest path) is passed to render_video;
  2. GlseaField.times are ISO *datetimes*, which viz's date coercion
     cannot parse, so the pipeline normalizes them to date-only strings.

Skipped when any of the four peers is not installed/importable.
"""

from __future__ import annotations

import datetime as _dt
import os
import types

import pytest

viz = pytest.importorskip("viz", reason="survey-viz not installed")
glsea = pytest.importorskip("currents.glsea", reason="survey-currents not installed")
animate = pytest.importorskip("animate", reason="survey-animate not installed")

from studio import pipeline  # noqa: E402


def _wired(field, series):
    def _render_viz(spec, field, series, out_dir):
        # Offline by contract: the GEBCO/Natural Earth underlay would
        # otherwise hit the network on first use.
        return viz.render_viz(spec, field, series, out_dir=out_dir,
                              underlay=False)
    return types.SimpleNamespace(
        is_fetchable=viz.is_fetchable,
        fetch_sst=lambda bbox, start, end, stride_days=30: field,
        fetch_averages=lambda lake, start, end: series,
        render_viz=_render_viz,
        render_video=animate.render_video,
        glsea_bounds=(-92.4199507342304, 38.8749871947297,
                      -75.8816402880531, 50.6059751976539),
    )


def test_e2e_offline_real_peers(tmp_path):
    spec = viz.parse_description(
        "Lake Michigan water temperature last summer",
        today=_dt.date(2026, 9, 26))
    field = glsea.GlseaField.synthetic(
        nt=4, ny=8, nx=10, lats=(41.5, 46.2), lons=(-88.2, -85.8),
        start="2026-06-01T12:00:00+00:00", step_days=30)
    series = types.SimpleNamespace(
        dates=["2026-06-01", "2026-07-01", "2026-08-01"],
        temps=[12.5, 18.0, 21.3],
        provenance={"synthetic": True})

    events = []
    result = pipeline.run_pipeline(
        spec, _wired(field, series), str(tmp_path),
        progress=lambda frac, msg: events.append((frac, msg)))

    # 4 daily-ish timesteps bucket into 3 monthly frames (Jun/Jul/Aug).
    assert result.n_frames == 3
    assert result.video_path.endswith("reel.mp4")
    assert os.path.isfile(result.video_path)
    assert os.path.getsize(result.video_path) > 10_000
    assert os.path.isfile(result.sidecar_path)
    assert os.path.isfile(result.manifest_path)
    assert len(os.listdir(result.frames_dir)) >= 3
    assert events[-1] == (1.0, "Done")
    # Real GlseaField provenance flows through (synthetic fixture here).
    assert result.provenance["fetch"]["sst"]["synthetic"] is True
    assert result.provenance["lake"] == "michigan"


def test_e2e_mp4_is_valid_video(tmp_path):
    """The output is a real MP4 (ftyp box), not just bytes on disk."""
    spec = viz.parse_description(
        "Lake Michigan water temperature last summer",
        today=_dt.date(2026, 9, 26))
    field = glsea.GlseaField.synthetic(
        nt=4, ny=8, nx=10, lats=(41.5, 46.2), lons=(-88.2, -85.8),
        start="2026-06-01T12:00:00+00:00", step_days=30)
    series = types.SimpleNamespace(
        dates=["2026-06-01", "2026-07-01", "2026-08-01"],
        temps=[12.5, 18.0, 21.3], provenance={})
    result = pipeline.run_pipeline(
        spec, _wired(field, series), str(tmp_path))
    with open(result.video_path, "rb") as fh:
        header = fh.read(12)
    assert header[4:8] == b"ftyp"


def test_field_adapter_normalizes_iso_datetimes():
    from studio.pipeline import _field_to_dict, _time_to_date_str

    assert _time_to_date_str("2026-06-01T12:00:00+00:00") == "2026-06-01"
    assert _time_to_date_str("2026-06-01") == "2026-06-01"
    assert _time_to_date_str(_dt.datetime(2026, 6, 1, 12)) == "2026-06-01"
    assert _time_to_date_str(_dt.date(2026, 6, 1)) == "2026-06-01"

    field = glsea.GlseaField.synthetic(nt=2)
    adapted = _field_to_dict(field)
    assert adapted["times"] == [t[:10] for t in field.times]
    assert adapted["values"].shape == (2, 6, 8)


def test_field_adapter_rejects_gridless_object():
    from studio.pipeline import _field_to_dict

    with pytest.raises(TypeError, match="no 3D grid data"):
        _field_to_dict(types.SimpleNamespace(times=["2026-01-01"],
                                             lats=[0.0], lons=[0.0]))
