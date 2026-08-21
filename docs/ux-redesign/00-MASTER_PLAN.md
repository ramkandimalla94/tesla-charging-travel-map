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

## Later

Fuller Jinja JS split (playback/routes), denser corridor polish, README screenshot refresh.
