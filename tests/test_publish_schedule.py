"""Tests for the v0.11.0 Publish step scheduling (survey-publish >= 0.2.0 queue).

Covers the headless-safe helpers added for "Schedule for later" (time
resolution, slot labels, enqueue, partial-failure requeue logic, the
``"queued_publication"`` manifest record) plus headless smoke-renders of
the step and its queue panel with a fake Streamlit. No real Streamlit
runtime is needed; the real survey-publish 0.2.0 engine is used against
tmp-path queue files.
"""

from __future__ import annotations

import datetime as dt
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


@pytest.fixture(scope="module")
def qpub(app_module):
    peer = app_module._publish_queue_peer()
    assert peer is not None, "survey-publish >= 0.2.0 must be installed"
    return peer


# --- a minimal fake Streamlit ---------------------------------------------------


class _Ctx:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeSt:
    def __init__(self):
        self.session_state = {}
        self.calls = []
        self.buttons = {}   # widget key -> bool
        self.inputs = {}    # widget key -> text value

    def _rec(self, name, *args):
        self.calls.append((name, args))

    def subheader(self, *a, **k): self._rec("subheader", *a)
    def write(self, *a, **k): self._rec("write", *a)
    def info(self, *a, **k): self._rec("info", *a)
    def warning(self, *a, **k): self._rec("warning", *a)
    def error(self, *a, **k): self._rec("error", *a)
    def success(self, *a, **k): self._rec("success", *a)
    def caption(self, *a, **k): self._rec("caption", *a)
    def markdown(self, *a, **k): self._rec("markdown", *a)
    def code(self, *a, **k): self._rec("code", *a)
    def video(self, *a, **k): self._rec("video", *a)
    def columns(self, n): return [_Ctx() for _ in range(n)]

    def expander(self, *a, **k):
        self._rec("expander", *a)
        return _Ctx()

    def text_input(self, label, value="", key=None, **k):
        # like real Streamlit: an existing session_state widget value wins
        if key is not None and key in self.session_state:
            return self.session_state[key]
        return self.inputs.get(key, value)

    def text_area(self, label, value="", key=None, **k):
        if key is not None and key in self.session_state:
            return self.session_state[key]
        return self.inputs.get(key, value)

    def date_input(self, label, value=None, key=None, **k):
        return value

    def radio(self, label, options, key=None, **k):
        self._rec("radio", label)
        if key and key not in self.session_state:
            self.session_state[key] = options[0]
        return self.session_state.get(key, options[0])

    def button(self, label, key=None, **k):
        self._rec("button", label)
        return bool(self.buttons.get(key, False))

    def multiselect(self, label, options, default=None, key=None, **k):
        return list(default or [])


@pytest.fixture()
def fake_st(app_module, monkeypatch):
    fake = _FakeSt()
    monkeypatch.setattr(app_module, "st", fake)
    return fake


@pytest.fixture()
def tmp_qpub(app_module, qpub, tmp_path, monkeypatch):
    """Queue peer whose store writes to a tmp queue file, not ~/.survey-publish."""
    store_path = str(tmp_path / "queue.json")
    ns = types.SimpleNamespace(
        QueuedItem=qpub.QueuedItem,
        QueueStore=lambda: qpub.QueueStore(store_path),
        parse_schedule_time=qpub.parse_schedule_time,
        DEFAULT_SLOTS=qpub.DEFAULT_SLOTS,
    )
    monkeypatch.setattr(app_module, "_publish_queue_peer", lambda: ns)
    return ns


def _statuses(app_module, connected=("youtube",)):
    import publish as publish_mod
    from studio import peers
    probed = {
        "youtube": {"connected": "youtube" in connected, "label": "@chan",
                    "adapter": None},
        "tiktok": {"connected": "tiktok" in connected, "label": "",
                   "adapter": None},
    }
    statuses = {
        "survey-publish": peers.PeerStatus(
            repo="survey-publish", module_name="publish",
            module=publish_mod, pip_command="pip ...",
            needed_for="publishing + queue"),
    }
    return statuses, probed


