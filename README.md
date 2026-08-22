# My Mile Diary

A personal **travel diary** on a 3D satellite map — replay road trips, enrich paths with Apple Photos GPS, and hover memories along the route.

![Overview](docs/screenshots/01-overview.png)

## Live demo

**https://ramkandimalla94.github.io/mymilediary/**

> **Repo name:** GitHub already maps this project to [`ramkandimalla94/mymilediary`](https://github.com/ramkandimalla94/mymilediary). Live Pages URL: **https://ramkandimalla94.github.io/mymilediary/** (after the next successful Pages deploy from `main`).

The Mapbox **public** token (`pk.…`) is baked into the Pages build as base64 (decoded in the browser) so GitHub push protection does not block `gh-pages` deploys. Do not use a secret `sk.…` token.

Every merge to `main` rebuilds this site via GitHub Actions (`.github/workflows/pages.yml`) and publishes to the `gh-pages` branch (HTML + photo thumbs).

## Features

- **Atlas overview** — home hubs + destination constellation + corridor spokes; destination-grouped trip list
- **Featured journey CTA** — one clear “open a journey” action on the atlas (no mystery auto-reel)
- **Photo memories** — dump albums into `data/photos/<album>/`; EXIF GPS clusters become real path waypoints (hike spurs to exact shot locations) plus cinematic memory beats during replay
- **Year era filter** — isolate diary chapters by year
- **3D satellite map** with Mapbox terrain and elevated route lines
- **Watch mode** — Play hides chrome for a passenger-seat replay; pan, pinch-zoom, scroll, and the zoom controls stay available so you can look around while the path keeps moving
- **Steady journey playback** — distance-paced travel along chargers and photo GPS; smart zoom when you linger in a small area; photo memories linger ~5s on screen (default **1×** = former easy 0.5× pace; continuous **0.25×–4×** speed slider; `[` / `]` nudge by 0.25×)
- **Separated play + speed** — play/pause stays bottom-center with rewind / skip-ahead (±8s, `←` / `→`); continuous speed rail sits bottom-right so captions and Journey Complete never cover controls
- **Live trip clock** — large HUD date/time tracks the playhead in the trip’s local timezone; memory captions use the same photo capture instant (not charger stop times). Day and night play at the same rate (no night skip / morning linger).
- **Gradual day↔night** — map lighting ramps through twilight from the live clock (visual only; does not change playback speed)
- **Navigation-style chase** — heading-up follow camera (Apple/Google Maps feel): look-ahead course, capped turn rate, car glued to the path; confined/slow stretches (Maroon Bells) zoom in, then pull back on the highway; end-of-trip pulls back to a north-up route recap. Dragging or pinching the map pauses chase briefly, then follow resumes after a short idle.
- **Hike vs drive** — photo trail legs and slow camp crawls switch to an on-foot walker badge (and livelier trail pacing); highway legs keep the car
- **Large memory stage** — replay photos appear as a large, stable mid-viewport overlay (no map-marker shake); clustered pins stay at exact EXIF GPS — the path routes to those shots; nearby camp shots use short spurs instead of out-and-back corridor snaps
- **Clean watch chrome** — one status line (en route / stop); polished Journey Complete card; no nearby-place labels or duplicate POI pills

## Quick start (local)

```bash
git clone https://github.com/ramkandimalla94/mymilediary.git
cd mymilediary
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env → MAPBOX_TOKEN=pk.eyJ...

python scripts/build_map.py
python -m http.server 8765
open http://127.0.0.1:8765/output/travel_map.html
```

### Trip history (CSV backbone)

Place travel/charging history CSV exports in the project root or `data/imports/`, then:

```bash
python scripts/merge_csvs.py
python scripts/geocode_locations.py   # first run only
python scripts/segment_trips.py
python scripts/build_map.py
```

Optional: copy `data/owner_config.json.example` → `data/owner_config.json` to pin home bases.

### Apple Photos → path enrichment

```bash
# data/photos/colorado/*.HEIC|jpg|png   (album folders)
python scripts/ingest_photos.py
python scripts/enrich_trips_with_photos.py
python scripts/build_map.py
```

- Originals stay in `data/photos/` (gitignored)
- Hover thumbs live in `output/photos/thumbs/<album>/`
- Metadata: `data/photos_index.json`, `data/trip_photos.json`

See [`data/photos/README.md`](data/photos/README.md).

## Screenshots

| Atlas overview | Colorado journey | Photo memories |
|----------|---------------|--------------|
| ![Overview](docs/screenshots/01-overview.png) | ![Colorado](docs/screenshots/04-colorado-fixed.png) | ![Photos](docs/screenshots/07-photo-memories.png) |

| Watch mode | Featured CTA | Mobile sheet |
|------------|-------------|---------------------|
| ![Watch mode](docs/screenshots/06-watch-mode.png) | ![Featured](docs/screenshots/02-featured-cta.png) | ![Mobile sheet](docs/screenshots/08-mobile-sheet.png) |

## GitHub Pages setup

1. Repo secret `MAPBOX_TOKEN` = public `pk.` token
2. Rename repo to `mymilediary` (Pages URL follows)
3. Merge to `main` → Actions deploys `gh-pages`

## Project layout

```text
data/
  photos/<album>/     # your dumps (gitignored)
  photos_index.json   # EXIF index
  trip_photos.json    # photos matched to trips
  trips.json
scripts/
  ingest_photos.py
  enrich_trips_with_photos.py
  build_map.py
  segment_trips.py
output/
  travel_map.html
  photos/thumbs/      # hover thumbnails (committed)
```

Personal travel diary — trip geometry may be derived from your own exports; photos are yours.
