# Road Replay UX — Master Plan (implementation)

**Branch:** `cursor/road-replay-ux-redesign-9f19`  
**Sources:** PM / Design / Eng / FE / QA parallel audit (`docs/ux-redesign/`)

## Thesis

Personal **Atlas of Journeys**: Where have I been? → Which memory? → Relive it → Share it.

## Sprint 1 scope (shipping now)

1. **Home declutter** — home hubs (one pin per Addison/Bellevue), destination-forward labels (hover-only chips at destination, not stacked at home), no per-trip overview dots at hubs
2. **Sidebar** — destination-grouped trip list under Destinations + keep Epic list
3. **Chrome** — auto-collapse panel on trip select; panel-aware camera padding; softer first paint
4. **Watch mode** — soft cinema chrome on Play (not only Export); story captions own location; HUD muted while watching
5. **Bugfixes** — beacon popup pointer-events; rAF delta clamp; intro/outro title cards while watching
6. **Responsive** — basic `@media` collapse for narrow widths
7. **Verify** — rebuild map, Playwright smoke + manual QA

## Out of scope (later)

Full constellation ribbons, year scrub, memory reel autoplay, Jinja partial split, overview layer consolidation to shared GeoJSON source.
