#!/usr/bin/env python3
"""Match geotagged photos to trips by time; write trip_photos.json waypoints."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TRIPS_FILE = ROOT / "data" / "trips.json"
PHOTOS_INDEX = ROOT / "data" / "photos_index.json"
OUT_FILE = ROOT / "data" / "trip_photos.json"

# Pad trip windows so trailhead / evening photos still attach
PAD_HOURS = 18


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def assign_photos_to_trips(
    trips: list[dict],
    photos: list[dict],
    pad_hours: float = PAD_HOURS,
) -> dict[str, list[dict]]:
    pad = timedelta(hours=pad_hours)
    windows: list[tuple[str, datetime, datetime]] = []
    for trip in trips:
        start = parse_ts(str(trip.get("start")))
        end = parse_ts(str(trip.get("end")))
        if not start or not end:
            continue
        if end < start:
            start, end = end, start
        windows.append((trip["id"], start - pad, end + pad))

    # Prefer the tightest matching window when trips overlap
    assigned: dict[str, list[dict]] = {tid: [] for tid, _, _ in windows}
    unmatched = 0
    for photo in photos:
        captured = parse_ts(photo.get("captured_at"))
        if not captured:
            unmatched += 1
            continue
        hits = [
            (tid, end - start)
            for tid, start, end in windows
            if start <= captured <= end
        ]
        if not hits:
            unmatched += 1
            continue
        hits.sort(key=lambda x: x[1])
        trip_id = hits[0][0]
        assigned[trip_id].append({
            "id": photo["id"],
            "kind": "photo",
            "datetime": photo["captured_at"],
            "location": f"Photo · {photo.get('album', 'memory')}",
            "lat": photo["lat"],
            "lng": photo["lng"],
            "album": photo.get("album", ""),
            "thumb": photo.get("thumb", ""),
            "source_name": photo.get("source_name", ""),
            "kwh": 0,
            "is_photo": True,
        })

    for trip_id, items in assigned.items():
        items.sort(key=lambda p: parse_ts(p["datetime"]) or datetime.min.replace(tzinfo=timezone.utc))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pad_hours": pad_hours,
        "unmatched_photos": unmatched,
        "trips": {
            tid: {"photos": items, "photo_count": len(items)}
            for tid, items in assigned.items()
            if items
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Attach album photos to trips by capture time")
    parser.add_argument("--pad-hours", type=float, default=PAD_HOURS)
    args = parser.parse_args()

    if not TRIPS_FILE.exists():
        raise SystemExit(f"Missing {TRIPS_FILE} — run segment_trips.py first")
    if not PHOTOS_INDEX.exists():
        raise SystemExit(f"Missing {PHOTOS_INDEX} — run ingest_photos.py first")

    trips_data = load_json(TRIPS_FILE)
    photos_data = load_json(PHOTOS_INDEX)
    payload = assign_photos_to_trips(
        trips_data.get("trips", []),
        photos_data.get("photos", []),
        pad_hours=args.pad_hours,
    )
    OUT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    trip_n = len(payload["trips"])
    photo_n = sum(v["photo_count"] for v in payload["trips"].values())
    print(f"Matched {photo_n} photos across {trip_n} trips "
          f"(unmatched: {payload['unmatched_photos']})")
    print(f"  Wrote {OUT_FILE}")


if __name__ == "__main__":
    main()
