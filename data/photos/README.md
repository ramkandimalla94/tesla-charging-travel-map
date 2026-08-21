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

Photos without GPS are skipped. Capture time is matched to trip windows so hike destinations (e.g. Maroon Bells) become waypoints on the path.
