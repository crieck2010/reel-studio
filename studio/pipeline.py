"""UI-free orchestration: description -> fetch -> frames -> MP4.

Everything here works with injected callables, so the pipeline is fully
testable with fake peers and never hard-imports survey-viz,
survey-currents, or survey-animate. The Streamlit app (``app.py``) builds
the real callables with :func:`studio.peers.wire_peers`.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os
import types
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

#: survey-viz region_key -> survey-currents lake name for
#: ``fetch_glsea_lake_averages``. Lake-average series only exist for the
#: 5 Great Lakes; global SST (OISST/MUR) renders with series=None.
FETCHABLE_REGION_TO_LAKE = {
    "lake-superior": "superior",
    "lake-michigan": "michigan",
    "lake-huron": "huron",
    "lake-erie": "erie",
    "lake-ontario": "ontario",
}

#: The only SST variable (via GLSEA/OISST/MUR).
SST_VARIABLE = "sst"

#: The ERA5 atmosphere variables (Copernicus ERA5 reanalysis via
#: ``currents.era5.fetch_era5``, survey-currents >= 0.4.0). Fetchable in
#: any region — the 0.25° grid is global.
ERA5_VARIABLES = ("wind", "msl", "t2m", "tp")

#: The currents variable (OSCAR v2.0 via ``currents.currents_global``,
#: survey-currents >= 0.5.0). Fetchable in any region EXCEPT the
#: 5 Great Lakes (no lake-scale current adapter exists — that stays an
#: honest refusal).
CURRENTS_VARIABLES = ("currents",)

#: The active-fire variable (NASA FIRMS via ``currents.fires``,
#: survey-currents >= 0.6.0). Fetchable in any region — the FIRMS area
#: API is global. ``burn-scar`` is deliberately NOT in this list: it
#: parses (survey-viz >= 0.5.0) but has no fetch adapter, because FIRMS
#: is active-fire detections only and burn-scar mapping is
#: survey-burn's future imagery domain — plan_fetch refuses it
#: honestly, naming survey-burn.
FIRE_VARIABLES = ("fire",)

#: The sea-ice variable (NSIDC G02135 via ``currents.sea_ice``,
#: survey-currents >= 0.7.0, survey-viz >= 0.6.0). Fetchable only in the
#: polar regions — the G02135 grids cover north of 30.98°N / south of
#: 39.23°S. ``land-ice`` is deliberately NOT in this list: it parses
#: (survey-viz >= 0.6.0) but has no fetch adapter, because glaciers /
#: ice sheets / icebergs are a different physical product from sea-ice
#: concentration — plan_fetch refuses it honestly, never routing it to
#: NSIDC.
ICE_VARIABLES = ("sea-ice",)

#: The night-lights variable (NASA Black Marble VNP46A2 via
#: ``currents.blackmarble``, survey-currents >= 0.9.0, survey-viz >=
#: 0.8.0). Fetchable in any region — the VNP46A2 tiles are global.
#: ``power-outage`` is deliberately NOT in this list: it parses
#: (survey-viz >= 0.8.0) but has no fetch adapter, because outage
#: mapping is temporal change detection across two or more epochs and
#: a single daily Black Marble map cannot show it — plan_fetch refuses
#: it honestly, never misrendering a blackout as a lights map.
NIGHT_VARIABLES = ("night-lights",)

#: The GEBCO variables (GEBCO 2024 global topography/bathymetry via
#: ``currents.basemaps``, survey-currents >= 0.10.0, survey-viz >=
#: 0.9.0). Fetchable in any region — the 15 arc-second grid is
#: global. ``country-borders`` is deliberately NOT in this list: it
#: parses (survey-viz >= 0.9.0) but has no fetch adapter, because
#: Natural Earth country vectors are cartographic context, not a data
#: variable — plan_fetch refuses it honestly, never rendering an
#: empty map.
GEBCO_VARIABLES = ("bathymetry", "elevation")

#: The storm-track variable (NOAA IBTrACS v04r01 tropical-cyclone best
#: tracks via ``currents.storms``, survey-currents >= 0.11.0,
#: survey-viz >= 0.10.0). Fetchable in any region — the v04r01 archive
#: is global (1980–present by default).
STORM_VARIABLES = ("storm-tracks",)

#: The water-storage variable (CSR GRACE/GRACE-FO RL06.3 terrestrial
#: water storage anomalies via ``currents.grace``, survey-currents >=
#: 0.12.0, survey-viz >= 0.11.0). Fetchable in any region — the CSR
#: mascon archive is global and land-only (oceans masked).
#: ``sea-level`` is deliberately NOT in this list: it parses
#: (survey-viz >= 0.11.0) but has no fetch adapter, because sea level
#: is satellite altimetry, a different observable from GRACE
#: terrestrial water storage — plan_fetch refuses it honestly, never
#: answering with a GRACE map. ``streamflow`` lives in
#: ``USGS_VARIABLES`` below (survey-viz >= 0.12.0, survey-currents >=
#: 0.13.0).
GRACE_VARIABLES = ("water-storage",)

#: The streamflow variable (USGS Water Services NWIS streamgage daily
#: values via ``currents.streamgages``, survey-currents >= 0.13.0,
#: survey-viz >= 0.12.0). Routable from any region — NWIS coverage is
#: US-only, so a bbox outside USGS coverage returns an honest empty
#: field (the renderer draws an explicit no-gages message, never
#: fabricated data).
USGS_VARIABLES = ("streamflow",)

#: Variables with a fetch adapter: SST + the ERA5 atmosphere set +
#: currents + active fires + sea ice + night lights + GEBCO topography
#: + IBTrACS storm tracks + GRACE water storage + USGS streamflow.
#: ``sea-level`` parses but has no fetch adapter (honest refusal).
SUPPORTED_VARIABLES = (SST_VARIABLE,) + ERA5_VARIABLES + CURRENTS_VARIABLES + FIRE_VARIABLES + ICE_VARIABLES + NIGHT_VARIABLES + GEBCO_VARIABLES + STORM_VARIABLES + GRACE_VARIABLES + USGS_VARIABLES + ("sea-level",)

#: Region keys of the 5 Great Lakes (currents have no adapter there).
GREAT_LAKES_KEYS = frozenset(FETCHABLE_REGION_TO_LAKE)

#: Source names the pipeline knows how to fetch (see viz.sources).
SOURCE_LABELS = {
    "glsea": "NOAA GLSEA",
    "oisst": "NOAA OISST v2.1",
    "mur": "NASA JPL MUR v4.1",
    "era5": "Copernicus ERA5 (CDS)",
    "oscar": "NASA PODAAC OSCAR v2.0",
    "cmems-currents": "CMEMS Global Ocean Physics (daily)",
    "firms": "NASA FIRMS",
    "nsidc": "NSIDC Sea Ice Index (G02135 v4.0)",
    "imerg": "NASA GPM IMERG V07",
    "blackmarble": "NASA Black Marble VNP46A2",
    "gebco": "GEBCO 2024",
    "ibtracs": "NOAA IBTrACS v04r01",
    "grace": "CSR GRACE/GRACE-FO RL06.3",
    "usgs": "USGS Water Services (NWIS)",
    "oceancolor": "NOAA CoastWatch Ocean Color",
    "comcat": "USGS Earthquake Catalog (ComCat)",
}

#: GLSEA daily SST, sampled this often for the reel. Monthly-ish cadence
#: keeps multi-year windows to a manageable frame count.
DEFAULT_STRIDE_DAYS = 30

#: ERA5 hourly reanalysis, sampled this often for the reel. Daily 12:00 UTC
#: keeps multi-month windows to a manageable frame count (the viz spec's
#: own cadence then buckets timesteps into frames).
DEFAULT_STRIDE_HOURS = 24


class UnfetchableRegionError(ValueError):
    """The spec's region has no fetch adapter yet (honest, not a crash)."""


class UnsupportedVariableError(ValueError):
    """The spec's variable has no fetch adapter yet."""


@dataclass
class FetchPlan:
    """What :func:`plan_fetch` decided, and why."""

    fetchable: bool
    reason: str
    lake: Optional[str] = None       # survey-currents lake name, when fetchable
    region_key: str = ""
    variable: str = ""
    #: "ok" | "no_adapter" (region not fetchable) | "bad_variable"
    kind: str = "ok"
    #: which adapter the fetch uses:
    #: "glsea" | "oisst" | "mur" | "era5" | "oscar" | "cmems-currents" |
    #: "firms" | "nsidc" | "imerg" | "blackmarble" ("ok" plans only)
    source: str = ""


def region_to_lake(region_key: str) -> Optional[str]:
    """Map a survey-viz region key to a survey-currents lake name."""
    return FETCHABLE_REGION_TO_LAKE.get(region_key)


