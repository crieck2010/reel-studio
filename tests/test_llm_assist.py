"""LLM assist path with mocked urllib (no network, no key needed)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from studio import llm_assist

SPEC_DICT = {
    "title": "Lake Superior — Surface Water Temperature, 2016–2026",
    "region_key": "lake-superior",
    "bbox": [-92.5, 46.0, -84.5, 48.8],
    "variable": "sst",
    "start": "2016-09-26",
    "end": "2026-09-26",
    "cadence": "monthly",
    "layout": "reel-vertical",
    "style": "reel-dark",
    "vmin": None,
    "vmax": None,
}


def _ok_response(payload):
    body = {"choices": [{"message": {"content": json.dumps(payload)}}]}

    class Resp:
        def read(self):
            return json.dumps(body).encode("utf-8")

    return Resp()


def test_success_returns_spec_dict():
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["timeout"] = timeout
        seen["auth"] = request.headers.get("Authorization")
        assert json.loads(request.data)["model"] == "gpt-4o-mini"
        return _ok_response(SPEC_DICT)

    out = llm_assist.assist_fetch_spec_dict(
        "lake superior temps", api_key="sk-test", urlopen=fake_urlopen)
    assert out == SPEC_DICT
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["timeout"] == 60


def test_custom_base_url():
    def fake_urlopen(request, timeout=None):
        assert request.full_url == "https://llm.example.com/v1/chat/completions"
        return _ok_response(SPEC_DICT)

    out = llm_assist.assist_fetch_spec_dict(
        "x", api_key="k", base_url="https://llm.example.com/v1",
        urlopen=fake_urlopen)
    assert out["region_key"] == "lake-superior"


def test_http_error_wrapped():
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(
            request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"bad key"))

    with pytest.raises(llm_assist.LLMAssistError, match="HTTP 401"):
        llm_assist.assist_fetch_spec_dict("x", api_key="bad", urlopen=fake_urlopen)


def test_url_error_wrapped():
    def fake_urlopen(request, timeout=None):
        raise urllib.error.URLError("connection refused")

    with pytest.raises(llm_assist.LLMAssistError, match="connection refused"):
        llm_assist.assist_fetch_spec_dict("x", api_key="k", urlopen=fake_urlopen)


def test_non_json_body_wrapped():
    class Resp:
        def read(self):
            return b"not json at all"

    with pytest.raises(llm_assist.LLMAssistError, match="non-JSON"):
        llm_assist.assist_fetch_spec_dict(
            "x", api_key="k", urlopen=lambda r, timeout=None: Resp())


def test_missing_choices_wrapped():
    class Resp:
        def read(self):
            return json.dumps({"nope": True}).encode()

    with pytest.raises(llm_assist.LLMAssistError, match="no choices"):
        llm_assist.assist_fetch_spec_dict(
            "x", api_key="k", urlopen=lambda r, timeout=None: Resp())


def test_non_object_json_wrapped():
    body = {"choices": [{"message": {"content": "[1, 2, 3]"}}]}

    class Resp:
        def read(self):
            return json.dumps(body).encode()

    with pytest.raises(llm_assist.LLMAssistError, match="not an object"):
        llm_assist.assist_fetch_spec_dict(
            "x", api_key="k", urlopen=lambda r, timeout=None: Resp())


def test_markdown_fences_tolerated():
    content = "```json\n" + json.dumps(SPEC_DICT) + "\n```"
    body = {"choices": [{"message": {"content": content}}]}

    class Resp:
        def read(self):
            return json.dumps(body).encode()

    out = llm_assist.assist_fetch_spec_dict(
        "x", api_key="k", urlopen=lambda r, timeout=None: Resp())
    assert out == SPEC_DICT


def test_assist_from_env_no_key_returns_none():
    assert llm_assist.assist_from_env("x", getenv=lambda k: None) is None


def test_assist_from_env_uses_env(monkeypatch):
    captured = {}

    def fake_assist(text, api_key, base_url=None, model=None, **kw):
        captured.update(text=text, api_key=api_key, base_url=base_url,
                        model=model)
        return {"ok": True}

    monkeypatch.setattr(llm_assist, "assist_fetch_spec_dict", fake_assist)
    env = {"LLM_API_KEY": "sk-env", "LLM_BASE_URL": "https://x/v1",
           "LLM_MODEL": "my-model"}
    out = llm_assist.assist_from_env("hello", getenv=env.get)
    assert out == {"ok": True}
    assert captured == {"text": "hello", "api_key": "sk-env",
                        "base_url": "https://x/v1", "model": "my-model"}


def test_assist_from_env_defaults(monkeypatch):
    captured = {}

    def fake_assist(text, api_key, base_url=None, model=None, **kw):
        captured.update(base_url=base_url, model=model)
        return {}

    monkeypatch.setattr(llm_assist, "assist_fetch_spec_dict", fake_assist)
    llm_assist.assist_from_env("x", getenv={"LLM_API_KEY": "k"}.get)
    assert captured["base_url"] == llm_assist.DEFAULT_BASE_URL
    assert captured["model"] == llm_assist.DEFAULT_MODEL


def test_spec_schema_summary_mentions_required_fields():
    for token in ("region_key", "bbox", "variable", "reel-vertical",
                  "lake-superior"):
        assert token in llm_assist.SPEC_SCHEMA_SUMMARY
