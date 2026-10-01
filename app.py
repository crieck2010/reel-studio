"""reel-studio — plain-English description -> finished vertical reel (MP4).

100% local: ``streamlit run app.py``. All heavy lifting is delegated to
the optional peer engines:

* survey-viz      — ``parse_description`` -> VizSpec, ``render_viz`` -> frames
* survey-currents — ``fetch_glsea_sst`` + ``fetch_glsea_lake_averages``
* survey-animate  — ``render_video`` (resolves ffmpeg itself)

Every peer import is optional: the app launches with any subset missing
and shows the exact ``pip install git+https://...`` command that fixes
it. The UI-free orchestration lives in :mod:`studio.pipeline` so it can
be tested without Streamlit.

The optional ``LLM_API_KEY`` env var enables ONE LLM assist attempt when
the deterministic parser fails; the app works fully without it.
"""

from __future__ import annotations

import inspect
import importlib
import json
import os
import sys
import tempfile
import traceback
import types
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make the ``studio`` package importable when run as ``streamlit run app.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import streamlit as st
except ImportError:  # headless / tests: import still works, main() won't run
    st = None  # type: ignore

from studio import batch, caching, llm_assist, peers, pipeline, scheduler, styling, timescale

APP_TITLE = "reel-studio"
APP_VERSION = "0.17.0"
PEER_REPOS = ("survey-viz", "survey-currents", "survey-animate",
              "survey-layout", "survey-style", "survey-schedule",
              "survey-publish")

#: Minimum peer versions for the v0.4.0 features.
MIN_VIZ_STORY = (0, 17, 0)      # story captions + motion refine intents
MIN_ANIMATE_MOTION = (0, 2, 0)   # cinematic motion + audio muxing
#: Minimum peer versions for the v0.5.0 platform layouts.
MIN_VIZ_CANVAS = (0, 18, 0)     # render_viz canvas= (platform canvases)
#: Minimum peer version for the v0.15.0 mapped.earth aesthetic presets.
MIN_VIZ_PRESET = (0, 22, 0)     # render_viz preset=/rotation=/watermark=

#: Minimum peer version for the v0.16.0 place labels.
MIN_VIZ_LABELS = (0, 23, 0)     # render_viz place_labels=/max_labels=

#: Minimum peer version for the v0.17.0 basemap styles + strand controls.
MIN_VIZ_STRANDS = (0, 24, 0)    # render_viz basemap=/strand_count=/
                                # strand_linewidth= + dark_strands preset

#: Camera-motion widget options (values are the MotionSpec vocabularies).
ZOOM_MODES = ("off", "in", "out")
PAN_DIRECTIONS = ("off", "up", "down", "left", "right",
                  "up-left", "up-right", "down-left", "down-right")
PAN_LABELS = {
    "off": "Off", "up": "Up (north)", "down": "Down (south)",
    "left": "Left (west)", "right": "Right (east)",
    "up-left": "Up-left (northwest)", "up-right": "Up-right (northeast)",
    "down-left": "Down-left (southwest)",
    "down-right": "Down-right (southeast)",
}

#: Audio extensions the uploader accepts (ffmpeg decodes all of these).
AUDIO_TYPES = ["mp3", "wav", "ogg", "flac", "m4a", "aac", "opus", "wma"]

#: Social platforms the Publish step knows about. When survey-publish is
#: installed its registry's ``list_platforms()`` is authoritative; this
#: is the fallback (and the documented set).
PUBLISH_PLATFORM_ORDER = ("youtube", "instagram", "facebook", "tiktok")

#: Per-platform connect docs in the survey-publish repo (paths per the
#: interface contract — verify against the repo once it ships).
PUBLISH_SETUP_DOCS = {
    "youtube": "docs/SETUP_YOUTUBE.md",
    "instagram": "docs/SETUP_INSTAGRAM.md",
    "facebook": "docs/SETUP_FACEBOOK.md",
    "tiktok": "docs/SETUP_TIKTOK.md",
}


# ---------------------------------------------------------------------------
# Headless-safe entry point
# ---------------------------------------------------------------------------

def _streamlit_is_running() -> bool:
    """True only inside a real ``streamlit run`` script execution."""
    if st is None:
        return False
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx
        return get_script_run_ctx() is not None
    except Exception:
        return False


# ---------------------------------------------------------------------------
# UI helpers (only called from main(), i.e. under a real Streamlit runtime)
# ---------------------------------------------------------------------------

def _peer_version_tuple(status: peers.PeerStatus) -> tuple:
    """(major, minor, patch) of an installed peer, (0,0,0) when missing."""
    mod = status.module if status.installed else None
    ver = str(getattr(mod, "__version__", "0") or "0")
    parts = []
    for piece in ver.split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def _version_ok(status: peers.PeerStatus, minimum: tuple) -> bool:
    return status.installed and _peer_version_tuple(status) >= minimum


def _current_run_settings() -> Dict[str, Any]:
    """Snapshot of the current single-run settings for batch jobs."""
    return {
        "motion": dict(st.session_state.get("motion") or {}),
        "audio_path": st.session_state.get("audio_path"),
        "story_captions": bool(st.session_state.get("story_captions")),
        "cmap": st.session_state.get("cmap"),
        "platform": st.session_state.get("platform") or "legacy",
        "style_preset": st.session_state.get("style_preset"),
        "derived": _run_derived(),
    }


def _upgrade_hint(status: peers.PeerStatus, minimum: str, feature: str) -> str:
    repo = getattr(status, "repo", "this peer")
    return (f"{feature} needs {repo} ≥ {minimum} — upgrade it "
            f"(`{status.pip_command}`) to unlock.")


def _peer_status_panel(statuses: Dict[str, peers.PeerStatus]) -> None:
    st.subheader("Engine status")
    cols = st.columns(len(PEER_REPOS))
    for col, repo in zip(cols, PEER_REPOS):
        status = statuses[repo]
        with col:
            if status.installed:
                st.success(f"**{repo}**\n\ninstalled")
            else:
                st.warning(f"**{repo}**\n\nmissing")
    missing = [r for r in PEER_REPOS if not statuses[r].installed]
    if missing:
        with st.expander("Install the missing engines", expanded=True):
            st.write(
                "reel-studio launches without its peers, but parsing, "
                "fetching, and encoding each need their engine:")
            for repo in missing:
                st.code(statuses[repo].pip_command, language="bash")
                st.caption(statuses[repo].needed_for)


def _parse_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    st.subheader("1 · Describe the reel")
    text = st.text_area(
        "Plain-English description",
        value=("surface water temperature oscillation on Lake Superior "
               "for the past 10 years"),
        height=90,
        help=("Examples: 'Lake Michigan water temperature last summer', "
              "'chlorophyll in the Gulf of Mexico 2020 to 2022'"),
    )
    if not st.button("Parse", type="primary"):
        return

    status = statuses["survey-viz"]
    if not status.installed:
        st.error(
            "Parsing needs survey-viz, which is not installed.\n\n"
            f"Install it with:\n\n    {status.pip_command}")
        return

    viz = status.module
    used_assist = False
    try:
        spec, used_assist = pipeline.parse_with_fallback(
            text, viz.parse_description, viz.UnparseableDescription,
            assist_fn=_make_assist_fn(statuses),
        )
    except viz.UnparseableDescription as exc:
        st.error("Could not parse that description.")
        st.code(str(exc))
        st.session_state.pop("spec_dict", None)
        return

    spec_dict = spec.to_dict()
    st.session_state["spec_dict"] = spec_dict
    st.session_state["description"] = text
    st.session_state["style_preset"] = None  # fresh parse, no preset yet
    # Fresh parse, fresh window: drop any suggestion/picker state so the
    # Time window pickers re-initialize from this spec's dates.
    for key in (timescale.START_KEY, timescale.END_KEY,
                timescale.SUGGESTION_KEY, timescale.ERROR_KEY):
        st.session_state.pop(key, None)
    if used_assist:
        st.info("The deterministic parser could not handle this description, "
                "so one LLM assist attempt was used (LLM_API_KEY was set). "
                "Please confirm the spec below before running.")
    else:
        st.success("Parsed — confirm the spec, then press Run.")
    st.subheader("Parsed VizSpec")
    st.json(spec_dict)


def _describe_unsafe(rect: tuple) -> str:
    """Plain-words label for one platform-chrome rectangle.

    Rects are figure fractions (left, bottom, width, height),
    bottom-left origin. The label names the platform UI that lives
    there, so the guidance reads like a person, not coordinates.
    """
    left, bottom, width, height = (float(v) for v in rect)
    top = bottom + height
    spans_width = left < 0.05 and left + width > 0.95
    right_side = left > 0.7 and width < 0.35
    if spans_width and top > 0.9:
        return "Top navigation / status bar — profile, tabs, search"
    if right_side:
        return "Right action rail — like, comment, share, follow buttons"
    if spans_width and bottom < 0.05:
        return "Bottom captions / channel name / progress bar"
    return (f"Platform UI overlay "
            f"({left:.0%}, {bottom:.0%} → {left + width:.0%}, "
            f"{bottom + height:.0%})")


def _safe_zone_schematic(plat) -> str:
    """HTML schematic: canvas outline with unsafe zones shaded.

    The div keeps the platform's aspect ratio; unsafe rects are drawn
    with CSS ``bottom`` positioning, matching the rects' bottom-left
    origin. Pure presentation — geometry comes from the engine.
    """
    disp_w = 200
    disp_h = max(60, round(disp_w * plat.height / plat.width))
    bands = []
    for rect in plat.unsafe:
        left, bottom, width, height = (float(v) for v in rect)
        bands.append(
            f'<div style="position:absolute;'
            f'left:{left * 100:.1f}%;bottom:{bottom * 100:.1f}%;'
            f'width:{width * 100:.1f}%;height:{height * 100:.1f}%;'
            f'background:rgba(255,80,80,0.45);'
            f'border:1px solid rgba(255,80,80,0.8);"></div>')
    return (
        f'<div style="position:relative;width:{disp_w}px;height:{disp_h}px;'
        f'background:#1e1e1e;border:2px solid #888;border-radius:8px;'
        f'margin:8px 0;">'
        + "".join(bands) +
        f'<div style="position:absolute;inset:0;display:flex;'
        f'align-items:center;justify-content:center;'
        f'color:#aaa;font-size:12px;">safe area</div></div>')


