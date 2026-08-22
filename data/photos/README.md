# Photo albums

Drop Apple Photos (or any geotagged images) here:

```text
data/photos/<album>/*.HEIC|jpg|png
```

Example:

```text
data/photos/colorado/IMG_1234.HEIC
data/photos/colorado/maroon_bells.jpg
```

Then on your machine:

```bash
python scripts/ingest_photos.py
python scripts/enrich_trips_with_photos.py
python scripts/build_map.py
```

- **Originals** stay in this folder (gitignored).
- **Hover thumbnails** are written to `output/photos/thumbs/<album>/` and served by the map UI.
- **Metadata** lands in `data/photos_index.json` + `data/trip_photos.json`.

Photos without GPS are skipped. Capture time prefers **GPS UTC** (`GPSDateStamp` + `GPSTimeStamp`) so photo→trip matching uses the same clock as charging history; EXIF `DateTimeOriginal` is local wall-clock and is only a fallback.

## After ingest (path quality)

The map traces **roads and trails**, not straight lines between pins.

- Burst shots within ~0.35 mi and 8 minutes collapse to one waypoint. Two trail photos 0.18 mi / 8+ minutes apart stay separate (Maroon Dam). Do not raise that window blindly.
- If you walked a **U-turn** on a dotted trail and there is no photo at the tip, Mapbox will often stay on the forest road. Add an OSM polyline on `data/owner_config.json` → `trip_overrides` → `route_via_paths` (see `.agents/TRIP_PATH_LEARNINGS.md`).
- Pin start/end home when Supercharging after return would glue extra days onto the trip.
- Rebuild with `python scripts/build_map.py --public` and play the journey before calling it done. The live Pages site only updates after **merge to main**.

