# Road Replay — Engineering Plan (ideas only)

**Branch for future implementation:** `cursor/road-replay-ux-redesign-9f19`  
**This branch (`cursor/road-replay-ux-scrum-board-c6cb`):** planning docs only.

Keep: Mapbox GL, `scripts/build_map.py` pipeline, single rendered HTML for Pages, base64 `MAPBOX_TOKEN` guardrail.

---

## Primary technical approach for home overlap

**Hybrid (chosen):**

1. **`build_hubs()` in `build_map.py`** — bucket stops near Addison/Bellevue (`home_config` radii) → emit `hubs` with `{label, lat, lng, trip_ids}`.
2. **Overview markers** — suppress per-trip start/end dots inside hub radius; render **one DOM hub marker** with count badge.
3. **Overview labels** — replace `drawRouteLabels()` DOM chips with a **Mapbox symbol layer** at destination (or mid-route ~40%) using `text-allow-overlap: false`. Or interim: hide chips until hover / featured-only.
4. **Keep DOM markers** for trip-focus beacons/popups/vehicle (interaction model already DOM-based).

Not chosen: rewrite everything as SymbolLayers (boils the ocean).

---

## Quick wins (ship first)

| Item | Touch | Notes |
|------|-------|-------|
| Fix popups | CSS `.beacon-wrap` | `pointer-events:none` only on `.beacon-ring`; `auto` on `.beacon-core` |
| Panel-aware `fitBounds` | `fitTrip`, `introFlyover` | Read panel collapsed + clamp pad to viewport width |
| Hide overview chips until hover | `drawRouteLabels`, `setHoveredTrip` | Half-day perceived fix |
| Auto-collapse panel on trip/play | `selectTrip`, `startPlayback` | Map becomes hero |
| Soft watch-mode on Play | reuse `cinema-mode` CSS without `portrait-export` | Cinema for watching, not only export |

---

## Template architecture (no HTTP split)

Jinja `{% include %}` partials under `scripts/templates/travel_map/` still bake **one** `output/travel_map.html`. Introduce `AppState` + `window.RR.*` namespaces. Do **not** split token into a separate `.js` file (Pages push protection).

---

## Playback reliability

- Cache DOM refs; diff stop-marker class updates (don’t toggle all every frame)
- Don’t full `drawRoutes()` rebuild all 23×6 layers on every `selectTrip` — style only changed trips
- Optional later: `PlaybackQueue` for “watch every trip”
- Clamp large `rAF` deltas after tab backgrounding

---

## Test plan (`scripts/verify_map.py`)

- Overview declutter: hub/label count ≪ 23  
- Popup click smoke (real click, not evaluate-only)  
- Trip play: `isPlaying` + advancing `animTimeMs`  
- Mobile 390×844: panel collapsed, padding sane  
- Screenshot pair: before/after overview  

---

## Workstream scores (I / E / C)

| # | Workstream | I | E | C | Sequence |
|---|------------|--:|--:|--:|----------|
| 1 | Popup + fitBounds + chip hide | 5 | 1 | 5 | 1st |
| 2 | AppState + soft watch-mode | 4 | 2 | 4 | 2nd |
| 3 | Home hubs + symbol labels | 5 | 3 | 4 | 3rd (headline) |
| 4 | Destination-grouped sidebar list | 4 | 2 | 5 | with #3 |
| 5 | Playback DOM/perf | 3 | 2 | 4 | 4th |
| 6 | Responsive breakpoints | 4 | 4 | 3 | 5th |
| 7 | Template partial split | 3 | 3 | 5 | anytime after #1 |
| 8 | verify_map extensions | 5 | 2 | 5 | continuous |

## Risks

- Pages dirty-build → copy HTML aside → reset → `gh-pages` must stay intact  
- Token must remain base64 in HTML  
- Cinema export must still hide chrome after AppState migration  
- Don’t invalidate `routes_cache.json` key format while deleting legacy `path3d`