def plan_fetch(spec: Any, is_fetchable: Callable[[str], bool],
             resolve_source: Optional[Callable[[Any], str]] = None) -> FetchPlan:
    """Decide whether ``spec`` can be fetched, without touching the network.

    ``spec`` is any object with ``region_key`` and ``variable`` attributes
    (a real ``VizSpec`` or a test double); ``is_fetchable`` is the
    ``viz.gazetteer.is_fetchable`` callable (or a test double);
    ``resolve_source`` is the optional ``viz.sources.resolve_source``
    callable (absent on survey-viz < 0.2.0, in which case the legacy
    GLSEA-only path applies).

    Returns a :class:`FetchPlan`. Callers turn ``fetchable=False`` into a
    clear user-facing message instead of a crash.
    """
    region_key = str(getattr(spec, "region_key", ""))
    variable = str(getattr(spec, "variable", ""))

    # Source-aware routing (survey-viz >= 0.2.0): an explicitly pinned
    # source, or the regional default (Great-Lakes SST -> glsea, other
    # SST -> oisst, wind/msl/t2m/tp -> era5, non-Great-Lakes currents ->
    # oscar), decides the adapter -- not the region key alone.
    source = ""
    if resolve_source is not None:
        try:
            source = str(resolve_source(spec) or "").strip().lower()
        except ValueError:
            source = ""
    if not source:
        source = str(getattr(spec, "source", "") or "").strip().lower()
    if source in ("oisst", "mur"):
        # OISST/MUR only serve SST: an explicit pin cannot redirect a
        # currents/ERA5 request onto the SST adapters.
        if variable != SST_VARIABLE:
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Variable '{variable}' has no fetch adapter for source "
                    f"'{source}' -- '{source}' serves sea-surface temperature "
                    "only. The description parsed fine; pick a matching "
                    "source (see docs/INTEROP.md)."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        label = SOURCE_LABELS[source]
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via {label} "
                f"(variable '{variable}', source '{source}')."
            ),
            region_key=region_key,
            variable=variable,
            source=source,
        )

    # ERA5 atmosphere (survey-viz >= 0.3.0): fetchable in ANY region — the
    # reanalysis grid is global, so no region-key check applies.
    if source == "era5":
        if variable not in ERA5_VARIABLES:
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'era5' only serves the atmosphere variables "
                    f"{list(ERA5_VARIABLES)}; got variable '{variable}'. "
                    "(SST goes through glsea/oisst/mur.)"
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['era5']} (variable '{variable}', "
                "source 'era5')."
            ),
            region_key=region_key,
            variable=variable,
            source="era5",
        )

    # Global currents (survey-viz >= 0.4.0): fetchable in ANY region
    # except the 5 Great Lakes — no lake-scale current adapter exists,
    # so Great Lakes currents stay an honest refusal.
    if source in ("oscar", "cmems-currents"):
        if variable != "currents":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source '{source}' only serves the 'currents' variable; "
                    f"got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        if region_key in GREAT_LAKES_KEYS:
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Region '{region_key}' has no current adapter yet -- "
                    "OSCAR v2.0 and CMEMS global physics do not resolve the "
                    "Great Lakes. The description parsed fine; other regions "
                    "need no new adapter (see docs/INTEROP.md)."
                ),
                region_key=region_key,
                variable=variable,
                kind="no_adapter",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS[source]} (variable '{variable}', "
                f"source '{source}')."
            ),
            region_key=region_key,
            variable=variable,
            source=source,
        )

    # IBTrACS storm tracks (survey-viz >= 0.10.0, survey-currents >=
    # 0.11.0): fetchable in ANY region — the v04r01 best-track archive
    # is global (1980–present by default), so no region-key check
    # applies.
    if source == "ibtracs":
        if variable != "storm-tracks":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'ibtracs' only serves the 'storm-tracks' "
                    f"variable; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['ibtracs']} (variable '{variable}', "
                "source 'ibtracs')."
            ),
            region_key=region_key,
            variable=variable,
            source="ibtracs",
        )

    # GRACE terrestrial water storage (survey-viz >= 0.11.0,
    # survey-currents >= 0.12.0): fetchable in ANY region — the CSR
    # RL06.3 mascon archive is global and land-only, so no region-key
    # check applies.
    if source == "grace":
        if variable != "water-storage":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'grace' only serves the 'water-storage' "
                    f"variable; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['grace']} (variable '{variable}', "
                "source 'grace')."
            ),
            region_key=region_key,
            variable=variable,
            source="grace",
        )
    # USGS streamgage daily values (survey-viz >= 0.12.0,
    # survey-currents >= 0.13.0): routable from ANY region — NWIS
    # coverage is US-only, so a bbox outside USGS coverage returns an
    # honest empty field (rendered as an explicit no-gages message,
    # never fabricated data), not a fetch failure.
    if source == "usgs":
        if variable != "streamflow":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'usgs' only serves the 'streamflow' "
                    f"variable; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is routable via "
                f"{SOURCE_LABELS['usgs']} (variable '{variable}', "
                "source 'usgs')."
            ),
            region_key=region_key,
            variable=variable,
            source="usgs",
        )
    # Ocean color (survey-viz >= 0.13.0, survey-currents >= 0.14.0):
    # routable from ANY region — the NOAA CoastWatch L3 grids are
    # global, so no region-key check applies. Cloud-covered frames
    # are honest gaps (never interpolated), which the planner notes
    # because a reel in a persistently cloudy window can be mostly
    # NO OBSERVATION panels.
    if source == "oceancolor":
        if variable != "ocean-color":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'oceancolor' only serves the 'ocean-color' "
                    f"variable; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is routable via "
                f"{SOURCE_LABELS['oceancolor']} (variable '{variable}', "
                "source 'oceancolor'). Chlorophyll-a is keyless via "
                "NOAA CoastWatch ERDDAP; cloud-covered frames render as "
                "NO OBSERVATION panels, never interpolated."
            ),
            region_key=region_key,
            variable=variable,
            source="oceancolor",
        )
    # Earthquakes (survey-viz >= 0.14.0, survey-currents >= 0.15.0):
    # routable from ANY region — ComCat is a global keyless catalog.
    # Honesty contract: the catalog is OBSERVED events, never a
    # forecast or hazard model; empty windows are legitimate quiet
    # periods. The renderer draws cumulative daily frames with
    # magnitude-scaled, depth-colored markers over a GEBCO underlay.
    if source == "comcat":
        if variable != "earthquakes":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'comcat' only serves the 'earthquakes' "
                    f"variable; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is routable via "
                f"{SOURCE_LABELS['comcat']} (variable '{variable}', "
                "source 'comcat'). ComCat is a keyless catalog of "
                "observed seismic events — not a forecast or hazard "
                "model; magnitude completeness varies by region and "
                "time."
            ),
            region_key=region_key,
            variable=variable,
            source="comcat",
        )
    # Sea level (survey-viz >= 0.11.0) is refused honestly here: no
    # adapter exists, so it is never answered with a GRACE map.
    if variable == "sea-level":
        return FetchPlan(
            fetchable=False,
            reason=(
                "Sea level ('sea-level') has no fetch adapter: it is "
                "satellite altimetry, a different observable from GRACE "
                "terrestrial water storage, so it is never answered with "
                "a GRACE map. The description parsed fine; asking for "
                "'sea level pressure' routes to ERA5 instead."
            ),
            region_key=region_key,
            variable=variable,
            kind="no_adapter",
        )
    # Sea ice / land ice (survey-viz >= 0.6.0, survey-currents >= 0.7.0).
    # ``land-ice`` is refused honestly here: glaciers, ice sheets, and
    # icebergs are a different physical product from sea-ice
    # concentration, and routing them to NSIDC would be a lie — no
    # adapter exists yet. ``sea-ice`` that resolved to no source (a
    # non-polar region) is refused too: the G02135 grids cover north of
    # 30.98°N / south of 39.23°S, so fetching it would return an
    # all-NaN field.
    if variable == "land-ice":
        return FetchPlan(
            fetchable=False,
            reason=(
                "Glaciers, ice sheets, and icebergs ('land-ice') have no "
                "fetch adapter yet: they are a different physical product "
                "from sea-ice concentration, so they are never routed to "
                "NSIDC. The description parsed fine; asking for 'sea ice' "
                "in a polar region (Arctic Ocean, Southern Ocean) routes "
                "to the NSIDC Sea Ice Index instead."
            ),
            region_key=region_key,
            variable=variable,
            kind="no_adapter",
        )

    # Power outages / blackouts (survey-viz >= 0.8.0). ``power-outage``
    # is refused honestly here: outage mapping is temporal change
    # detection across two or more epochs, and a single daily Black
    # Marble map cannot show it — so it is never routed to the
    # ``blackmarble`` adapter (viz.sources.default_source refuses it
    # too). This check runs before the region fall-through so the
    # refusal names the real reason instead of the legacy-path message.
    if variable == "power-outage":
        return FetchPlan(
            fetchable=False,
            reason=(
                "Power-outage / blackout mapping ('power-outage') has no "
                "fetch adapter yet: it is temporal change detection "
                "across two or more epochs, and a single daily Black "
                "Marble night-lights map cannot show it. The description "
                "parsed fine; asking for 'night lights', 'city lights', "
                "or 'electrification' instead routes to NASA Black "
                "Marble VNP46A2."
            ),
            region_key=region_key,
            variable=variable,
            kind="no_adapter",
        )
    # Country borders (survey-viz >= 0.9.0). ``country-borders`` is
    # refused honestly here: Natural Earth country vectors are
    # cartographic context (drawn as the coastline underlay on every
    # map), not a data variable — rendering them alone would be an
    # empty map, so the request is refused instead. This check runs
    # before the region fall-through so the refusal names the real
    # reason instead of the legacy-path message.
    if variable == "country-borders":
        return FetchPlan(
            fetchable=False,
            reason=(
                "Country borders ('country-borders') have no fetch "
                "adapter: Natural Earth country vectors are a "
                "cartographic underlay, not a data variable. Country "
                "coastlines are drawn on every map automatically — "
                "asking for a data variable instead (e.g. 'bathymetry', "
                "'elevation', 'sea surface temperature') renders a map "
                "with borders as context."
            ),
            region_key=region_key,
            variable=variable,
            kind="no_adapter",
        )
    if variable == "sea-ice" and source != "nsidc":
        return FetchPlan(
            fetchable=False,
            reason=(
                f"Sea ice is only fetchable in the polar regions via NSIDC "
                f"G02135 — region '{region_key}' is outside the product's "
                "ice domain (north of 30.98°N / south of 39.23°S). The "
                "description parsed fine; try 'Arctic Ocean sea ice' or "
                "'Southern Ocean sea ice'."
            ),
            region_key=region_key,
            variable=variable,
            kind="no_adapter",
        )

    # Active fires (survey-viz >= 0.5.0, survey-currents >= 0.6.0):
    # fetchable in ANY region — the FIRMS area API is global, so no
    # region-key check applies. ``burn-scar`` is refused honestly here:
    # FIRMS is active-fire detections only; burn-scar mapping is
    # survey-burn's future imagery adapter, so we name it instead of
    # misrouting to fire detections.
    if source == "firms":
        if variable == "burn-scar":
            return FetchPlan(
                fetchable=False,
                reason=(
                    "FIRMS only serves ACTIVE fire detections "
                    "(variable 'fire'); 'burn-scar' (burned area / burn "
                    "severity mapping) has no fetch adapter yet — the "
                    "imagery-based burn-scar product belongs to survey-burn "
                    "(future). The description parsed fine; asking for "
                    "'wildfire', 'burning', or 'fire' instead routes to "
                    "NASA FIRMS detections."
                ),
                region_key=region_key,
                variable=variable,
                kind="no_adapter",
            )
        if variable != "fire":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'firms' only serves the 'fire' variable; "
                    f"got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['firms']} (variable '{variable}', "
                "source 'firms')."
            ),
            region_key=region_key,
            variable=variable,
            source="firms",
        )

    # NSIDC sea ice (survey-viz >= 0.6.0, survey-currents >= 0.7.0):
    # fetchable in the polar regions — viz.sources.default_source only
    # routes "nsidc" there, so reaching this branch means the region is
    # in the product's ice domain. ``land-ice`` never reaches this
    # branch (refused above); an explicit nsidc pin on another variable
    # is refused as a bad variable.
    if source == "nsidc":
        if variable != "sea-ice":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'nsidc' only serves the 'sea-ice' variable; "
                    f"got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['nsidc']} (variable '{variable}', "
                "source 'nsidc')."
            ),
            region_key=region_key,
            variable=variable,
            source="nsidc",
        )

    # GPM IMERG precipitation (survey-viz >= 0.7.0, survey-currents >=
    # 0.8.0): fetchable in ANY region — the 0.1° IMERG grid is global,
    # so no region-key check applies. Only the "tp" variable: an
    # explicit imerg pin on another variable is refused as a bad
    # variable (ERA5 keeps serving tp for long-record requests).
    if source == "imerg":
        if variable != "tp":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'imerg' only serves the 'tp' (precipitation) "
                    f"variable; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['imerg']} (variable '{variable}', "
                "source 'imerg')."
            ),
            region_key=region_key,
            variable=variable,
            source="imerg",
        )

    # NASA Black Marble night lights (survey-viz >= 0.8.0,
    # survey-currents >= 0.9.0): fetchable in ANY region — the VNP46A2
    # tiles are global, so no region-key check applies. Only the
    # "night-lights" variable: an explicit blackmarble pin on another
    # variable is refused as a bad variable. ``power-outage`` never
    # reaches this branch — viz.sources.default_source refuses it
    # honestly (outage mapping is change detection, not a
    # single-epoch map), and plan_fetch refuses it below.
    if source == "blackmarble":
        if variable != "night-lights":
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'blackmarble' only serves the 'night-lights' "
                    f"variable; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['blackmarble']} (variable '{variable}', "
                "source 'blackmarble')."
            ),
            region_key=region_key,
            variable=variable,
            source="blackmarble",
        )

    # GEBCO topography/bathymetry (survey-viz >= 0.9.0,
    # survey-currents >= 0.10.0): fetchable in ANY region — the 15
    # arc-second grid is global, so no region-key check applies. Only
    # the "bathymetry"/"elevation" variables: an explicit gebco pin on
    # another variable is refused as a bad variable. ``country-borders``
    # never reaches this branch — viz.sources.default_source refuses it
    # honestly (Natural Earth vectors are a cartographic underlay, not
    # a data variable), and plan_fetch refuses it below.
    if source == "gebco":
        if variable not in GEBCO_VARIABLES:
            return FetchPlan(
                fetchable=False,
                reason=(
                    f"Source 'gebco' only serves the 'bathymetry' / "
                    f"'elevation' variables; got variable '{variable}'."
                ),
                region_key=region_key,
                variable=variable,
                kind="bad_variable",
            )
        return FetchPlan(
            fetchable=True,
            reason=(
                f"Region '{region_key}' is fetchable via "
                f"{SOURCE_LABELS['gebco']} (variable '{variable}', "
                "source 'gebco')."
            ),
            region_key=region_key,
            variable=variable,
            source="gebco",
        )

    if not is_fetchable(region_key):
        return FetchPlan(
            fetchable=False,
            reason=(
                f"Region '{region_key}' has no fetch adapter yet -- only the 5 "
                "Great Lakes (Superior, Michigan, Huron, Erie, Ontario) can "
                "be fetched today, via NOAA GLSEA. The description parsed "
                "fine; fetching other regions needs a new data adapter "
                "(see docs/INTEROP.md)."
            ),
            region_key=region_key,
            variable=variable,
            kind="no_adapter",
        )
    # Legacy path (survey-viz < 0.2.0, no resolve_source): only the
    # original GLSEA SST adapter exists — currents/ERA5 requests cannot
    # be served without source routing.
    if variable != SST_VARIABLE:
        return FetchPlan(
            fetchable=False,
            reason=(
                f"Variable '{variable}' has no fetch adapter without source "
                "routing -- upgrade survey-viz to >= 0.4.0 so "
                "viz.sources.resolve_source can pick the right adapter "
                "(see docs/INTEROP.md)."
            ),
            region_key=region_key,
            variable=variable,
            kind="bad_variable",
        )
    return FetchPlan(
        fetchable=True,
        reason=(
            f"Region '{region_key}' is fetchable via NOAA GLSEA "
            f"(variable '{variable}')."
        ),
        lake=region_to_lake(region_key),
        region_key=region_key,
        variable=variable,
        source="glsea",
    )


