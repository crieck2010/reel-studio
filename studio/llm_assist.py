"""Optional LLM assist for descriptions the deterministic parser rejects.

Fully optional: the app works 100% without ``LLM_API_KEY``. When the key
is set AND ``parse_description`` raises ``UnparseableDescription``, ONE
assist attempt is made: the description plus a VizSpec schema summary is
POSTed to an OpenAI-compatible chat-completions endpoint using stdlib
``urllib`` only (no extra dependencies). The returned JSON is handed back
as a plain dict — the caller validates it via ``VizSpec.from_dict`` and,
on failure, surfaces the ORIGINAL parse error.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_MODEL = "gpt-4o-mini"
TIMEOUT_SECONDS = 60

#: Compact summary of the VizSpec contract, sent to the LLM so it emits a
#: dict that ``VizSpec.from_dict`` will accept.
SPEC_SCHEMA_SUMMARY = """\
You must return ONLY a JSON object with these fields:
- title: string, human-readable, e.g. "Lake Superior — Surface Water Temperature, 2016–2026"
- region_key: string, one of "lake-superior", "lake-michigan", "lake-huron", "lake-erie", "lake-ontario"
  (today only these 5 Great Lakes are supported; map nearby phrasings to the closest lake,
  e.g. "upper peninsula lakes" -> "lake-superior")
- bbox: [lon_min, lat_min, lon_max, lat_max] in decimal degrees, one of:
  lake-superior [-92.5, 46.0, -84.5, 48.8], lake-michigan [-88.2, 41.5, -85.8, 46.2],
  lake-huron [-84.8, 43.5, -79.5, 46.5], lake-erie [-83.5, 41.2, -78.8, 43.0],
  lake-ontario [-80.2, 43.0, -76.0, 44.2]
- variable: string, exactly "sst"
- start: string "YYYY-MM-DD", the inclusive start date
- end: string "YYYY-MM-DD", the inclusive end date
- cadence: string, one of "daily", "monthly", "yearly" (prefer "monthly")
- layout: string, exactly "reel-vertical"
- style: string, exactly "reel-dark"
- vmin: null, vmax: null
No commentary, no markdown fences, JSON only.\
"""


class LLMAssistError(RuntimeError):
    """The single LLM assist attempt failed (network, API, or bad payload)."""


def _endpoint(base_url: str) -> str:
    return base_url.rstrip("/") + "/chat/completions"


def build_request(text: str, api_key: str,
                  base_url: str = DEFAULT_BASE_URL,
                  model: str = DEFAULT_MODEL) -> urllib.request.Request:
    """Build the chat-completions POST for one assist attempt."""
    payload = {
        "model": model,
        "temperature": 0,
        "messages": [
            {"role": "system",
             "content": "You convert a plain-English data-visualization request "
                        "into a VizSpec JSON object. " + SPEC_SCHEMA_SUMMARY},
            {"role": "user", "content": text},
        ],
    }
    data = json.dumps(payload).encode("utf-8")
    return urllib.request.Request(
        _endpoint(base_url),
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )


def _extract_json_dict(raw: bytes) -> Dict[str, Any]:
    """Pull the assistant's message content and parse it as a JSON object."""
    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LLMAssistError(f"LLM returned non-JSON output: {exc}") from exc
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMAssistError(
            f"LLM response had no choices[0].message.content: "
            f"{str(body)[:200]}"
        ) from exc
    if not isinstance(content, str):
        raise LLMAssistError("LLM message content was not a string")
    text = content.strip()
    # Tolerate markdown fences the model may add despite instructions.
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[: -3]
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMAssistError(
            f"LLM output was not valid JSON: {exc}; "
            f"first 200 chars: {text[:200]!r}"
        ) from exc
    if not isinstance(parsed, dict):
        raise LLMAssistError(
            f"LLM output was JSON {type(parsed).__name__}, not an object")
    return parsed


def assist_fetch_spec_dict(
    text: str,
    api_key: str,
    base_url: str = DEFAULT_BASE_URL,
    model: str = DEFAULT_MODEL,
    timeout: int = TIMEOUT_SECONDS,
    urlopen: Optional[Callable[..., Any]] = None,
) -> Dict[str, Any]:
    """Make the ONE assist attempt; return the raw spec dict.

    ``urlopen`` is injectable for tests (defaults to
    ``urllib.request.urlopen``). Raises :class:`LLMAssistError` on any
    failure. The caller validates the dict via ``VizSpec.from_dict``.
    """
    opener = urlopen or urllib.request.urlopen
    request = build_request(text, api_key, base_url=base_url, model=model)
    try:
        response = opener(request, timeout=timeout)
        raw = response.read()
    except urllib.error.HTTPError as exc:
        try:
            detail = exc.read().decode("utf-8", "replace")[:300]
        except Exception:
            detail = ""
        raise LLMAssistError(
            f"LLM endpoint returned HTTP {exc.code}: {detail}"
        ) from exc
    except urllib.error.URLError as exc:
        raise LLMAssistError(f"LLM request failed: {exc.reason}") from exc
    except (TimeoutError, OSError) as exc:
        raise LLMAssistError(f"LLM request failed: {exc}") from exc
    return _extract_json_dict(raw)


def assist_from_env(
    text: str,
    getenv: Callable[[str], Optional[str]] = os.environ.get,
    **kwargs: Any,
) -> Optional[Dict[str, Any]]:
    """One assist attempt using ``LLM_API_KEY`` / ``LLM_BASE_URL`` env vars.

    Returns None when ``LLM_API_KEY`` is not set (the app then shows the
    original parse error). Extra ``kwargs`` are forwarded to
    :func:`assist_fetch_spec_dict`.
    """
    api_key = getenv("LLM_API_KEY")
    if not api_key:
        return None
    base_url = getenv("LLM_BASE_URL") or DEFAULT_BASE_URL
    model = getenv("LLM_MODEL") or DEFAULT_MODEL
    return assist_fetch_spec_dict(text, api_key, base_url=base_url,
                                  model=model, **kwargs)