def _platform_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Step 2: platform / aspect-ratio picker + safe-zone guidance.

    Uses the survey-layout peer when installed; without it, only the
    legacy 1080×1920 layout is offered and no safe-zone support is
    claimed. The choice is stored in ``st.session_state["platform"]``
    and read by the run step and the batch snapshot.
    """
    st.subheader("2 · Platform")
    status = statuses["survey-layout"]
    layout = status.module if status.installed else None
    if layout is None:
        st.info(
            "Platform layouts need the survey-layout engine — install "
            f"it (`{status.pip_command}`) to unlock TikTok, Instagram "
            "Reels, YouTube Shorts, X portrait, square, and widescreen. "
            "The legacy 1080×1920 layout is used in the meantime; "
            "safe-zone guidance is not shown without the engine.")
        st.session_state["platform"] = "legacy"
        return

    keys = list(layout.list_platforms())

    def _label(key: str) -> str:
        plat = layout.get_platform(key)
        return f"{plat.label} — {plat.width}×{plat.height} ({plat.aspect})"

    current = st.session_state.get("platform") or "tiktok"
    choice = st.selectbox(
        "Platform",
        options=keys,
        format_func=_label,
        index=keys.index(current) if current in keys else 0,
        help="Frame dimensions follow the platform. Title, map, chart, "
             "caption, and footer are placed clear of the platform's "
             "measured interface chrome (red zones below).",
        key="platform_select",
    )
    st.session_state["platform"] = choice

    viz_status = statuses["survey-viz"]
    if not _version_ok(viz_status, MIN_VIZ_CANVAS):
        st.warning(_upgrade_hint(viz_status, "0.18.0",
                                 "Platform aspect ratios") +
                   " The legacy layout is used until then.")

    plat = layout.get_platform(choice)
    st.markdown("**Safe zones** — keep content out of the red:",
                help=None)
    st.markdown(_safe_zone_schematic(plat), unsafe_allow_html=True)
    for rect in plat.unsafe:
        st.caption("🔴 " + _describe_unsafe(rect))
    if plat.notes:
        st.caption(plat.notes)
    st.caption(
        "Chrome measurements are community-measured approximations, "
        "not official platform specs — platforms change their UI; "
        "the engine versions them as data.")


def _style_preset_section(statuses: Dict[str, peers.PeerStatus],
                          spec_dict: Dict[str, Any], rev: int) -> None:
    """Style presets (survey-style peer) at the top of the Aesthetics step.

    Applying a preset rewrites the spec's style/title/caption/underlay
    and the colormap in one click; the manual controls below then show
    the preset's values for further tweaking. A preset is a starting
    point, not a lock: pressing "Apply aesthetics" afterwards with
    manual edits records the run as hand-tuned (style_preset=None).
    """
    status = statuses.get("survey-style")
    style_peer = status.module if status is not None and status.installed else None
    if style_peer is None:
        pip_cmd = (status.pip_command if status is not None
                   else "pip install git+https://github.com/crieck2010/survey-style.git")
        st.info(
            "Style presets need the survey-style engine — install it "
            f"(`{pip_cmd}`) to unlock one-click looks "
            "(Midnight Ocean, Storm Chaser, Field Notes, Creator brand "
            "kit, ...). The manual controls below work without it.")
        return

    names = styling.preset_names(style_peer)
    if not names:  # pragma: no cover - the peer always ships built-ins
        return
    default = (st.session_state.get("style_preset")
               or styling.suggested_preset(style_peer,
                                           spec_dict.get("variable")))
    choice = st.selectbox(
        "Style preset",
        options=names,
        format_func=lambda n: styling.preset_label(style_peer, n),
        index=names.index(default) if default in names else 0,
        key="aes_preset_select",
        help="One-click looks: base style, per-variable colormap, title "
             "and footer templates, underlay — applied to the spec below. "
             "You can still tweak every control afterwards.",
    )
    channel = None
    if styling.preset_needs_channel(style_peer, choice):
        channel = st.text_input(
            "Channel name",
            value=st.session_state.get("channel_name") or "",
            key="aes_preset_channel",
            help="Used by the preset's footer, e.g. © Your Channel.",
        )
        st.session_state["channel_name"] = channel
    if st.button("Apply preset", type="secondary",
                 key="aes_preset_apply",
                 help="Fills the style, colormap, title, caption, and "
                      "underlay controls below with this preset's values."):
        try:
            new_spec, cmap = styling.apply_preset(
                style_peer, spec_dict, choice, channel=channel)
        except ValueError as exc:
            st.error(f"That preset did not apply: {exc}")
            return
        st.session_state["spec_dict"] = new_spec
        st.session_state["cmap"] = cmap
        st.session_state["style_preset"] = choice
        st.rerun()
    current = st.session_state.get("style_preset")
    if current:
        st.caption(f"Preset in effect: **{current}** — tweak the controls "
                   "below freely, or pick another preset. Manual edits "
                   "are recorded as hand-tuned.")


def _aesthetics_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Step 3: style / title / caption / underlay / colormap controls.

    Reads the parsed ``spec_dict`` from session state, lets the user
    tweak the aesthetics, and writes the validated result back on
    "Apply aesthetics". Widget keys embed a hash of the current spec
    so a re-parse always resets the controls to the new spec's values.
    """
    st.subheader("3 · Aesthetics")
    spec_dict: Optional[Dict[str, Any]] = st.session_state.get("spec_dict")
    if not spec_dict:
        st.info("Parse a description first (step 1).")
        return

    status = statuses["survey-viz"]
    viz = status.module if status.installed else None
    rev = abs(hash(json.dumps(spec_dict, sort_keys=True, default=str)))

    _style_preset_section(statuses, spec_dict, rev)

    style = st.selectbox(
        "Style",
        options=["reel-dark", "light"],
        format_func=lambda v: {"reel-dark": "Dark", "light": "Light"}[v],
        index=["reel-dark", "light"].index(
            spec_dict.get("style") or "reel-dark"),
        key=f"aes_style_{rev}",
        help="Dark: near-black background, turbo default colormap. "
             "Light: white background, viridis default colormap.",
    )
    title = st.text_input(
        "Title",
        value=spec_dict.get("title") or "",
        key=f"aes_title_{rev}",
        help="Burned into the top of every frame.",
    )
    caption = st.text_input(
        "Footer caption (optional)",
        value=spec_dict.get("caption") or "",
        key=f"aes_caption_{rev}",
        help="A custom line prepended to the frame footer, e.g. your "
             "channel name. Per-renderer honesty wording (such as the "
             "earthquake catalog's “observed events — not a forecast”) "
             "is always kept.",
    )
    underlay = st.checkbox(
        "Basemap underlay (GEBCO tint + Natural Earth coastlines)",
        value=bool(spec_dict.get("underlay", True)),
        key=f"aes_underlay_{rev}",
        help="Drawn beneath the data map wherever the variable is NaN. "
             "For earthquakes this is the topographic context.",
    )

    # Colormap: needs survey-viz >= 0.15.0 (viz.CURATED_CMAPS).
    curated = list(getattr(viz, "CURATED_CMAPS", None) or [])
    categorical = spec_dict.get("variable") in (
        "storm-tracks", "streamflow", "earthquakes")
    cmap: Optional[str] = None
    if not curated:
        st.info("Colormap choices need survey-viz ≥ 0.15.0 — "
                f"upgrade it (`{status.pip_command}`) to unlock them. "
                "The variable default is used in the meantime.")
        st.session_state["cmap"] = None
    elif categorical:
        st.info("Colormap does not apply here: "
                f"`{spec_dict.get('variable')}` uses fixed scientific "
                "colors (Saffir-Simpson / WaterWatch / depth bins), so the "
                "variable default is kept.")
        st.session_state["cmap"] = None
    else:
        auto = "Automatic (variable default)"
        options = [auto] + curated
        current = st.session_state.get("cmap")
        choice = st.selectbox(
            "Colormap",
            options=options,
            index=options.index(current) if current in options else 0,
            key=f"aes_cmap_{rev}",
            help="Recolors continuous data maps only (SST, chlorophyll, "
                 "precipitation, night lights, ...).",
        )
        cmap = None if choice == auto else choice
        st.session_state["cmap"] = cmap

    _copy_look_section(viz, spec_dict, categorical, rev, status)

    # Story captions: needs survey-viz >= 0.17.0 (viz.insights).
    if not _version_ok(status, MIN_VIZ_STORY):
        st.info(_upgrade_hint(status, "0.17.0",
                              "Data-driven story captions") +
                " The reel renders without captions in the meantime.")
        st.session_state["story_captions"] = False
    elif categorical:
        st.info("Story captions do not apply here: "
                f"`{spec_dict.get('variable')}` uses fixed scientific "
                "encodings (storm tracks / streamgages / earthquakes), "
                "so no captions are generated for it.")
        st.session_state["story_captions"] = False
    else:
        story_captions = st.checkbox(
            "Data-driven story captions",
            value=bool(st.session_state.get("story_captions")),
            key=f"aes_story_{rev}",
            help="Burns data-driven captions onto the frames (peak "
                 "values and significant trends in the rendered region). "
                 "Captions describe only what the reel shows — see the "
                 "story-captions honesty rules in the survey-viz docs.",
        )
        st.session_state["story_captions"] = story_captions

    st.divider()
    st.markdown("**mapped.earth aesthetic preset**")
    _aesthetic_preset_section(status, spec_dict, rev)

    if not st.button(
        "Apply aesthetics",
        type="secondary",
        help="Applies the title, style, caption, and underlay to the spec "
             "that the run below uses. The colormap applies immediately — "
             "no need to press this for it.",
    ):
        return

    new_dict = dict(spec_dict)
    new_dict["title"] = title
    new_dict["style"] = style
    new_dict["underlay"] = underlay
    new_dict["caption"] = caption.strip() or None
    # Older survey-viz peers (< 0.15.0) have no caption field; drop it
    # rather than crashing their from_dict.
    if viz is not None:
        if "caption" not in inspect.signature(viz.VizSpec).parameters:
            new_dict.pop("caption", None)
    try:
        viz.VizSpec.from_dict(new_dict)  # validate before storing
    except (TypeError, ValueError) as exc:
        st.error(f"Those aesthetics did not validate: {exc}")
        return
    st.session_state["spec_dict"] = new_dict
    st.session_state["style_preset"] = None  # hand-tuned from here on
    st.success("Aesthetics applied — the spec below is what will run.")
    st.json(new_dict)


def _aesthetic_preset_section(status, spec_dict: Dict[str, Any], rev: int) -> None:
    """mapped.earth preset picker — inside the Aesthetics step.

    Needs survey-viz >= 0.22.0; older peers get the upgrade hint and
    the whole section stays inert (session state keeps the legacy
    defaults). Every auto choice is user-overridable: preset, rotation
    (auto/manual/off), subtitle, watermark, and the honesty line.
    """
    viz = status.module if status.installed else None
    if not _version_ok(status, MIN_VIZ_PRESET):
        st.info(_upgrade_hint(status, "0.22.0",
                              "mapped.earth aesthetic presets") +
                " The reel renders with the legacy look in the meantime.")
        st.session_state["aesthetic_preset"] = None
        st.session_state["aes_rotation"] = None
        st.session_state["watermark"] = None
        st.session_state["aes_subtitle"] = None
        st.session_state["aes_encoding_line"] = True
        st.session_state["aes_basemap"] = None
        st.session_state["aes_strand_count"] = None
        st.session_state["aes_strand_linewidth"] = None
        st.session_state["aes_landmask"] = None
        return

    presets = list(getattr(viz, "AESTHETIC_PRESETS",
                           ("dark_flow", "dark_glow", "paper_prism")))
    var_map = getattr(viz, "PRESET_VARIABLES", {}) or {}
    variable = spec_dict.get("variable")

    labels = {
        "dark_flow": "Dark flow — LIC current/wind streaks on black",
        "dark_glow": "Dark glow — event glow with bloom on black",
        "paper_prism": "Paper prism — 3D extrusion on warm paper",
        "dark_strands": "Dark strands — advected particle trails on black",
    }
    options = [None] + presets
    current = st.session_state.get("aesthetic_preset")
    choice = st.selectbox(
        "Aesthetic preset",
        options=options,
        format_func=lambda v: ("Off (legacy renderer)" if v is None
                               else labels.get(v, v)),
        index=options.index(current) if current in options else 0,
        key=f"aes_preset_{rev}",
        help="Renders through the survey-aesthetics engine: chrome-free "
             "frames, editorial typography, custom legends, fixed "
             "reel-wide scales. Off keeps the current renderer.",
    )
    st.session_state["aesthetic_preset"] = choice

    if choice is not None:
        allowed = var_map.get(choice)
        if allowed is not None and variable not in allowed:
            st.warning(
                f"`{choice}` renders {', '.join(allowed)} — "
                f"`{variable}` has no compatible data and the run will "
                f"fail fast with the reason. Pick another preset or turn "
                f"it off.")
        elif choice == "paper_prism" and variable in (
                "storm-tracks", "streamflow", "earthquakes"):
            st.warning(
                f"`paper_prism` needs a gridded variable — `{variable}` "
                f"is event/track data and the run will fail fast. Pick "
                f"another preset or turn it off.")
        if (st.session_state.get("platform")
                not in (None, "legacy", "")):
            st.warning(
                "Aesthetic presets use their own layout grammar — "
                "platform safe-zone canvases do not apply. Set Platform "
                "back to Legacy to run with a preset.")

    # Rotation: off / auto / manual degrees.
    rot_mode = st.radio(
        "Frame rotation",
        options=["Off", "Auto", "Manual degrees"],
        index={"Off": 0, "Auto": 1, "Manual": 2}.get(
            st.session_state.get("aes_rot_mode"), 0),
        key=f"aes_rotmode_{rev}",
        help="Auto rotates elongated regions (e.g. Lake Ontario ~90°) "
             "to maximize zoom; the north arrow rotates with the map.",
        horizontal=True,
    )
    st.session_state["aes_rot_mode"] = rot_mode
    if rot_mode == "Manual degrees":
        deg = st.slider("Rotation (degrees counter-clockwise)",
                        -90.0, 90.0,
                        float(st.session_state.get("aes_rot_deg") or 0.0),
                        key=f"aes_rotdeg_{rev}")
        st.session_state["aes_rot_deg"] = deg
        st.session_state["aes_rotation"] = deg
    elif rot_mode == "Auto":
        st.session_state["aes_rotation"] = "auto"
    else:
        st.session_state["aes_rotation"] = None

    subtitle = st.text_input(
        "Subtitle (preset title block)",
        value=st.session_state.get("aes_subtitle") or "",
        key=f"aes_subtitle_{rev}",
        help="Burned under the serif title. Empty = the automatic "
             "time-window label (e.g. “16–23 September 2026”).",
    )
    st.session_state["aes_subtitle"] = subtitle.strip() or None

    wm_on = st.checkbox(
        "Watermark",
        value=bool(st.session_state.get("watermark")),
        key=f"aes_wm_on_{rev}",
        help="Burns your brand handle + © + data-source line into the "
             "preset furniture. Off by default — your call.",
    )
    if wm_on:
        wm_text = st.text_input(
            "Brand handle",
            value=(st.session_state.get("watermark") or ""),
            key=f"aes_wm_text_{rev}",
            help="Shown bottom-right, e.g. your channel handle.",
        ).strip()
        st.session_state["watermark"] = wm_text or None
        if not wm_text:
            st.info("Type a brand handle — the watermark stays off "
                    "until you do.")
    else:
        st.session_state["watermark"] = None

    enc = st.checkbox(
        "Show the encoding honesty line",
        value=bool(st.session_state.get("aes_encoding_line", True)),
        key=f"aes_enc_{rev}",
        help="The preset's statement of what is encoded, e.g. "
             "“BRIGHTNESS = SPEED”.",
    )
    st.session_state["aes_encoding_line"] = enc

    _place_labels_section(status, spec_dict, rev)
    _basemap_strands_section(status, rev)