def parse_with_fallback(
    text: str,
    parse_fn: Callable[[str], Any],
    parse_exc: type,
    assist_fn: Optional[Callable[[str], Any]] = None,
) -> Tuple[Any, bool]:
    """Parse ``text`` with the deterministic parser, optionally LLM-assisted.

    Returns ``(spec, assist_used)``. When the deterministic parser raises
    ``parse_exc`` and ``assist_fn`` is given, one assist attempt is made;
    if it also fails, the ORIGINAL parse error is re-raised (per the
    documented contract).
    """
    try:
        return parse_fn(text), False
    except parse_exc as first_error:
        if assist_fn is None:
            raise
        try:
            return assist_fn(text), True
        except Exception:
            raise first_error from None


@dataclass
class RunResult:
    """Everything one :func:`run_pipeline` call produced."""

    video_path: str
    sidecar_path: str
    frames_dir: str
    manifest_path: str
    n_frames: int
    spec: Dict[str, Any]
    lake: str
    #: which adapter fetched the data:
    #: "glsea" | "oisst" | "mur" | "era5" | "oscar" | "cmems-currents"
    source: str = ""
    provenance: Dict[str, Any] = field(default_factory=dict)


def _sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _clamp_bbox(bbox: Any, bounds: Tuple[float, float, float, float],
                provenance_note: List[str]) -> Tuple[float, float, float, float]:
    """Clamp ``bbox`` into the GLSEA grid ``bounds``.

    The survey-viz gazetteer bbox for lake-superior starts at lon -92.5,
    slightly west of the GLSEA grid's longitude floor (-92.4199507342304),
    which ``validate_glsea_bbox`` would reject. Clamping keeps the fetch
    honest: any clamping is recorded in ``provenance_note``.
    """
    lon_min, lat_min, lon_max, lat_max = (float(x) for x in bbox)
    b_lon_min, b_lat_min, b_lon_max, b_lat_max = bounds
    clamped = (max(lon_min, b_lon_min), max(lat_min, b_lat_min),
               min(lon_max, b_lon_max), min(lat_max, b_lat_max))
    if clamped != (lon_min, lat_min, lon_max, lat_max):
        provenance_note.append(
            f"fetch bbox clamped from {list(bbox)} to {list(clamped)} "
            "to fit the GLSEA grid")
    if not (clamped[0] < clamped[2] and clamped[1] < clamped[3]):
        raise UnfetchableRegionError(
            f"Region bbox {list(bbox)} does not overlap the GLSEA grid "
            f"{list(bounds)}")
    return clamped


