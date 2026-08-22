# Trip path learnings (Sep 2025 Colorado and after)

This file is for the **next agent** that dumps photos for a new trip. The Sep 2025 Frisco → Colorado round trip (`trip_016_2025-09-25_Frisco_to_Frisco`) taught these the hard way. User screenshots of “still wrong” were often the **live Pages site on `main`**, not the PR branch.

Live site: https://ramkandimalla94.github.io/mymilediary/

## What the path is allowed to be

The cyan line must follow a **mapped** road or trail (Mapbox Directions or OSM footway). It must not:

- crow-fly between photo GPS pins
- snap a photo onto a highway and draw a chord to it
- skip a photo “to avoid a valley cut” (Independence Pass then never got visited)
- even-sample a simplified polyline (Independence Pass / CO-82 hairpins became 0.2 mi stripes)

Miles on intro / Journey Complete cards are **plotted polyline miles**, not charger crow-flies.

## Maroon Bells / Maroon Dam U-turn

User: Fri Sep 26, “bridge and rapids” / striped trail, walking **U-turn**. Map drew a sharp triangle/V.

Facts:

- Photos `IMG_2855` (39.094983, −106.952019, 18:34 UTC) and `IMG_2856` (39.093764, −106.955047, 18:42 UTC) are **0.183 mi** apart on Maroon Creek Road (FR 1975 / Maroon-Snowmass Trail).
- There is **no photo at the U-turn tip**. They walked the dotted **Scenic Loop Trail** south to ~39.092634, −106.953103 (OSM way `422888565`) and back.
- Mapbox walking between those two photos is a 0.19 mi road walk (ratio ~1.05). A Mapbox **via** off the road either snaps back to the road or detours 1.5 mi **north**. Mapbox does not know that footway.
- `MIN_ROUTE_MILES = 0.3` used to skip the 0.18 mi pair entirely (2-point chord). Keep it at **0.04**.
- `PHOTO_CLUSTER_GAP_S = 8 min` almost merged 2855+2856 (8 min 3 s). Do not raise the gap without checking trail pairs.

Fix in repo:

- `data/owner_config.json` → `trip_overrides[].route_via_paths` for the 18:34–18:43 and 21:20–21:32 UTC windows (outbound + return).
- `scripts/osm_trails.py` tries the same class of OSM bulge/U-turn on **future** short walking legs when Mapbox stays on the road.
- Validation fails if the Sep trip path in the Maroon box never goes south of lat 39.0930.

AllTrails “bridge and rapids” is a Scenic Loop viewpoint. That is this trail, not Crater Lake.

## Independence Pass

User: Sat Sep 27 ~12:35 PM MDT, “En route · Glenwood Springs”, path jumped / did not follow the pass.

Facts:

- Charger order: Aspen (Sep 26 23:36 UTC) → photos on CO-82 west of the summit → Glenwood (Sep 28 00:30 UTC).
- Photos: `IMG_2898` 39.10385, −106.572297; `IMG_2899` 39.1016, −106.578256 (matches 12:35 PM MDT); `IMG_2904` 39.10085, −106.580636; `IMG_9429` 39.092244, −106.586014 (20:12 UTC, **~0.9 mi south** of the highway).
- Independence Pass summit is ~39.1083, −106.5636. The Sep 27 photos are **west** of the summit. Do **not** invent a summit via after 2898 (that would be a backtrack). Follow **CO-82 hairpins** into the pull-offs.
- `IMG_9429` sits on OSM way `423037223` (informal Strava path) / Mapbox “Independence Pass Trail” fork. Two-point walking 2904→9429 has ratio ~1.09 (looks like a stripe). Driving snaps to a useless 0.01 mi fragment.
- Skipping that photo as a “valley cut” made the path **never go there**. Do not skip photo vertices.
- Force-pinning photo GPS onto Mapbox-snapped roads created 0.33 mi and 1.04 mi crow-flies. `PIN_ENDPOINT_MI = 0.03`.
- `simplify_path` even-sampling down to 160–900 points destroyed CO-82 switchbacks. **Never even-sample.** Restore hops > ~0.11 mi from the original geometry. `SIMPLIFY_MAX_POINTS` is a soft DP target, not a hard downsample.

## Home pin (this trip)

Start and end: **8404 Warren Parkway, Frisco, TX** (33.1097617, −96.8125339) via `trip_overrides` `start_location` / `end_location`. Supercharging after arriving home with leftover range must not extend the trip (`end_before`).

Shared trip: Rama + Akash, Akash’s car (`shared_trips` match owner Akash + Colorado).

## Playback (do not regress)

- `PLAYBACK_SPEED_REF = 0.5` so UI **1×** = former easy 0.5×. Default speed 1. Slider 0.25×–4×.
- Photo linger ~4 s wall-clock at 1× (`MEMORY_HOLD_MS = 2500` with SPEED_REF).
- Rewind / skip-ahead ±8 s, `#btn-rewind` / `#btn-forward`, `←` / `→`.
- Hike legs: walker badge when `leg_is_hiking` (photo walking spur or slow < 4 mph short crawl). Highway stays a car. Do not treat every `spur_miles ≤ 18` as a hike if it is not a photo leg.
- Intro / outro stats: **miles along the plotted path**, days, places, photos (`#cinema-stats`).
- Watch mode: pan/zoom stay available; chase camera resumes after idle.

## How to add the next trip

```text
data/photos/<album>/*.HEIC|jpg
python scripts/ingest_photos.py
python scripts/enrich_trips_with_photos.py
python scripts/segment_trips.py          # if charging CSV also changed
python scripts/build_map.py --public
python scripts/verify_map.py             # needs http://127.0.0.1:8765
```

If a hike U-turn has no tip photo:

1. Confirm Mapbox walking is the **road** (ratio ~1.0, follows FR/highway name).
2. Pull the OSM footway (Overpass `highway~path|footway`) that bulges off the chord.
3. Put it on `trip_overrides.route_via_paths` with `after_timestamp` / `before_timestamp` between the two photos. Coordinates are `[lat, lng]`.
4. Rebuild. Do not “fix” it with a Mapbox via unless Mapbox already has that trail.

If a pass/highway looks like a straight jump: inspect consecutive `route_path` hops in that bbox before adding vias. Usually simplify/even-sample or a skipped photo.

## Code map

| Piece | Where |
| --- | --- |
| Route builder | `scripts/build_map.py` |
| OSM U-turn helper | `scripts/osm_trails.py` |
| Home / trip match | `scripts/home_config.py` (`matching_trip_override`) |
| Trip split + home pin | `scripts/segment_trips.py` |
| Overrides | `data/owner_config.json` (example: `data/owner_config.json.example`) |
| Photo dump | `data/photos/README.md` |
| Token | env `MAPBOX_TOKEN` (`pk.` only). Public HTML is base64. |
| Cache | `data/routes_cache.json` (`ROUTE_CACHE_VER`), `data/osm_trails_cache.json` |
| Pages | `.github/workflows/pages.yml` on merge to `main` |

Bump `ROUTE_CACHE_VER` when routing rules change so stale Mapbox polylines are not reused.
