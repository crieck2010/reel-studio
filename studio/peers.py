"""Optional peer imports with graceful degradation.

The three peers are separate packages, installed from their own GitHub
repos. Nothing in reel-studio hard-imports them: :func:`load_peers`
tries each one and records what is missing, :func:`require_peer` raises
a :class:`MissingPeerError` whose message names the exact
``pip install git+https://...`` command that fixes it, and
:func:`wire_peers` builds the callables namespace that
:mod:`studio.pipeline` runs against.
"""

from __future__ import annotations

import importlib
import types
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

#: repo -> importable module + why reel-studio wants it.
PEER_SPECS: Dict[str, Dict[str, str]] = {
    "survey-viz": {
        "module": "viz",
        "pip": "pip install git+https://github.com/crieck2010/survey-viz.git",
        "needed_for": "description parsing (parse_description/VizSpec), frame rendering (render_viz), and source routing (viz.sources)",
    },
    "survey-currents": {
        "module": "currents",
        "pip": "pip install git+https://github.com/crieck2010/survey-currents.git",
        "needed_for": "SST fetching: Great-Lakes GLSEA (fetch_glsea_sst, fetch_glsea_lake_averages) plus global OISST/MUR (fetch_oisst, fetch_mur)",
    },
    "survey-animate": {
        "module": "animate",
        "pip": "pip install git+https://github.com/crieck2010/survey-animate.git",
        "needed_for": "MP4 encoding (render_video)",
    },
}


class MissingPeerError(ImportError):
    """A peer package is not installed. Carries the fix command."""

    def __init__(self, repo: str, pip_command: str, needed_for: str) -> None:
        self.repo = repo
        self.pip_command = pip_command
        super().__init__(
            f"reel-studio needs '{repo}' ({needed_for}), but it is not installed.\n"
            f"Install it with:\n\n    {pip_command}"
        )


@dataclass
class PeerStatus:
    """One peer's availability record from :func:`load_peers`."""

    repo: str
    module_name: str
    module: Optional[Any]      # the imported module, or None when missing
    pip_command: str
    needed_for: str

    @property
    def installed(self) -> bool:
        return self.module is not None


def load_peers() -> Dict[str, PeerStatus]:
    """Attempt to import every peer; never raises for a missing peer."""
    statuses: Dict[str, PeerStatus] = {}
    for repo, spec in PEER_SPECS.items():
        try:
            module = importlib.import_module(spec["module"])
        except ImportError:
            module = None
        statuses[repo] = PeerStatus(
            repo=repo,
            module_name=spec["module"],
            module=module,
            pip_command=spec["pip"],
            needed_for=spec["needed_for"],
        )
    return statuses


def require_peer(statuses: Dict[str, PeerStatus], repo: str) -> Any:
    """Return the peer module, or raise :class:`MissingPeerError`."""
    status = statuses[repo]
    if status.module is None:
        raise MissingPeerError(status.repo, status.pip_command, status.needed_for)
    return status.module


def wire_peers(statuses: Dict[str, PeerStatus]) -> types.SimpleNamespace:
    """Build the callables namespace :mod:`studio.pipeline` runs against.

    Raises :class:`MissingPeerError` naming the first missing peer, in
    pipeline order (parse -> fetch -> render -> encode).
    """
    viz = require_peer(statuses, "survey-viz")
    currents = require_peer(statuses, "survey-currents")
    animate = require_peer(statuses, "survey-animate")

    glsea = importlib.import_module("currents.glsea")

    # Source routing (survey-viz >= 0.2.0) and the global-SST adapters
    # (survey-currents >= 0.3.0) are optional: older peers simply lack
    # them, and the pipeline falls back to the legacy GLSEA-only path.
    try:
        viz_sources = importlib.import_module("viz.sources")
    except ImportError:
        viz_sources = None
    try:
        sst_global = importlib.import_module("currents.sst_global")
    except ImportError:
        sst_global = None

    return types.SimpleNamespace(
        parse_description=viz.parse_description,
        parse_error=viz.UnparseableDescription,
        VizSpec=viz.VizSpec,
        is_fetchable=viz.is_fetchable,
        get_region=viz.get_region,
        resolve_source=(viz_sources.resolve_source if viz_sources else None),
        fetch_sst=glsea.fetch_glsea_sst,
        fetch_averages=glsea.fetch_glsea_lake_averages,
        fetch_oisst=(sst_global.fetch_oisst if sst_global else None),
        fetch_mur=(sst_global.fetch_mur if sst_global else None),
        # GLSEA grid bounds (lon_min, lat_min, lon_max, lat_max); the pipeline
        # clamps spec bboxes into this window before fetching, because the
        # lake-superior gazetteer bbox starts slightly west of the grid floor.
        glsea_bounds=(glsea.GLSEA_LON_MIN, glsea.GLSEA_LAT_MIN,
                      glsea.GLSEA_LON_MAX, glsea.GLSEA_LAT_MAX),
        render_viz=viz.render_viz,
        render_video=animate.render_video,
    )
