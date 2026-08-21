# Road Replay — Sprint 0 Audit Notes

**Sources:** code review of `scripts/templates/travel_map.html.j2` + `scripts/build_map.py`, live local QA at `http://127.0.0.1:8765/output/travel_map.html`, committed screenshots under `docs/screenshots/`.

**Data reality:** 23 trips. ~11 start at Addison TX, ~12 at Bellevue WA. 22/23 are same-home round trips. Destinations repeat (Leavenworth ×5, Olympic Peninsula ×4, Colorado ×3).

---

## Critical findings (live QA)

1. **Home / overview label soup** — Route chips and start/end markers stack at Addison + Bellevue. Overview draws every trip’s chip at `stops[0]` with no collision engine (DOM Markers, not SymbolLayers).
2. **Chrome overcrowding** — First viewport always shows: left 320px panel + top stat pills + full transport dock + journey timeline + map. Play is disabled until a trip is selected, so dock is dead weight on home.
3. **Playback “cinema” is still a dashboard** — Live Play does **not** enter `cinema-mode` (that only runs on Export). HUD + story captions + dock + panel all compete; same place name can appear in 3–4 places at once.
4. **Same-home Start/Return pins** — Bellevue→Leavenworth shows START + RETURN labels near the same hub; readable after prior fixes but still noisy.
5. **Broken stop popups** — `.beacon-wrap { pointer-events: none }` kills click targets despite `setPopup(...)`.
6. **No responsive layout** — Zero `@media` queries; `fitBounds` left pad hardcodes `360` even when panel collapsed.

## Structural root cause (not a polish bug)

Overview tries to be “show all 23 trips as full neon routes + labels.” With two home bases, that cannot look good without **grouping** (destination hubs) and **collision** (SymbolLayer or hide-until-hover). Line `overview_offset` / dash styles only separate *lines*, not labels.

## What works

- Sidebar Epic vs All grouping; hover route highlight/dim
- Trip select camera framing
- Multi-layer neon routes + terrain
- Playback timeline data model (intro/dwell/travel/outro) in `build_map.py`
- Export cinema-mode hide chrome + 9:16 path exists

## Evidence artifacts

- Overview density: `/opt/cursor/artifacts/qa_25d71.webp`, `qa_1c310.webp`
- Bellevue same-home: `/opt/cursor/artifacts/qa_58d77.webp`
- Trip idle / charging HUD: `/opt/cursor/artifacts/qa_ca525.webp`, `qa_a9425.webp`
- Committed: `docs/screenshots/01-overview.png`
