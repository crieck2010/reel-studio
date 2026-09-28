"""pytest configuration: make local peer checkouts importable.

In dev (and in the fresh-clone verification on this machine) the four
peer repos live as sibling checkouts under ~/workspace. Adding their
``src/`` layouts to sys.path lets the integration tests exercise the
REAL peer code offline. Tests that need a peer use pytest.skip when it
is unavailable, so the suite stays green with or without them.
"""

from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
WORKSPACE = os.path.dirname(REPO_ROOT)

# reel-studio's own studio package.
sys.path.insert(0, REPO_ROOT)

# Real peer checkouts (survey-viz, survey-currents, survey-animate).
for repo, subdir in (
    ("survey-viz", "src"),
    ("survey-currents", "src"),
    ("survey-animate", "src"),
):
    path = os.path.join(WORKSPACE, repo, subdir)
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)