def _place_labels_section(status, spec_dict: Dict[str, Any], rev: int) -> None:
    """Place labels — inside the Aesthetics step's preset section.

    Off / Auto / Custom. Needs survey-viz >= 0.23.0; older peers get
    the upgrade hint and the controls stay inert (nothing is passed, so
    the peer default applies and the run never crashes). Auto at the
    default slider values likewise passes nothing — the survey-viz
    default (auto labels when a preset is active) applies, which keeps
    older peers working and the choice version-keyed in the cache
    fingerprint. Explicit Off / tuned Auto / Custom values are passed
    through and need survey-viz >= 0.23.0 at run time (PeerTooOldError
    with the upgrade command otherwise).
    """
    st.markdown("**Place labels**")
    if not _version_ok(status, MIN_VIZ_LABELS):
        st.info(_upgrade_hint(status, "0.23.0", "Place labels") +
                " The reel renders without place labels in the meantime.")
        st.session_state["aes_place_labels"] = None
        st.session_state["aes_max_labels"] = None
        st.session_state["aes_min_population"] = None
        return

    if st.session_state.get("aesthetic_preset") is None:
        st.caption("Place labels are a preset-path feature — pick an "
                   "aesthetic preset above to use them.")

    label_mode = st.radio(
        "Place labels",
        options=["Off", "Auto", "Custom"],
        index={"Off": 0, "Auto": 1, "Custom": 2}.get(
            st.session_state.get("aes_label_mode"), 1),
        key=f"aes_labelmode_{rev}",
        help="Auto fetches city/town labels for the region from the "
             "survey-gazetteer peer, once per reel. Custom lets you name "
             "the places yourself. Off draws no labels.",
        horizontal=True,
    )
    st.session_state["aes_label_mode"] = label_mode

    if label_mode == "Off":
        st.session_state["aes_place_labels"] = False
        st.session_state["aes_max_labels"] = None
        st.session_state["aes_min_population"] = None
    elif label_mode == "Auto":
        max_labels = st.slider(
            "Max labels", 1, 20,
            int(st.session_state.get("aes_max_labels_ui") or 8),
            key=f"aes_maxlabels_{rev}",
            help="Cap on automatic place labels (survey-viz default 8).",
        )
        st.session_state["aes_max_labels_ui"] = max_labels
        min_population = st.number_input(
            "Min population", min_value=0, step=1000,
            value=int(st.session_state.get("aes_min_population_ui") or 0),
            key=f"aes_minpop_{rev}",
            help="Only places at least this populous are labeled "
                 "(survey-viz default 0).",
        )
        st.session_state["aes_min_population_ui"] = int(min_population)
        if max_labels != 8 or int(min_population) != 0:
            st.session_state["aes_place_labels"] = True
            st.session_state["aes_max_labels"] = int(max_labels)
            st.session_state["aes_min_population"] = int(min_population)
        else:
            # Peer default: pass nothing (see docstring).
            st.session_state["aes_place_labels"] = None
            st.session_state["aes_max_labels"] = None
            st.session_state["aes_min_population"] = None
    else:
        _custom_place_labels_section(rev)


def _basemap_strands_section(status, rev: int) -> None:
    """Basemap style + strand controls — inside the Aesthetics step's preset section.

    Basemap: Preset default / Void black / No basemap / Subtle land.
    Strands: trail count + line width sliders, shown when the
    ``dark_strands`` preset is picked (they only affect that preset).
    Needs survey-viz >= 0.24.0; older peers get the upgrade hint and the
    controls stay inert (nothing is passed, so the peer default applies
    and the run never crashes). Sliders at their defaults likewise pass
    nothing — the survey-viz defaults (3000 trails, 1.4 pt) apply, which
    keeps the choice version-keyed in the cache fingerprint. Explicit
    choices need survey-viz >= 0.24.0 at run time (PeerTooOldError with
    the upgrade command otherwise).
    """
    st.markdown("**Basemap & strands**")
    if not _version_ok(status, MIN_VIZ_STRANDS):
        st.info(_upgrade_hint(status, "0.24.0",
                              "Basemap styles and strand controls") +
                " The reel renders with the preset's default basemap in "
                "the meantime.")
        st.session_state["aes_basemap"] = None
        st.session_state["aes_strand_count"] = None
        st.session_state["aes_strand_linewidth"] = None
        st.session_state["aes_landmask"] = None
        return

    if st.session_state.get("aesthetic_preset") is None:
        st.caption("Basemap styles and strand controls are preset-path "
                   "features — pick an aesthetic preset above to use them.")

    basemap_labels = {
        None: "Preset default",
        "void_black": "Void black — pure black land",
        "no_basemap": "No basemap — geography emerges from the data",
        "subtle_land": "Subtle land — faint land fill",
    }
    basemap_options = [None, "void_black", "no_basemap", "subtle_land"]
    current = st.session_state.get("aes_basemap")
    basemap = st.selectbox(
        "Basemap style",
        options=basemap_options,
        format_func=lambda v: basemap_labels.get(v, v),
        index=(basemap_options.index(current)
               if current in basemap_options else 0),
        key=f"aes_basemap_{rev}",
        help="How land is drawn under the data. Preset default keeps "
             "the preset's bundled look.",
    )
    st.session_state["aes_basemap"] = basemap

    if st.session_state.get("aesthetic_preset") == "dark_strands":
        count = st.slider(
            "Strand count",
            500, 10000,
            int(st.session_state.get("aes_strand_count") or 3000),
            step=100,
            key=f"aes_strand_count_{rev}",
            help="Particle trails per frame. More strands = denser "
                 "texture, slower renders. 3000 is the survey-viz default.",
        )
        st.session_state["aes_strand_count"] = (
            None if count == 3000 else count)
        width = st.slider(
            "Strand line width",
            0.5, 3.0,
            float(st.session_state.get("aes_strand_linewidth") or 1.4),
            step=0.1,
            key=f"aes_strand_linewidth_{rev}",
            help="Strand width in points. 1.4 is the survey-viz default.",
        )
        st.session_state["aes_strand_linewidth"] = (
            None if abs(width - 1.4) < 1e-9 else width)
        clip = st.checkbox(
            "Clip strands to land",
            value=bool(st.session_state.get("aes_landmask", True)),
            key=f"aes_landmask_{rev}",
            help="Clip wind strands to the Natural Earth land polygons "
                 "so the continent emerges from the strands (the "
                 "warming.watch look). Uncheck to draw strands "
                 "everywhere.",
        )
        # Default (checked) leaves the peer default: nothing is
        # forwarded, so older peers keep working untouched.
        st.session_state["aes_landmask"] = None if clip else False
    else:
        st.caption("Strand controls apply to the Dark strands preset.")
        st.session_state["aes_strand_count"] = None
        st.session_state["aes_strand_linewidth"] = None
        st.session_state["aes_landmask"] = None


def _custom_place_labels_section(rev: int) -> None:
    """Custom place labels: one name per line, gazetteer search, and a
    per-name disambiguation picker (city + region + country shown)."""
    try:
        import gazetteer as _gz
    except ImportError:
        _gz = None
    if _gz is None:
        st.info("Custom place search needs the survey-gazetteer peer — "
                "run `Update reel-studio.bat` to install it. No custom "
                "labels will be drawn in the meantime.")
        st.session_state["aes_place_labels"] = None
        st.session_state["aes_max_labels"] = None
        st.session_state["aes_min_population"] = None
        return
    names_text = st.text_area(
        "Place names (one per line)",
        value=st.session_state.get("aes_custom_names") or "",
        key=f"aes_customnames_{rev}",
        help="Each name is looked up in the gazetteer; pick the right "
             "match below — city, region, and country are shown, so "
             "“Rochester, New York” wins over “Rochester, Minnesota”.",
    )
    st.session_state["aes_custom_names"] = names_text
    names = [ln.strip() for ln in names_text.splitlines() if ln.strip()]
    chosen: List[Dict[str, Any]] = []
    for i, name in enumerate(names):
        try:
            results = _gz.search(name, limit=5)
        except Exception as exc:  # never break the UI on a search hiccup
            st.warning(f"Search for {name!r} failed: {exc}")
            continue
        if not results:
            st.warning(f"No gazetteer match for {name!r} — skipped.")
            continue
        options = [
            f"{r['text']} — {r.get('adm1') or '?'}, "
            f"{r.get('adm0') or '?'} ({r.get('kind')}, "
            f"pop {(r.get('pop') or 0):,})"
            for r in results
        ]
        pick = st.selectbox(
            f"Match for {name!r}",
            options=range(len(results)),
            format_func=lambda k, _o=options: _o[k],
            key=f"aes_custompick_{rev}_{i}",
            help="Pick the right place — region and country included.",
        )
        chosen.append(results[pick])
    if chosen:
        st.caption(f"{len(chosen)} custom label(s) will be drawn.")
    st.session_state["aes_place_labels"] = chosen or None
    st.session_state["aes_max_labels"] = None
    st.session_state["aes_min_population"] = None


def _copy_look_section(viz, spec_dict: Dict[str, Any], categorical: bool,
                       rev: int, status) -> None:
    """'Copy the look of a reel' — inside the Aesthetics step.

    Analyzes a reel URL's thumbnail (or an uploaded screenshot) with
    ``viz.suggest_aesthetic`` and offers to apply the measured style +
    colormap. Needs survey-viz >= 0.16.0; older peers get the upgrade
    hint instead of a crash.
    """
    st.divider()
    st.markdown("**Copy the look of a reel**")
    if viz is None or not hasattr(viz, "suggest_aesthetic"):
        st.info("Copying a reel's look needs survey-viz ≥ 0.16.0 — "
                f"upgrade it (`{status.pip_command}`) to unlock it.")
        return
    st.caption(
        "Paste a reel link (Instagram, TikTok, YouTube, ...) or upload a "
        "screenshot. The app reads the reference's *color mood* — overall "
        "brightness and dominant hues — and suggests the closest dark/light "
        "style and colormap. Fonts, layouts, and transitions can't be read "
        "from a thumbnail and are not copied.")
    url = st.text_input(
        "Reel URL",
        key=f"look_url_{rev}",
        help="The page's preview thumbnail is analyzed; the video itself "
             "is never downloaded.",
    )
    upload = st.file_uploader(
        "…or upload a screenshot",
        type=["png", "jpg", "jpeg", "webp"],
        key=f"look_up_{rev}",
    )
    if st.button("Analyze look", key=f"look_go_{rev}"):
        data: Optional[bytes] = None
        origin = ""
        try:
            if upload is not None:
                data = upload.read()
                origin = "upload"
            elif url and url.strip():
                origin = url.strip()
                data = viz.fetch_image_bytes(origin)
            else:
                st.warning("Paste a reel URL or upload a screenshot first.")
                return
            profile = viz.analyze_image(data, source=origin)
        except viz.AestheticError as exc:
            st.error(f"Could not read that reference: {exc}")
            return
        except Exception as exc:  # pragma: no cover - defensive
            st.error(f"Could not read that reference: {exc}")
            return
        st.session_state["look_profile"] = profile.to_dict()
        st.session_state["look_bytes"] = data

    profile_dict = st.session_state.get("look_profile")
    if not profile_dict:
        return
    look_bytes = st.session_state.get("look_bytes")
    if look_bytes:
        st.image(look_bytes, caption="Reference thumbnail")
    swatches = "".join(
        f'<span title="{color}" style="display:inline-block;width:34px;'
        f'height:34px;background:{color};border-radius:6px;'
        'margin-right:6px;border:1px solid #888;"></span>'
        for color in profile_dict["palette"])
    st.markdown(f"Measured palette:<br>{swatches}", unsafe_allow_html=True)
    style_label = {"reel-dark": "Dark", "light": "Light"}[profile_dict["style"]]
    cmap_label = profile_dict["colormap"] or "— (keep the variable default)"
    st.write(f"**Suggested style:** {style_label}  ·  "
             f"**Suggested colormap:** {cmap_label}")
    for note in profile_dict["notes"]:
        st.caption(note)
    if not st.button("Apply this look", key=f"look_apply_{rev}",
                     type="primary"):
        return
    new_dict = dict(st.session_state.get("spec_dict") or spec_dict)
    new_dict["style"] = profile_dict["style"]
    st.session_state["spec_dict"] = new_dict
    st.session_state["style_preset"] = None  # copied look, not a preset
    if profile_dict["colormap"] and not categorical:
        st.session_state["cmap"] = profile_dict["colormap"]
    elif profile_dict["colormap"] and categorical:
        st.warning("The suggested colormap does not apply here: "
                   f"`{new_dict.get('variable')}` uses fixed scientific "
                   "colors, so the variable default is kept. The dark/light "
                   "style was still applied.")
    st.success("Look applied — the style (and colormap, where it applies) "
               "are set above.")
    st.rerun()


