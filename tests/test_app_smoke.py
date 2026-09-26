"""app.py headless import smoke test.

Must pass WITH and WITHOUT streamlit installed: when streamlit is
missing, a stub module is injected into sys.modules; either way,
``import app`` must succeed and must NOT execute the UI (no Streamlit
runtime is running headless).
"""

from __future__ import annotations

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
        stub = types.ModuleType("streamlit")
        sys.modules["streamlit"] = stub
    # Fresh import each time the fixture is set up.
    sys.modules.pop("app", None)
    import app as app_mod
    return app_mod


def test_app_imports_headless(app_module):
    assert app_module.APP_TITLE == "reel-studio"


def test_main_not_called_on_import(app_module, monkeypatch):
    called = []
    monkeypatch.setattr(app_module, "main",
                        lambda: called.append(True))
    import importlib
    importlib.reload(app_module)
    assert called == []


def test_streamlit_is_running_false_headless(app_module):
    assert app_module._streamlit_is_running() is False


def test_import_does_not_change_cwd(app_module):
    cwd_before = os.getcwd()
    import importlib
    importlib.reload(app_module)
    assert os.getcwd() == cwd_before


def test_main_exists_and_is_callable(app_module):
    assert callable(app_module.main)
