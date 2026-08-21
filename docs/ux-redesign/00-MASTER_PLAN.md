# Road Replay UX — Master Plan

**Thesis:** Personal Atlas of Journeys — Where have I been? → Which memory? → Relive it → Share it.

## Sprint 1 (shipped — #13)

Home hubs, destination-forward labels, destination-grouped sidebar, watch mode, panel auto-collapse, popup fix, responsive basics.

## Sprint 2 (shipped — #14)

Destination constellation, year era filter, snappier dwell + dock %, a11y 44px controls.

## Sprint 3 (shipped — #15)

Memory reel, spokes + corridor banding, featured CTA, epic queue + badge, Era rail, mobile sheet, Jinja partials, playback perf.

## Sprint 4 (this branch + #15 carryover)

1. **Export title/outro holds** — shipped in #15
2. **9:16 safe margins** — shipped in #15
3. **Caption pacing** — shipped in #15
4. **Richer dwell story captions** — kWh / visited POI / halfway copy in `build_trip_story`
5. **Share blurb** — dock Share copies trip caption + live demo URL; toast after export
6. **Web Share API** — `navigator.share` when available, clipboard fallback
7. **Post-play share prompt** — journey-complete toast invites Share
8. **story_overrides** — optional `owner_config.json` intro/outro/share/stop caption patches

## Later

Further share UI polish as needed.
