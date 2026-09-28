"""Content smoke test for the Windows one-click updater.

``Update reel-studio.bat`` cannot execute on Linux, so this test pins
the script's essential behavior by content: it must pull the repo,
upgrade all six peers (from a local checkout when present, from
GitHub otherwise), report installed versions, and wait before closing.
"""

from __future__ import annotations

import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAT = os.path.join(REPO_ROOT, "Update reel-studio.bat")
PEERS = ("survey-viz", "survey-currents", "survey-animate",
         "survey-layout", "survey-style", "survey-schedule")


def _text():
    with open(BAT, "r", encoding="utf-8-sig") as fh:
        return fh.read()


def test_updater_exists():
    assert os.path.isfile(BAT)


def test_updater_targets_repo_dir():
    assert 'cd /d "%~dp0"' in _text()


def test_updater_pulls_reel_studio():
    assert "git pull --ff-only" in _text()


def test_updater_handles_all_peers():
    text = _text()
    for peer in PEERS:
        assert peer in text, f"updater never mentions {peer}"
    # Peers live NEXT TO the reel-studio checkout (siblings under the CODE
    # folder), so every local path must be prefixed with "..\\" — never the
    # bare "%%P" form, which would wrongly probe inside reel-studio itself.
    assert 'if exist "..\\%%P\\.git"' in text
    assert 'if exist "%%P\\.git"' not in text
    assert 'git -C "..\\%%P" pull --ff-only' in text
    assert 'python -m pip install --upgrade "..\\%%P"' in text
    # ... and the GitHub fallback path when no sibling checkout exists.
    assert "git+https://github.com/crieck2010/%%P.git" in text


def test_updater_reports_versions():
    text = _text()
    for mod in ("viz", "currents", "animate", "layout", "style",
                "schedx"):
        assert f"import {mod}" in text
        assert "not installed" in text


def test_updater_guards_missing_git():
    assert "winget install Git.Git" in _text()


def test_updater_pauses_before_exit():
    lines = [ln.strip().lower() for ln in _text().splitlines()
             if ln.strip() and not ln.strip().startswith("REM")]
    assert lines[-1] == "pause"
