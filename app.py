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

from studio import llm_assist, peers, pipeline

APP_TITLE = "reel-studio"
PEER_REPOS = ("survey-viz", "survey-currents", "survey-animate")


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
    if used_assist:
        st.info("The deterministic parser could not handle this description, "
                "so one LLM assist attempt was used (LLM_API_KEY was set). "
                "Please confirm the spec below before running.")
    else:
        st.success("Parsed — confirm the spec, then press Run.")
    st.subheader("Parsed VizSpec")
    st.json(spec_dict)


def _aesthetics_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    """Step 2: style / title / caption / underlay / colormap controls.

    Reads the parsed ``spec_dict`` from session state, lets the user
    tweak the aesthetics, and writes the validated result back on
    "Apply aesthetics". Widget keys embed a hash of the current spec
    so a re-parse always resets the controls to the new spec's values.
    """
    st.subheader("2 · Aesthetics")
    spec_dict: Optional[Dict[str, Any]] = st.session_state.get("spec_dict")
    if not spec_dict:
        st.info("Parse a description first (step 1).")
        return

    status = statuses["survey-viz"]
    viz = status.module if status.installed else None
    rev = abs(hash(json.dumps(spec_dict, sort_keys=True, default=str)))

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


def _run_step(statuses: Dict[str, peers.PeerStatus]) -> None:
    st.subheader("3 · Run the pipeline")
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
            cmap=st.session_state.get("cmap"))
    except (pipeline.UnfetchableRegionError,
            pipeline.UnsupportedVariableError) as exc:
        progress_bar.empty()
        status_box.update(label="Nothing to fetch", state="error")
        st.warning(f"**Not fetchable — no crash, just honesty.**\n\n{exc}")
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
    }
    _show_result()


def _refine_section(statuses: Dict[str, peers.PeerStatus]) -> None:
    """'Refine in plain language' — inside the Run step, before the button.

    Applies a plain-language follow-up instruction to the parsed spec
    via ``viz.refine_spec`` and shows exactly what changed (plus
    anything not understood). Needs survey-viz >= 0.16.0; older peers
    get the upgrade hint instead of a crash.
    """
    viz_status = statuses["survey-viz"]
    viz = viz_status.module if viz_status.installed else None
    with st.expander("Refine in plain language (optional)", expanded=False):
        if viz is None or not hasattr(viz, "refine_spec"):
            st.info("Plain-language refinements need survey-viz ≥ 0.16.0 — "
                    f"upgrade it (`{viz_status.pip_command}`) to unlock them.")
            return
        st.caption(
            "Describe the changes you want — e.g. “zoom in on the Gulf of "
            "Mexico”, “use a warmer colormap”, “title it ‘Gulf Heat’”, "
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
        st.success(f"Applied {len(result.applied)} change(s) — press "
                   "Run below to regenerate the reel.")
        for change in result.applied:
            st.write(f"**{change.field}**: `{change.old}` → `{change.new}`")
            st.caption(change.reason)
        for note in result.unparsed:
            st.warning(note)


def _show_result() -> None:
    result = st.session_state.get("result")
    if not result:
        return
    st.subheader("4 · Your reel")
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
    with st.expander("Provenance — fetch URLs, SHA-256, spec JSON"):
        st.json(result["provenance"])


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, page_icon="🎬", layout="centered")
    st.title("🎬 reel-studio")
    st.caption(
        "Plain-English description → Great-Lakes SST fetch → vertical "
        "reel MP4. v0.3.0 · 100% local.")

    statuses = peers.load_peers()
    _peer_status_panel(statuses)
    st.divider()
    _parse_step(statuses)
    st.divider()
    _aesthetics_step(statuses)
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