def _make_assist_fn(statuses: Dict[str, peers.PeerStatus]):
    """Build the one-shot LLM assist callable, or None when disabled."""
    viz_status = statuses["survey-viz"]
    if not viz_status.installed or not os.environ.get("LLM_API_KEY"):
        return None

    VizSpec = viz_status.module.VizSpec

    def assist(text: str):
        # Returns the dict; VizSpec.from_dict validates. On failure the
        # caller (parse_with_fallback) re-raises the ORIGINAL parse error.
        spec_dict = llm_assist.assist_from_env(text)
        if spec_dict is None:  # key vanished between check and call
            raise llm_assist.LLMAssistError("LLM_API_KEY is not set")
        return VizSpec.from_dict(spec_dict)

    return assist


def _motion_audio_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Step 4: cinematic camera motion + audio track.

    Motion settings live in ``st.session_state["motion"]`` (a plain
    dict of MotionSpec fields, or ``{}`` when disabled) so the refine
    section and the batch queue can read and merge them.
    """
    st.subheader("4 · Cinematic motion & audio")
    status = statuses["survey-animate"]
    if not _version_ok(status, MIN_ANIMATE_MOTION):
        st.info(_upgrade_hint(status, "0.2.0",
                              "Cinematic motion and audio muxing") +
                " The reel encodes with the peer default in the meantime.")
        return

    motion = dict(st.session_state.get("motion") or {})
    enabled = st.checkbox(
        "Enable cinematic camera motion",
        value=bool(motion),
        help="Zoom / pan the camera while the reel plays, with smooth "
             "crossfade transitions between frames. Applied at encode "
             "time by survey-animate — the data frames are unchanged.",
    )
    with st.expander("Camera settings", expanded=bool(motion)):
        zoom = st.selectbox(
            "Zoom",
            options=list(ZOOM_MODES),
            index=list(ZOOM_MODES).index(motion.get("zoom", "off")),
            format_func=lambda v: {"off": "Off", "in": "Zoom in",
                                   "out": "Zoom out"}[v],
            help="Zoom in or out over the whole reel.",
        )
        zoom_speed = st.slider(
            "Zoom speed", 0.0, 1.0,
            float(motion.get("zoom_speed", 0.4)), 0.05,
            help="0 = barely moves, 1 = up to 1.6× total zoom.",
        )
        pan = st.selectbox(
            "Pan direction",
            options=list(PAN_DIRECTIONS),
            index=list(PAN_DIRECTIONS).index(motion.get("pan", "off")),
            format_func=lambda v: PAN_LABELS[v],
            help="Drift the camera across the map during the reel.",
        )
        pan_speed = st.slider(
            "Pan speed", 0.0, 1.0,
            float(motion.get("pan_speed", 0.35)), 0.05,
            help="0 = barely moves, 1 = up to 30% of the viewport.",
        )
        smooth = st.checkbox(
            "Smooth crossfade transitions",
            value=bool(motion.get("smooth", True)),
            help="Blend neighboring frames so motion and data changes "
                 "play smoothly instead of stepping.",
        )
        smooth_steps = st.slider(
            "Crossfade steps", 1, 10,
            int(motion.get("smooth_steps", 3)),
            help="Interpolated frames inserted between real frames.",
        )
    if st.button("Save motion settings", type="secondary"):
        if enabled:
            st.session_state["motion"] = {
                "zoom": zoom, "zoom_speed": zoom_speed,
                "pan": pan, "pan_speed": pan_speed,
                "smooth": smooth, "smooth_steps": smooth_steps,
            }
            st.success("Motion settings saved — they apply to the next run.")
        else:
            st.session_state["motion"] = {}
            st.success("Camera motion off — the reel encodes plain.")
    elif motion:
        st.caption("Current: "
                   f"zoom={motion.get('zoom')}, pan={motion.get('pan')}, "
                   f"smooth={motion.get('smooth')} "
                   "(refinements in step 6 can change these).")

    st.divider()
    st.markdown("**Audio track** (optional)")
    st.caption("Attach your own audio file — it is muxed under the reel "
               "and trimmed to the video length. Only MP4 is produced, "
               "so it is always AAC-encoded. Use audio you own or have "
               "the rights to.")
    uploaded = st.file_uploader(
        "Choose an audio file", type=AUDIO_TYPES, key="audio_upload")
    if uploaded is not None:
        audio_dir = os.path.join(tempfile.gettempdir(), "reel-studio-audio")
        os.makedirs(audio_dir, exist_ok=True)
        audio_path = os.path.join(audio_dir, uploaded.name)
        with open(audio_path, "wb") as fh:
            fh.write(uploaded.getbuffer())
        st.session_state["audio_path"] = audio_path
        st.success(f"Audio attached: `{uploaded.name}`")
    current_audio = st.session_state.get("audio_path")
    if current_audio and uploaded is None:
        st.caption(f"Attached: `{os.path.basename(current_audio)}`")
        if st.button("Remove audio", key="audio_remove"):
            st.session_state["audio_path"] = None
            try:
                os.remove(current_audio)
            except OSError:
                pass


def _batch_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Step 5: batch queue — several descriptions, one unattended run."""
    st.subheader("5 · Batch queue")
    st.caption("Queue several descriptions and generate them one after "
               "another, unattended. Each job snapshots your current "
               "settings (motion, audio, captions, colormap, platform, style "
               "preset) and gets its "
               "own output folder — a failed job never loses completed "
               "ones.")

    text = st.text_area(
        "Descriptions (one per line)", height=90, key="batch_input",
        help="Each line becomes one job, parsed exactly like step 1.",
    )
    if st.button("Add to queue", type="secondary"):
        lines = [ln.strip() for ln in (text or "").splitlines()
                 if ln.strip()]
        if not lines:
            st.warning("Write at least one description first.")
        else:
            jobs = list(st.session_state.get("batch_jobs") or [])
            settings = _current_run_settings()
            for line in lines:
                jobs.append({"description": line,
                             "settings": dict(settings),
                             "status": "queued"})
            st.session_state["batch_jobs"] = jobs
            st.success(f"Added {len(lines)} job(s) to the queue.")

    jobs = list(st.session_state.get("batch_jobs") or [])
    remove_idx: Optional[int] = None
    for i, job in enumerate(jobs):
        c1, c2, c3 = st.columns([7, 2, 1])
        c1.write(job["description"])
        c2.write(f"`{job['status']}`")
        if job["status"] == "queued" and c3.button(
                "✕", key=f"batch_rm_{i}", help="Remove this job"):
            remove_idx = i
    if remove_idx is not None:
        jobs.pop(remove_idx)
        st.session_state["batch_jobs"] = jobs

    queued = [j for j in jobs if j["status"] == "queued"]
    col_a, col_b = st.columns(2)
    run_clicked = col_a.button(
        "Run batch", type="primary",
        disabled=not queued,
        help="Generate every queued job, one after another.")
    if col_b.button("Clear finished", type="secondary",
                    disabled=not any(j["status"] in ("done", "failed",
                                                     "skipped")
                                     for j in jobs)):
        st.session_state["batch_jobs"] = [
            j for j in (st.session_state.get("batch_jobs") or [])
            if j["status"] == "queued"]

    if run_clicked:
        _run_batch(statuses, queued)


def _run_batch(statuses: Dict[str, peers.PeerStatus],
               queued: list) -> None:
    """Execute the queued jobs sequentially with per-job isolation."""
    try:
        wired = peers.wire_peers(statuses)
    except peers.MissingPeerError as exc:
        st.error(str(exc))
        return

    batch_jobs = [batch.BatchJob(description=j["description"],
                                 settings=j["settings"]) for j in queued]
    out_root = tempfile.mkdtemp(prefix="reel-studio-batch-")
    progress_bar = st.progress(0.0, text="Starting batch…")
    status_box = st.status("Running batch…", expanded=False)

    def on_progress(idx: int, n: int, frac: float, message: str) -> None:
        overall = (idx + frac) / max(n, 1)
        progress_bar.progress(min(max(overall, 0.0), 1.0), text=message)
        status_box.update(label=message, state="running")

    def parse_fn(text: str) -> Any:
        spec, _used = pipeline.parse_with_fallback(
            text, wired.parse_description, wired.parse_error,
            assist_fn=_make_assist_fn(statuses))
        return spec

    batch.run_batch(batch_jobs, wired, out_root, parse_fn,
                    progress=on_progress,
                    cache=_open_run_cache(statuses))

    # Merge statuses back into the session queue (matched by order).
    jobs = list(st.session_state.get("batch_jobs") or [])
    qi = 0
    for job in jobs:
        if job["status"] != "queued":
            continue
        bj = batch_jobs[qi]
        qi += 1
        job["status"] = bj.status
        job["error"] = bj.error
        job["out_dir"] = bj.out_dir
        if bj.status == "done" and bj.result is not None:
            job["video_path"] = bj.result.video_path
            job["n_frames"] = bj.result.n_frames
    st.session_state["batch_jobs"] = jobs

    done = sum(1 for b in batch_jobs if b.status == "done")
    failed = sum(1 for b in batch_jobs if b.status == "failed")
    status_box.update(label="Batch complete", state="complete")
    progress_bar.progress(1.0, text="Batch complete")
    if failed:
        st.warning(f"Batch finished: {done} done, {failed} failed — "
                   "completed reels are kept, failures are listed below.")
    else:
        st.success(f"Batch finished: {done} reel(s) generated.")

    for b in batch_jobs:
        with st.expander(
                f"{'✅' if b.status == 'done' else '❌'} {b.description}",
                expanded=(b.status != "done")):
            if b.status == "done" and b.result is not None:
                st.video(b.result.video_path)
                with open(b.result.video_path, "rb") as fh:
                    st.download_button(
                        label="Download MP4",
                        data=fh.read(),
                        file_name=f"{b.out_dir.rsplit('/', 1)[-1]}.mp4",
                        mime="video/mp4",
                        key=f"batch_dl_{b.index}",
                    )
            else:
                st.error(b.error or b.status)
                if b.detail:
                    with st.expander("Traceback"):
                        st.code(b.detail)