def _result(tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake-mp4")
    return {"video_path": str(video),
            "manifest_path": str(tmp_path / "manifest.json"),
            "spec_dict": {"title": "Test Reel"}}


# --- queue peer -----------------------------------------------------------------


def test_queue_peer_imports_engine(qpub):
    assert qpub.DEFAULT_SLOTS == ["08:30", "12:30", "18:30"]
    for attr in ("QueuedItem", "QueueStore", "parse_schedule_time"):
        assert callable(getattr(qpub, attr))


def test_queue_peer_returns_none_when_engine_too_old(app_module,
                                                     monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "publish":
            raise ImportError("no queue engine here")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    assert app_module._publish_queue_peer() is None


# --- slot labels / time resolution ----------------------------------------------


def test_schedule_slot_labels(app_module, qpub):
    assert app_module._schedule_slot_labels(qpub) == [
        "Morning 08:30", "Midday 12:30", "Evening 18:30"]


def test_resolve_preset_future_today(app_module, qpub):
    now = dt.datetime(2026, 9, 28, 7, 0)
    when = app_module._resolve_schedule_time(
        qpub, ("preset", "08:30"), None, "", now=now)
    assert (when.year, when.month, when.day, when.hour, when.minute) == (
        2026, 9, 28, 8, 30)


def test_resolve_preset_rolls_to_tomorrow(app_module, qpub):
    now = dt.datetime(2026, 9, 28, 9, 0)
    when = app_module._resolve_schedule_time(
        qpub, ("preset", "08:30"), None, "", now=now)
    assert (when.year, when.month, when.day, when.hour, when.minute) == (
        2026, 9, 29, 8, 30)


def test_resolve_custom_date_and_time(app_module, qpub):
    now = dt.datetime(2026, 9, 28, 7, 0)
    when = app_module._resolve_schedule_time(
        qpub, ("custom", None), dt.date(2026, 10, 2), "14:05", now=now)
    assert (when.year, when.month, when.day, when.hour, when.minute) == (
        2026, 10, 2, 14, 5)
    assert when.tzinfo is not None  # engine stamps local tz


def test_resolve_bad_time_raises(app_module, qpub):
    with pytest.raises(ValueError):
        app_module._resolve_schedule_time(
            qpub, ("custom", None), dt.date(2026, 10, 2), "25:99")


def test_format_schedule_local(app_module, qpub):
    when = app_module._resolve_schedule_time(
        qpub, ("custom", None), dt.date(2026, 10, 2), "14:05")
    shown = app_module._format_schedule_local(when)
    assert "2026-10-02 14:05" in shown


# --- partial-failure rule ---------------------------------------------------------


def test_failed_platforms_partial(app_module):
    item = types.SimpleNamespace(
        platforms=["youtube", "tiktok", "facebook"],
        published_urls={"youtube": "https://youtu.be/x"})
    assert app_module._failed_platforms(item) == ["tiktok", "facebook"]


def test_failed_platforms_all_succeeded(app_module):
    item = types.SimpleNamespace(
        platforms=["youtube"],
        published_urls={"youtube": "https://youtu.be/x"})
    assert app_module._failed_platforms(item) == []


def test_failed_platforms_tolerates_missing_attrs(app_module):
    assert app_module._failed_platforms(
        types.SimpleNamespace(platforms=["a"], published_urls=None)) == ["a"]
    assert app_module._failed_platforms(types.SimpleNamespace()) == []


# --- enqueue / store round trip ----------------------------------------------------


def test_enqueue_publish_round_trip(app_module, qpub, tmp_path):
    store = qpub.QueueStore(str(tmp_path / "queue.json"))
    item_id = app_module._enqueue_publish(
        qpub, store, video_path="/tmp/reel.mp4", title="T", caption="C",
        hashtags=["a"], platforms=["youtube", "tiktok"],
        scheduled_at="2026-09-29T08:30:00-04:00")
    assert item_id
    items = store.list_queue()
    assert len(items) == 1
    item = items[0]
    assert item.id == item_id
    assert item.status == "queued"
    assert item.platforms == ["youtube", "tiktok"]
    assert item.scheduled_at == "2026-09-29T08:30:00-04:00"
    assert item.published_urls == {}

    store.reschedule(item_id, "2026-09-29T12:30:00-04:00")
    assert store.get(item_id).scheduled_at == "2026-09-29T12:30:00-04:00"

    store.cancel(item_id)
    assert store.list_queue(status_filter="queued") == []


def test_queued_record_shape(app_module):
    record = app_module._queued_record(
        "T", "C", ["a"], "q-1", "2026-09-29T08:30:00-04:00",
        ["youtube"], queued_at="2026-09-28T22:10:00")
    assert record["queued_at"] == "2026-09-28T22:10:00"
    assert record["item_id"] == "q-1"
    assert record["scheduled_at"] == "2026-09-29T08:30:00-04:00"
    assert record["platforms"] == ["youtube"]
    assert record["hashtags"] == ["a"]
    # queued_at defaults to now when omitted
    assert app_module._queued_record("T", "C", [], "q-2", "x",
                                     [])["queued_at"]


# --- pipeline.record_queued_publication --------------------------------------------


def test_record_queued_publication_updates_manifest(pipeline_module,
                                                   tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"render": {"n_frames": 3}}))
    queued = {"queued_at": "2026-09-28T22:10:00", "item_id": "q-1",
              "scheduled_at": "2026-09-29T08:30:00-04:00",
              "platforms": ["youtube"], "title": "T", "caption": "C",
              "hashtags": []}
    data = pipeline_module.record_queued_publication(str(manifest), queued)
    assert data["queued_publication"] == queued
    assert data["render"]["n_frames"] == 3
    reloaded = json.loads(manifest.read_text())
    assert reloaded["queued_publication"]["item_id"] == "q-1"


def test_record_queued_publication_tolerates_missing_manifest(pipeline_module,
                                                             tmp_path):
    manifest = tmp_path / "manifest.json"
    queued = {"item_id": "q-1"}
    data = pipeline_module.record_queued_publication(str(manifest), queued)
    assert data == {"queued_publication": {"item_id": "q-1"}}


# --- peers.py note ------------------------------------------------------------------


