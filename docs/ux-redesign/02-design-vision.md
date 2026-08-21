# Road Replay — Design Vision: “Atlas of Journeys” + “Overland Instrument”

**Product thesis:** Charging receipts → personal life atlas → relive a memory → share it. Emotion is nostalgia (“I forgot we did that”), not analytics.

---

## Information architecture (four modes)

| Mode | Feeling | Shows |
|------|---------|-------|
| **Atlas (Home)** | Photo-album cover | 2 home hubs + destination constellations; dim corridor ribbons; year era optional. **No** 23 stacked chips. |
| **Select** | Pick a movie | Destination-first cards (Leavenworth, Colorado, Olympic…) → filmstrip of trip instances |
| **Experience** | Passenger seat | Full-bleed map, chase camera, one caption line, minimal dock |
| **Export** | Wrap a gift | 9:16 frame, chrome gone, one CTA |

Today these are toggles on one overloaded canvas. They must become distinct UI states.

---

## Visual direction: Overland Instrument

- **Do:** Warm asphalt/graphite ground; terracotta “active trail” + Supercharger amber for charging; Syne display + monospaced numerals (odometer); flat instrument bezels; film grain/vignette.
- **Don’t:** Teal/sky/gold/pink neon competition; glass blur pills everywhere; emoji icons; purple-AI glow; cream-serif editorial.

### Motion (exactly 3)

1. One shared glide easing for camera + panels  
2. Reveal (fade + 6–8px slide) — never bounce/pop  
3. Charging pulse is the **only** recurring life animation  

### Camera grammar (exactly 3 moves)

Establish → Chase → Arrival. Nothing else.

---

## Home declutter system (design, not “add clustering”)

1. **Home hub glyph** — one per Addison / Bellevue; badge count; trips emerge from hub  
2. **Destination-forward labels** — overview labels destinations only, never 11× “Addison”  
3. **Zoom LOD** — continental: hubs + epic destinations; regional: waypoints; trip-focus: all stops  
4. **Leader lines** when labels must sit near the same pixel  
5. **Corridor ribbons** for shared I-5 / I-90 / I-25 at overview zoom  

### Viewport 1

Map-first. Small wordmark. Thin stat ticker. One CTA (“Replay an Epic”). Trip list is a drawer, not a permanent 320px wall. Dock muted until a trip is selected.

### Playback cinema language

Title card → chase → dwell hold with one caption → charging ritual (amber) → arrival. Soft “watch mode” on Play (reuse cinema chrome hide), not only on Export.

---

## Ranked visual fixes (I / E / C)

| Fix | I | E | C | Score (I×C/E) |
|-----|--:|--:|--:|-------------:|
| Home hubs + destination-forward labels | 5 | 2 | 5 | 12.5 |
| First-viewport chrome collapse | 5 | 3 | 4 | 6.7 |
| Soft watch-mode on Play | 4 | 2 | 4 | 8.0 |
| Palette collapse to trail + amber | 4 | 2 | 5 | 10.0 |
| Zoom LOD + leader lines | 5 | 3 | 4 | 6.7 |
| HUD → single caption | 4 | 2 | 4 | 8.0 |
| Responsive bottom-sheet | 4 | 4 | 3 | 3.0 |