def _schedule_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Step 6: scheduled generation (survey-schedule peer)."""
    st.subheader("6 · Scheduled generation")
    sched_status = statuses["survey-schedule"]
    if not sched_status.installed:
        st.info(
            "Scheduled generation — unattended reels on a cron, interval, "
            "or one-shot schedule — needs the optional `survey-schedule` "
            "peer. One-off and batch generation keep working without it.")
        st.code(sched_status.pip_command)
        return
    schedx = sched_status.module
    jobs_dir = scheduler.default_jobs_dir()
    st.caption(
        "Put the reel factory on autopilot: a job reuses a description "
        "plus your current run settings on a schedule. "
        f"Jobs live in `{jobs_dir}` (one JSON file each, hand-editable). "
        "A ticker runs them — cron on Linux or Task Scheduler on Windows, "
        "every minute — and every attempt lands in the ledger below. "
        "Honest limit: the PC must be on at run time; a missed run is "
        "skipped unless the job says `run-once`.")

    st.markdown("**New scheduled job**")
    st.session_state.setdefault("sched_expr", "cron:0 6 * * mon")
    name = st.text_input(
        "Job name (lowercase slug)", key="sched_name",
        help="e.g. weekly-sst — becomes the JSON file name.")
    description = st.text_area(
        "Description", key="sched_description",
        value=st.session_state.get("description") or "",
        help="Parsed exactly like step 1 when the job runs.")
    schedule_expr = st.text_input(
        "Schedule", key="sched_expr",
        help="`cron:<minute hour dom month dow>` (e.g. cron:0 6 * * mon), "
             "`every:<n>s|m|h|d|w` (e.g. every:1w), or `once:<ISO datetime>` "
             "(e.g. once:2026-10-01T06:00).")
    col_tz, col_catch, col_ret = st.columns(3)
    timezone = col_tz.text_input(
        "Timezone", value="local", key="sched_tz",
        help="IANA name like America/New_York, or `local` (this machine).")
    catchup = col_catch.selectbox(
        "If a run is missed", ["skip", "run-once"], key="sched_catchup",
        help="`skip`: one ledger entry, never backfilled. `run-once`: "
             "execute once at the next tick, then resume.")
    retries = int(col_ret.number_input(
        "Retries on failure", min_value=0, max_value=5, value=0,
        key="sched_retries"))
    enabled = st.checkbox("Enabled", value=True, key="sched_enabled")

    try:
        st.caption("📅 " + schedx.describe_schedule(schedule_expr))
        schedule_ok = True
    except Exception as exc:
        st.warning(f"Schedule problem: {exc}")
        schedule_ok = False

    settings = _current_run_settings()
    st.caption(
        "Snapshot with this job: motion, audio, story captions, colormap, "
        f"platform (`{settings.get('platform')}`), style preset "
        f"(`{settings.get('style_preset')}`)"
        + (f", derived product (`{settings['derived']['product']}` vs "
           f"{settings['derived']['baseline_start']}–"
           f"{settings['derived']['baseline_end']})"
           if settings.get("derived") else "")
        + ". Strides stay default — "
        "hand-edit the job JSON for `stride_days` / `stride_hours`.")

    if st.button("Create scheduled job", type="secondary",
                 disabled=not schedule_ok):
        if not (name or "").strip():
            st.warning("Give the job a name first.")
        elif not (description or "").strip():
            st.warning("Give the job a description first.")
        else:
            try:
                job = schedx.Job(
                    name=name.strip(), description=description.strip(),
                    schedule=schedule_expr.strip(), settings=settings,
                    enabled=bool(enabled), timezone=timezone.strip(),
                    catchup=catchup, retries=retries)
                schedx.JobStore(jobs_dir).save_job(job)
            except Exception as exc:
                st.error(f"Could not create the job: {exc}")
            else:
                st.success(f"Scheduled job `{job.name}` created — next run "
                           "shown below once the ticker ticks.")
                st.session_state["sched_name"] = ""

    st.divider()
    st.markdown("**Jobs**")
    store = schedx.JobStore(jobs_dir)
    jobs = store.list_jobs()
    if not jobs:
        st.caption("No scheduled jobs yet.")
    for job in jobs:
        nxt = scheduler.next_run_iso(statuses, job) or "—"
        c1, c2, c3, c4, c5 = st.columns([3, 3, 3, 1, 1])
        c1.write(f"**{job.name}**")
        c2.write(f"`{job.schedule}`")
        c3.write(f"next: `{nxt}`")
        if c4.button("⏸️" if job.enabled else "▶️",
                     key=f"sched_toggle_{job.name}",
                     help="Disable" if job.enabled else "Enable"):
            toggled = schedx.job_from_dict(
                {**schedx.job_to_dict(job), "enabled": not job.enabled})
            store.save_job(toggled)
            st.rerun()
        if c5.button("🗑️", key=f"sched_del_{job.name}",
                     help="Delete this job"):
            store.delete_job(job.name)
            st.rerun()

    st.divider()
    st.markdown("**Ticker**")
    st.caption(
        "Run this every minute (Windows Task Scheduler or Linux cron) "
        "from the reel-studio folder:")
    st.code(f"python -m studio.scheduler run-due --jobs-dir \"{jobs_dir}\"")

    st.markdown("**Recent runs**")
    probe = schedx.Runner(store, schedx.null_executor,
                          scheduler.default_out_root())
    records = probe.ledger_tail(10)
    if not records:
        st.caption("Nothing in the ledger yet.")
    for record in records:
        icon = {"ok": "✅", "failed": "❌", "skipped": "⏭️"}.get(
            record.status, "•")
        st.write(f"{icon} `{record.job}` — {record.status} "
                 f"(attempt {record.attempt}) · {record.finished_at}")
        if record.error:
            st.caption(f"error: {record.error}")


def _cache_section(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Render-cache controls — inside the Run step, before the button.

    The survey-cache peer (optional seventh engine) stores rendered
    frame batches and encoded MP4s keyed by fingerprints of their
    inputs, so re-running an unchanged reel skips the render and the
    encode. Everything is transparent: same pixels, less waiting.
    """
    cache_status = statuses.get("survey-cache")
    with st.expander("Render cache (optional)", expanded=False):
        if cache_status is None or not cache_status.installed:
            st.info("Install the render cache to skip re-rendering "
                    "unchanged reels:\n\n"
                    f"`{cache_status.pip_command if cache_status else 'pip install git+https://github.com/crieck2010/survey-cache.git'}`\n\n"
                    "Without it, every run renders and encodes from "
                    "scratch — nothing else changes.")
            return
        st.checkbox("Use render cache for this run", value=True,
                    key="use_cache",
                    help="When on, identical re-runs reuse cached frames "
                         "and the cached MP4 instead of re-rendering.")
        cache = caching.open_cache()
        if cache is None:
            st.caption("Caching is disabled "
                       "(`REEL_STUDIO_CACHE=0` is set).")
            return
        info = caching.describe_stats(cache)
        st.caption(f"Cache: **{info['entries']}** objects, "
                   f"**{info['tags']}** tags, **{info['used_human']}** "
                   f"({info['used_pct']}% of budget) · "
                   f"{info['hits']} hits / {info['misses']} misses")
        st.caption(f"Location: `{caching.default_cache_dir()}` "
                   f"(override with `{caching.CACHE_DIR_ENV}`).")
        if st.button("Clear render cache", key="cache_clear"):
            cache.clear()
            st.success("Render cache cleared.")
            st.rerun()


def _open_run_cache(statuses: Dict[str, peers.PeerStatus]) -> Any:
    """The cache for a run, or None when disabled/unavailable."""
    cache_status = statuses.get("survey-cache")
    if cache_status is None or not cache_status.installed:
        return None
    if not st.session_state.get("use_cache", True):
        return None
    return caching.open_cache()


def _run_derived() -> Optional[Dict[str, Any]]:
    """The derived-product config for this run, or None when disabled.

    Read from the Run-step expander widgets; defaults match
    :func:`studio.pipeline._normalize_derived` so an untouched expander
    still validates.
    """
    if not st.session_state.get("derived_enabled"):
        return None
    return {
        "product": st.session_state.get("derived_product", "anomaly"),
        "baseline_start": (
            st.session_state.get("derived_baseline_start")
            or "1991-01-01").strip(),
        "baseline_end": (
            st.session_state.get("derived_baseline_end")
            or "2020-12-31").strip(),
        "baseline_stride_days": int(st.session_state.get("derived_stride", 30)),
        "window_days": int(st.session_state.get("derived_window", 15)),
        "min_samples": int(st.session_state.get("derived_min_samples", 10)),
        "symmetric_quantile": float(
            st.session_state.get("derived_quantile", 0.99)),
    }


def _derived_section(statuses: Dict[str, peers.PeerStatus]) -> None:
    """'Derived product' — inside the Run step, before the button.

    The survey-derive peer (optional eighth engine) computes
    climatological anomaly products: each frame minus its day-of-year
    climatology over a user-chosen baseline period. Needs survey-viz >=
    0.19.0 at render time; the pipeline says so plainly otherwise.
    """
    derive_status = statuses.get("survey-derive")
    with st.expander("Derived product: climatological anomaly (optional)",
                     expanded=False):
        if derive_status is None or not derive_status.installed:
            st.info("Render anomaly maps instead of the raw variable with "
                    "the derive engine:\n\n"
                    f"`{derive_status.pip_command if derive_status else 'pip install git+https://github.com/crieck2010/survey-derive.git'}`\n\n"
                    "Without it, reels show the raw variable — nothing else "
                    "changes.")
            return
        st.checkbox("Render anomaly instead of the raw variable",
                    key="derived_enabled",
                    help="The analysis field is replaced by its departure "
                         "from the day-of-year climatology over the baseline "
                         "period below.")
        if not st.session_state.get("derived_enabled"):
            return
        st.selectbox("Product",
                     ["anomaly", "standardized", "percent"],
                     key="derived_product",
                     help="anomaly: field minus climatology (native units); "
                          "standardized: anomaly in standard deviations; "
                          "percent: field as % of the climatology mean.")
        col1, col2 = st.columns(2)
        col1.text_input("Baseline start (YYYY-MM-DD)",
                        value="1991-01-01", key="derived_baseline_start")
        col2.text_input("Baseline end (YYYY-MM-DD)",
                        value="2020-12-31", key="derived_baseline_end")
        col3, col4 = st.columns(2)
        col3.number_input("Baseline stride (days)", min_value=1,
                          value=30, step=1, key="derived_stride",
                          help="Sample one baseline frame every N days "
                               "(30 ≈ monthly).")
        col4.number_input("Day-of-year window (days)", min_value=0,
                          value=15, step=1, key="derived_window",
                          help="The climatology for each calendar day is "
                               "built from ±N days around it (0 = exact "
                               "day-of-year matches only).")
        col5, col6 = st.columns(2)
        col5.number_input("Minimum samples per day-of-year", min_value=1,
                          value=10, step=1, key="derived_min_samples",
                          help="Floor on pooled baseline samples per "
                               "day-of-year and grid cell. Day/cells below "
                               "it are masked out of standardized "
                               "anomalies.")
        col6.number_input("Color-limit quantile", min_value=0.5,
                          max_value=0.999, value=0.99, step=0.01,
                          format="%.2f", key="derived_quantile",
                          help="The shared symmetric color range covers up "
                               "to this quantile of |anomaly| — lower it "
                               "to saturate the extremes sooner.")
        st.caption("Available for SST, ERA5 atmosphere, currents, sea ice, "
                   "precipitation, night lights and ocean color. Not for "
                   "GRACE (already an anomaly), fires, static topography, "
                   "storm tracks, streamgages or earthquakes — the pipeline "
                   "refuses those instead of guessing. The baseline is "
                   "re-fetched with the same source, so its archive must "
                   "actually cover the period you pick. Needs survey-viz ≥ "
                   "0.19.0 at render time.")


def _timescale_enabled(statuses: Dict[str, peers.PeerStatus]) -> bool:
    """True when the optional survey-timescales peer is installed."""
    ts_status = statuses.get("survey-timescales")
    return ts_status is not None and ts_status.installed


def _timescale_resolver(statuses: Dict[str, peers.PeerStatus]):
    """The viz source resolver for the tp disambiguation, or None.

    Precedence mirrors the pipeline's source routing: the spec's own
    pinned ``source`` wins (see :func:`studio.timescale.resolve_source`);
    this only supplies the regional default when nothing is pinned.
    """
    viz_status = statuses.get("survey-viz")
    if viz_status is None or not viz_status.installed:
        return None
    try:
        return importlib.import_module("viz.sources").resolve_source
    except (ImportError, AttributeError):
        return None


