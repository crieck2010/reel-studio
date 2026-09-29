# Publishing (survey-publish peer, v0.10.0)

Step 9 of reel-studio publishes the finished reel to social platforms.
All publishing logic lives in the
[survey-publish](https://github.com/crieck2010/survey-publish) engine
(the optional ninth peer); the app only collects the approval, builds
one `PublishRequest` per connected platform, calls the adapters, and
records the results in the run manifest. The engine/UI split is
deliberate: reel-studio never touches credentials or platform APIs
directly.

## Platforms

YouTube, Instagram, Facebook, TikTok — each with its own adapter in the
engine's registry. The app asks the engine's `list_platforms()` which
adapters exist and falls back to this documented set only when the
registry can't answer.

## Account prerequisites

Each platform needs its own app/API credentials (API key and/or OAuth
client, per platform rules), set up once per that platform's
`docs/SETUP_<PLATFORM>.md` in the survey-publish repo:

* `docs/SETUP_YOUTUBE.md`
* `docs/SETUP_INSTAGRAM.md`
* `docs/SETUP_FACEBOOK.md`
* `docs/SETUP_TIKTOK.md`

(Paths per the peer's interface contract — confirm against the
survey-publish repo once it ships.)

## Connect flow

Connect each platform once from a terminal:

```bash
survey-publish connect youtube
survey-publish status
```

OAuth needs a browser, so connect is deliberately terminal-based — the
honest flow — and the app never asks for tokens. Step 9 shows a
per-platform connected/not-connected panel (with the account label when
the adapter provides one); each unconnected platform gets an expander
with the exact `survey-publish connect <platform>` command and a link
to that platform's setup doc.

## Approval model

Edit the title, caption, and hashtags (all prefilled — title and
caption from the reel's spec, hashtags defaulting to
`reelstudio, dataviz, remotesensing, earthobservation`), then press
**Approve & Publish**. The app builds one `PublishRequest` per
**connected** platform and publishes — an unconnected platform is never
published to (skipped, not failed). Results show per platform: success
with the post URL/id, or the error text.

Approval is in-app only in this version. Email click-to-approve was
evaluated and deliberately excluded: mail scanners prefetch links
(which would fire approvals on their own) and the flow would need
public hosting.

## Manifest record

Every approval is appended to the run's `manifest.json` (written by
survey-viz during the Run step, i.e. before the Publish step runs) via
`studio.pipeline.record_publication`, under a `"publication"` section:

```json
{
  "publication": {
    "approved_at": "2026-09-28T21:40:00",
    "platforms": {
      "youtube": {"ok": true, "url_or_id": "https://...", "error": ""}
    },
    "title": "...",
    "caption": "...",
    "hashtags": ["reelstudio", "dataviz", "remotesensing", "earthobservation"]
  }
}
```

`approved_at` is local time. A missing or corrupt manifest is replaced
with just the publication record rather than crashing.

## Honest limits

* The reel publishes as-is — survey-animate already muxed the audio
  track upstream; there is no audio work in this step.
* Platform availability is whatever adapters survey-publish ships; the
  app shows its documented set and defers to the engine's registry.
* Without survey-publish installed, step 9 shows the install command
  and reels stay local — nothing else changes.