def test_peers_note_names_queue_engine():
    from studio import peers
    note = peers.PEER_SPECS["survey-publish"]["needed_for"]
    assert "QueuedItem" in note
    assert "0.2.0" in note


# --- headless step smoke renders ------------------------------------------------------


def test_publish_step_renders_without_result(app_module, fake_st,
                                             tmp_path):
    statuses, _ = _statuses(app_module)
    app_module._publish_step(statuses)
    infos = [a[0] for name, a in fake_st.calls if name == "info"]
    assert any("Generate a reel first" in str(t) for t in infos)


def test_publish_step_renders_publish_now_mode(app_module, fake_st,
                                               tmp_qpub, tmp_path,
                                               monkeypatch):
    statuses, probed = _statuses(app_module)
    monkeypatch.setattr(app_module, "_probe_publish_adapters",
                        lambda pub: probed)
    fake_st.session_state["result"] = _result(tmp_path)
    app_module._publish_step(statuses)
    radios = [a for name, a in fake_st.calls if name == "radio"]
    # the per-reel mode radio is offered
    assert any("Publish mode" in str(a[0]) for a in radios)
    buttons = [a[0] for name, a in fake_st.calls if name == "button"]
    assert "Approve & Publish" in buttons
    # queue panel + onboarding hint always render
    expanders = [a[0] for name, a in fake_st.calls if name == "expander"]
    assert any("Publish queue" in str(t) for t in expanders)
    infos = [a[0] for name, a in fake_st.calls if name == "info"]
    assert any("survey-publish/blob/main/docs/SCHEDULING.md" in str(t)
               for t in infos)


def test_schedule_mode_queues_item(app_module, fake_st, tmp_qpub, tmp_path,
                                   monkeypatch):
    statuses, probed = _statuses(app_module, connected=("youtube",))
    monkeypatch.setattr(app_module, "_probe_publish_adapters",
                        lambda pub: probed)
    fake_st.session_state["publish_mode"] = "Schedule for later"
    fake_st.session_state["result"] = _result(tmp_path)
    fake_st.buttons["publish_queue"] = True
    app_module._publish_step(statuses)

    successes = [a[0] for name, a in fake_st.calls if name == "success"]
    assert any("Queued — id" in str(t) for t in successes)
    items = tmp_qpub.QueueStore().list_queue()
    assert len(items) == 1
    item = items[0]
    assert item.status == "queued"
    assert item.platforms == ["youtube"]  # defaulted to connected only
    assert item.title == "Test Reel"
    # manifest got the queued_publication section
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest["queued_publication"]["item_id"] == item.id


def test_queue_panel_cancel_and_reschedule(app_module, fake_st, tmp_qpub,
                                           monkeypatch):
    item_id = app_module._enqueue_publish(
        tmp_qpub, tmp_qpub.QueueStore(), video_path="/tmp/r.mp4", title="T",
        caption="C", hashtags=[], platforms=["youtube"],
        scheduled_at="2026-09-29T08:30:00-04:00")
    key = item_id.replace("-", "")
    fake_st.inputs[f"qresched_{key}"] = "18:30"
    fake_st.buttons[f"qresched_go_{key}"] = True
    app_module._publish_queue_panel(tmp_qpub)
    item = tmp_qpub.QueueStore().get(item_id)
    assert "18:30" in item.scheduled_at

    fake_st.buttons = {f"qcancel_{key}": True}
    fake_st.calls.clear()
    app_module._publish_queue_panel(tmp_qpub)
    assert tmp_qpub.QueueStore().list_queue(status_filter="queued") == []
    assert any("Canceled" in str(a[0])
               for name, a in fake_st.calls if name == "success")


def test_queue_panel_requeue_failed_platforms_only(app_module, fake_st,
                                                   tmp_qpub, tmp_path):
    # Build the failed item the way the engine's tick() leaves one:
    # status "failed", last_error set, and only the platforms that
    # actually published recorded in published_urls.
    failed = tmp_qpub.QueuedItem(
        id="failed1", video_path="/tmp/r.mp4", title="Partial", caption="C",
        hashtags=[], platforms=["youtube", "tiktok"], status="failed",
        attempts=1, last_error="tiktok quota exceeded",
        published_urls={"youtube": "https://youtu.be/x"},
        scheduled_at="2026-09-29T08:30:00-04:00")
    (tmp_path / "queue.json").write_text(json.dumps(
        {"version": 1, "items": {"failed1": failed.to_dict()}}))
    store = tmp_qpub.QueueStore()
    assert store.get("failed1").status == "failed"

    fake_st.buttons["qrequeue_failed1"] = True
    app_module._publish_queue_panel(tmp_qpub)
    queued = store.list_queue(status_filter="queued")
    assert len(queued) == 1
    # only the missing platform was re-enqueued — no double post
    assert queued[0].platforms == ["tiktok"]
    errors = [a[0] for name, a in fake_st.calls if name == "error"]
    assert any("tiktok quota exceeded" in str(t) for t in errors)
    assert any("NOT" in str(a[0]) and "reposted" in str(a[0])
               for name, a in fake_st.calls if name == "success")