def _apply_timescale_suggestion(statuses: Dict[str, peers.PeerStatus]) -> None:
    """on_click callback for the Suggest window button.

    Runs before the script body on the click's rerun, so it may only
    touch session state — never widgets. The engine's (start, end,
    mode, reason) only ever lands in the two pickers plus a display
    note — the user can edit the dates freely afterwards, and the
    pickers are what the Run button consumes. Static variables
    (bathymetry/elevation) get a plain-words note instead of a window.
    """
    spec_dict = st.session_state.get("spec_dict") or {}
    ts_module = statuses["survey-timescales"].module
    spec = types.SimpleNamespace(
        variable=spec_dict.get("variable"),
        region_key=spec_dict.get("region_key"),
        source=spec_dict.get("source") or "")
    static_err = getattr(ts_module, "StaticVariableError", ())
    try:
        suggestion = timescale.suggest(
            spec, today=datetime.now().date(),
            timescales_module=ts_module,
            resolver=_timescale_resolver(statuses))
    except static_err as exc:
        st.session_state[timescale.SUGGESTION_KEY] = None
        st.session_state[timescale.ERROR_KEY] = (
            "warning",
            f"No time window applies to {spec_dict.get('variable')!r} — "
            f"{exc}")
        return
    except Exception as exc:
        st.session_state[timescale.SUGGESTION_KEY] = None
        st.session_state[timescale.ERROR_KEY] = (
            "error", f"Could not suggest a window: {exc}")
        return
    st.session_state[timescale.ERROR_KEY] = None
    st.session_state[timescale.START_KEY] = suggestion.start
    st.session_state[timescale.END_KEY] = suggestion.end
    st.session_state[timescale.SUGGESTION_KEY] = {
        "start": suggestion.start.isoformat(),
        "end": suggestion.end.isoformat(),
        "mode": suggestion.mode,
        "reason": suggestion.reason,
    }


def _timescale_section(statuses: Dict[str, peers.PeerStatus]) -> None:
    """'Suggested window' — time-window pickers + one-click suggestion.

    Inside the Run step, before the button. The survey-timescales peer
    (optional tenth engine) suggests a [start, end] framing tuned to
    the spec's variable and region (season alignment, trailing
    event-density windows, annual cycles, trend horizons) and says why.
    The suggestion only ever fills the two date pickers — the user can
    edit them freely afterwards, and the Run button consumes the
    pickers' values. With the peer missing, the control is hidden and an
    explanatory note shows instead: never a crash.
    """
    ts_status = statuses.get("survey-timescales")
    pip_cmd = (ts_status.pip_command if ts_status is not None
               else "pip install "
                    "git+https://github.com/crieck2010/survey-timescales.git")
    with st.expander("Time window: suggested framing (optional)",
                     expanded=False):
        spec_dict = st.session_state.get("spec_dict") or {}
        if not spec_dict:
            st.info("Parse a description first (step 1) — the suggestion "
                    "is tuned to that spec's variable and region.")
            return
        if not _timescale_enabled(statuses):
            st.info("One click can suggest the right time window for this "
                    "variable and region — fire season, melt season, the "
                    "full annual cycle, an event-density window — and say "
                    f"why:\n\n`{pip_cmd}`\n\n"
                    "Without it, the dates stay exactly as parsed — nothing "
                    "else changes.")
            return
        start0, end0 = timescale.picker_defaults(spec_dict)
        help_text = ("Explicit dates always win: the suggestion only fills "
                     "these in — you can edit them freely afterwards, and "
                     "the Run button uses whatever they hold.")
        col1, col2 = st.columns(2)
        col1.date_input("Start date", value=start0,
                        key=timescale.START_KEY, help=help_text)
        col2.date_input("End date", value=end0,
                        key=timescale.END_KEY, help=help_text)
        if st.button("Suggest window", key="timescale_suggest",
                     on_click=_apply_timescale_suggestion, args=(statuses,),
                     help="Ask the survey-timescales engine for the right "
                          "window for this variable + region. Overwrites "
                          "the two pickers above — you can edit them "
                          "afterwards."):
            pass  # the on_click callback did the work before this rerun
        error = st.session_state.get(timescale.ERROR_KEY)
        if error:
            kind, message = error
            if kind == "warning":
                st.warning(message)
            else:
                st.error(message)
        suggestion = st.session_state.get(timescale.SUGGESTION_KEY)
        if suggestion:
            st.success(f"Suggested window applied: {suggestion['start']} → "
                       f"{suggestion['end']}.")
            st.caption(f"**{suggestion['mode']}** — {suggestion['reason']}")
            st.caption("Edit the dates above freely — your edits are what "
                       "the Run button uses.")


def _run_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    st.subheader("7 · Run the pipeline")
    spec_dict: Optional[Dict[str, Any]] = st.session_state.get("spec_dict")
    if not spec_dict:
        st.info("Parse a description first (step 1).")
        return

    _refine_section(statuses)
    _derived_section(statuses)
    _cache_section(statuses)
    _timescale_section(statuses)

    if not st.button("Run — fetch, render, encode", type="primary"):
        # Show a previous result if the session already ran.
        _show_result()
        return

    try:
        wired = peers.wire_peers(statuses)
    except peers.MissingPeerError as exc:
        st.error(str(exc))
        return

    # The Time window pickers are the user's explicit dates: when they
    # were rendered they win over whatever the spec carried (which is
    # what they default to, so this is a no-op unless edited).
    try:
        run_dict = timescale.override_with_pickers(
            spec_dict, st.session_state,
            enabled=_timescale_enabled(statuses))
    except ValueError as exc:
        st.warning(str(exc))
        return
    spec = wired.VizSpec.from_dict(run_dict)
    out_dir = tempfile.mkdtemp(prefix="reel-studio-")
    progress_bar = st.progress(0.0, text="Starting…")
    status_box = st.status("Running pipeline…", expanded=False)

    def on_progress(frac: float, message: str) -> None:
        progress_bar.progress(min(max(frac, 0.0), 1.0), text=message)
        status_box.update(label=message, state="running")

    try:
        result = pipeline.run_pipeline(
            spec, wired, out_dir, progress=on_progress,
            cache=_open_run_cache(statuses),
            cmap=st.session_state.get("cmap"),
            motion=(st.session_state.get("motion") or None),
            audio_path=st.session_state.get("audio_path"),
            story_captions=bool(st.session_state.get("story_captions")),
            platform=st.session_state.get("platform") or "legacy",
            style_preset=st.session_state.get("style_preset"),
            aesthetic_preset=st.session_state.get("aesthetic_preset"),
            rotation=st.session_state.get("aes_rotation"),
            watermark=st.session_state.get("watermark"),
            subtitle=st.session_state.get("aes_subtitle"),
            encoding_line=bool(
                st.session_state.get("aes_encoding_line", True)),
            place_labels=st.session_state.get("aes_place_labels"),
            max_labels=st.session_state.get("aes_max_labels"),
            min_population=st.session_state.get("aes_min_population"),
            basemap=st.session_state.get("aes_basemap"),
            strand_count=st.session_state.get("aes_strand_count"),
            strand_linewidth=st.session_state.get("aes_strand_linewidth"),
            landmask=st.session_state.get("aes_landmask"),
            derived=_run_derived())
    except (pipeline.UnfetchableRegionError,
            pipeline.UnsupportedVariableError) as exc:
        progress_bar.empty()
        status_box.update(label="Nothing to fetch", state="error")
        st.warning(f"**Not fetchable — no crash, just honesty.**\n\n{exc}")
        return
    except pipeline.PeerTooOldError as exc:
        progress_bar.empty()
        status_box.update(label="Peer too old", state="error")
        st.warning(f"**Upgrade needed — nothing was lost.**\n\n{exc}")
        return
    except peers.MissingPeerError as exc:  # pragma: no cover - wired already
        st.error(str(exc))
        return
    except Exception as exc:
        progress_bar.empty()
        status_box.update(label="Pipeline failed", state="error")
        st.error(f"**Pipeline failed:** {exc}")
        with st.expander("Traceback"):
            st.code(traceback.format_exc())
        return

    status_box.update(label="Done", state="complete")
    progress_bar.progress(1.0, text="Done")
    st.session_state["result"] = {
        "video_path": result.video_path,
        "sidecar_path": result.sidecar_path,
        "provenance": result.provenance,
        "spec_dict": run_dict,
        "n_frames": result.n_frames,
        "manifest_path": result.manifest_path,
        "platform": result.platform,
        "style_preset": st.session_state.get("style_preset"),
    }
    _show_result()


def _refine_section(statuses: Dict[str, peers.PeerStatus]) -> None:
    """'Refine in plain language' — inside the Run step, before the button.

    Applies a plain-language follow-up instruction to the parsed spec
    via ``viz.refine_spec`` and shows exactly what changed (plus
    anything not understood). Camera-motion intents (survey-viz >=
    0.17.0) are merged into the session's motion settings from step 4.
    Needs survey-viz >= 0.16.0; older peers get the upgrade hint
    instead of a crash.
    """
    viz_status = statuses["survey-viz"]
    viz = viz_status.module if viz_status.installed else None
    with st.expander("Refine in plain language (optional)", expanded=False):
        if viz is None or not hasattr(viz, "refine_spec"):
            st.info("Plain-language refinements need survey-viz ≥ 0.16.0 — "
                    f"upgrade it (`{viz_status.pip_command}`) to unlock them.")
            return
        if not _version_ok(viz_status, MIN_VIZ_STORY):
            st.info("Tip: camera-motion refinements (“add a slow zoom in”) "
                    "need survey-viz ≥ 0.17.0 — "
                    f"`{viz_status.pip_command}`.")
        st.caption(
            "Describe the changes you want — e.g. “zoom in on the Gulf of "
            "Mexico”, “add a slow zoom in during the video”, “pan left”, "
            "“use a warmer colormap”, “title it ‘Gulf Heat’”, "
            "“run it from 2015 to 2020”, “switch to light mode”. Only the "
            "things you mention change.")
        text = st.text_area("Changes", key="refine_text", height=70)
        if not st.button("Apply refinements", key="refine_apply"):
            return
        if not text or not text.strip():
            st.warning("Describe the changes first.")
            return
        try:
            spec = viz.VizSpec.from_dict(
                st.session_state.get("spec_dict") or {})
            result = viz.refine_spec(spec, text)
        except viz.UnparseableDescription as exc:
            st.error(str(exc))
            return
        except (TypeError, ValueError) as exc:  # pragma: no cover
            st.error(f"Those refinements did not validate: {exc}")
            return
        st.session_state["spec_dict"] = result.spec.to_dict()
        if result.cmap is not None:
            st.session_state["cmap"] = result.cmap
        motion_delta = dict(getattr(result, "motion", None) or {})
        if motion_delta:
            motion = dict(st.session_state.get("motion") or {})
            motion.update(motion_delta)
            st.session_state["motion"] = motion
            st.success("Camera-motion settings updated from your "
                       "instruction — review them in step 4, then press "
                       "Run below to regenerate the reel.")
        else:
            st.success(f"Applied {len(result.applied)} change(s) — press "
                       "Run below to regenerate the reel.")
        for change in result.applied:
            st.write(f"**{change.field}**: `{change.old}` → `{change.new}`")
            st.caption(change.reason)
        for key, value in motion_delta.items():
            st.write(f"**motion.{key}**: → `{value}`")
            st.caption("instruction set camera-motion "
                       f"{key} to {value!r}")
        for note in result.unparsed:
            st.warning(note)


def _show_result() -> None:
    result = st.session_state.get("result")
    if not result:
        return
    st.subheader("8 · Your reel")
    video_path = result["video_path"]
    st.video(video_path)
    with open(video_path, "rb") as fh:
        st.download_button(
            label="Download MP4",
            data=fh.read(),
            file_name="reel-studio.mp4",
            mime="video/mp4",
        )
    st.caption(
        f"{result['n_frames']} frames · sidecar: `{result['sidecar_path']}`")
    cache_info = ((result.get("provenance") or {}).get("cache") or {})
    if cache_info.get("enabled"):
        bits = []
        bits.append("frames from cache" if cache_info.get("frame_hit")
                    else "frames freshly rendered")
        bits.append("video from cache" if cache_info.get("video_hit")
                    else "video freshly encoded")
        st.caption("Render cache: " + " · ".join(bits))
    platform = result.get("platform") or "legacy"
    canvas = ((result.get("provenance") or {}).get("render") or {}
              ).get("canvas")
    if canvas:
        st.caption(f"Platform: **{platform}** — "
                   f"{canvas['width']}×{canvas['height']}")
    else:
        st.caption(f"Platform: **{platform}** (legacy 1080×1920 layout)")
    style_preset = result.get("style_preset")
    st.caption(f"Style: **{style_preset}** (preset)"
               if style_preset else "Style: hand-tuned")
    derived_info = ((result.get("provenance") or {}).get("derived") or {})
    if derived_info:
        st.caption(f"Derived product: **{derived_info.get('product')}** vs "
                   f"{derived_info.get('baseline_start')}–"
                   f"{derived_info.get('baseline_end')} climatology "
                   f"(engine: {derived_info.get('engine')})")
    captions = _caption_events(result.get("manifest_path"))
    if captions:
        with st.expander(f"Story captions ({len(captions)})"):
            for event in captions:
                st.write(event.get("text", ""))
                st.caption(f"frames {event.get('frame_start')}–"
                           f"{event.get('frame_end')}")
    with st.expander("Provenance — fetch URLs, SHA-256, spec JSON"):
        st.json(result["provenance"])


