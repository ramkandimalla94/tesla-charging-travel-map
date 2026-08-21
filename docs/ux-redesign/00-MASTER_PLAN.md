# Road Replay UX — Master Plan

**Thesis:** Personal Atlas of Journeys — Where have I been? → Which memory? → Relive it → Share it.

## Sprint 1 (shipped — #13)

Home hubs, destination-forward labels, destination-grouped sidebar, watch mode, panel auto-collapse, popup fix, responsive basics.

## Sprint 2 (shipped — #14)

Destination constellation, year era filter, snappier dwell + dock %, a11y 44px controls.

## Sprint 3 (this branch)

1. **Memory reel** — auto-cycles featured routes on atlas with Pause/Resume
2. **Hub→destination spokes** — dashed atlas lines from home hubs to constellation stars
3. **Featured epic CTA** — one-click focus on the headline trip
4. **Cinema caption safe margins** — caption plate + higher portrait placement
5. **Mobile sheet polish** — grab handle, larger toggle, CTA reposition
6. **Epic queue** — Loop button third state plays featured trips in sequence
7. **Corridor banding** — soft band + parallel filaments for multi-visit destinations
8. **Template CSS partial** — `travel_map/_map_css.html.j2` include
9. **Playback DOM cache** — hot-path scrubber/prog updates skip repeated `getElementById`
10. **Atlas JS partial** — `travel_map/_atlas_js.html.j2` (CTA / reel / spokes)
11. **Shared spokes GeoJSON** — band + filaments from one `atlas-spokes` source with `kind` filters
12. **Era rail discoverability** — labeled Era chips, larger hit targets
13. **Epic-queue dock badge** — `Epic queue · i/n` when Loop is in queue mode
14. **Playback JS partial** — `travel_map/_playback_js.html.j2`
15. **Routes JS partial** — `travel_map/_routes_js.html.j2`
16. **Mobile sheet regression** — 390×844 verify + denser grab handle / dock fit
17. **Markers JS partial** — hubs / constellation / stop beacons
18. **Denser corridor polish** — stronger bands + more filaments for multi-visit dests
19. **README screenshot gallery** — watch / memory / mobile captions; removed duplicate Quick start

## Sprint 4 (this branch — started)

1. **Export title/outro holds** — record intro card ~2.4s + outro ~2.2s on-canvas
2. **9:16 safe margins** — portrait title in upper third; caption plate mid-lower
3. **Export chrome hide** — CTA/memory/dock/timeline fully suppressed in cinema-mode
4. **Export JS partial** — `travel_map/_export_js.html.j2`
5. **Caption pacing** — travel captions only at segment edges; dwell POIs after settle-in; DOM-diff overlay

## Sprint 3 DoD (ready for review)

- [x] Atlas declutter: hubs + dest stars (no per-trip stop soup)
- [x] Memory reel + Pause; featured CTA; spokes + corridor banding
- [x] Era filter chips work and are discoverable
- [x] Watch mode collapses panel; Esc returns to atlas
- [x] Epic queue Loop state + dock badge `i/n`
- [x] Mobile 390×844 sheet + grab + 44px toggle (`verify_map`)
- [x] `verify_map.py` green (desktop + mobile + cinema framing)
- [x] README live URL + screenshot gallery current
- [ ] Human review of PR #15 / merge to `main` (Pages deploy)

## Later

Further share polish (optional dwell caption copy from trip story).
