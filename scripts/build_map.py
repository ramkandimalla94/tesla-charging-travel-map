#!/usr/bin/env python3
"""Build premium 3D satellite travel map with Mapbox GL JS + deck.gl arcs."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
GPX_DIR = OUTPUT_DIR / "gpx"
TRIPS_FILE = DATA_DIR / "trips.json"
HTML_OUTPUT = OUTPUT_DIR / "travel_map.html"
GEOJSON_OUTPUT = OUTPUT_DIR / "trips.geojson"

# Continental US bounds for overview camera
US_BOUNDS = {"west": -125.0, "east": -95.0, "south": 24.0, "north": 49.5}

TRIP_COLORS = [
    "#FF6B6B", "#4ECDC4", "#45B7D1", "#FFEAA7", "#DDA0DD",
    "#98D8C8", "#F7DC6F", "#BB8FCE", "#85C1E9", "#F8B500",
    "#00CED1", "#FF69B4", "#32CD32", "#FF8C00", "#9370DB",
    "#20B2AA", "#FFD700", "#DC143C", "#00FA9A", "#FF4500",
    "#1E90FF", "#ADFF2F", "#FF1493", "#00BFFF", "#FFA07A",
]

STATE_NAMES = {
    "TX": "Texas", "CO": "Colorado", "NM": "New Mexico", "AZ": "Arizona",
    "UT": "Utah", "ID": "Idaho", "OR": "Oregon", "WA": "Washington",
    "CA": "California", "NV": "Nevada", "OK": "Oklahoma", "KS": "Kansas",
}


def load_trips() -> dict:
    with open(TRIPS_FILE, encoding="utf-8") as f:
        return json.load(f)


def trip_color(index: int) -> str:
    return TRIP_COLORS[index % len(TRIP_COLORS)]


def is_valid_coord(lat: float | None, lng: float | None) -> bool:
    if lat is None or lng is None:
        return False
    if lat == 0 and lng == 0:
        return False
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return False
    # Reject swapped lat/lng (common geocode error)
    if abs(lat) > 90 or (abs(lat) > 60 and abs(lng) < 50):
        return False
    return True


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3959.0
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def short_lng_delta(lng1: float, lng2: float) -> float:
    """Return lng2 adjusted for shortest-path travel from lng1."""
    d = lng2 - lng1
    while d > 180:
        d -= 360
    while d < -180:
        d += 360
    return lng1 + d


def great_circle_arc(
    lat1: float, lng1: float, lat2: float, lng2: float, num_points: int = 48
) -> list[list[float]]:
    """Short-path great-circle arc as [[lat, lng], ...]."""
    if not is_valid_coord(lat1, lng1) or not is_valid_coord(lat2, lng2):
        return [[lat1, lng1], [lat2, lng2]]

    # Normalize target longitude for short path
    lng2s = short_lng_delta(lng1, lng2)

    p = math.pi / 180
    lat1r, lng1r = lat1 * p, lng1 * p
    lat2r, lng2r = lat2 * p, lng2s * p

    d = 2 * math.asin(
        math.sqrt(
            math.sin((lat2r - lat1r) / 2) ** 2
            + math.cos(lat1r) * math.cos(lat2r) * math.sin((lng2r - lng1r) / 2) ** 2
        )
    )
    if d < 1e-10:
        return [[lat1, lng1], [lat2, lng2]]

    points: list[list[float]] = []
    for i in range(num_points + 1):
        f = i / num_points
        a = math.sin((1 - f) * d) / math.sin(d)
        b = math.sin(f * d) / math.sin(d)
        x = a * math.cos(lat1r) * math.cos(lng1r) + b * math.cos(lat2r) * math.cos(lng2r)
        y = a * math.cos(lat1r) * math.sin(lng1r) + b * math.cos(lat2r) * math.sin(lng2r)
        z = a * math.sin(lat1r) + b * math.sin(lat2r)
        lat = math.atan2(z, math.sqrt(x * x + y * y)) / p
        lng = math.atan2(y, x) / p
        # Normalize output longitude to [-180, 180]
        while lng > 180:
            lng -= 360
        while lng < -180:
            lng += 360
        points.append([lat, lng])
    return points


def elevated_arc_coords(
    lat1: float, lng1: float, lat2: float, lng2: float,
    num_points: int = 64, max_alt_m: float = 80000,
) -> list[list[float]]:
    """Return [lng, lat, alt] coords for 3D elevated short-path arc."""
    dist = haversine_miles(lat1, lng1, lat2, lng2)
    if dist > 2500:
        max_alt_m = min(max_alt_m, 120000)
    peak = min(max_alt_m, max(12000, dist * 100))
    arc = great_circle_arc(lat1, lng1, lat2, lng2, num_points)
    coords: list[list[float]] = []
    for i, (lat, lng) in enumerate(arc):
        t = i / max(len(arc) - 1, 1)
        alt = peak * math.sin(t * math.pi)
        coords.append([lng, lat, alt])
    return coords


def path_miles(path: list[list[float]]) -> float:
    total = 0.0
    for i in range(1, len(path)):
        total += haversine_miles(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1])
    return round(total)


def build_route_path(stops: list[dict]) -> list[list[float]]:
    """Build route path within a single trip — never cross-trip."""
    if not stops:
        return []
    path: list[list[float]] = [[stops[0]["lat"], stops[0]["lng"]]]
    for i in range(1, len(stops)):
        a, b = stops[i - 1], stops[i]
        if not is_valid_coord(a["lat"], a["lng"]) or not is_valid_coord(b["lat"], b["lng"]):
            continue
        dist = haversine_miles(a["lat"], a["lng"], b["lat"], b["lng"])
        if dist > 80:
            segment = great_circle_arc(a["lat"], a["lng"], b["lat"], b["lng"])
            path.extend(segment[1:])
        else:
            path.append([b["lat"], b["lng"]])
    return path


def build_arcs(stops: list[dict], color: str, trip_id: str) -> list[dict]:
    """Build short-path arc segments for deck.gl PathLayer (selected trip only)."""
    arcs: list[dict] = []
    for i in range(1, len(stops)):
        a, b = stops[i - 1], stops[i]
        if not is_valid_coord(a["lat"], a["lng"]) or not is_valid_coord(b["lat"], b["lng"]):
            continue
        dist = haversine_miles(a["lat"], a["lng"], b["lat"], b["lng"])
        # Skip absurd jumps (bad data)
        if dist > 1500:
            continue
        path_coords = great_circle_arc(a["lat"], a["lng"], b["lat"], b["lng"], num_points=32)
        arcs.append({
            "fromLng": a["lng"], "fromLat": a["lat"],
            "toLng": b["lng"], "toLat": b["lat"],
            "color": color, "tripId": trip_id,
            "distance": round(dist),
            "height": min(0.6, max(0.1, dist / 1000)),
            "path": [[p[1], p[0]] for p in path_coords],
            "path3d": elevated_arc_coords(a["lat"], a["lng"], b["lat"], b["lng"], num_points=32),
        })
    return arcs


def extract_state(location: str) -> str | None:
    m = re.search(r",\s*([A-Z]{2})\b", location)
    return m.group(1) if m else None


def trip_duration_days(start: str, end: str) -> int:
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return max(1, (e - s).days + 1)
    except ValueError:
        return 1


def prepare_trips(trips_data: dict) -> list[dict]:
    prepared: list[dict] = []
    for i, trip in enumerate(trips_data["trips"]):
        stops = [
            s for s in trip["stops"]
            if is_valid_coord(s.get("lat"), s.get("lng"))
        ]
        if not stops:
            continue
        route_path = build_route_path(stops)
        miles = path_miles(route_path)
        total_kwh = round(sum(s["kwh"] for s in stops), 1)
        states = trip.get("via_states") or sorted({
            st for s in stops
            if (st := extract_state(s["location"]) or ("CO" if s.get("in_colorado") else None))
        })
        state_names = [STATE_NAMES.get(s, s) for s in states]
        via_summary = trip.get("via_summary") or ", ".join(states)
        arcs = build_arcs(stops, trip_color(i), trip["id"])
        prepared.append({
            "id": trip["id"],
            "name": trip["name"],
            "start": str(trip["start"])[:10],
            "end": str(trip["end"])[:10],
            "startTs": str(trip["start"]),
            "endTs": str(trip["end"]),
            "color": trip_color(i),
            "stops": stops,
            "route_path": route_path,
            "stop_count": len(stops),
            "total_kwh": total_kwh,
            "miles": miles,
            "duration_days": trip_duration_days(trip["start"], trip["end"]),
            "states": states,
            "state_names": state_names,
            "via_summary": via_summary,
            "origin_label": trip.get("origin_label", ""),
            "dest_label": trip.get("dest_label", ""),
            "has_colorado": trip.get("has_colorado", False),
            "colorado_stops": trip.get("colorado_stops", 0),
            "arcs": arcs,
            "region": "colorado" if trip.get("has_colorado") else (
                "pnw" if any(s in states for s in ("WA", "OR", "ID")) else "other"
            ),
        })
    return prepared


def build_dashboard(trips: list[dict]) -> dict:
    all_states: set[str] = set()
    for t in trips:
        all_states.update(t["states"])
    longest = max(trips, key=lambda t: t["miles"]) if trips else None
    return {
        "total_kwh": round(sum(t["total_kwh"] for t in trips), 0),
        "trip_count": len(trips),
        "total_miles": sum(t["miles"] for t in trips),
        "states": sorted(all_states),
        "state_names": sorted(STATE_NAMES.get(s, s) for s in all_states),
        "longest_trip": {
            "name": longest["name"], "miles": longest["miles"], "id": longest["id"],
        } if longest else None,
        "colorado_trips": sum(1 for t in trips if t["has_colorado"]),
        "us_bounds": US_BOUNDS,
    }


def build_timeline(trips: list[dict]) -> list[dict]:
    if not trips:
        return []
    starts = [datetime.fromisoformat(t["start"].replace("Z", "+00:00")) for t in trips]
    ends = [datetime.fromisoformat(t["end"].replace("Z", "+00:00")) for t in trips]
    t_min, t_max = min(starts), max(ends)
    span = (t_max - t_min).total_seconds() or 1
    segments = []
    for t in trips:
        s = datetime.fromisoformat(t["start"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(t["end"].replace("Z", "+00:00"))
        left = (s - t_min).total_seconds() / span * 100
        width = max(1.5, (e - s).total_seconds() / span * 100)
        segments.append({
            "id": t["id"], "name": t["name"], "color": t["color"],
            "start": t["start"], "end": t["end"],
            "left_pct": round(left, 2), "width_pct": round(width, 2),
            "has_colorado": t["has_colorado"],
        })
    return segments


def build_geojson(trips: list[dict]) -> dict:
    features = []
    for trip in trips:
        coords = [[p[1], p[0]] for p in trip["route_path"]]
        if len(coords) >= 2:
            features.append({
                "type": "Feature",
                "properties": {"trip_id": trip["id"], "name": trip["name"], "color": trip["color"]},
                "geometry": {"type": "LineString", "coordinates": coords},
            })
        for s in trip["stops"]:
            features.append({
                "type": "Feature",
                "properties": {
                    "trip_id": trip["id"], "location": s["location"],
                    "datetime": s["datetime"], "kwh": s["kwh"],
                },
                "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
            })
    return {"type": "FeatureCollection", "features": features}


def write_gpx(trip: dict, path: Path) -> None:
    gpx = ET.Element("gpx", {
        "version": "1.1", "creator": "tesla-charging-history-map",
        "xmlns": "http://www.topografix.com/GPX/1/1",
    })
    ET.SubElement(ET.SubElement(gpx, "metadata"), "name").text = trip["name"]
    for stop in trip["stops"]:
        if stop.get("lat") is None:
            continue
        wpt = ET.SubElement(gpx, "wpt", {"lat": str(stop["lat"]), "lon": str(stop["lng"])})
        ET.SubElement(wpt, "name").text = stop["location"]
        ET.SubElement(wpt, "desc").text = f"{stop['kwh']} kWh"
    ET.ElementTree(gpx).write(path, encoding="utf-8", xml_declaration=True)


def validate_output(trips: list[dict]) -> None:
    """Sanity-check generated trip data."""
    issues = []
    for t in trips:
        for s in t["stops"]:
            lat, lng = s.get("lat"), s.get("lng")
            if not is_valid_coord(lat, lng):
                issues.append(f"Invalid coord: {t['id']} {s['location']} ({lat},{lng})")
        for a in t["arcs"]:
            for key in ("fromLat", "fromLng", "toLat", "toLng"):
                v = a.get(key)
                if v is None or (key.endswith("Lat") and abs(v) > 90):
                    issues.append(f"Bad arc endpoint: {t['id']} {key}={v}")
    co_trip = next((t for t in trips if t.get("has_colorado")), None)
    if co_trip:
        co_stops = [s for s in co_trip["stops"] if s.get("in_colorado")]
        if not co_stops:
            issues.append("Colorado trip has no in_colorado stops")
        else:
            for s in co_stops:
                if not (37 <= s["lat"] <= 41 and -109 <= s["lng"] <= -102):
                    issues.append(f"CO stop outside bounds: {s['location']} ({s['lat']},{s['lng']})")
    if issues:
        print("Validation warnings:")
        for i in issues[:10]:
            print(f"  ⚠ {i}")
    else:
        print(f"Validation OK — {len(trips)} trips, CO trip: {co_trip['name'] if co_trip else 'none'}")


def render_html(trips: list[dict], dashboard: dict, timeline: list[dict], mapbox_token: str) -> str:
    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parent / "templates")),
        autoescape=True,
    )
    return env.get_template("travel_map.html.j2").render(
        trips=trips,
        trips_json=json.dumps(trips),
        dashboard=dashboard,
        dashboard_json=json.dumps(dashboard),
        timeline=timeline,
        timeline_json=json.dumps(timeline),
        mapbox_token=mapbox_token,
        has_mapbox=bool(mapbox_token),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 3D Tesla travel map")
    parser.add_argument("--public", action="store_true", help="Build without embedded Mapbox token (for GitHub Pages)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not TRIPS_FILE.exists():
        raise FileNotFoundError(f"Run segment_trips.py first. Missing {TRIPS_FILE}")

    mapbox_token = "" if args.public else os.getenv("MAPBOX_TOKEN", "").strip()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GPX_DIR.mkdir(parents=True, exist_ok=True)

    trips_data = load_trips()
    prepared = prepare_trips(trips_data)
    dashboard = build_dashboard(prepared)
    timeline = build_timeline(prepared)

    validate_output(prepared)

    with open(GEOJSON_OUTPUT, "w", encoding="utf-8") as f:
        json.dump(build_geojson(prepared), f, indent=2)

    valid_ids = {t["id"] for t in trips_data["trips"]}
    for old in GPX_DIR.glob("*.gpx"):
        if old.stem not in valid_ids:
            old.unlink()
    for trip in trips_data["trips"]:
        write_gpx(trip, GPX_DIR / f"{trip['id']}.gpx")

    HTML_OUTPUT.write_text(render_html(prepared, dashboard, timeline, mapbox_token), encoding="utf-8")
    if args.public:
        pages_index = OUTPUT_DIR / "index.html"
        pages_index.write_text(HTML_OUTPUT.read_text(encoding="utf-8"), encoding="utf-8")
        print(f"  Public index: {pages_index}")

    print(f"Built 3D map — {len(prepared)} trips")
    print(f"  Dashboard: {dashboard['total_kwh']} kWh · {dashboard['total_miles']:,} mi")
    print(f"  Mapbox token: {'loaded from .env' if mapbox_token else 'MISSING'}")
    print(f"  HTML: {HTML_OUTPUT}")


if __name__ == "__main__":
    main()