def _caption_events(manifest_path: Optional[str]) -> list:
    """Story-caption events from a survey-viz frame manifest (or []).

    survey-viz >= 0.17.0 records them as a plain list at
    ``manifest["render"]["story_captions"]``; each event carries
    ``frame_start``/``frame_end``/``text``.
    """
    if not manifest_path:
        return []
    try:
        with open(manifest_path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    events = data.get("render", {}).get("story_captions", [])
    return list(events) if isinstance(events, list) else []


# ---------------------------------------------------------------------------
# Publish step (survey-publish peer, optional ninth engine)
# ---------------------------------------------------------------------------

def _publish_peer():
    """Import the survey-publish API (models + registry).

    Defensive: returns ``None`` when the peer is not installed, so the
    step can show the install hint instead of crashing.
    """
    try:
        import publish.models as models
        import publish.registry as registry
    except ImportError:
        return None
    return types.SimpleNamespace(models=models, registry=registry)


def _publish_platform_names(pub) -> list:
    """Platform names from the peer registry, in the documented order.

    Falls back to :data:`PUBLISH_PLATFORM_ORDER` when the registry
    lists nothing (or its ``list_platforms()`` is unusable).
    """
    names: list = []
    try:
        names = [str(n) for n in pub.registry.list_platforms() if n]
    except Exception:
        names = []
    ordered = [n for n in PUBLISH_PLATFORM_ORDER if n in names]
    ordered += [n for n in names if n not in ordered]
    return ordered or list(PUBLISH_PLATFORM_ORDER)


def _adapter_label(adapter) -> str:
    """Best-effort account label from an adapter (``""`` when unavailable)."""
    try:
        label = getattr(adapter, "account_label", None)
        if callable(label):
            label = label()
        return str(label or "")
    except Exception:
        return ""


def _probe_publish_adapters(pub) -> Dict[str, Dict[str, Any]]:
    """Probe every platform: ``{name: {connected, label, adapter}}``.

    Each adapter is probed defensively — one broken platform never
    hides the others.
    """
    probed: Dict[str, Dict[str, Any]] = {}
    for name in _publish_platform_names(pub):
        try:
            adapter = pub.registry.get_adapter(name)
        except Exception:
            probed[name] = {"connected": False, "label": "", "adapter": None}
            continue
        try:
            connected = bool(adapter.is_connected())
        except Exception:
            connected = False
        probed[name] = {
            "connected": connected,
            "label": _adapter_label(adapter) if connected else "",
            "adapter": adapter,
        }
    return probed


def _parse_hashtags(raw: str) -> list:
    """Split a comma-separated hashtag field into clean tags (no ``#``)."""
    return [h.strip().lstrip("#").strip() for h in (raw or "").split(",")
            if h.strip().lstrip("#").strip()]


def _publish_defaults(result: Dict[str, Any]) -> Dict[str, str]:
    """Prefill for the Publish step's title / caption / hashtag fields.

    Title comes from the run's spec; the caption adds variable, region
    and date detail when the spec carries them. Headless-safe.
    """
    spec = (result or {}).get("spec_dict") or {}
    title = str(spec.get("title") or "reel-studio reel")
    variable = str(spec.get("variable") or "")
    region = str(spec.get("region_key") or spec.get("region") or "")
    region = region.replace("-", " ").replace("_", " ")
    date_bits = [str(spec.get(k)) for k in
                 ("start_date", "end_date", "start", "end", "date_range")
                 if spec.get(k)]
    caption_bits = [title]
    detail = " · ".join(b for b in (variable, region) if b)
    if detail:
        caption_bits.append(detail)
    if date_bits:
        caption_bits.append(" – ".join(date_bits))
    caption_bits.append("Generated locally with reel-studio.")
    return {
        "title": title[:100],
        "caption": "\n".join(caption_bits),
        "hashtags": "reelstudio, dataviz, remotesensing, earthobservation",
    }


def _publish_now(pub, video_path: str, title: str, caption: str,
                 hashtags: list, platform_names: list
                 ) -> Dict[str, Dict[str, str]]:
    """Publish the reel to each named platform's adapter.

    Only platforms whose adapter reports connected are published to —
    unconnected platforms are never published to (and never listed as
    failures). Returns ``{name: {"ok", "url_or_id", "error"}}``, the
    shape recorded into the manifest's ``"publication"`` section.
    Headless-safe (takes the peer namespace, so tests can fake it).
    """
    results: Dict[str, Dict[str, str]] = {}
    for name in platform_names:
        try:
            adapter = pub.registry.get_adapter(name)
        except Exception as exc:
            results[name] = {"ok": False, "url_or_id": "",
                             "error": f"no adapter: {exc}"}
            continue
        try:
            if not adapter.is_connected():
                continue  # never publish anywhere unconnected
        except Exception as exc:
            results[name] = {"ok": False, "url_or_id": "",
                             "error": f"status check failed: {exc}"}
            continue
        try:
            request = pub.models.PublishRequest(
                video_path=video_path, title=title, caption=caption,
                hashtags=list(hashtags), platform_options={})
            outcome = adapter.publish(request)
            results[name] = {
                "ok": bool(getattr(outcome, "ok", False)),
                "url_or_id": str(getattr(outcome, "url_or_id", "") or ""),
                "error": str(getattr(outcome, "error", "") or ""),
            }
        except Exception as exc:
            results[name] = {"ok": False, "url_or_id": "", "error": str(exc)}
    return results


def _publication_record(title: str, caption: str, hashtags: list,
                        results: Dict[str, Dict[str, str]],
                        approved_at: Optional[str] = None) -> Dict[str, Any]:
    """Build the ``"publication"`` manifest section. Headless-safe."""
    return {
        "approved_at": (approved_at or
                        datetime.now().isoformat(timespec="seconds")),
        "platforms": {name: {"ok": r.get("ok", False),
                             "url_or_id": r.get("url_or_id", ""),
                             "error": r.get("error", "")}
                      for name, r in results.items()},
        "title": title,
        "caption": caption,
        "hashtags": list(hashtags),
    }


# --- schedule-for-later (survey-publish >= 0.2.0 queue engine) ---------------

#: Human labels paired positionally with DEFAULT_SLOTS.
_SCHEDULE_SLOT_LABELS = ("Morning", "Midday", "Evening")


def _publish_queue_peer():
    """Import the survey-publish queue engine.

    Returns a namespace with ``QueuedItem``, ``QueueStore``,
    ``parse_schedule_time`` and ``DEFAULT_SLOTS`` — or ``None`` when
    survey-publish is missing or older than the 0.2.0 queue engine, so
    the step can show the upgrade hint instead of crashing.
    """
    try:
        from publish import (DEFAULT_SLOTS, QueuedItem, QueueStore,
                             parse_schedule_time)
    except ImportError:
        return None
    return types.SimpleNamespace(
        QueuedItem=QueuedItem, QueueStore=QueueStore,
        parse_schedule_time=parse_schedule_time, DEFAULT_SLOTS=DEFAULT_SLOTS)


def _schedule_slot_labels(qpub) -> list:
    """``["Morning 08:30", ...]``: DEFAULT_SLOTS paired with labels by position."""
    return [f"{label} {slot}"
            for label, slot in zip(_SCHEDULE_SLOT_LABELS, qpub.DEFAULT_SLOTS)]


def _resolve_schedule_time(qpub, choice, sched_date, custom_time,
                           now=None):
    """Resolve a schedule choice to a tz-aware local datetime.

    ``choice`` is ``("preset", "HH:MM")`` (next occurrence in local
    time — the engine rolls a slot that already passed today to
    tomorrow) or ``("custom", None)`` (``sched_date`` plus
    ``custom_time`` combined as ``"YYYY-MM-DD HH:MM"``). ``now`` is an
    optional injectable "current" datetime for deterministic tests.
    Raises ``ValueError`` on bad input. Headless-safe.
    """
    kind, slot = choice
    if kind == "preset":
        return qpub.parse_schedule_time(slot, now=now)
    text = f"{sched_date.isoformat()} {(custom_time or '').strip()}"
    return qpub.parse_schedule_time(text, now=now)


def _format_schedule_local(dt) -> str:
    """Human-readable local time, e.g. ``"Tue 2026-09-29 12:30 EDT"``."""
    return dt.strftime("%a %Y-%m-%d %H:%M %Z")


def _queue_item_when(item) -> str:
    """Display string for an item's ``scheduled_at`` (never crashes)."""
    try:
        return _format_schedule_local(
            datetime.fromisoformat(item.scheduled_at))
    except (ValueError, TypeError):
        return str(item.scheduled_at)


def _enqueue_publish(qpub, store, *, video_path, title, caption, hashtags,
                     platforms, scheduled_at) -> str:
    """Build a queued item and hand it to the engine. Returns the item id."""
    item = qpub.QueuedItem(
        video_path=video_path, title=title, caption=caption,
        hashtags=list(hashtags), platforms=list(platforms),
        scheduled_at=scheduled_at, status="queued", attempts=0,
        last_error="", published_urls={}, platform_options={})
    return store.enqueue(item)


def _failed_platforms(item) -> list:
    """Platforms on a failed item that still need publishing.

    A requeue enqueues ONLY these — platforms already in
    ``published_urls`` are never double-posted (partial-failure rule).
    Headless-safe.
    """
    published = set((getattr(item, "published_urls", None) or {}))
    return [p for p in (getattr(item, "platforms", None) or [])
            if p not in published]


def _queued_record(title: str, caption: str, hashtags: list, item_id: str,
                   scheduled_at: str, platforms: list,
                   queued_at: Optional[str] = None) -> Dict[str, Any]:
    """Build the ``"queued_publication"`` manifest section. Headless-safe."""
    return {
        "queued_at": (queued_at or
                      datetime.now().isoformat(timespec="seconds")),
        "item_id": item_id,
        "scheduled_at": scheduled_at,
        "platforms": list(platforms),
        "title": title,
        "caption": caption,
        "hashtags": list(hashtags),
    }


def _schedule_for_later_ui(pub, qpub, connected, video_path, title,
                             caption, hashtags, result) -> None:
    """Schedule mode: queue the reel for a preset slot or a custom time.

    Presets come from the engine's DEFAULT_SLOTS (08:30 / 12:30 / 18:30
    local). The resolved local time is shown before anything is
    enqueued, so there are no surprises. Only connected platforms can
    be queued to — an unconnected pick is skipped with a warning, the
    same honesty rule as publish-now.
    """
    if qpub is None:
        st.info(
            "Scheduled publishing needs survey-publish ≥ 0.2.0 "
            "(the queue engine):\n\n"
            "`pip install --upgrade "
            "git+https://github.com/crieck2010/survey-publish.git`\n\n"
            "Then re-run this step.")
        return

    slots = list(qpub.DEFAULT_SLOTS)
    labels = _schedule_slot_labels(qpub)
    choice = st.session_state.get("publish_sched_choice")
    if choice is None or choice[0] not in ("preset", "custom"):
        choice = ("preset", slots[0])

    st.write("Pick a slot")
    cols = st.columns(len(slots))
    for col, slot, label in zip(cols, slots, labels):
        with col:
            if st.button(label, key=f"publish_slot_{slot.replace(':', '')}"):
                st.session_state["publish_sched_choice"] = ("preset", slot)
                choice = ("preset", slot)
    st.caption(
        "Slots are local time — if the slot already passed today, it "
        "rolls to tomorrow.")
    st.write("Or a custom date & time")
    sched_date = st.date_input("Date", value=datetime.now().date(),
                               key="publish_sched_date")
    custom_time = st.text_input("Time (HH:MM)", value="12:30",
                                key="publish_sched_time")
    if st.button("Use custom date & time", key="publish_sched_custom"):
        st.session_state["publish_sched_choice"] = ("custom", None)
        choice = ("custom", None)

    try:
        when = _resolve_schedule_time(qpub, choice, sched_date, custom_time)
    except ValueError as exc:
        st.error(str(exc))
        return
    st.write("Will publish at (local time): "
             f"**{_format_schedule_local(when)}**")

    platforms = st.multiselect(
        "Platforms", options=_publish_platform_names(pub),
        default=list(connected), key="publish_sched_platforms",
        help="Only connected platforms are queued to.")
    if st.button("Queue for scheduled publish", type="primary",
                 key="publish_queue"):
        chosen = [p for p in platforms if p in connected]
        dropped = [p for p in platforms if p not in connected]
        if dropped:
            st.warning("Skipped unconnected platform(s): "
                       + ", ".join(dropped))
        if not chosen:
            st.error("Pick at least one connected platform.")
            return
        store = qpub.QueueStore()
        item_id = _enqueue_publish(
            qpub, store, video_path=video_path, title=title,
            caption=caption, hashtags=hashtags, platforms=chosen,
            scheduled_at=when.isoformat())
        record = _queued_record(title, caption, hashtags, item_id,
                                when.isoformat(), chosen)
        pipeline.record_queued_publication(result.get("manifest_path"),
                                           record)
        st.success(
            f"Queued — id `{item_id}`, publishes "
            f"{_format_schedule_local(when)} (local time). "
            "Recorded in the run manifest under "
            "\"queued_publication\".")


def _publish_queue_panel(qpub) -> None:
    """Queue management: cancel / reschedule queued items, requeue failures."""
    with st.expander("Publish queue", expanded=False):
        if qpub is None:
            st.info("The publish queue needs survey-publish ≥ 0.2.0: "
                    "`pip install --upgrade "
                    "git+https://github.com/crieck2010/survey-publish.git`")
            return
        store = qpub.QueueStore()
        items = store.list_queue()

        active = [i for i in items
                  if i.status in ("queued", "publishing")]
        if not active:
            st.caption("Nothing queued.")
        for item in active:
            st.write(
                f"**{item.title or '(untitled)'}** — "
                f"{_queue_item_when(item)} (local) · "
                f"{', '.join(item.platforms)} · `{item.status}`")
            if item.status == "queued":
                key = item.id.replace("-", "")
                if st.button("Cancel", key=f"qcancel_{key}"):
                    store.cancel(item.id)
                    st.success(f"Canceled `{item.id}`.")
                new_time = st.text_input(
                    "New time (HH:MM, or full ISO)", value="",
                    key=f"qresched_{key}")
                if st.button("Reschedule", key=f"qresched_go_{key}"):
                    if not new_time.strip():
                        st.error("Enter the new time first.")
                    else:
                        try:
                            moved = qpub.parse_schedule_time(
                                new_time.strip())
                        except ValueError as exc:
                            st.error(str(exc))
                        else:
                            store.reschedule(item.id, moved.isoformat())
                            st.success(
                                "Rescheduled to "
                                f"{_format_schedule_local(moved)} (local).")

        history = [i for i in items
                   if i.status in ("published", "failed", "canceled")]
        if history:
            st.write("History")
            for item in history:
                line = (f"**{item.title or '(untitled)'}** — "
                        f"{_queue_item_when(item)} (local) · "
                        f"{', '.join(item.platforms)} · `{item.status}`")
                if item.status == "failed":
                    detail = (f" — {item.last_error}"
                              if item.last_error else "")
                    st.error(line + detail)
                    missing = _failed_platforms(item)
                    if missing and st.button(
                            "Requeue failed platforms",
                            key=f"qrequeue_{item.id.replace('-', '')}"):
                        new_id = _enqueue_publish(
                            qpub, store, video_path=item.video_path,
                            title=item.title, caption=item.caption,
                            hashtags=list(item.hashtags), platforms=missing,
                            scheduled_at=(datetime.now().astimezone()
                                          .isoformat()))
                        st.success(
                            f"Requeued {', '.join(missing)} as `{new_id}` — "
                            "platforms that already published are NOT "
                            "reposted.")
                elif item.status == "published":
                    urls = ", ".join(
                        f"{p}={u}"
                        for p, u in (item.published_urls or {}).items()
                        if u)
                    st.success(line + (f" — {urls}" if urls else ""))
                else:
                    st.caption(line)


def _publish_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Step 9: publish the finished reel to social platforms.

    Two modes per reel. **Publish now** (the v0.10.0 behavior, unchanged):
    approval happens in-app, the adapters post immediately, and the
    results are recorded in the manifest. **Schedule for later** queues
    the reel in the survey-publish >= 0.2.0 queue engine (preset slots
    Morning 08:30 / Midday 12:30 / Evening 18:30 local, or a custom
    date+time) and a background ``survey-publish tick`` publishes it
    when due — the queue fires only while the PC is on and awake with
    the 15-minute tick job running. A queue panel below the modes lists
    queued items (cancel / reschedule) plus history, and failed items
    can be requeued for only the platforms that missed.

    All publishing logic lives in the survey-publish engine
    (``publish.registry`` adapters, ``publish`` queue store); this step
    only collects the approval, builds the requests, shows the results,
    and records them in the run manifest. Approval is in-app only in
    this version — email click-to-approve was evaluated and deliberately
    excluded (link-prefetch hazard, needs public hosting). The reel
    publishes as-is: survey-animate already muxed the audio track
    upstream.
    """
    st.subheader("9 · Publish")
    result = st.session_state.get("result")
    if not result:
        st.info("Generate a reel first (step 8) — the Publish step picks "
                "it up from there.")
        return
    video_path = result.get("video_path")
    if not video_path or not os.path.isfile(video_path):
        st.warning("The last run's video file is missing — re-run step 8 "
                   "before publishing.")
        return

    st.video(video_path)

    status = statuses.get("survey-publish")
    if status is None or not status.installed:
        st.info(
            "Publishing needs the survey-publish engine:\n\n"
            f"`{status.pip_command if status else 'pip install git+https://github.com/crieck2010/survey-publish.git'}`\n\n"
            "Without it, reels stay local — nothing else changes. Connect "
            "each platform once from a terminal with "
            "`survey-publish connect <platform>` (OAuth needs a browser, "
            "so terminal-based connect is the honest flow), then this step "
            "lights up.")
        return

    pub = _publish_peer()
    if pub is None:  # pragma: no cover - load_peers saw it installed
        st.error("survey-publish was detected but its modules would not "
                 "import — reinstall it "
                 f"(`{status.pip_command}`).")
        return

    probed = _probe_publish_adapters(pub)
    connected = [n for n, p in probed.items() if p["connected"]]

    st.write("Platform connections")
    cols = st.columns(len(probed))
    for col, name in zip(cols, probed):
        with col:
            info = probed[name]
            if info["connected"]:
                st.success(f"**{name}**\n\nconnected"
                           + (f" — {info['label']}" if info["label"] else ""))
            else:
                st.warning(f"**{name}**\n\nnot connected")
    for name, info in probed.items():
        if info["connected"]:
            continue
        setup_doc = PUBLISH_SETUP_DOCS.get(name, "docs/")
        repo_url = ("https://github.com/crieck2010/survey-publish/blob/main/"
                    + setup_doc)
        with st.expander(f"Connect {name}", expanded=False):
            st.code(f"survey-publish connect {name}", language="bash")
            st.caption(
                "OAuth needs a browser, so connect runs in your terminal, "
                "not here. Check the per-platform app-credentials "
                "prerequisites first, then re-run this step to refresh the "
                "status:")
            st.markdown(f"[survey-publish/{setup_doc}]({repo_url})")

    # --- editable metadata, prefilled from the run's spec ------------------
    if st.session_state.get("publish_for") != video_path:
        defaults = _publish_defaults(result)
        st.session_state["publish_title"] = defaults["title"]
        st.session_state["publish_caption"] = defaults["caption"]
        st.session_state["publish_hashtags"] = defaults["hashtags"]
        st.session_state["publish_for"] = video_path
    title = st.text_input("Title", key="publish_title")
    caption = st.text_area("Caption", key="publish_caption", height=110)
    raw_tags = st.text_input(
        "Hashtags (comma-separated)", key="publish_hashtags",
        help="Posted with the caption on each platform; a leading # is "
             "optional.")
    hashtags = _parse_hashtags(raw_tags)
    if hashtags:
        st.caption("Will post: " + " ".join(f"#{h}" for h in hashtags))
    st.caption("The reel publishes as-is — survey-animate already muxed "
               "your audio track upstream.")

    qpub = _publish_queue_peer()

    if not connected:
        st.info("Connect at least one platform above to enable publishing.")
    else:
        mode = st.radio(
            "Publish mode", ("Publish now", "Schedule for later"),
            key="publish_mode",
            help="Publish now posts immediately on approval. Schedule for "
                 "later queues the reel in the survey-publish queue engine "
                 "and a background tick publishes it at the chosen time.")
        if mode == "Publish now":
            if st.button("Approve & Publish", type="primary",
                         key="publish_approve"):
                results = _publish_now(pub, video_path, title, caption,
                                       hashtags, connected)
                record = _publication_record(title, caption, hashtags, results)
                pipeline.record_publication(result.get("manifest_path"),
                                            record)
                st.session_state["publication"] = record
                st.session_state["publication_for"] = video_path
        else:
            _schedule_for_later_ui(pub, qpub, connected, video_path,
                                   title, caption, hashtags, result)

    _publish_queue_panel(qpub)

    st.info(
        "Scheduled publishes only fire while this PC is on and awake "
        "with the 15-minute tick job running (`survey-publish tick`, "
        "every 15 minutes). See the Task Scheduler setup in the "
        "survey-publish docs: "
        "https://github.com/crieck2010/survey-publish/blob/main/docs/SCHEDULING.md")

    last = st.session_state.get("publication")
    if last and st.session_state.get("publication_for") == video_path:
        st.write("Last approval")
        for name, plat in last["platforms"].items():
            if plat.get("ok"):
                link = plat.get("url_or_id") or ""
                shown = (f"[{link}]({link})" if link.startswith("http")
                         else f"`{link}`" if link else "")
                st.success(f"**{name}**: published" +
                           (f" — {shown}" if shown else ""))
            else:
                st.error(f"**{name}**: failed — "
                         f"{plat.get('error') or 'unknown error'}")
        st.caption(f"Approved at {last.get('approved_at')} (local time), "
                   "recorded in the run manifest under "
                   "\"publication\".")
    st.caption(
        "Approval happens in this app only — pressing the button above "
        "is the approval. Email click-to-approve was evaluated and "
        "deliberately excluded: approval links get prefetched by mail "
        "scanners (link-prefetch hazard) and would need public hosting.")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🎬", layout="centered")
    st.title("🎬 reel-studio")
    st.caption(
        "Plain-English description → Great-Lakes SST fetch → vertical "
        f"reel MP4. v{APP_VERSION} · 100% local.")

    statuses = peers.load_peers()
    _peer_status_panel(statuses)
    st.divider()
    _parse_step(statuses)
    st.divider()
    _platform_step(statuses)
    st.divider()
    _aesthetics_step(statuses)
    st.divider()
    _motion_audio_step(statuses)
    st.divider()
    _batch_step(statuses)
    st.divider()
    _schedule_step(statuses)
    st.divider()
    _run_step(statuses)
    st.divider()
    _publish_step(statuses)

    with st.expander("About the optional LLM assist"):
        st.write(
            "If `LLM_API_KEY` is set and the deterministic parser fails, "
            "reel-studio makes **one** assist attempt against an "
            "OpenAI-compatible chat-completions endpoint "
            "(`LLM_BASE_URL`, default `https://api.openai.com/v1`, model "
            "`LLM_MODEL` default `gpt-4o-mini`), using only stdlib "
            "`urllib`. The returned JSON is validated with "
            "`VizSpec.from_dict`; on failure you see the original parse "
            "error. The app works fully without the key.")
        if os.environ.get("LLM_API_KEY"):
            st.success("LLM_API_KEY is set — assist is armed.")
        else:
            st.info("LLM_API_KEY is not set — deterministic parser only.")


if __name__ == "__main__" or _streamlit_is_running():
    main()
