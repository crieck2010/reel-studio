"""Optional peer imports with graceful degradation.

The nine peers are separate packages, installed from their own GitHub
repos. Nothing in reel-studio hard-imports them: :func:`load_peers`
tries each one and records what is missing, :func:`require_peer` raises
a :class:`MissingPeerError` whose message names the exact
``pip install git+https://...`` command that fixes it, and
:func:`wire_peers` builds the callables namespace that
:mod:`studio.pipeline` runs against. survey-layout is the optional
fourth peer, survey-style the optional fifth, survey-schedule the
optional sixth, survey-cache the optional seventh, survey-derive
the optional eighth, and survey-publish the optional ninth: none is ever
*required* (the legacy 1080×1920 layout always works, the manual
aesthetics controls always work, one-off plus batch generation always
work, rendering without a cache always works, raw-variable reels
always work, and reels simply stay unpublished), and the UI offers
their features only when they are installed.
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
        "needed_for": "fetching: Great-Lakes GLSEA (fetch_glsea_sst, fetch_glsea_lake_averages), global OISST/MUR (fetch_oisst, fetch_mur), ERA5 atmosphere (fetch_era5), global currents (fetch_oscar, fetch_cmems_currents), NASA FIRMS active fires (fetch_firms), NSIDC sea-ice concentration (fetch_nsidc), GPM IMERG precipitation (fetch_imerg), NASA Black Marble night lights (fetch_blackmarble), NOAA IBTrACS storm tracks (fetch_ibtracs), CSR GRACE/GRACE-FO water storage (fetch_grace), USGS streamgages (fetch_usgs), NOAA CoastWatch ocean color (fetch_oceancolor), USGS earthquake catalog (fetch_earthquakes)",
    },
    "survey-animate": {
        "module": "animate",
        "pip": "pip install git+https://github.com/crieck2010/survey-animate.git",
        "needed_for": "MP4 encoding (render_video)",
    },
    "survey-layout": {
        "module": "layout",
        "pip": "pip install git+https://github.com/crieck2010/survey-layout.git",
        "needed_for": "platform aspect ratios + safe-zone canvases for frame "
                      "rendering (layout.to_viz_canvas, consumed by "
                      "survey-viz >= 0.18.0's render_viz canvas=)",
    },
    "survey-style": {
        "module": "style",
        "pip": "pip install git+https://github.com/crieck2010/survey-style.git",
        "needed_for": "reusable style presets for the Aesthetics step "
                      "(style.apply_style: base style + per-variable "
                      "colormaps + title/footer templates + underlay)",
    },
    "survey-schedule": {
        "module": "schedx",
        "pip": "pip install git+https://github.com/crieck2010/survey-schedule.git",
        "needed_for": "scheduled generation for the Schedule step "
                      "(schedx.Job / JobStore / Runner: cron, interval "
                      "and once schedules with a run ledger)",
    },
    "survey-cache": {
        "module": "cachex",
        "pip": "pip install git+https://github.com/crieck2010/survey-cache.git",
        "needed_for": "smarter render caching (cachex.Cache: "
                      "content-addressed frame-batch and MP4 reuse, "
                      "keyed by fingerprints of the render inputs)",
    },
    "survey-derive": {
        "module": "derive",
        "pip": "pip install git+https://github.com/crieck2010/survey-derive.git",
        "needed_for": "derived products (derive.climatology / anomaly / "
                      "standardized_anomaly / percent_of_normal: "
                      "climatological anomaly maps vs a day-of-year "
                      "baseline, computed deterministically with NumPy)",
    },
    "survey-publish": {
        "module": "publish",
        "pip": "pip install git+https://github.com/crieck2010/survey-publish.git",
        "needed_for": "social-media publishing for the Publish step "
                      "(publish.registry: YouTube / Instagram / Facebook / "
                      "TikTok adapters with terminal-based OAuth connect, "
                      "publish.models.PublishRequest / PublishResult), plus "
                      "the survey-publish >= 0.2.0 queue engine for "
                      "scheduled publishing (publish.QueuedItem / "
                      "QueueStore / parse_schedule_time / DEFAULT_SLOTS, "
                      "fired by `survey-publish tick` on a 15-minute "
                      "schedule)",
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

    # survey-layout is the optional fourth peer: platform canvases only
    # exist when it is installed, so it is deliberately NOT required —
    # the pipeline raises PeerTooOldError with the install command only
    # when a non-legacy platform is actually requested.
    try:
        layout = importlib.import_module("layout")
    except ImportError:
        layout = None

    glsea = importlib.import_module("currents.glsea")

    # Source routing (survey-viz >= 0.2.0), the global-SST adapters
    # (survey-currents >= 0.3.0), the ERA5 atmosphere adapter
    # (survey-currents >= 0.4.0), the global-currents adapters
    # (survey-currents >= 0.5.0), the FIRMS active-fire adapter
    # (survey-currents >= 0.6.0), the NSIDC sea-ice adapter
    # (survey-currents >= 0.7.0), the GPM IMERG precipitation adapter
    # (survey-currents >= 0.8.0), the Black Marble night-lights adapter
    # (survey-currents >= 0.9.0), the GEBCO basemap adapter
    # (survey-currents >= 0.10.0), and the IBTrACS storm-track adapter
    # (survey-currents >= 0.11.0), and the GRACE water-storage adapter
    # (survey-currents >= 0.12.0) are optional: older peers simply lack
    # them, and the pipeline falls back to the legacy paths.
    try:
        viz_sources = importlib.import_module("viz.sources")
    except ImportError:
        viz_sources = None
    try:
        sst_global = importlib.import_module("currents.sst_global")
    except ImportError:
        sst_global = None
    try:
        era5 = importlib.import_module("currents.era5")
    except ImportError:
        era5 = None
    try:
        currents_global = importlib.import_module("currents.currents_global")
    except ImportError:
        currents_global = None
    try:
        fires = importlib.import_module("currents.fires")
    except ImportError:
        fires = None
    try:
        sea_ice = importlib.import_module("currents.sea_ice")
    except ImportError:
        sea_ice = None
    try:
        imerg = importlib.import_module("currents.imerg")
    except ImportError:
        imerg = None
    try:
        blackmarble = importlib.import_module("currents.blackmarble")
    except ImportError:
        blackmarble = None
    try:
        basemaps = importlib.import_module("currents.basemaps")
    except ImportError:
        basemaps = None
    try:
        storms = importlib.import_module("currents.storms")
    except ImportError:
        storms = None
    try:
        grace = importlib.import_module("currents.grace")
    except ImportError:
        grace = None
    try:
        streamgages = importlib.import_module("currents.streamgages")
    except ImportError:
        streamgages = None
    try:
        oceancolor = importlib.import_module("currents.oceancolor")
    except ImportError:
        oceancolor = None
    try:
        earthquakes = importlib.import_module("currents.earthquakes")
    except ImportError:
        earthquakes = None
    # survey-derive is the optional eighth peer (climatological anomaly
    # products): None when it is missing, and the pipeline raises
    # PeerTooOldError with the install command only when a derived
    # product is actually requested.
    try:
        derive = importlib.import_module("derive")
    except ImportError:
        derive = None

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
        fetch_era5=(era5.fetch_era5 if era5 else None),
        fetch_oscar=(currents_global.fetch_oscar if currents_global else None),
        fetch_cmems_currents=(currents_global.fetch_cmems_currents
                              if currents_global else None),
        fetch_firms=(fires.fetch_firms if fires else None),
        fetch_nsidc=(sea_ice.fetch_nsidc_sic if sea_ice else None),
        fetch_imerg=(imerg.fetch_imerg if imerg else None),
        fetch_blackmarble=(blackmarble.fetch_blackmarble
                           if blackmarble else None),
        fetch_gebco=(basemaps.fetch_gebco if basemaps else None),
        fetch_ibtracs=(storms.fetch_ibtracs if storms else None),
        fetch_grace=(grace.fetch_grace if grace else None),
        fetch_usgs=(streamgages.fetch_usgs if streamgages else None),
        fetch_oceancolor=(oceancolor.fetch_oceancolor
                          if oceancolor else None),
        fetch_earthquakes=(earthquakes.fetch_earthquakes
                           if earthquakes else None),
        # GLSEA grid bounds (lon_min, lat_min, lon_max, lat_max); the pipeline
        # clamps spec bboxes into this window before fetching, because the
        # lake-superior gazetteer bbox starts slightly west of the grid floor.
        glsea_bounds=(glsea.GLSEA_LON_MIN, glsea.GLSEA_LAT_MIN,
                      glsea.GLSEA_LON_MAX, glsea.GLSEA_LAT_MAX),
        render_viz=viz.render_viz,
        render_video=animate.render_video,
        # survey-animate >= 0.2.0 exposes MotionSpec (zoom/pan/smooth);
        # None on older peers, which the pipeline turns into a
        # PeerTooOldError only when motion is actually requested.
        MotionSpec=getattr(animate, "MotionSpec", None),
        # survey-layout is optional (see above): the callables are None
        # when it is missing, and the pipeline raises PeerTooOldError
        # with the install command only on actual use.
        layout=layout,
        to_viz_canvas=(layout.to_viz_canvas if layout else None),
        get_platform=(layout.get_platform if layout else None),
        list_platforms=(layout.list_platforms if layout else None),
        layout_pip=PEER_SPECS["survey-layout"]["pip"],
        # survey-derive is optional (see above): the module is None
        # when it is missing, and the pipeline raises PeerTooOldError
        # with the install command only on actual use.
        derive=derive,
        derive_pip=PEER_SPECS["survey-derive"]["pip"],
    )
