"""Tests for the v0.10.0 Publish step wiring (survey-publish peer).

All publishing logic lives in the survey-publish engine; reel-studio
only calls it. These tests pin the wiring: the peer registry entry,
the manifest ``"publication"`` section, and the headless-safe app
helpers that probe adapters, build requests, and never publish to an
unconnected platform. No Streamlit runtime is needed.
"""

from __future__ import annotations

import json
import os
import sys
import types

import pytest


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


@pytest.fixture(scope="module")
def pipeline_module():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)
    from studio import pipeline
    return pipeline


# --- peer registry ------------------------------------------------------------


def test_publish_peer_spec(pipeline_module):
    from studio import peers
    spec = peers.PEER_SPECS["survey-publish"]
    assert spec["module"] == "publish"
    assert spec["pip"] == ("pip install "
                           "git+https://github.com/crieck2010/"
                           "survey-publish.git")
    assert "Publish" in spec["needed_for"] or "publish" in spec["needed_for"]


def test_publish_peer_status_loads(pipeline_module):
    from studio import peers
    statuses = peers.load_peers()
    assert "survey-publish" in statuses
    assert statuses["survey-publish"].module_name == "publish"


# --- pipeline.record_publication ------------------------------------------------


def test_record_publication_updates_existing_manifest(pipeline_module, tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"render": {"n_frames": 3}}))
    publication = {
        "approved_at": "2026-09-28T21:40:00",
        "platforms": {"youtube": {"ok": True, "url_or_id": "abc",
                                  "error": ""}},
        "title": "T", "caption": "C", "hashtags": ["reelstudio"],
    }
    data = pipeline_module.record_publication(str(manifest), publication)
    assert data["publication"] == publication
    # Existing content is preserved.
    assert data["render"]["n_frames"] == 3
    reloaded = json.loads(manifest.read_text())
    assert reloaded["publication"]["platforms"]["youtube"]["ok"] is True


def test_record_publication_tolerates_missing_manifest(pipeline_module,
                                                      tmp_path):
    manifest = tmp_path / "manifest.json"
    publication = {"approved_at": "x", "platforms": {}, "title": "T",
                   "caption": "C", "hashtags": []}
    data = pipeline_module.record_publication(str(manifest), publication)
    assert data == {"publication": publication}
    assert json.loads(manifest.read_text()) == data


def test_record_publication_tolerates_corrupt_manifest(pipeline_module,
                                                       tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{not json")
    publication = {"approved_at": "x", "platforms": {}, "title": "T",
                   "caption": "C", "hashtags": []}
    data = pipeline_module.record_publication(str(manifest), publication)
    assert data == {"publication": publication}


# --- app helpers ----------------------------------------------------------------


def test_publish_peer_returns_none_when_missing(app_module, monkeypatch):
    # Simulate survey-publish not being installed, regardless of the ambient
    # environment: an explicit None entry makes `import publish...` raise
    # ImportError even when the real peer is importable.
    monkeypatch.setitem(sys.modules, "publish", None)
    monkeypatch.delitem(sys.modules, "publish.models", raising=False)
    monkeypatch.delitem(sys.modules, "publish.registry", raising=False)
    assert app_module._publish_peer() is None


def test_publish_platform_names_falls_back_to_documented_four(app_module):
    class Registry:
        def list_platforms(self):
            raise RuntimeError("no registry")
    pub = types.SimpleNamespace(registry=Registry())
    assert app_module._publish_platform_names(pub) == [
        "youtube", "instagram", "facebook", "tiktok"]


def test_publish_defaults_prefills_from_spec(app_module):
    result = {"spec_dict": {"title": "Gulf Heat",
                            "variable": "sst",
                            "region_key": "gulf-of-mexico",
                            "start_date": "2020-01-01",
                            "end_date": "2022-12-31"}}
    defaults = app_module._publish_defaults(result)
    assert defaults["title"] == "Gulf Heat"
    assert "Gulf Heat" in defaults["caption"]
    assert "gulf of mexico" in defaults["caption"]
    assert "reel-studio" in defaults["caption"]
    assert "reelstudio" in defaults["hashtags"]


def test_publish_defaults_survives_empty_spec(app_module):
    defaults = app_module._publish_defaults({})
    assert defaults["title"] == "reel-studio reel"
    assert defaults["caption"]


def test_parse_hashtags(app_module):
    assert app_module._parse_hashtags("#a, b , #c") == ["a", "b", "c"]
    assert app_module._parse_hashtags("") == []


def test_publication_record_shape(app_module):
    record = app_module._publication_record(
        "T", "C", ["a"],
        {"youtube": {"ok": True, "url_or_id": "u", "error": ""},
         "tiktok": {"ok": False, "url_or_id": "", "error": "boom"}},
        approved_at="2026-09-28T21:40:00")
    assert record["approved_at"] == "2026-09-28T21:40:00"
    assert record["title"] == "T"
    assert record["caption"] == "C"
    assert record["hashtags"] == ["a"]
    assert record["platforms"]["youtube"]["ok"] is True
    assert record["platforms"]["tiktok"]["error"] == "boom"


class _FakeResult:
    def __init__(self, ok, url_or_id="", error=""):
        self.ok = ok
        self.url_or_id = url_or_id
        self.error = error


class _FakeAdapter:
    def __init__(self, connected, outcome=None, label=""):
        self._connected = connected
        self._outcome = outcome
        self._label = label
        self.published = []

    def is_connected(self):
        return self._connected

    def account_label(self):
        return self._label

    def publish(self, request):
        self.published.append(request)
        return self._outcome


def _fake_pub(adapters):
    class Models:
        class PublishRequest:
            def __init__(self, video_path, title, caption, hashtags,
                         platform_options):
                self.video_path = video_path
                self.title = title
                self.caption = caption
                self.hashtags = hashtags
                self.platform_options = platform_options

    class Registry:
        def get_adapter(self, name):
            return adapters[name]

        def list_platforms(self):
            return list(adapters)

    return types.SimpleNamespace(models=Models(), registry=Registry())


def test_publish_now_only_touches_connected(app_module):
    connected = _FakeAdapter(True, _FakeResult(True, url_or_id="vid123"))
    unconnected = _FakeAdapter(False)
    pub = _fake_pub({"youtube": connected, "tiktok": unconnected})
    results = app_module._publish_now(pub, "/tmp/reel.mp4", "T", "C",
                                      ["a"], ["youtube", "tiktok"])
    assert results["youtube"]["ok"] is True
    assert results["youtube"]["url_or_id"] == "vid123"
    assert "tiktok" not in results  # never published, never reported
    assert len(connected.published) == 1
    assert unconnected.published == []
    request = connected.published[0]
    assert request.video_path == "/tmp/reel.mp4"
    assert request.title == "T"
    assert request.hashtags == ["a"]
    assert request.platform_options == {}


def test_publish_now_records_failures(app_module):
    adapter = _FakeAdapter(True, _FakeResult(False, error="quota"))
    pub = _fake_pub({"youtube": adapter})
    results = app_module._publish_now(pub, "/tmp/reel.mp4", "T", "C",
                                      [], ["youtube"])
    assert results["youtube"] == {"ok": False, "url_or_id": "",
                                  "error": "quota"}


def test_probe_publish_adapters(app_module):
    pub = _fake_pub({"youtube": _FakeAdapter(True, label="@chan"),
                     "tiktok": _FakeAdapter(False)})
    probed = app_module._probe_publish_adapters(pub)
    assert probed["youtube"]["connected"] is True
    assert probed["youtube"]["label"] == "@chan"
    assert probed["tiktok"]["connected"] is False