def _time_to_date_str(value: Any) -> str:
    """Coerce a timestep label to a ``YYYY-MM-DD`` string.

    survey-viz's ``_normalize_field`` only parses date-only ISO strings
    (it raises on ISO *datetimes* like ``"2026-06-01T12:00:00+00:00"``),
    which is exactly what ``GlseaField.times`` holds — real fetched data
    included. We normalize here so the real peer field just works
    (see docs/INTEROP.md).
    """
    if isinstance(value, _dt.datetime):
        return value.date().isoformat()
    if isinstance(value, _dt.date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return _dt.date.fromisoformat(text).isoformat()
    except ValueError:
        pass
    try:
        return _dt.datetime.fromisoformat(text).date().isoformat()
    except ValueError:
        pass
    return text[:10]


def _field_to_dict(field: Any) -> Dict[str, Any]:
    """Adapt a field-shaped object to survey-viz's documented dict form.

    viz's ``render_viz`` documents plain dicts with ``times``/``lats``/
    ``lons``/``values`` keys; we use that form (rather than passing the
    field through) so the ISO-datetime ``times`` get normalized by
    :func:`_time_to_date_str` first. ``overlay_grids`` (ERA5 contour
    overlays, e.g. isobars) are carried through under the same key —
    dropping them would silently lose a requested overlay. A
    survey-currents ``CurrentField`` (3D ``u``/``v``) is adapted to the
    scalar current speed ``sqrt(u^2+v^2)`` — no quiver/streamline/particle
    rendering here; that is the survey-flow renderer's job (and it
    matches survey-viz >= 0.4.0's own renderer). A survey-currents
    ``FireField`` (NASA FIRMS active fires, survey-currents >= 0.6.0) is
    adapted through its ``to_density_grid()`` — daily fire-count grids in
    the dict shape, so rendering works with zero renderer changes;
    per-detection point markers are a future renderer feature and are
    deliberately not half-plumbed. A survey-currents ``IceField``
    (NSIDC G02135 sea ice, survey-currents >= 0.7.0) needs no
    adaptation: its 3D ``values`` (percent concentration, NaN for
    land/missing) flow through the generic attribute path unchanged.
    A survey-currents ``RainField`` (GPM IMERG precipitation,
    survey-currents >= 0.8.0) needs no adaptation either: its 3D
    ``values`` (mm/day daily totals, or mm/hr rates for
    ``accumulate="native"``; NaN for missing) flow through the generic
    attribute path unchanged. A survey-currents ``LightsField`` (NASA
    Black Marble night lights, survey-currents >= 0.9.0) needs no
    adaptation either: its 3D ``values`` (nW/cm²/sr radiance, NaN for
    unlit/missing) flow through the generic attribute path unchanged.
    A survey-currents ``TopoField`` (GEBCO 2024 topography/bathymetry,
    survey-currents >= 0.10.0) needs no adaptation either: its 3D
    ``values`` (metres, positive up) flow through the generic
    attribute path unchanged.
    """

    # FireField (NASA FIRMS active fires): bin detections into daily
    # count grids via to_density_grid() and re-enter through the
    # plain-dict path below. The hasattr guard must come before the
    # getattr fallbacks, which would otherwise misread the point field.
    if hasattr(field, "to_density_grid") and callable(field.to_density_grid):
        return _field_to_dict(field.to_density_grid())

    def _overlay_dict(src: Any) -> Dict[str, Any]:
        ov = None
        if isinstance(src, dict):
            ov = src.get("overlay_grids")
        else:
            ov = getattr(src, "overlay_grids", None)
        if not ov:
            return {}
        return {
            str(name): np.ma.filled(np.ma.asarray(arr, dtype=float), np.nan)
            for name, arr in dict(ov).items()
        }

    if isinstance(field, dict):
        out = {
            "times": [_time_to_date_str(t) for t in field["times"]],
            "lats": field["lats"],
            "lons": field["lons"],
            "values": field["values"],
        }
        overlays = _overlay_dict(field)
        if overlays:
            out["overlay_grids"] = overlays
        return out
    values = None
    # CurrentField (survey-currents): scalar current speed sqrt(u^2+v^2).
    # Masked (land/missing) cells become NaN so they stay out of the color
    # scale and the map.
    u = getattr(field, "u", None)
    v = getattr(field, "v", None)
    if u is not None and v is not None:
        u3 = np.ma.asarray(u, dtype=float)
        v3 = np.ma.asarray(v, dtype=float)
        if u3.ndim == 3 and v3.ndim == 3:
            mask = np.ma.getmaskarray(u3) | np.ma.getmaskarray(v3)
            values = np.where(
                mask, np.nan,
                np.sqrt(np.ma.getdata(u3) ** 2 + np.ma.getdata(v3) ** 2))
    if values is None:
        for attr in ("values", "data", "sst", "grids"):
            arr = getattr(field, attr, None)
            if arr is not None:
                candidate = np.ma.asarray(arr, dtype=float)
                if candidate.ndim == 3:
                    values = candidate
                    break
    if values is None:
        raise TypeError(
            "field has no 3D grid data: expected a (ntime, nlat, nlon) "
            "attribute among ('values', 'data', 'sst', 'grids'), or "
            "CurrentField-style 3D 'u'/'v'")
    out = {
        "times": [_time_to_date_str(t) for t in field.times],
        "lats": np.asarray(field.lats, dtype=float),
        "lons": np.asarray(field.lons, dtype=float),
        "values": np.ma.filled(values, np.nan),
    }
    overlays = _overlay_dict(field)
    if overlays:
        out["overlay_grids"] = overlays
    return out


def _fetch_storm_context(peers: Any, overlays: List[str], spec: Any,
                         stride_hours: int) -> Dict[str, Dict[str, Any]]:
    """Fetch ERA5 context grids for a storm-tracks render; ``{}`` on failure.

    The storm renderer draws optional ERA5 contours (e.g. wind, msl)
    *under* the IBTrACS tracks. Context is best-effort: a missing
    ``fetch_era5`` peer, missing CDS credentials, or a failed download
    degrades gracefully — the renderer records each requested overlay
    as ``"absent"`` in the manifest instead of failing the reel.
    """
    era5_fn = getattr(peers, "fetch_era5", None)
    if era5_fn is None:
        return {}
    try:
        atmo = era5_fn(list(overlays), tuple(spec.bbox),
                       spec.start, spec.end, stride_hours=stride_hours)
    except Exception:
        return {}
    grids = getattr(atmo, "overlay_grids", None) or {}
    base_var = getattr(atmo, "base_variable", "")
    times = [_time_to_date_str(t) for t in getattr(atmo, "times", [])]
    lats = np.asarray(getattr(atmo, "lats", []), dtype=float)
    lons = np.asarray(getattr(atmo, "lons", []), dtype=float)
    out: Dict[str, Dict[str, Any]] = {}
    for name in overlays:
        arr = grids.get(name)
        if arr is None and name == base_var:
            # The base variable's grid rides on .values, not
            # .overlay_grids.
            arr = getattr(atmo, "values", None)
        if arr is None:
            continue
        out[name] = {
            "times": list(times),
            "lats": lats,
            "lons": lons,
            "grid": np.ma.filled(np.ma.asarray(arr, dtype=float), np.nan),
        }
    return out


def _bilinear_resample(grid: np.ndarray,
                       src_lats: np.ndarray,
                       src_lons: np.ndarray,
                       tgt_lats: np.ndarray,
                       tgt_lons: np.ndarray) -> np.ndarray:
    """Bilinearly resample a 2-D lat/lon grid onto a target grid.

    Out-of-range target points get NaN; source NaNs are ignored (the
    weighted mean is computed over the non-NaN corner values). Used to
    align ERA5 precipitation context onto the GRACE 0.25° grid.
    """
    src_lats = np.asarray(src_lats, dtype=float)
    src_lons = np.asarray(src_lons, dtype=float)
    grid = np.asarray(grid, dtype=float)
    if src_lats.ndim != 1 or src_lons.ndim != 1:
        raise ValueError("src_lats/src_lons must be 1-D")
    lat_asc = np.argsort(src_lats)
    lon_asc = np.argsort(src_lons)
    src_lats = src_lats[lat_asc]
    src_lons = src_lons[lon_asc]
    grid = grid[lat_asc][:, lon_asc]
    tgt_lats = np.asarray(tgt_lats, dtype=float)
    tgt_lons = np.asarray(tgt_lons, dtype=float)
    lat_idx = np.interp(tgt_lats, src_lats,
                        np.arange(len(src_lats), dtype=float),
                        left=np.nan, right=np.nan)
    lon_idx = np.interp(tgt_lons, src_lons,
                        np.arange(len(src_lons), dtype=float),
                        left=np.nan, right=np.nan)
    out = np.full((len(tgt_lats), len(tgt_lons)), np.nan)
    for ti, fi in enumerate(lat_idx):
        if np.isnan(fi):
            continue
        i0 = int(fi)
        i1 = min(i0 + 1, len(src_lats) - 1)
        di = fi - i0
        for tj, fj in enumerate(lon_idx):
            if np.isnan(fj):
                continue
            j0 = int(fj)
            j1 = min(j0 + 1, len(src_lons) - 1)
            dj = fj - j0
            corners = np.array(
                [grid[i0, j0], grid[i0, j1], grid[i1, j0], grid[i1, j1]])
            weights = np.array(
                [(1 - di) * (1 - dj), (1 - di) * dj,
                 di * (1 - dj), di * dj])
            ok = ~np.isnan(corners)
            if ok.any():
                out[ti, tj] = float(np.sum(corners[ok] * weights[ok])
                                   / np.sum(weights[ok]))
    return out


def _fetch_water_context(peers: Any, overlays: List[str],
                         grace_dict: Dict[str, Any],
                         spec: Any) -> Dict[str, Dict[str, Any]]:
    """Fetch ERA5 precipitation context for a water-storage render; ``{}`` on failure.

    The water-storage renderer draws an optional ``"tp"`` precipitation
    contour overlay (``"… vs rainfall"`` descriptions) *over* the GRACE
    anomaly map. Context is best-effort: a missing ``fetch_era5`` peer,
    missing CDS credentials, or a failed download degrades gracefully —
    the renderer records the requested overlay as ``"absent"`` in the
    manifest instead of failing the reel.

    The ERA5 ``tp`` snapshots are grouped by calendar month (matching
    the GRACE monthly frames) and averaged; each monthly frame is
    bilinearly resampled onto the GRACE 0.25° grid so the overlay
    aligns cell-for-cell. This is approximate context, not a true
    monthly precipitation accumulation — documented here and in
    docs/INTEROP.md.
    """
    names = [str(o).strip().lower() for o in overlays]
    if "tp" not in names:
        return {}
    era5_fn = getattr(peers, "fetch_era5", None)
    if era5_fn is None:
        return {}
    try:
        atmo = era5_fn(["tp"], tuple(spec.bbox), spec.start, spec.end,
                       stride_hours=24)
    except Exception:
        return {}
    grids = getattr(atmo, "overlay_grids", None) or {}
    arr = grids.get("tp")
    if arr is None and getattr(atmo, "base_variable", "") == "tp":
        arr = getattr(atmo, "values", None)
    if arr is None:
        return {}
    era_times = [_time_to_date_str(t) for t in getattr(atmo, "times", [])]
    era_lats = np.asarray(getattr(atmo, "lats", []), dtype=float)
    era_lons = np.asarray(getattr(atmo, "lons", []), dtype=float)
    arr = np.ma.filled(np.ma.asarray(arr, dtype=float), np.nan)
    grace_times = [str(t)[:10] for t in grace_dict.get("times", [])]
    grace_lats = np.asarray(grace_dict.get("lats", []), dtype=float)
    grace_lons = np.asarray(grace_dict.get("lons", []), dtype=float)
    if not grace_times or not len(grace_lats) or not len(grace_lons):
        return {}
    months = []
    for gtime in grace_times:
        month = gtime[:7]
        idx = [i for i, t in enumerate(era_times)
               if str(t)[:7] == month]
        if idx:
            frame = np.nanmean(arr[idx], axis=0)
        else:
            frame = np.full((len(era_lats), len(era_lons)), np.nan)
        months.append(_bilinear_resample(frame, era_lats, era_lons,
                                         grace_lats, grace_lons))
    return {"tp": {
        "times": list(grace_dict.get("times", [])),
        "lats": grace_lats,
        "lons": grace_lons,
        "grid": np.asarray(months),
    }}


def _fetch_streamflow_context(peers: Any, overlays: List[str],
                              spec: Any,
                              stride_days: int) -> Dict[str, Dict[str, Any]]:
    """Fetch precipitation context for a streamflow render; ``{}`` on failure.

    The streamflow renderer draws an optional ``"tp"`` precipitation
    contour overlay (``"… with rainfall"`` / ``"Flooding after heavy
    rainfall"`` descriptions) *under* the gage markers. IMERG
    (observed, survey-currents >= 0.8.0) is preferred; ERA5
    (survey-currents >= 0.4.0) is the fallback. Context is best-effort:
    a missing peer, missing credentials, or a failed download degrades
    gracefully — the renderer records the requested overlay as
    ``"absent"`` in the manifest instead of failing the reel.

    Daily grids: one frame per day of the spec window. IMERG's
    ``accumulate="daily", run="late"`` product already yields daily
    totals; the ERA5 fallback reuses :func:`_fetch_storm_context` with
    daily 12:00 UTC snapshots (the viz overlay rule draws the nearest
    timestep at/below each frame date, so one snapshot per day is
    enough).
    """
    names = [str(o).strip().lower() for o in overlays]
    if "tp" not in names:
        return {}
    imerg_fn = getattr(peers, "fetch_imerg", None)
    if imerg_fn is not None:
        try:
            rain = imerg_fn(tuple(spec.bbox), spec.start, spec.end,
                            accumulate="daily", run="late",
                            stride_days=stride_days)
        except Exception:
            rain = None
        if rain is not None:
            try:
                times = [_time_to_date_str(t) for t in getattr(rain, "times", [])]
                grid = np.ma.filled(
                    np.ma.asarray(getattr(rain, "values"), dtype=float),
                    np.nan)
                return {"tp": {
                    "times": times,
                    "lats": np.asarray(getattr(rain, "lats", []), dtype=float),
                    "lons": np.asarray(getattr(rain, "lons", []), dtype=float),
                    "grid": grid,
                }}
            except Exception:
                pass
    # ERA5 fallback (daily 12:00 UTC snapshots).
    return _fetch_storm_context(peers, ["tp"], spec, 24)


def _fetch_oceancolor_context(peers: Any, context: List[str], spec: Any,
                              stride_days: int) -> Dict[str, Dict[str, Any]]:
    """Fetch best-effort companion data for an ocean-color reel; ``{}`` on failure.

    ``context`` comes from the viz spec: ``("currents",)`` for "bloom
    with ocean currents" wordings, ``("sst",)`` for "bloom conditions"
    / "chlorophyll vs temperature" wordings. Each companion is fetched
    on the spec bbox and window — OSCAR (Earthdata) preferred for
    currents with CMEMS as the peer-order fallback, OISST preferred
    for SST with MUR as the fallback — matching the pipeline's normal
    source ordering for those variables. Context is best-effort: a
    missing peer, missing credentials, or a failed download degrades
    gracefully to a recorded ``"absent"`` status — the main
    ocean-color reel never fails because a context dataset is
    unavailable. The results land in the reel provenance under
    ``fetch["oceancolor_context"]`` (the renderer also records
    ``spec.context`` in its own manifest).
    """
    out: Dict[str, Dict[str, Any]] = {}
    for name in context:
        name = str(name).strip().lower()
        if name == "currents":
            if getattr(peers, "fetch_oscar", None) is not None:
                fetch_fn, ctx_source = peers.fetch_oscar, "oscar"
            else:
                fetch_fn = getattr(peers, "fetch_cmems_currents", None)
                ctx_source = "cmems-currents"
        elif name == "sst":
            if getattr(peers, "fetch_oisst", None) is not None:
                fetch_fn, ctx_source = peers.fetch_oisst, "oisst"
            else:
                fetch_fn = getattr(peers, "fetch_mur", None)
                ctx_source = "mur"
        else:
            out[name] = {"fetched": False, "status": "absent",
                         "reason": f"unknown context '{name}'"}
            continue
        if fetch_fn is None:
            out[name] = {"fetched": False, "status": "absent",
                         "reason": f"no {ctx_source} peer configured"}
            continue
        try:
            field = fetch_fn(tuple(spec.bbox), spec.start, spec.end,
                             stride_days=stride_days)
        except Exception as exc:
            out[name] = {"fetched": False, "status": "absent",
                         "reason": f"{type(exc).__name__}: {exc}"}
            continue
        out[name] = {
            "fetched": True,
            "status": "ok",
            "source": ctx_source,
            "n_times": len(getattr(field, "times", []) or []),
            "provenance": dict(getattr(field, "provenance", {}) or {}),
        }
    return out


def _series_to_dict(series: Any) -> Optional[Dict[str, Any]]:
    """Adapt a survey-currents LakeSeries to survey-viz's duck-typed series.

    survey-viz's ``_normalize_series`` accepts a dict with ``dates``/``values``
    keys; LakeSeries exposes ``.dates``/``.temps``, so we adapt explicitly
    instead of relying on attribute coincidence. ``None`` (global SST:
    no lake-average equivalent) passes through, and render_viz draws a
    placeholder chart panel.
    """
    if series is None:
        return None
    if isinstance(series, dict):
        return series
    return {
        "dates": list(getattr(series, "dates")),
        "values": [float(t) for t in getattr(series, "temps")],
    }


def run_pipeline(
    spec: Any,
    peers: types.SimpleNamespace,
    out_dir: str,
    progress: Optional[Callable[[float, str], None]] = None,
    stride_days: int = DEFAULT_STRIDE_DAYS,
    stride_hours: int = DEFAULT_STRIDE_HOURS,
    cmap: Optional[str] = None,
) -> RunResult:
    """Run the full fetch -> render -> encode pipeline for ``spec``.

    Args:
        spec: a ``VizSpec`` (from ``parse_with_fallback``).
        peers: namespace from :func:`studio.peers.wire_peers` (or test doubles)
            with ``is_fetchable``, ``resolve_source`` (optional),
            ``fetch_sst``, ``fetch_averages``, ``fetch_oisst``,
            ``fetch_mur``, ``fetch_era5``, ``fetch_oscar``,
            ``fetch_cmems_currents`` (optional), ``fetch_oceancolor``
            (optional), ``render_viz``,
            ``render_video``.
        out_dir: working directory for frames + the MP4 (created if needed).
        progress: optional ``(fraction, message)`` callback.
        stride_days: time-axis stride for the daily fetch calls (SST,
            global currents, NSIDC sea ice); 30 samples roughly monthly
            frames from a multi-year window. FIRMS is daily by construction
            and ignores it.
        stride_hours: time-axis stride for the ERA5 fetch call (hourly
            reanalysis; 24 = daily 12:00 UTC).
        cmap: optional matplotlib colormap name overriding the data map's
            colormap (survey-viz >= 0.15.0; ``viz.CURATED_CMAPS`` lists
            the recommended names). ``None`` (default) keeps the peer's
            variable default and is never passed, so older survey-viz
            peers keep working. Categorical renderers (storms,
            streamgages, earthquakes) validate but ignore it — see
            survey-viz docs.

    Raises:
        UnfetchableRegionError / UnsupportedVariableError: honest,
            no-crash refusals naming what is missing.
        RuntimeError: wrapped fetch failures (network, NetCDF, ...).
    """
    def report(frac: float, message: str) -> None:
        if progress is not None:
            progress(frac, message)

    plan = plan_fetch(spec, peers.is_fetchable,
                      getattr(peers, "resolve_source", None))
    if not plan.fetchable:
        if plan.kind == "bad_variable":
            raise UnsupportedVariableError(plan.reason)
        raise UnfetchableRegionError(plan.reason)
    lake = plan.lake or ""
    source = plan.source or "glsea"

    os.makedirs(out_dir, exist_ok=True)
    frames_dir = os.path.join(out_dir, "frames")
    os.makedirs(frames_dir, exist_ok=True)

    # -- 1. fetch -----------------------------------------------------------
    clamp_notes: List[str] = []
    fetch_key = "sst"
    # Ocean-color context companions (currents / sst) land here; the
    # oceancolor execution branch fills it via _fetch_oceancolor_context
    # and the provenance builder records it under
    # fetch["oceancolor_context"].
    oceancolor_context: Dict[str, Dict[str, Any]] = {}
    # Storm tracks bypass the scalar-grid adapter: the ibtracs branch
    # sets render_dict to the StormField's to_dict() form, which
    # render_viz (survey-viz >= 0.10.0) draws as track polylines.
    render_dict: Optional[Dict[str, Any]] = None
    if source == "glsea":
        report(0.05, "Fetching GLSEA sea-surface-temperature grid…")
        fetch_bbox = _clamp_bbox(
            spec.bbox, getattr(peers, "glsea_bounds",
                               (-180.0, -90.0, 180.0, 90.0)), clamp_notes)
        try:
            field = peers.fetch_sst(
                fetch_bbox, spec.start, spec.end, stride_days=stride_days)
        except Exception as exc:
            raise RuntimeError(
                f"SST fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection and that netCDF4 is installed "
                "(pip install netCDF4)."
            ) from exc

        report(0.35, f"Fetching {lake} lake-average temperature series…")
        try:
            series = peers.fetch_averages(lake, spec.start, spec.end)
        except Exception as exc:
            raise RuntimeError(
                f"Lake-average fetch failed ({type(exc).__name__}: {exc})."
            ) from exc
    elif source == "era5":
        # ERA5 atmosphere: fetch the base variable plus any contour
        # overlays (e.g. msl isobars for the storm combination) in one
        # call, on the spec bbox directly — the 0.25° grid is global,
        # nothing to clamp. series=None: there is no lake-average
        # equivalent; render_viz shows a placeholder chart panel.
        variables = [str(getattr(spec, "variable", ""))] + [
            str(o) for o in (getattr(spec, "overlays", None) or ())]
        label = SOURCE_LABELS["era5"]
        report(0.05, f"Fetching {label} {', '.join(variables)} grid…")
        fetch_fn = getattr(peers, "fetch_era5", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'era5' needs survey-currents>=0.4.0 with the ERA5 "
                "adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                variables, tuple(spec.bbox), spec.start, spec.end,
                stride_hours=stride_hours)
        except Exception as exc:
            raise RuntimeError(
                f"ERA5 fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection, that cdsapi and netCDF4 are "
                "installed (pip install cdsapi netCDF4), and that a free "
                "Copernicus CDS account is configured (~/.cdsapirc or "
                "CDSAPI_URL/CDSAPI_KEY)."
            ) from exc
        series = None
        fetch_key = "era5"
    elif source in ("oscar", "cmems-currents"):
        # Global currents: fetch(bbox, start, end, stride_days=...) on the
        # spec bbox directly — the 0.25°/1/12° grids are global, nothing
        # to clamp. series=None: there is no lake-average equivalent;
        # render_viz shows a placeholder chart panel. _field_to_dict
        # renders the scalar current speed sqrt(u^2+v^2).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} surface-current grid…")
        fetch_fn = getattr(peers, f"fetch_{source.replace('-', '_')}", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                f"Source '{source}' needs survey-currents>=0.5.0 with the "
                "global-currents adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end,
                stride_days=stride_days)
        except Exception as exc:
            raise RuntimeError(
                f"Currents fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection, that netCDF4 is installed "
                "(pip install netCDF4), and that the right credentials are "
                "configured — free Earthdata Login for OSCAR, free CMEMS "
                "account + copernicusmarine toolbox for CMEMS."
            ) from exc
        series = None
        fetch_key = source
    elif source == "firms":
        # NASA FIRMS active fires: fetch_detections(bbox, start, end) on
        # the spec bbox directly — the area API is global, nothing to
        # clamp. series=None: there is no lake-average equivalent;
        # render_viz shows a placeholder chart panel. _field_to_dict
        # adapts the FireField via to_density_grid() (daily fire-count
        # grids in the render dict form).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} active-fire detections…")
        fetch_fn = getattr(peers, "fetch_firms", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'firms' needs survey-currents>=0.6.0 with the FIRMS "
                "adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end)
        except Exception as exc:
            raise RuntimeError(
                f"FIRMS fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection, and that a free NASA FIRMS "
                "MAP_KEY is set (FIRMS_MAP_KEY env var or the map_key "
                "argument — https://firms.modaps.eosdis.nasa.gov/api/area/)."
            ) from exc
        series = None
        fetch_key = "firms"
    elif source == "nsidc":
        # NSIDC G02135 daily sea-ice concentration: fetch_nsidc_sic(bbox,
        # start, end, stride_days=...) on the spec bbox directly — the
        # polar grids are hemispheric, nothing to clamp. series=None:
        # there is no lake-average equivalent; render_viz shows a
        # placeholder chart panel. _field_to_dict adapts the IceField
        # via its 3D ``values`` (percent, NaN for land/missing).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} sea-ice concentration grid…")
        fetch_fn = getattr(peers, "fetch_nsidc", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'nsidc' needs survey-currents>=0.7.0 with the "
                "NSIDC sea-ice adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end,
                stride_days=stride_days)
        except Exception as exc:
            raise RuntimeError(
                f"NSIDC fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection — the archive is keyless "
                "HTTPS (no account needed), so this is usually a "
                "connectivity or date-range issue."
            ) from exc
        series = None
        fetch_key = "nsidc"
    elif source == "imerg":
        # NASA GPM IMERG V07 half-hourly precipitation:
        # fetch_imerg(bbox, start, end, accumulate="daily", run="late",
        # stride_days=...) on the spec bbox directly — the 0.1° grid is
        # global, nothing to clamp. series=None: there is no
        # lake-average equivalent; render_viz shows a placeholder chart
        # panel. _field_to_dict adapts the RainField via its 3D
        # ``values`` (mm/day daily totals, NaN for missing).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} precipitation grid…")
        fetch_fn = getattr(peers, "fetch_imerg", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'imerg' needs survey-currents>=0.8.0 with the "
                "GPM IMERG adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end,
                accumulate="daily", run="late",
                stride_days=stride_days)
        except Exception as exc:
            raise RuntimeError(
                f"IMERG fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection, that h5py is installed "
                "(pip install \"survey-currents[imerg]\"), and that a free "
                "Earthdata Login is configured (EARTHDATA_USERNAME / "
                "EARTHDATA_PASSWORD or ~/.netrc)."
            ) from exc
        series = None
        fetch_key = "imerg"
    elif source == "blackmarble":
        # NASA Black Marble VNP46A2 daily night lights:
        # fetch_blackmarble(bbox, start, end, product="daily",
        # stride_days=...) on the spec bbox directly — the tiles are
        # global, nothing to clamp. series=None: there is no
        # lake-average equivalent; render_viz shows a placeholder chart
        # panel. _field_to_dict adapts the LightsField via its 3D
        # ``values`` (nW/cm²/sr radiance, NaN for unlit/missing).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} night lights…")
        fetch_fn = getattr(peers, "fetch_blackmarble", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'blackmarble' needs survey-currents>=0.9.0 with the "
                "Black Marble night-lights adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end,
                product="daily", stride_days=stride_days)
        except Exception as exc:
            raise RuntimeError(
                f"Black Marble fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection, that h5py is installed "
                "(pip install \"survey-currents[blackmarble]\"), and that a free "
                "Earthdata Login is configured (EARTHDATA_USERNAME / "
                "EARTHDATA_PASSWORD or ~/.netrc)."
            ) from exc
        series = None
        fetch_key = "blackmarble"
    elif source == "gebco":
        # GEBCO 2024 global topography/bathymetry: fetch_gebco(bbox,
        # resolution=...) on the spec bbox directly — the grid is
        # global, nothing to clamp. Static compilation: no time axis,
        # so resolution is picked from the bbox span (grid stays <=
        # ~720 cells per axis). series=None: there is no lake-average
        # equivalent; render_viz shows a placeholder chart panel.
        # _field_to_dict adapts the TopoField via its 3D ``values``
        # (metres, positive up).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} topography…")
        fetch_fn = getattr(peers, "fetch_gebco", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'gebco' needs survey-currents>=0.10.0 with the "
                "GEBCO basemap adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        lon_min, lat_min, lon_max, lat_max = (float(v) for v in spec.bbox)
        span = max(lon_max - lon_min, lat_max - lat_min)
        resolution = 0.25
        for candidate in (0.25, 0.5, 1.0, 2.0):
            if span / candidate <= 720:
                resolution = candidate
                break
        else:
            resolution = 2.0
        try:
            field = fetch_fn(tuple(spec.bbox), resolution=resolution)
        except Exception as exc:
            raise RuntimeError(
                f"GEBCO fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection. The first fetch in a new "
                "region downloads the intersecting 90° tile entries "
                "(~500 MB each, cached afterwards); a corrupt cache entry "
                "is re-downloaded automatically."
            ) from exc
        series = None
        fetch_key = "gebco"
    elif source == "ibtracs":
        # NOAA IBTrACS v04r01 tropical-cyclone best tracks:
        # fetch_ibtracs(bbox, start, end, storm_name=...) on the spec
        # bbox directly — the archive is global, nothing to clamp.
        # series=None: there is no lake-average equivalent; render_viz
        # shows a placeholder chart panel. The StormField goes to
        # render_viz as its to_dict() form (never through
        # _field_to_dict, which only understands scalar 3-D grids):
        # survey-viz >= 0.10.0 draws cumulative track polylines from
        # the "storm_tracks" key. Ranking (storm_rank="strongest" +
        # storm_top_n) is applied here, on the documented lifetime
        # maximum sustained wind rule, via
        # StormField.rank_by_intensity(). A requested ERA5 overlay
        # ("… with the wind field") is fetched as context and attached
        # as overlay_grids dicts; an unavailable context degrades
        # gracefully (the renderer records "absent" per overlay).
        label = SOURCE_LABELS[source]
        storm_name = str(getattr(spec, "storm_name", "") or "").strip() or None
        report(0.05, f"Fetching {label} storm tracks"
               + (f" for '{storm_name}'" if storm_name else "") + "…")
        fetch_fn = getattr(peers, "fetch_ibtracs", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'ibtracs' needs survey-currents>=0.11.0 with the "
                "IBTrACS storm-track adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end,
                storm_name=storm_name)
        except Exception as exc:
            raise RuntimeError(
                f"IBTrACS fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection and that netCDF4 is installed "
                "(pip install netCDF4). The archive is keyless HTTPS (no "
                "account needed), so this is usually a connectivity or "
                "date-range issue."
            ) from exc
        rank = str(getattr(spec, "storm_rank", "") or "").strip().lower()
        if rank == "strongest" and hasattr(field, "rank_by_intensity"):
            top_n = getattr(spec, "storm_top_n", None) or 5
            try:
                top_n = max(1, int(top_n))
            except (TypeError, ValueError):
                top_n = 5
            field = replace(field, tracks=field.rank_by_intensity()[:top_n])
        render_dict = (field.to_dict() if hasattr(field, "to_dict")
                       else field)
        overlays = [str(o) for o in (getattr(spec, "overlays", None) or ())]
        if overlays and isinstance(render_dict, dict):
            context = _fetch_storm_context(
                peers, overlays, spec, stride_hours)
            if context:
                render_dict = dict(render_dict)
                render_dict["overlay_grids"] = context
        series = None
        fetch_key = "ibtracs"
    elif source == "grace":
        # CSR GRACE/GRACE-FO RL06.3 terrestrial water storage anomalies:
        # fetch_grace(bbox, start, end) on the spec bbox directly — the
        # mascon archive is global, nothing to clamp. series=None: there
        # is no lake-average equivalent; render_viz shows a placeholder
        # chart panel. The WaterField goes to render_viz as its
        # to_dict() form (never through _field_to_dict, which only
        # understands the scalar 3-D CurrentField grid): survey-viz >=
        # 0.11.0 draws monthly cm-LWE anomaly maps from the "values"
        # key, with all-NaN gap months rendered as NO GRACE OBSERVATION
        # panels (never interpolated) and the manifest carrying the
        # anomaly baseline + gap months. A requested "tp" precipitation
        # overlay ("… vs rainfall") is fetched as ERA5 context —
        # monthly means of daily precipitation snapshots, bilinearly
        # resampled onto the GRACE grid — and attached as an
        # overlay_grids dict; an unavailable context degrades
        # gracefully (the renderer records "absent").
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} terrestrial water storage…")
        fetch_fn = getattr(peers, "fetch_grace", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'grace' needs survey-currents>=0.12.0 with the "
                "GRACE water-storage adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(tuple(spec.bbox), spec.start, spec.end)
        except Exception as exc:
            raise RuntimeError(
                f"GRACE fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection and that netCDF4 is "
                "installed (pip install netCDF4). The CSR archive is "
                "keyless HTTPS (no account needed), so this is usually "
                "a connectivity issue."
            ) from exc
        render_dict = (field.to_dict() if hasattr(field, "to_dict")
                       else field)
        overlays = [str(o) for o in (getattr(spec, "overlays", None) or ())]
        if overlays and isinstance(render_dict, dict):
            context = _fetch_water_context(peers, overlays, render_dict,
                                           spec)
            if context:
                render_dict = dict(render_dict)
                render_dict["overlay_grids"] = context
        series = None
        fetch_key = "grace"
    elif source == "usgs":
        # USGS Water Services NWIS streamgage daily values:
        # fetch_usgs(bbox, start, end) on the spec bbox directly — NWIS
        # is keyless HTTPS, nothing to clamp (US-only coverage; a bbox
        # outside USGS coverage returns an honest empty field, which
        # survey-viz >= 0.12.0 renders as an explicit no-gages message,
        # never fabricated data). series=None: there is no lake-average
        # equivalent; render_viz draws the hydrograph panel from the
        # GageField itself. The GageField goes to render_viz as its
        # to_dict() form (never through _field_to_dict, which only
        # understands scalar 3-D grids): survey-viz draws gage markers
        # + a hydrograph from the "gage_records" key. A requested
        # "tp" precipitation overlay ("… with rainfall" / "Flooding
        # after heavy rainfall") is fetched as best-effort context —
        # IMERG observed first, ERA5 fallback — and attached as
        # overlay_grids dicts; an unavailable context degrades
        # gracefully (the renderer records "absent" per overlay).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} streamgage daily values…")
        fetch_fn = getattr(peers, "fetch_usgs", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'usgs' needs survey-currents>=0.13.0 with the "
                "USGS streamgage adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(tuple(spec.bbox), spec.start, spec.end)
        except Exception as exc:
            raise RuntimeError(
                f"USGS streamgage fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection. The NWIS water services are "
                "keyless HTTPS (no account needed), so this is usually a "
                "connectivity or date-range issue."
            ) from exc
        render_dict = (field.to_dict() if hasattr(field, "to_dict")
                       else field)
        overlays = [str(o) for o in (getattr(spec, "overlays", None) or ())]
        if overlays and isinstance(render_dict, dict):
            context = _fetch_streamflow_context(peers, overlays, spec,
                                                stride_days)
            if context:
                render_dict = dict(render_dict)
                render_dict["overlay_grids"] = context
        series = None
        fetch_key = "usgs"
    elif source == "oceancolor":
        # Ocean color (survey-viz >= 0.13.0, survey-currents >= 0.14.0):
        # fetch_oceancolor(bbox, start, end, cadence=..., sensor=...)
        # on the spec bbox directly — the CoastWatch L3 grids are
        # global, nothing to clamp. series=None: there is no
        # spatial-average equivalent; render_viz shows a placeholder
        # chart panel. The OceanColorField goes to render_viz as its
        # to_dict() form (never through _field_to_dict, which only
        # understands CurrentField's u/v pair) with times normalized
        # to date strings first (the adapter emits full ISO datetimes,
        # which viz's date coercion cannot parse — same treatment as
        # the glsea/oisst fields). survey-viz >= 0.13.0 draws
        # log-scaled chlorophyll-a maps from the "values" key, with
        # all-NaN frames rendered as NO OCEAN COLOR OBSERVATION panels
        # (never interpolated) and the manifest carrying log_scale +
        # gap_frames. Requested context ("… with ocean currents" /
        # "… vs temperature") is fetched as best-effort companion data
        # and recorded in the reel provenance; unavailable context
        # degrades gracefully.
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} chlorophyll-a grid…")
        fetch_fn = getattr(peers, "fetch_oceancolor", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'oceancolor' needs survey-currents>=0.14.0 with the "
                "ocean-color adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        # The viz spec cadence is "daily"/"monthly"/"yearly"; the
        # adapter takes "daily"/"weekly"/"monthly" (yearly is refused
        # there). "weekly" can only come from an explicit API call.
        oc_cadence = {"daily": "daily", "weekly": "weekly"}.get(
            str(getattr(spec, "cadence", "monthly")), "monthly")
        try:
            # Default to MODIS Aqua R2022 (the survey-currents
            # fetch_oceancolor default) unless the spec pins a sensor.
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end,
                cadence=oc_cadence,
                sensor=getattr(spec, "sensor", None) or "modis-aqua")
        except Exception as exc:
            raise RuntimeError(
                f"Ocean color fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection and that the NOAA CoastWatch "
                "ERDDAP service is up (it occasionally returns 502/503 "
                "during outages)."
            ) from exc
        render_dict = (field.to_dict() if hasattr(field, "to_dict")
                       else field)
        if isinstance(render_dict, dict):
            render_dict = dict(render_dict)
            render_dict["times"] = [
                _time_to_date_str(t)
                for t in render_dict.get("times", [])]
        context = [str(c) for c in
                   (getattr(spec, "context", None) or ())]
        if context:
            report(0.25, "Fetching ocean-color context datasets…")
            oceancolor_context = _fetch_oceancolor_context(
                peers, context, spec, stride_days)
        series = None
        fetch_key = "oceancolor"
    elif source == "comcat":
        # USGS ComCat earthquake catalog (survey-viz >= 0.14.0,
        # survey-currents >= 0.15.0): fetch_earthquakes(bbox, start,
        # end) on the spec bbox directly — ComCat is global and
        # keyless, nothing to clamp. series=None: there is no
        # spatial-average equivalent; render_viz draws the cumulative
        # event map, the top-5 ranking panel, and the daily-count
        # series from the QuakeField itself. The QuakeField goes to
        # render_viz as its to_dict() form (never through
        # _field_to_dict, which only understands scalar 3-D grids):
        # survey-viz draws magnitude-scaled, depth-colored cumulative
        # daily frames from the "events" key, with an explicit
        # no-earthquakes message for empty windows. Observed catalog
        # only — never a forecast.
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} events…")
        fetch_fn = getattr(peers, "fetch_earthquakes", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                "Source 'comcat' needs survey-currents>=0.15.0 with the "
                "USGS earthquake catalog adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(tuple(spec.bbox), spec.start, spec.end)
        except Exception as exc:
            raise RuntimeError(
                f"ComCat earthquake fetch failed ({type(exc).__name__}: "
                f"{exc}). Check the network connection. The ComCat FDSN "
                "event service is keyless HTTPS (no account needed), so "
                "this is usually a connectivity or date-range issue."
            ) from exc
        render_dict = (field.to_dict() if hasattr(field, "to_dict")
                       else field)
        series = None
        fetch_key = "comcat"
    else:
        # Global SST (OISST/MUR): fetch on the spec bbox directly — the
        # global grids have no lake bounds to clamp to — and render with
        # series=None (there is no lake-average equivalent; render_viz
        # shows a placeholder chart panel).
        label = SOURCE_LABELS[source]
        report(0.05, f"Fetching {label} sea-surface-temperature grid…")
        fetch_fn = getattr(peers, f"fetch_{source}", None)
        if fetch_fn is None:
            raise UnfetchableRegionError(
                f"Source '{source}' needs survey-currents>=0.3.0 with the "
                f"global-SST adapter: pip install --upgrade "
                "git+https://github.com/crieck2010/survey-currents.git"
            )
        try:
            field = fetch_fn(
                tuple(spec.bbox), spec.start, spec.end,
                stride_days=stride_days)
        except Exception as exc:
            raise RuntimeError(
                f"SST fetch failed ({type(exc).__name__}: {exc}). "
                "Check the network connection, that netCDF4 is installed "
                "(pip install netCDF4), and (for MUR) that Earthdata "
                "credentials are configured."
            ) from exc
        series = None

    # -- 2. render frames ----------------------------------------------------
    # render_viz is duck-typed: the field is adapted to its documented dict
    # form (also normalizing GlseaField's ISO-datetime times, which viz's
    # own date coercion cannot parse — see docs/INTEROP.md), and the series
    # to the documented dates/values dict.
    report(0.50, "Rendering reel frames…")
    render_field = (render_dict if render_dict is not None
                    else _field_to_dict(field))
    frames, manifest_path = peers.render_viz(
        spec, render_field, _series_to_dict(series),
        out_dir=frames_dir,
        **({"cmap": cmap} if cmap is not None else {}))

    # -- 3. encode ------------------------------------------------------------
    # NOTE: pass the frames DIRECTORY, not the viz manifest path:
    # survey-animate's manifest reader only accepts the
    # "survey-flow.frame-manifest/" schema prefix, while survey-viz writes
    # "survey-viz.frame-manifest/1.0" (see docs/INTEROP.md).
    report(0.85, "Encoding MP4…")
    video_path = os.path.join(out_dir, "reel.mp4")
    result = peers.render_video(
        frames_dir, video_path, preset="reel",
        title=getattr(spec, "title", ""), burn_timestamps_=False)

    report(1.0, "Done")

    provenance = {
        "spec": spec.to_dict() if hasattr(spec, "to_dict") else dict(spec),
        "lake": lake,
        "source": source,
        "fetch": {
            # "sst" for the SST adapters, "era5" for the atmosphere adapter,
            # "oscar"/"cmems-currents" for the currents adapters, "firms"
            # for active fires, "nsidc" for sea ice, "imerg" for
            # precipitation, "blackmarble" for night lights, "gebco" for
            # topography, "ibtracs" for storm tracks, "grace" for
            # terrestrial water storage, "usgs" for streamgages,
            # "oceancolor" for ocean color, "comcat" for earthquakes
            # (the field provenance carries
            # the source-specific payload).
            fetch_key: dict(getattr(field, "provenance", {}) or {}),
            # Ocean-color context companions (currents / sst), only
            # populated for source "oceancolor".
            "oceancolor_context": oceancolor_context,
            "averages": dict(getattr(series, "provenance", {}) or {}),
            "bbox_clamp_notes": clamp_notes,
        },
        "render": {
            "frames_dir": frames_dir,
            "manifest_path": manifest_path,
            "n_frames": len(frames),
        },
        "encode": {
            "video_path": os.path.abspath(video_path),
            "sidecar_path": getattr(result, "sidecar_path", ""),
            "fps": getattr(result, "fps", None),
            "n_frames": getattr(result, "n_frames", len(frames)),
            "video_sha256": _sha256_file(video_path),
        },
    }

    return RunResult(
        video_path=os.path.abspath(video_path),
        sidecar_path=getattr(result, "sidecar_path", ""),
        frames_dir=frames_dir,
        manifest_path=manifest_path,
        n_frames=len(frames),
        spec=provenance["spec"],
        lake=lake,
        source=source,
        provenance=provenance,
    )
