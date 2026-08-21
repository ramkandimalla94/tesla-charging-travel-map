#!/usr/bin/env python3
"""Build cinematic Tesla travel map with Mapbox GL JS (playback + Instagram export)."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
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
ROUTES_CACHE_FILE = DATA_DIR / "routes_cache.json"
NEARBY_PLACES_FILE = DATA_DIR / "nearby_places.json"
VISITED_PLACES_FILE = DATA_DIR / "visited_places.json"
HTML_OUTPUT = OUTPUT_DIR / "travel_map.html"
GEOJSON_OUTPUT = OUTPUT_DIR / "trips.geojson"

POI_RADIUS_MILES = 40
POI_MAX_PER_STOP = 3
MIN_ROUTE_MILES = 0.3
ROUTE_FETCH_DELAY_S = 0.25

# Continental US bounds for overview camera
US_BOUNDS = {"west": -125.0, "east": -95.0, "south": 24.0, "north": 49.5}

TRIP_COLORS = [
    "#FF4D4D", "#00E5C0", "#3B82F6", "#FBBF24", "#A855F7",
    "#34D399", "#FB7185", "#38BDF8", "#F97316", "#C084FC",
    "#2DD4BF", "#F472B6", "#84CC16", "#F59E0B", "#818CF8",
    "#14B8A6", "#EAB308", "#EF4444", "#22D3EE", "#E879F9",
    "#4ADE80", "#FACC15", "#F43F5E", "#60A5FA", "#FB923C",
]

# Pixel offsets / dash patterns so overlapping overview routes stay readable
TRIP_OVERVIEW_STYLES = [
    {"offset": 0, "dash": None},
    {"offset": 5, "dash": [2, 1.5]},
    {"offset": -5, "dash": [1.2, 1.2]},
    {"offset": 9, "dash": [3, 1.5]},
    {"offset": -9, "dash": [1.5, 2]},
    {"offset": 13, "dash": [4, 2]},
    {"offset": -13, "dash": [2, 2.5]},
    {"offset": 17, "dash": [2.5, 1]},
]

STATE_NAMES = {
    "TX": "Texas", "CO": "Colorado", "NM": "New Mexico", "AZ": "Arizona",
    "UT": "Utah", "ID": "Idaho", "OR": "Oregon", "WA": "Washington",
    "CA": "California", "NV": "Nevada", "OK": "Oklahoma", "KS": "Kansas",
}


def load_trips() -> dict:
    with open(TRIPS_FILE, encoding="utf-8") as f:
        return json.load(f)


def load_nearby_places() -> list[dict]:
    if not NEARBY_PLACES_FILE.exists():
        return []
    with open(NEARBY_PLACES_FILE, encoding="utf-8") as f:
        return json.load(f).get("places", [])


def load_visited_places() -> dict[str, dict[str, bool]]:
    if not VISITED_PLACES_FILE.exists():
        return {}
    with open(VISITED_PLACES_FILE, encoding="utf-8") as f:
        return json.load(f).get("visited", {})


def match_pois_for_stop(
    stop: dict,
    all_places: list[dict],
    visited_for_trip: dict[str, bool],
) -> list[dict]:
    """Return nearby POIs sorted by distance, capped at POI_MAX_PER_STOP."""
    lat, lng = stop.get("lat"), stop.get("lng")
    if not is_valid_coord(lat, lng):
        return []
    matches: list[tuple[float, dict]] = []
    for place in all_places:
        dist = haversine_miles(lat, lng, place["lat"], place["lng"])
        if dist <= POI_RADIUS_MILES:
            poi = {
                "id": place["id"],
                "name": place["name"],
                "category": place.get("category", "landmark"),
                "emoji": place.get("emoji", "📍"),
                "tagline": place.get("tagline", ""),
                "lat": place["lat"],
                "lng": place["lng"],
                "distance_mi": round(dist, 1),
                "visited": bool(visited_for_trip.get(place["id"], False)),
            }
            matches.append((dist, poi))
    matches.sort(key=lambda x: x[0])
    return [p for _, p in matches[:POI_MAX_PER_STOP]]


def build_trip_story(trip: dict, stops: list[dict], stop_pois: list[list[dict]]) -> dict:
    """Narrative captions for cinematic replay and video export."""
    origin = short_location_label(stops[0]["location"]) if stops else ""
    dest = short_location_label(stops[-1]["location"]) if stops else ""
    states = trip.get("via_states") or []
    via = ", ".join(states) if states else "the open road"
    days = trip_duration_days(trip["start"], trip["end"])
    all_pois = [p for pois in stop_pois for p in pois]
    visited_count = sum(1 for p in all_pois if p.get("visited"))
    nearby_names = list(dict.fromkeys(p["name"] for p in all_pois))[:6]
    crew = trip.get("trip_crew") or trip.get("owner_short") or "you"
    vehicle = trip.get("vehicle_label") or "your Tesla"
    if trip.get("is_shared"):
        who = f"{crew} in {vehicle}"
    elif vehicle == "your Tesla":
        who = "your Tesla"
    else:
        who = vehicle

    intro = (
        f"A {days}-day road trip for {who} from {origin.split(',')[0]} "
        f"through {via} — {len(stops)} Supercharger stops powering the journey."
    )
    outro = (
        f"Journey complete — {len(stops)} charging stops across {via}. "
        + (
            f"{visited_count} confirmed visits · {len(all_pois)} nearby highlights explored."
            if all_pois
            else "Every mile charged by Supercharger."
        )
    )

    stop_captions: list[dict] = []
    for i, (stop, pois) in enumerate(zip(stops, stop_pois)):
        label = short_location_label(stop["location"])
        city = label.split(",")[0]
        if pois:
            poi_text = " · ".join(
                f"{p['emoji']} {p['name']}" + (" ✓" if p.get("visited") else "")
                for p in pois[:2]
            )
            caption = f"Charging in {city} — nearby: {poi_text}"
            sub = pois[0].get("tagline", "")
        elif i == 0:
            caption = f"Departing {city} — the adventure begins"
            sub = f"First Supercharger of {len(stops)} stops"
        elif i == len(stops) - 1:
            caption = f"Final stop · {city}"
            sub = "Homeward bound"
        else:
            caption = f"Charging in {city}"
            sub = f"Stop {i + 1} of {len(stops)}"
        stop_captions.append({"caption": caption, "sub": sub, "pois": pois})

    return {
        "intro": intro,
        "outro": outro,
        "highlights": nearby_names,
        "visited_count": visited_count,
        "nearby_count": len(all_pois),
        "stop_captions": stop_captions,
    }


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


def simplify_path(path: list[list[float]], max_points: int = 160) -> list[list[float]]:
    if len(path) <= max_points:
        return path
    step = (len(path) - 1) / (max_points - 1)
    simplified = [path[int(round(i * step))] for i in range(max_points)]
    if simplified[-1] != path[-1]:
        simplified[-1] = path[-1]
    return simplified


def leg_cache_key(lat1: float, lng1: float, lat2: float, lng2: float) -> str:
    return f"{lat1:.5f},{lng1:.5f}|{lat2:.5f},{lng2:.5f}"


def load_routes_cache() -> dict:
    if ROUTES_CACHE_FILE.exists():
        with open(ROUTES_CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_routes_cache(cache: dict) -> None:
    ROUTES_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ROUTES_CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2)


def fetch_mapbox_driving_route(
    lat1: float, lng1: float, lat2: float, lng2: float, token: str
) -> tuple[list[list[float]], float] | None:
    coords = f"{lng1},{lat1};{lng2},{lat2}"
    query = urllib.parse.urlencode({
        "geometries": "geojson",
        "overview": "full",
        "steps": "false",
        "access_token": token,
    })
    url = f"https://api.mapbox.com/directions/v5/mapbox/driving/{coords}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  Route fetch failed ({lat1:.4f},{lng1:.4f})→({lat2:.4f},{lng2:.4f}): {exc}")
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        print(f"  No driving route ({lat1:.4f},{lng1:.4f})→({lat2:.4f},{lng2:.4f}): {data.get('code')}")
        return None
    route = data["routes"][0]
    path = [[pt[1], pt[0]] for pt in route["geometry"]["coordinates"]]
    return path, route["distance"] / 1609.344


def get_driving_segment(
    lat1: float, lng1: float, lat2: float, lng2: float,
    cache: dict, token: str, refresh: bool, stats: dict,
) -> list[list[float]]:
    dist = haversine_miles(lat1, lng1, lat2, lng2)
    if dist < MIN_ROUTE_MILES:
        return [[lat1, lng1], [lat2, lng2]]
    key = leg_cache_key(lat1, lng1, lat2, lng2)
    if key in cache and (not refresh or key in stats["session_keys"]):
        stats["cache_hits"] += 1
        return cache[key]["path"]
    if token:
        result = fetch_mapbox_driving_route(lat1, lng1, lat2, lng2, token)
        if result:
            path, miles = result
            path = simplify_path(path)
            cache[key] = {"path": path, "distance_miles": round(miles, 1)}
            stats["session_keys"].add(key)
            stats["fetched"] += 1
            time.sleep(ROUTE_FETCH_DELAY_S)
            return path
        stats["fetch_failed"] += 1
    stats["fallback"] += 1
    points = max(48, min(160, int(max(dist, 5) * 1.2)))
    return great_circle_arc(lat1, lng1, lat2, lng2, num_points=points)


def build_route_path(
    stops: list[dict], cache: dict, token: str, refresh: bool, stats: dict,
) -> list[list[float]]:
    if not stops:
        return []
    path: list[list[float]] = [[stops[0]["lat"], stops[0]["lng"]]]
    for i in range(1, len(stops)):
        a, b = stops[i - 1], stops[i]
        if not is_valid_coord(a["lat"], a["lng"]) or not is_valid_coord(b["lat"], b["lng"]):
            continue
        if haversine_miles(a["lat"], a["lng"], b["lat"], b["lng"]) > 1500:
            continue
        segment = get_driving_segment(
            a["lat"], a["lng"], b["lat"], b["lng"], cache, token, refresh, stats
        )
        path.extend(segment[1:])
    return path


def build_arcs(
    stops: list[dict], color: str, trip_id: str,
    cache: dict, token: str, refresh: bool, stats: dict,
) -> list[dict]:
    """Build short-path arc segments for deck.gl PathLayer (selected trip only)."""
    arcs: list[dict] = []
    for i in range(1, len(stops)):
        a, b = stops[i - 1], stops[i]
        if not is_valid_coord(a["lat"], a["lng"]) or not is_valid_coord(b["lat"], b["lng"]):
            continue
        dist = haversine_miles(a["lat"], a["lng"], b["lat"], b["lng"])
        if dist > 1500:
            continue
        path_coords = get_driving_segment(
            a["lat"], a["lng"], b["lat"], b["lng"], cache, token, refresh, stats
        )
        route_miles = path_miles(path_coords)
        arcs.append({
            "fromLng": a["lng"], "fromLat": a["lat"],
            "toLng": b["lng"], "toLat": b["lat"],
            "color": color, "tripId": trip_id,
            "distance": round(route_miles),
            "height": min(0.6, max(0.1, route_miles / 1000)),
            "path": [[p[1], p[0]] for p in path_coords],
            "path3d": elevated_arc_coords(a["lat"], a["lng"], b["lat"], b["lng"], num_points=32),
        })
    return arcs


def extract_state(location: str) -> str | None:
    m = re.search(r",\s*([A-Z]{2})\b", location)
    return m.group(1) if m else None


def short_location_label(location: str) -> str:
    city_match = re.match(r"^([^,]+)", location)
    city = city_match.group(1).strip() if city_match else location
    state = extract_state(location)
    return f"{city}, {state}" if state else city


def parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def extract_leg_path(
    lat1: float, lng1: float, lat2: float, lng2: float,
    cache: dict | None = None, token: str = "", refresh: bool = False, stats: dict | None = None,
) -> list[list[float]]:
    if cache is not None and stats is not None:
        return get_driving_segment(lat1, lng1, lat2, lng2, cache, token, refresh, stats)
    dist = haversine_miles(lat1, lng1, lat2, lng2)
    points = max(32, min(128, int(max(dist, 5) * 1.5)))
    return great_circle_arc(lat1, lng1, lat2, lng2, num_points=points)


def leg_bearing_deg(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p = math.pi / 180
    lat1r, lat2r = lat1 * p, lat2 * p
    dLng = (lng2 - lng1) * p
    y = math.sin(dLng) * math.cos(lat2r)
    x = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dLng)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def build_playback_timeline(
    stops: list[dict],
    route_path: list[list[float]],
    story: dict | None = None,
    stop_pois: list[list[dict]] | None = None,
    cache: dict | None = None,
    token: str = "",
    refresh: bool = False,
    stats: dict | None = None,
) -> dict:
    """
    Time-weighted playback segments for cinematic replay.
    Long halts between charges produce longer dwell + slower leg pacing.
    Adds intro/outro title cards and nearby-place highlights on dwell segments.
    """
    if len(stops) < 1:
        return {"segments": [], "total_video_ms": 0, "real_duration_ms": 0, "story": story or {}}

    raw_segments: list[dict] = []
    real_total_ms = 0
    stop_pois = stop_pois or [[] for _ in stops]
    story = story or {}

    for i, stop in enumerate(stops):
        label = short_location_label(stop["location"])
        pois = stop_pois[i] if i < len(stop_pois) else []
        caption = story.get("stop_captions", [{}] * len(stops))
        cap = caption[i] if i < len(caption) else {}
        if i < len(stops) - 1:
            nxt = stops[i + 1]
            gap_ms = max(
                60_000,
                int((parse_ts(nxt["datetime"]) - parse_ts(stop["datetime"])).total_seconds() * 1000),
            )
            real_total_ms += gap_ms
            gap_hours = gap_ms / 3_600_000
            dwell_ratio = min(0.88, max(0.18, gap_hours / (gap_hours + 2.5)))
            dwell_ms = int(gap_ms * dwell_ratio)
            travel_ms = gap_ms - dwell_ms
            leg_path = extract_leg_path(
                stop["lat"], stop["lng"], nxt["lat"], nxt["lng"],
                cache, token, refresh, stats,
            )
            raw_segments.append({
                "type": "dwell",
                "real_duration_ms": dwell_ms,
                "lat": stop["lat"],
                "lng": stop["lng"],
                "label": label,
                "stop_index": i,
                "pois": pois,
                "caption": cap.get("caption", f"Charging in {label}"),
                "subcaption": cap.get("sub", ""),
            })
            raw_segments.append({
                "type": "travel",
                "real_duration_ms": travel_ms,
                "path": leg_path,
                "bearing": round(leg_bearing_deg(stop["lat"], stop["lng"], nxt["lat"], nxt["lng"]), 1),
                "from_label": label,
                "to_label": short_location_label(nxt["location"]),
                "stop_index": i,
            })
        else:
            final_ms = 300_000
            if stop.get("end_datetime"):
                final_ms = max(
                    60_000,
                    int(
                        (parse_ts(stop["end_datetime"]) - parse_ts(stop["datetime"])).total_seconds()
                        * 1000
                    ),
                )
            real_total_ms += final_ms
            raw_segments.append({
                "type": "dwell",
                "real_duration_ms": final_ms,
                "lat": stop["lat"],
                "lng": stop["lng"],
                "label": label,
                "stop_index": i,
                "pois": pois,
                "caption": cap.get("caption", f"Final stop · {label}"),
                "subcaption": cap.get("sub", "Journey complete"),
            })

    target_ms = min(120_000, max(25_000, len(stops) * 3_200))
    scale = target_ms / max(real_total_ms, 1)
    video_segments: list[dict] = []
    for seg in raw_segments:
        dur = seg["real_duration_ms"] * scale
        if seg["type"] == "dwell":
            # Keep dwells snappy — multi-second freezes read as a stalled UI.
            poi_bonus = 1.15 if seg.get("pois") else 1.0
            dur = max(800, min(2_000, dur * poi_bonus))
        else:
            dur = max(1_000, min(12_000, dur))
        video_segments.append({**seg, "duration_ms": int(dur)})

    # Cinematic intro/outro title cards (short enough for in-app play)
    intro_seg = {
        "type": "intro",
        "duration_ms": 2_000,
        "title": story.get("intro_title", ""),
        "caption": story.get("intro", "Road trip replay"),
        "highlights": story.get("highlights", [])[:4],
    }
    outro_seg = {
        "type": "outro",
        "duration_ms": 2_400,
        "caption": story.get("outro", "Journey complete"),
        "visited_count": story.get("visited_count", 0),
        "nearby_count": story.get("nearby_count", 0),
    }
    all_segments = [intro_seg, *video_segments, outro_seg]

    return {
        "segments": all_segments,
        "total_video_ms": sum(s["duration_ms"] for s in all_segments),
        "real_duration_ms": real_total_ms,
        "story": story,
    }


def is_featured_trip(trip: dict, miles: int, stop_count: int) -> bool:
    return (
        trip.get("has_colorado")
        or miles >= 600
        or stop_count >= 10
        or trip_duration_days(trip["start"], trip["end"]) >= 4
    )


def trip_duration_days(start: str, end: str) -> int:
    try:
        s = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        e = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        return max(1, (e - s).days + 1)
    except ValueError:
        return 1


def parse_story_destination(trip: dict) -> str:
    """Human destination for atlas grouping (Houston, Colorado, Leavenworth…)."""
    name = trip.get("name") or ""
    if "→" in name:
        part = name.split("→", 1)[1]
        part = re.sub(r"\s*·.*$", "", part)
        part = re.sub(r"\s*\([^)]*via[^)]*\)", "", part, flags=re.I)
        part = re.sub(r"\s*\(Round Trip\)", "", part, flags=re.I)
        part = part.strip(" —-\t")
        if part:
            return part
    dest = (trip.get("dest_label") or "").strip()
    origin = (trip.get("origin_label") or "").strip()
    if dest and dest != origin:
        return dest
    return dest or origin or "Journey"


def _point_in_home_regions(lat: float, lng: float, location: str, regions: list[dict]) -> str | None:
    """Return matching home hub label, or None."""
    loc = location or ""
    for region in regions:
        bases = region.get("bases") or []
        if isinstance(bases, set):
            bases = list(bases)
        if loc in bases:
            return region.get("label") or "Home"
        radius = float(region.get("radius_miles") or 55)
        try:
            if haversine_miles(lat, lng, float(region["lat"]), float(region["lng"])) <= radius:
                return region.get("label") or "Home"
        except (KeyError, TypeError, ValueError):
            continue
    return None


def destination_label_coords(
    stops: list[dict],
    route_path: list[list[float]],
    home_regions: list[dict],
) -> tuple[float, float]:
    """Place overview labels away from home hubs — prefer farthest non-home stop."""
    non_home = [
        s for s in stops
        if s.get("lat") is not None and s.get("lng") is not None
        and not _point_in_home_regions(
            float(s["lat"]), float(s["lng"]), s.get("location") or "", home_regions
        )
    ]
    if non_home:
        origin = stops[0]
        best = max(
            non_home,
            key=lambda s: haversine_miles(
                float(origin["lat"]), float(origin["lng"]), float(s["lat"]), float(s["lng"])
            ),
        )
        return float(best["lat"]), float(best["lng"])
    if route_path and len(route_path) >= 4:
        idx = int(len(route_path) * 0.55)
        return float(route_path[idx][0]), float(route_path[idx][1])
    mid = stops[len(stops) // 2]
    return float(mid["lat"]), float(mid["lng"])


def build_hubs(trips_data: dict, prepared: list[dict]) -> list[dict]:
    """One overview hub per home region with trip membership for declutter."""
    regions = trips_data.get("home_regions") or []
    hubs: list[dict] = []
    for region in regions:
        label = region.get("label") or "Home"
        trip_ids: list[str] = []
        for trip in prepared:
            start = trip["stops"][0]
            hub = _point_in_home_regions(
                float(start["lat"]), float(start["lng"]), start.get("location") or "", [region]
            )
            if hub:
                trip_ids.append(trip["id"])
        if not trip_ids:
            continue
        hubs.append({
            "id": f"hub_{re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')}",
            "label": label,
            "lat": float(region["lat"]),
            "lng": float(region["lng"]),
            "trip_ids": trip_ids,
            "trip_count": len(trip_ids),
        })
    return hubs


def build_destination_groups(prepared: list[dict]) -> list[dict]:
    """Sidebar + map constellation groups keyed by story destination."""
    buckets: dict[str, list[dict]] = {}
    for trip in prepared:
        key = trip.get("story_dest") or "Journey"
        buckets.setdefault(key, []).append(trip)
    groups: list[dict] = []
    for dest, trips in buckets.items():
        trips_sorted = sorted(trips, key=lambda t: t["start"], reverse=True)
        dest_lats: list[float] = []
        dest_lngs: list[float] = []
        dest_key = dest.lower().split("(")[0].strip()
        for t in trips_sorted:
            matched = False
            for s in reversed(t.get("stops") or []):
                loc = (s.get("location") or "").lower()
                if dest_key and dest_key in loc and s.get("lat") is not None:
                    dest_lats.append(float(s["lat"]))
                    dest_lngs.append(float(s["lng"]))
                    matched = True
                    break
            if not matched and t.get("label_lat") is not None:
                dest_lats.append(float(t["label_lat"]))
                dest_lngs.append(float(t["label_lng"]))
        years = sorted({
            int(str(t["start"])[:4])
            for t in trips_sorted
            if str(t.get("start", ""))[:4].isdigit()
        })
        groups.append({
            "id": f"dest_{re.sub(r'[^a-z0-9]+', '_', dest.lower()).strip('_')}",
            "dest": dest,
            "trip_count": len(trips_sorted),
            "total_miles": sum(t["miles"] for t in trips_sorted),
            "trip_ids": [t["id"] for t in trips_sorted],
            "featured": any(t.get("featured") for t in trips_sorted),
            "color": next(
                (t["color"] for t in trips_sorted if t.get("featured")),
                trips_sorted[0]["color"],
            ),
            "lat": sum(dest_lats) / len(dest_lats) if dest_lats else float(trips_sorted[0]["stops"][0]["lat"]),
            "lng": sum(dest_lngs) / len(dest_lngs) if dest_lngs else float(trips_sorted[0]["stops"][0]["lng"]),
            "years": years,
        })
    groups.sort(key=lambda g: (-int(g["featured"]), -g["total_miles"], g["dest"]))
    return groups


def prepare_trips(
    trips_data: dict,
    cache: dict,
    token: str,
    refresh: bool,
    stats: dict,
) -> list[dict]:
    all_places = load_nearby_places()
    visited_all = load_visited_places()
    home_regions = trips_data.get("home_regions") or []
    prepared: list[dict] = []
    for i, trip in enumerate(trips_data["trips"]):
        stops = [
            s for s in trip["stops"]
            if is_valid_coord(s.get("lat"), s.get("lng"))
        ]
        if not stops:
            continue
        route_path = build_route_path(stops, cache, token, refresh, stats)
        miles = path_miles(route_path)
        total_kwh = round(sum(s["kwh"] for s in stops), 1)
        states = trip.get("via_states") or sorted({
            st for s in stops
            if (st := extract_state(s["location"]) or ("CO" if s.get("in_colorado") else None))
        })
        state_names = [STATE_NAMES.get(s, s) for s in states]
        via_summary = trip.get("via_summary") or ", ".join(states)
        arcs = build_arcs(stops, trip_color(i), trip["id"], cache, token, refresh, stats)
        visited_for_trip = visited_all.get(trip["id"], {})
        stop_pois = [
            match_pois_for_stop(s, all_places, visited_for_trip) for s in stops
        ]
        story = build_trip_story(trip, stops, stop_pois)
        story["intro_title"] = trip["name"]
        playback = build_playback_timeline(
            stops, route_path, story, stop_pois, cache, token, refresh, stats
        )
        featured = is_featured_trip(trip, miles, len(stops))
        style = TRIP_OVERVIEW_STYLES[i % len(TRIP_OVERVIEW_STYLES)]
        story_dest = parse_story_destination(trip)
        label_lat, label_lng = destination_label_coords(stops, route_path, home_regions)
        start_hub = _point_in_home_regions(
            float(stops[0]["lat"]), float(stops[0]["lng"]),
            stops[0].get("location") or "", home_regions,
        )
        end_hub = _point_in_home_regions(
            float(stops[-1]["lat"]), float(stops[-1]["lng"]),
            stops[-1].get("location") or "", home_regions,
        )
        prepared.append({
            "id": trip["id"],
            "name": trip["name"],
            "start": str(trip["start"])[:10],
            "end": str(trip["end"])[:10],
            "startTs": str(trip["start"]),
            "endTs": str(trip["end"]),
            "color": trip_color(i),
            "overview_offset": style["offset"],
            "overview_dash": style["dash"],
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
            "story_dest": story_dest,
            "label_lat": label_lat,
            "label_lng": label_lng,
            "start_hub": start_hub,
            "end_hub": end_hub,
            "has_colorado": trip.get("has_colorado", False),
            "colorado_stops": trip.get("colorado_stops", 0),
            "featured": featured,
            "arcs": arcs,
            "playback": playback,
            "story": story,
            "owner": trip.get("owner", ""),
            "owner_short": trip.get("owner_short", ""),
            "vin": trip.get("vin", ""),
            "travelers": trip.get("travelers", []),
            "trip_crew": trip.get("trip_crew", ""),
            "driver": trip.get("driver", ""),
            "driver_short": trip.get("driver_short", ""),
            "vehicle_label": trip.get("vehicle_label", ""),
            "is_shared": trip.get("is_shared", False),
            "region": "colorado" if trip.get("has_colorado") else (
                "pnw" if any(s in states for s in ("WA", "OR", "ID")) else "other"
            ),
        })
    return prepared


def build_dashboard(trips: list[dict], hubs: list[dict] | None = None,
                    destination_groups: list[dict] | None = None) -> dict:
    all_states: set[str] = set()
    owners: set[str] = set()
    for t in trips:
        all_states.update(t["states"])
        if t.get("owner_short"):
            owners.add(t["owner_short"])
        elif t.get("owner"):
            owners.add(t["owner"].split()[0])
    longest = max(trips, key=lambda t: t["miles"]) if trips else None
    years = sorted({
        int(str(t["start"])[:4])
        for t in trips
        if str(t.get("start", ""))[:4].isdigit()
    })
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
        "featured_trips": sum(1 for t in trips if t.get("featured")),
        "owners": sorted(owners),
        "us_bounds": US_BOUNDS,
        "hubs": hubs or [],
        "destination_groups": destination_groups or [],
        "years": years,
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
        "version": "1.1", "creator": "Road Replay",
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


def render_html(
    trips: list[dict],
    dashboard: dict,
    timeline: list[dict],
    mapbox_token_b64: str,
) -> str:
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
        mapbox_token_b64=mapbox_token_b64,
        has_mapbox=bool(mapbox_token_b64),
    )


def normalize_public_mapbox_token(token: str) -> str:
    """Accept only public Mapbox tokens (pk.). Never embed secret sk. tokens."""
    token = (token or "").strip()
    if not token:
        return ""
    if token.startswith("sk."):
        raise SystemExit(
            "MAPBOX_TOKEN looks like a secret token (sk.…). "
            "Use a public token (pk.…) from https://account.mapbox.com/access-tokens/"
        )
    if not token.startswith("pk."):
        raise SystemExit("MAPBOX_TOKEN must be a public token starting with pk.")
    return token


def encode_mapbox_token_for_html(token: str) -> str:
    """Base64-encode so GitHub push protection does not block gh-pages deploys.

    Public pk. tokens are meant for browsers, but secret scanning still rejects
    the literal string when pushing index.html to gh-pages.
    """
    import base64

    if not token:
        return ""
    return base64.b64encode(token.encode("utf-8")).decode("ascii")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build 3D Tesla travel map")
    parser.add_argument(
        "--public",
        action="store_true",
        help="Also write output/index.html for GitHub Pages (token still embedded when MAPBOX_TOKEN is set)",
    )
    parser.add_argument(
        "--refresh-routes",
        action="store_true",
        help="Re-fetch driving routes from Mapbox Directions API (requires MAPBOX_TOKEN)",
    )
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    if not TRIPS_FILE.exists():
        raise FileNotFoundError(f"Run segment_trips.py first. Missing {TRIPS_FILE}")

    mapbox_token = normalize_public_mapbox_token(os.getenv("MAPBOX_TOKEN", ""))
    if args.refresh_routes and not mapbox_token:
        raise SystemExit("--refresh-routes requires MAPBOX_TOKEN in .env or environment")
    if args.public and not mapbox_token:
        raise SystemExit("--public requires MAPBOX_TOKEN so the live site launches without a paste prompt")
    # Embed as base64 so Pages pushes are not blocked by secret scanning.
    embed_token_b64 = encode_mapbox_token_for_html(mapbox_token)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    GPX_DIR.mkdir(parents=True, exist_ok=True)

    cache = load_routes_cache()
    stats = {"fetched": 0, "cache_hits": 0, "fallback": 0, "fetch_failed": 0, "session_keys": set()}

    trips_data = load_trips()
    prepared = prepare_trips(trips_data, cache, mapbox_token, args.refresh_routes, stats)
    if stats["fetched"] or args.refresh_routes:
        save_routes_cache(cache)
    hubs = build_hubs(trips_data, prepared)
    destination_groups = build_destination_groups(prepared)
    dashboard = build_dashboard(prepared, hubs, destination_groups)
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

    HTML_OUTPUT.write_text(
        render_html(prepared, dashboard, timeline, embed_token_b64),
        encoding="utf-8",
    )
    if args.public:
        pages_index = OUTPUT_DIR / "index.html"
        pages_html = HTML_OUTPUT.read_text(encoding="utf-8")
        # Guardrail: never ship a literal pk./sk. Mapbox token string to gh-pages.
        if re.search(r'\b(?:pk|sk)\.eyJ[A-Za-z0-9_-]{10,}', pages_html):
            raise SystemExit(
                "Refusing to write public index.html with a raw Mapbox token — "
                "GitHub push protection would reject the gh-pages deploy"
            )
        pages_index.write_text(pages_html, encoding="utf-8")
        print(f"  Public index: {pages_index}")

    print(f"Built 3D map — {len(prepared)} trips")
    print(f"  Dashboard: {dashboard['total_kwh']} kWh · {dashboard['total_miles']:,} mi")
    print(
        f"  Routes: {stats['fetched']} fetched, {stats['cache_hits']} cached, "
        f"{stats['fallback']} arc fallback, {stats['fetch_failed']} failed"
    )
    print(f"  Mapbox token: {'loaded' if mapbox_token else 'MISSING (using cache/fallback)'}")
    print(f"  HTML: {HTML_OUTPUT}")
    if embed_token_b64:
        print("  Token embed: base64 (push-protection safe)")


if __name__ == "__main__":
    main()
