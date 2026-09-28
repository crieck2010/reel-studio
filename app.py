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
import json
import os
import sys
import tempfile
import traceback
from pathlib import Path
from typing import Any, Dict, Optional

# Make the ``studio`` package importable when run as ``streamlit run app.py``.
sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    import streamlit as st
except ImportError:  # headless / tests: import still works, main() won't run
    st = None  # type: ignore

from studio import batch, llm_assist, peers, pipeline, styling

APP_TITLE = "reel-studio"
APP_VERSION = "0.6.0"
PEER_REPOS = ("survey-viz", "survey-currents", "survey-animate",
              "survey-layout", "survey-style")

#: Minimum peer versions for the v0.4.0 features.
MIN_VIZ_STORY = (0, 17, 0)      # story captions + motion refine intents
MIN_ANIMATE_MOTION = (0, 2, 0)   # cinematic motion + audio muxing
#: Minimum peer versions for the v0.5.0 platform layouts.
MIN_VIZ_CANVAS = (0, 18, 0)     # render_viz canvas= (platform canvases)

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
                    progress=on_progress)

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


def _run_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    st.subheader("6 · Run the pipeline")
    spec_dict: Optional[Dict[str, Any]] = st.session_state.get("spec_dict")
    if not spec_dict:
        st.info("Parse a description first (step 1).")
        return

    _refine_section(statuses)

    if not st.button("Run — fetch, render, encode", type="primary"):
        # Show a previous result if the session already ran.
        _show_result()
        return

    try:
        wired = peers.wire_peers(statuses)
    except peers.MissingPeerError as exc:
        st.error(str(exc))
        return

    spec = wired.VizSpec.from_dict(spec_dict)
    out_dir = tempfile.mkdtemp(prefix="reel-studio-")
    progress_bar = st.progress(0.0, text="Starting…")
    status_box = st.status("Running pipeline…", expanded=False)

    def on_progress(frac: float, message: str) -> None:
        progress_bar.progress(min(max(frac, 0.0), 1.0), text=message)
        status_box.update(label=message, state="running")

    try:
        result = pipeline.run_pipeline(
            spec, wired, out_dir, progress=on_progress,
            cmap=st.session_state.get("cmap"),
            motion=(st.session_state.get("motion") or None),
            audio_path=st.session_state.get("audio_path"),
            story_captions=bool(st.session_state.get("story_captions")),
            platform=st.session_state.get("platform") or "legacy",
            style_preset=st.session_state.get("style_preset"))
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
        "spec_dict": spec_dict,
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
    st.subheader("7 · Your reel")
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
    _run_step(statuses)

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
