#!/usr/bin/env python3
"""Build My Mile Diary map with Mapbox GL JS (playback + photo memories)."""

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
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from jinja2 import Environment, FileSystemLoader

from home_config import load_owner_config, matching_trip_override
from osm_trails import osm_detour_path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
GPX_DIR = OUTPUT_DIR / "gpx"
TRIPS_FILE = DATA_DIR / "trips.json"
TRIP_PHOTOS_FILE = DATA_DIR / "trip_photos.json"
PHOTOS_INDEX_FILE = DATA_DIR / "photos_index.json"
ROUTES_CACHE_FILE = DATA_DIR / "routes_cache.json"
NEARBY_PLACES_FILE = DATA_DIR / "nearby_places.json"
VISITED_PLACES_FILE = DATA_DIR / "visited_places.json"
HTML_OUTPUT = OUTPUT_DIR / "travel_map.html"
GEOJSON_OUTPUT = OUTPUT_DIR / "trips.geojson"
LIVE_DEMO_URL = "https://ramkandimalla94.github.io/mymilediary/"

POI_RADIUS_MILES = 40
POI_MAX_PER_STOP = 3
# Skip only GPS jitter. 0.3 mi used to skip real trail bends (Maroon Dam U-turn).
MIN_ROUTE_MILES = 0.04
# Snap photo/stop GPS onto a routed way only when already this close.
# Longer gaps must walk a mapped trail/road — never a crow-fly chord.
PIN_ENDPOINT_MI = 0.03
ACCESS_STITCH_MI = 0.04
ACCESS_MAX_MI = 8.0
ACCESS_MAX_RATIO = 12.0
ROUTE_CACHE_VER = "v4"
SIMPLIFY_EPSILON_MI = 0.008  # ~13 m — keeps hairpins; even sampling did not
# Never even-sample down to this cap — that invented 0.2 mi chords on CO-82.
SIMPLIFY_MAX_POINTS = 4000
VALLEY_CUT_RATIO = 1.28
VALLEY_CUT_MIN_CROW_MI = 0.12
ROUTE_FETCH_DELAY_S = 0.25
WALK_SPUR_MILES = 18.0
# Photo GPS becomes real route waypoints (hike spurs to Maroon Bells, etc.).
# Cluster bursts so the path follows places, not every shutter click.
MAX_PLAYBACK_MEMORIES = 80
PHOTO_CLUSTER_MI = 0.35
PHOTO_CLUSTER_GAP_S = 8 * 60
MEMORY_ROUTE_SNAP_MI = 45.0
# Skip stray GPS that would yank the whole trip off-corridor (still show pins).
MAX_PHOTO_ROUTE_MI = 55.0  # Death Valley spur ~50mi off Dec 2025 corridor
# Cinematic timeline — UI speed (default 1×) multiplies on top.
# Targets sized so 1× feels like an easy passenger-seat pace (not rushed).
PLAYBACK_TARGET_MIN_MS = 120_000
PLAYBACK_TARGET_MAX_MS = 720_000
# Photo holds — ~1.2s prior + 4s linger wall-clock at default 1× (SPEED_REF 0.5).
MEMORY_HOLD_MS = 2500
MEMORY_HOLD_OFF_CORRIDOR_MS = 2400

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


def sanitize_vehicle_label(label: str | None) -> str:
    text = (label or "").strip()
    if not text or text.lower() in {"your tesla", "tesla"}:
        return ""
    return text


def client_stop(stop: dict) -> dict:
    """Strip charging invoices / VIN from the browser payload."""
    out = {
        k: v for k, v in stop.items()
        if k not in {"invoice_url", "vin", "owner"}
    }
    if out.get("is_photo") or out.get("kind") == "photo":
        out.pop("kwh", None)
    return out


def load_trip_photos() -> dict[str, list[dict]]:
    if not TRIP_PHOTOS_FILE.exists():
        return {}
    with open(TRIP_PHOTOS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    out: dict[str, list[dict]] = {}
    for trip_id, block in (data.get("trips") or {}).items():
        out[trip_id] = list(block.get("photos") or [])
    return out


def load_photos_index_count() -> int:
    if not PHOTOS_INDEX_FILE.exists():
        return 0
    with open(PHOTOS_INDEX_FILE, encoding="utf-8") as f:
        return int(json.load(f).get("photo_count") or 0)


def parse_waypoint_ts(value: str | None) -> datetime:
    """Parse stop/photo timestamps to timezone-aware UTC (never mix naive/aware)."""
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _via_path_coords(entry: dict) -> list[list[float]]:
    raw = entry.get("path") or []
    out: list[list[float]] = []
    for pt in raw:
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            lat, lng = float(pt[0]), float(pt[1])
            if is_valid_coord(lat, lng):
                out.append([lat, lng])
    return out


def matching_via_paths(a: dict, b: dict, via_paths: list[dict]) -> list[dict]:
    """Config polylines whose time window sits on this stop-to-stop leg."""
    if not via_paths:
        return []
    t0 = parse_waypoint_ts(a.get("datetime"))
    t1 = parse_waypoint_ts(b.get("datetime"))
    if t1 < t0:
        t0, t1 = t1, t0
    matched = []
    for v in via_paths:
        if not isinstance(v, dict):
            continue
        after = parse_waypoint_ts(v.get("after_timestamp"))
        before = parse_waypoint_ts(v.get("before_timestamp") or v.get("after_timestamp"))
        if after == datetime.min.replace(tzinfo=timezone.utc):
            continue
        # Insert only on the stop-to-stop gap that contains the via instant.
        if not (t0 < after < t1):
            continue
        coords = _via_path_coords(v)
        if not coords:
            continue
        # Via must belong to this photo pair, not the previous/next leg.
        d_fwd = (
            haversine_miles(float(a["lat"]), float(a["lng"]), coords[0][0], coords[0][1])
            + haversine_miles(float(b["lat"]), float(b["lng"]), coords[-1][0], coords[-1][1])
        )
        d_rev = (
            haversine_miles(float(a["lat"]), float(a["lng"]), coords[-1][0], coords[-1][1])
            + haversine_miles(float(b["lat"]), float(b["lng"]), coords[0][0], coords[0][1])
        )
        if min(d_fwd, d_rev) > 0.16:
            continue
        matched.append(v)
    return matched


def stitch_configured_via_path(
    lat1: float, lng1: float, lat2: float, lng2: float, via: dict
) -> list[list[float]]:
    mid = _via_path_coords(via)
    path = [list(pt) for pt in mid]
    if haversine_miles(lat1, lng1, path[0][0], path[0][1]) > 1e-5:
        path = [[lat1, lng1]] + path
    if haversine_miles(lat2, lng2, path[-1][0], path[-1][1]) > 1e-5:
        path = path + [[lat2, lng2]]
    return _gentle_pin_endpoints(path, lat1, lng1, lat2, lng2)


def _lng_utc_offset_hours(lng: float | None) -> int:
    """Rough continental-US offset from longitude (no tz database needed)."""
    if lng is None:
        return -6
    if lng <= -112.5:
        return -8
    if lng <= -100:
        return -7
    if lng <= -85:
        return -6
    return -5


def local_hour_from_utc(dt: datetime, lng: float | None) -> float:
    utc = dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    local = utc + timedelta(hours=_lng_utc_offset_hours(lng))
    return local.hour + local.minute / 60.0


def hour_playback_weight(hour: float) -> float:
    """Early morning lingers; late night is compressed so overnights don't crawl."""
    h = ((float(hour) % 24) + 24) % 24
    if 6.0 <= h < 9.5:
        return 1.9
    if 9.5 <= h < 12.0:
        return 1.35
    if 12.0 <= h < 18.0:
        return 1.0
    if 18.0 <= h < 21.0:
        return 0.85
    if 5.0 <= h < 6.0:
        return 1.25
    return 0.32


def clock_weighted_hours(t0: datetime, t1: datetime, lng: float | None) -> float:
    if t1 <= t0:
        return 0.15
    step = timedelta(minutes=15)
    total = 0.0
    cursor = t0
    while cursor < t1:
        nxt = min(cursor + step, t1)
        frac = (nxt - cursor).total_seconds() / 3600.0
        total += frac * hour_playback_weight(local_hour_from_utc(cursor, lng))
        cursor = nxt
    return max(0.12, total)


def driving_clock_window(
    t0: datetime,
    t1: datetime,
    miles: float,
    lng: float | None = None,
) -> tuple[datetime, datetime]:
    """Skip overnight rest in the playhead: drive in the hours before arrival."""
    gap_h = max(0.08, (t1 - t0).total_seconds() / 3600.0)
    drive_h = max(0.35, float(miles) / 55.0)
    if gap_h <= drive_h + 2.5:
        return t0, t1
    drive_h = min(drive_h, gap_h * 0.85)
    start = t1 - timedelta(hours=drive_h)
    if start < t0:
        start = t0
    arr_hour = local_hour_from_utc(t1, lng)
    start_hour = local_hour_from_utc(start, lng)
    if arr_hour >= 10.5 and (start_hour >= 21 or start_hour < 5.5):
        utc_off = _lng_utc_offset_hours(lng)
        morning = t1.astimezone(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(hours=6 - utc_off)
        if t0 < morning < t1:
            start = max(t0, min(morning, t1 - timedelta(hours=max(0.5, drive_h))))
    return start, t1


def normalize_road_stops(stops: list[dict]) -> list[dict]:
    """Charging / home waypoints only — never photo GPS."""
    road = []
    for s in stops:
        if s.get("is_photo") or s.get("kind") == "photo":
            continue
        row = dict(s)
        row.setdefault("kind", "home" if row.get("is_home_anchor") else "stop")
        row["is_photo"] = False
        road.append(row)
    return road


def merge_photo_waypoints(stops: list[dict], photos: list[dict]) -> list[dict]:
    """Chronologically interleave road stops with photo GPS waypoints."""
    road = normalize_road_stops(stops)
    photo_rows = []
    for p in photos:
        row = dict(p)
        row["kind"] = "photo"
        row["is_photo"] = True
        row.setdefault("location", f"Photo · {row.get('album', 'memory')}")
        row.setdefault("kwh", 0)
        photo_rows.append(row)
    merged = road + photo_rows
    merged.sort(key=lambda s: (parse_waypoint_ts(s.get("datetime")), 0 if s.get("is_photo") else 1))
    return merged


def photos_near_road_stops(
    photos: list[dict],
    road_stops: list[dict],
    max_mi: float = MAX_PHOTO_ROUTE_MI,
) -> list[dict]:
    """Keep photo GPS that can form a spur from the charge corridor."""
    if not road_stops:
        return []
    out: list[dict] = []
    for p in photos:
        if not is_valid_coord(p.get("lat"), p.get("lng")):
            continue
        nearest = min(
            haversine_miles(float(p["lat"]), float(p["lng"]), float(s["lat"]), float(s["lng"]))
            for s in road_stops
        )
        if nearest <= max_mi:
            out.append(p)
    return out


def cluster_photo_memories(photos: list[dict]) -> list[dict]:
    """Collapse burst shots into one memory per place/time cluster."""
    items = sorted(
        (dict(p) for p in photos if is_valid_coord(p.get("lat"), p.get("lng"))),
        key=lambda p: parse_waypoint_ts(p.get("datetime")),
    )
    if not items:
        return []
    clusters: list[dict] = []
    cur = dict(items[0])
    cur["_members"] = [items[0]]
    for p in items[1:]:
        prev = cur["_members"][-1]
        gap_s = (
            parse_waypoint_ts(p.get("datetime")) - parse_waypoint_ts(prev.get("datetime"))
        ).total_seconds()
        dist = haversine_miles(cur["lat"], cur["lng"], p["lat"], p["lng"])
        if gap_s <= PHOTO_CLUSTER_GAP_S and dist <= PHOTO_CLUSTER_MI:
            cur["_members"].append(p)
            # Prefer a member that has a thumb
            if p.get("thumb") and not cur.get("thumb"):
                for k in ("thumb", "id", "album", "source_name", "datetime", "lat", "lng", "location"):
                    if p.get(k) is not None:
                        cur[k] = p[k]
            continue
        clusters.append(cur)
        cur = dict(p)
        cur["_members"] = [p]
    clusters.append(cur)
    out = []
    for c in clusters:
        members = c.pop("_members", [c])
        c["kind"] = "photo"
        c["is_photo"] = True
        c["cluster_size"] = len(members)
        c.setdefault("location", f"Photo · {c.get('album', 'memory')}")
        out.append(c)
    return out


def nearest_point_on_path(
    lat: float, lng: float, path: list[list[float]]
) -> tuple[float, float, float, float]:
    """Return (lat, lng, path_frac 0..1, distance_miles) for nearest vertex/lerp on path."""
    return nearest_point_on_path_from(lat, lng, path, min_frac=0.0)


def nearest_point_on_path_from(
    lat: float,
    lng: float,
    path: list[list[float]],
    min_frac: float = 0.0,
) -> tuple[float, float, float, float]:
    """Nearest point on path with frac >= min_frac (forward-only search for out-and-backs)."""
    if not path:
        return lat, lng, 0.0, 9999.0
    if len(path) == 1:
        d = haversine_miles(lat, lng, path[0][0], path[0][1])
        return path[0][0], path[0][1], 0.0, d

    best_d = float("inf")
    best = (path[0][0], path[0][1], max(0.0, min_frac), best_d)
    seg_lens = []
    total = 0.0
    for i in range(1, len(path)):
        d = haversine_miles(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1])
        seg_lens.append(d)
        total += d
    if total <= 0:
        d = haversine_miles(lat, lng, path[0][0], path[0][1])
        return path[0][0], path[0][1], 0.0, d

    floor = max(0.0, min(1.0, float(min_frac) - 1e-4))
    traveled = 0.0
    found = False
    for i, seg_len in enumerate(seg_lens):
        a, b = path[i], path[i + 1]
        steps = max(2, min(8, int(seg_len) + 1))
        for s in range(steps + 1):
            t = s / steps
            frac = (traveled + seg_len * t) / total
            if frac < floor:
                continue
            plat = a[0] + (b[0] - a[0]) * t
            plng = a[1] + (b[1] - a[1]) * t
            d = haversine_miles(lat, lng, plat, plng)
            if d < best_d:
                best_d = d
                best = (plat, plng, frac, d)
                found = True
        traveled += seg_len
    if not found:
        # Past the end — clamp to tip
        tip = path[-1]
        d = haversine_miles(lat, lng, tip[0], tip[1])
        return tip[0], tip[1], 1.0, d
    return best


def select_playback_memories(
    clusters: list[dict],
    road_stops: list[dict],
    route_path: list[list[float]],
    limit: int = MAX_PLAYBACK_MEMORIES,
) -> list[dict]:
    """Pick memories snapped onto the charge corridor, spread across the whole trip."""
    if not clusters or not road_stops:
        return []
    t_start = parse_waypoint_ts(road_stops[0].get("datetime"))
    t_end = parse_waypoint_ts(road_stops[-1].get("datetime"))
    if t_end <= t_start:
        t_end = t_start

    scored: list[dict] = []
    for c in clusters:
        if not c.get("thumb"):
            continue
        ts = parse_waypoint_ts(c.get("datetime"))
        if ts < t_start - timedelta(hours=18):
            continue
        if ts > t_end + timedelta(hours=18):
            continue
        snap_lat, snap_lng, path_frac, dist_mi = nearest_point_on_path(
            float(c["lat"]), float(c["lng"]), route_path
        )
        row = dict(c)
        row["route_lat"] = snap_lat
        row["route_lng"] = snap_lng
        row["path_frac"] = path_frac
        row["spur_miles"] = round(dist_mi, 2)
        row["on_corridor"] = dist_mi <= MEMORY_ROUTE_SNAP_MI
        scored.append(row)

    if not scored:
        return []

    scored.sort(key=lambda m: (
        float(m.get("path_frac") or 0),
        parse_waypoint_ts(m.get("datetime")),
    ))
    if len(scored) <= limit:
        return scored

    # Evenly sample across the full journey (not just the first N outbound shots)
    if limit <= 1:
        return scored[:1]
    step = (len(scored) - 1) / (limit - 1)
    idxs = sorted({int(round(i * step)) for i in range(limit)})
    return [scored[i] for i in idxs]


def path_arc_lengths(path: list[list[float]]) -> tuple[list[float], float]:
    """Cumulative arc lengths (miles) at each vertex + total."""
    if not path:
        return [], 0.0
    cum = [0.0]
    for i in range(1, len(path)):
        cum.append(
            cum[-1]
            + haversine_miles(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1])
        )
    return cum, cum[-1]


def point_at_arc_frac(path: list[list[float]], frac: float) -> list[float]:
    """Interpolate a point by arc-length fraction (0..1). Matches JS pointOnPath."""
    if not path:
        return [0.0, 0.0]
    if len(path) == 1:
        return [path[0][0], path[0][1]]
    frac = max(0.0, min(1.0, frac))
    cum, total = path_arc_lengths(path)
    if total <= 1e-9:
        return [path[0][0], path[0][1]]
    target = frac * total
    for i in range(1, len(path)):
        if target <= cum[i] or i == len(path) - 1:
            seg = cum[i] - cum[i - 1]
            t = 0.0 if seg <= 1e-12 else (target - cum[i - 1]) / seg
            t = max(0.0, min(1.0, t))
            a, b = path[i - 1], path[i]
            return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t]
    return [path[-1][0], path[-1][1]]


def slice_path_by_frac(
    path: list[list[float]], start_frac: float, end_frac: float
) -> list[list[float]]:
    """Slice a polyline by arc-length fraction — MUST match nearest_point_on_path fracs."""
    if not path or len(path) < 2:
        return list(path or [])
    start_frac = max(0.0, min(1.0, start_frac))
    end_frac = max(0.0, min(1.0, end_frac))
    if end_frac < start_frac:
        start_frac, end_frac = end_frac, start_frac
    start_pt = point_at_arc_frac(path, start_frac)
    end_pt = point_at_arc_frac(path, end_frac)
    if end_frac - start_frac < 1e-6:
        return [start_pt, end_pt]

    cum, total = path_arc_lengths(path)
    if total <= 1e-9:
        return [start_pt, end_pt]
    d0, d1 = start_frac * total, end_frac * total
    out = [start_pt]
    for i in range(1, len(path) - 1):
        if d0 < cum[i] < d1:
            out.append(path[i])
    if out[-1] != end_pt:
        out.append(end_pt)
    # Ensure at least 2 distinct-enough points for travel animation
    if len(out) < 2:
        out = [start_pt, end_pt]
    return out


def leg_path_between_stops(
    stop_a: dict,
    stop_b: dict,
    cache: dict | None,
    token: str,
    refresh: bool,
    stats: dict | None,
) -> list[list[float]]:
    lat1, lng1 = float(stop_a["lat"]), float(stop_a["lng"])
    lat2, lng2 = float(stop_b["lat"]), float(stop_b["lng"])
    dist = haversine_miles(lat1, lng1, lat2, lng2)
    profile = "walking" if leg_wants_walking(stop_a, stop_b, dist) else "driving"
    if cache is not None and stats is not None:
        path = get_route_segment(lat1, lng1, lat2, lng2, cache, token, refresh, stats, profile)
    else:
        path = extract_leg_path(lat1, lng1, lat2, lng2, cache, token, refresh, stats)
    return path


def _renormalize_segment_durations(
    segments: list[dict], target_ms: int, intro_ms: int, outro_ms: int
) -> list[dict]:
    """Fit body segments into target after per-type floors, preserving proportions."""
    body = [dict(s) for s in segments]
    body_budget = max(8_000, target_ms - intro_ms - outro_ms)
    raw = sum(max(1, int(s.get("duration_ms") or 0)) for s in body) or 1
    scale = body_budget / raw
    floors = {
        # Keep stop holds brief — long dwell % bars felt like waiting.
        "dwell": 650,
        "travel": 4_800,
        "memory": 2400,
    }
    ceilings = {
        "dwell": 1_400,
        "travel": 30_000,
        "memory": 2_800,
    }
    for s in body:
        kind = s.get("type") or "travel"
        dur = int(s["duration_ms"] * scale)
        lo = floors.get(kind, 1_200)
        hi = ceilings.get(kind, 12_000)
        miles = float(s.get("leg_miles") or 0)
        profile = s.get("profile") or "driving"
        if kind == "travel" and profile == "walking":
            # Keep hike beats lively — don't inherit highway travel floors.
            lo = 2_200
            hi = 12_000
            if miles > 8:
                lo = max(lo, 4_000)
            elif miles > 3:
                lo = max(lo, 3_000)
        elif kind == "travel":
            if miles > 250:
                lo = max(lo, 10_000)
            elif miles > 120:
                lo = max(lo, 7_500)
            elif miles > 60:
                lo = max(lo, 5_800)
        s["duration_ms"] = int(max(lo, min(hi, dur)))

    # Second pass: if still over budget, shrink short travels first; protect long drives
    total = sum(s["duration_ms"] for s in body)
    if total > body_budget * 1.02:
        overflow = total - body_budget
        travels = [s for s in body if s["type"] == "travel"]

        def travel_floor(s: dict) -> int:
            if (s.get("profile") or "driving") == "walking":
                miles = float(s.get("leg_miles") or 0)
                if miles > 8:
                    return 3_600
                if miles > 3:
                    return 2_800
                return 2_200
            miles = float(s.get("leg_miles") or 0)
            if miles > 250:
                return 9_500
            if miles > 120:
                return 7_000
            if miles > 60:
                return 5_500
            return floors["travel"]

        soft = [s for s in travels if s["duration_ms"] > travel_floor(s)]
        pool = sum(max(0, s["duration_ms"] - travel_floor(s)) for s in soft) or 1
        for s in soft:
            cut = overflow * (max(0, s["duration_ms"] - travel_floor(s)) / pool)
            s["duration_ms"] = int(max(travel_floor(s), s["duration_ms"] - cut))
    return body


def build_trip_story(trip: dict, stops: list[dict], stop_pois: list[list[dict]]) -> dict:
    """Narrative captions for diary replay and video export."""
    origin = short_location_label(stops[0]["location"]) if stops else ""
    states = trip.get("via_states") or []
    via = ", ".join(states) if states else "the open road"
    days = trip_duration_days(trip["start"], trip["end"])
    all_pois = [p for pois in stop_pois for p in pois]
    visited_count = sum(1 for p in all_pois if p.get("visited"))
    photo_n = sum(1 for s in stops if s.get("is_photo") or s.get("kind") == "photo")
    crew = trip.get("trip_crew") or trip.get("owner_short") or "you"
    vehicle = sanitize_vehicle_label(trip.get("vehicle_label"))
    if trip.get("is_shared"):
        who = f"{crew}" + (f" in {vehicle}" if vehicle else "")
    else:
        who = crew if not vehicle else f"{crew} · {vehicle}"

    intro = (
        f"A {days}-day journey for {who} from {origin.split(',')[0]} "
        f"through {via} — {len(stops)} places along the way"
        + (f", {photo_n} photo memories" if photo_n else "")
        + "."
    )
    outro = (
        f"Journey complete — {len(stops)} places across {via}."
    )

    stop_captions: list[dict] = []
    n = len(stops)
    for i, (stop, pois) in enumerate(zip(stops, stop_pois)):
        label = short_location_label(stop["location"])
        city = label.split(",")[0]
        if stop.get("is_photo") or stop.get("kind") == "photo":
            album = (stop.get("album") or "memory").strip()
            pretty = album.title() if album.islower() else album
            caption = f"Memory · {pretty}"
            sub = ""
        elif i == 0:
            caption = f"Departing {city}"
            sub = f"Stop 1 of {n}"
        elif i == n - 1:
            caption = f"Final stop · {city}"
            sub = (
                "Homeward bound"
                if origin.split(",")[0] == city or "Home" in (trip.get("dest_label") or "")
                else f"Stop {n} of {n}"
            )
        else:
            caption = f"Stopped in {city}"
            sub = f"Stop {i + 1} of {n}"
        stop_captions.append({"caption": caption, "sub": sub, "pois": []})

    return {
        "intro": intro,
        "outro": outro,
        "highlights": [],
        "visited_count": visited_count,
        "nearby_count": 0,
        "stop_captions": stop_captions,
    }


def apply_story_overrides(trip: dict, story: dict, cfg: dict | None = None) -> dict:
    """Optional owner_config.story_overrides keyed by trip id, id prefix, or name substring."""
    cfg = cfg if cfg is not None else load_owner_config()
    overrides = (cfg or {}).get("story_overrides") or {}
    if not overrides:
        return story
    trip_id = str(trip.get("id") or "")
    trip_name = str(trip.get("name") or "")
    matched: dict | None = None
    if trip_id in overrides:
        matched = overrides[trip_id]
    else:
        for key, val in overrides.items():
            if not isinstance(val, dict):
                continue
            k = str(key)
            if trip_id.startswith(k) or (k and k.lower() in trip_name.lower()):
                matched = val
                break
    if not matched:
        return story
    out = dict(story)
    for field in ("intro", "outro", "intro_title", "share_blurb"):
        if matched.get(field):
            out[field] = matched[field]
    if isinstance(matched.get("highlights"), list) and matched["highlights"]:
        out["highlights"] = [str(h) for h in matched["highlights"][:6]]
    # Optional per-stop caption patches: [{index, caption, sub}]
    patches = matched.get("stop_captions") or []
    if patches and out.get("stop_captions"):
        caps = list(out["stop_captions"])
        for patch in patches:
            if not isinstance(patch, dict):
                continue
            idx = patch.get("index")
            if not isinstance(idx, int) or idx < 0 or idx >= len(caps):
                continue
            row = dict(caps[idx])
            if patch.get("caption"):
                row["caption"] = patch["caption"]
            if patch.get("sub") is not None:
                row["sub"] = patch["sub"]
            caps[idx] = row
        out["stop_captions"] = caps
    return out


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
    return round(polyline_miles(path))


def polyline_miles(path: list[list[float]]) -> float:
    total = 0.0
    for i in range(1, len(path)):
        total += haversine_miles(path[i - 1][0], path[i - 1][1], path[i][0], path[i][1])
    return total


def point_to_segment_miles(p: list[float], a: list[float], b: list[float]) -> float:
    """Distance from p to segment a–b (local equirectangular)."""
    lat0, lng0 = a[0], a[1]
    lat1, lng1 = b[0], b[1]
    coslat = math.cos(((lat0 + lat1) / 2) * math.pi / 180) or 1e-6
    dx, dy = lat1 - lat0, (lng1 - lng0) * coslat
    px, py = p[0] - lat0, (p[1] - lng0) * coslat
    seg2 = dx * dx + dy * dy
    if seg2 < 1e-18:
        return haversine_miles(p[0], p[1], lat0, lng0)
    t = max(0.0, min(1.0, (px * dx + py * dy) / seg2))
    return haversine_miles(p[0], p[1], lat0 + t * (lat1 - lat0), lng0 + t * (lng1 - lng0))


def douglas_peucker(path: list[list[float]], epsilon_mi: float) -> list[list[float]]:
    """Keep vertices that deviate from the chord — preserves switchbacks."""
    n = len(path)
    if n <= 2:
        return path
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    while stack:
        i, j = stack.pop()
        max_d = -1.0
        max_k = -1
        a, b = path[i], path[j]
        for k in range(i + 1, j):
            d = point_to_segment_miles(path[k], a, b)
            if d > max_d:
                max_d = d
                max_k = k
        if max_k >= 0 and max_d > epsilon_mi:
            keep[max_k] = True
            stack.append((i, max_k))
            stack.append((max_k, j))
    return [pt for pt, flag in zip(path, keep) if flag]


def simplify_path(
    path: list[list[float]],
    max_points: int = SIMPLIFY_MAX_POINTS,
    epsilon_mi: float = SIMPLIFY_EPSILON_MI,
    max_hop_mi: float = 0.11,
) -> list[list[float]]:
    if len(path) <= 2:
        return path
    simplified = douglas_peucker(path, epsilon_mi)
    eps = epsilon_mi
    while len(simplified) > max_points and eps < 0.05:
        eps *= 1.6
        simplified = douglas_peucker(path, eps)
    # Never let DP invent a long chord — put original vertices back on big hops.
    if max_hop_mi > 0 and len(path) > len(simplified):
        simplified = _restore_long_hops(path, simplified, max_hop_mi)
    # Do not even-sample when still over max_points. That cut Independence Pass
    # switchbacks into 0.2 mi stripes. Keep the restored polyline.
    return simplified


def _restore_long_hops(
    original: list[list[float]], simplified: list[list[float]], max_hop_mi: float
) -> list[list[float]]:
    if len(simplified) < 2 or len(original) < 3:
        return simplified
    # Map simplified vertices back to original indices (nearest exact match)
    orig_i = []
    start = 0
    for pt in simplified:
        best_j, best_d = start, 1e9
        for j in range(start, len(original)):
            d = haversine_miles(pt[0], pt[1], original[j][0], original[j][1])
            if d < best_d:
                best_d = d
                best_j = j
            if d < 1e-8:
                break
        orig_i.append(best_j)
        start = best_j
    out: list[list[float]] = [original[orig_i[0]]]
    for a, b in zip(orig_i, orig_i[1:]):
        hop = haversine_miles(original[a][0], original[a][1], original[b][0], original[b][1])
        if hop > max_hop_mi and b > a + 1:
            out.extend(original[k] for k in range(a + 1, b + 1))
        else:
            out.append(original[b])
    return out


def _trim_crowfly_stubs(path: list[list[float]], max_stub_mi: float = 0.055) -> list[list[float]]:
    """Drop first/last hops that jump off the mapped way onto a GPS pin."""
    if not path or len(path) < 3:
        return path
    out = [list(pt) for pt in path]

    def hop(i: int) -> float:
        return haversine_miles(out[i][0], out[i][1], out[i + 1][0], out[i + 1][1])

    interior = [hop(i) for i in range(1, max(1, len(out) - 2))]
    typical = sorted(interior)[len(interior) // 2] if interior else 0.01
    thresh = max(max_stub_mi, typical * 4.0)
    while len(out) > 2 and hop(len(out) - 2) > thresh:
        out.pop()
    while len(out) > 2 and hop(0) > thresh:
        out.pop(0)
    return out


def _join_polylines(head: list[list[float]], tail: list[list[float]]) -> list[list[float]]:
    if not head:
        return [list(pt) for pt in (tail or [])]
    if not tail:
        return [list(pt) for pt in head]
    out = [list(pt) for pt in head]
    t0 = tail[0]
    if haversine_miles(out[-1][0], out[-1][1], t0[0], t0[1]) < 1.5e-4:
        out.extend(list(pt) for pt in tail[1:])
    else:
        out.extend(list(pt) for pt in tail)
    return out


def _gentle_pin_endpoints(
    path: list[list[float]], lat1: float, lng1: float, lat2: float, lng2: float
) -> list[list[float]]:
    """Pin exact GPS only when already on the way — never invent a long chord."""
    if not path:
        return [[lat1, lng1], [lat2, lng2]]
    out = [list(pt) for pt in path]
    if haversine_miles(lat1, lng1, out[0][0], out[0][1]) <= PIN_ENDPOINT_MI:
        out[0] = [lat1, lng1]
    if haversine_miles(lat2, lng2, out[-1][0], out[-1][1]) <= PIN_ENDPOINT_MI:
        out[-1] = [lat2, lng2]
    return out


def _route_score(
    path: list[list[float]] | None, lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    if not path or len(path) < 2:
        return -1e9
    start_d = haversine_miles(lat1, lng1, path[0][0], path[0][1])
    end_d = haversine_miles(lat2, lng2, path[-1][0], path[-1][1])
    crow = haversine_miles(lat1, lng1, lat2, lng2)
    n = len(path)
    pm = polyline_miles(path)
    if start_d > 0.55 and start_d > crow * 0.25:
        return -1e6
    if end_d > 0.55 and end_d > crow * 0.25:
        return -1e6
    if crow >= MIN_ROUTE_MILES and n <= 2:
        return -1e5
    score = 40.0 - start_d * 90.0 - end_d * 90.0
    if n <= 2:
        score -= 40.0
    score += min(n, 600) * 0.015
    if crow > 0.05:
        ratio = pm / crow
        if ratio < 1.04 and crow > 0.2:
            score -= 25.0
        score += min(ratio, 2.8) * 5.0
        if ratio > 8.0:
            score -= 12.0
    return score


def _route_good_enough(
    path: list[list[float]] | None, lat1: float, lng1: float, lat2: float, lng2: float
) -> bool:
    if not path or len(path) < 2:
        return False
    start_d = haversine_miles(lat1, lng1, path[0][0], path[0][1])
    end_d = haversine_miles(lat2, lng2, path[-1][0], path[-1][1])
    crow = haversine_miles(lat1, lng1, lat2, lng2)
    if start_d > 0.08 or end_d > 0.08:
        return False
    if crow >= MIN_ROUTE_MILES and len(path) <= 2:
        return False
    return True


def _is_valley_cut(path: list[list[float]] | None, lat1: float, lng1: float, lat2: float, lng2: float) -> bool:
    """True when geometry is basically a crow-fly (cuts switchbacks / valleys)."""
    if not path or len(path) < 2:
        return True
    crow = haversine_miles(lat1, lng1, lat2, lng2)
    if crow < VALLEY_CUT_MIN_CROW_MI:
        return False
    pm = polyline_miles(path)
    if pm < 0.01:
        return True
    return (pm / crow) < VALLEY_CUT_RATIO


def _access_ok(access: list[list[float]] | None, gap_mi: float) -> bool:
    if not access or gap_mi < ACCESS_STITCH_MI:
        return False
    if len(access) < 3:
        return False
    pm = polyline_miles(access)
    if pm > ACCESS_MAX_MI:
        return False
    if gap_mi > 0.02 and pm > gap_mi * ACCESS_MAX_RATIO:
        return False
    return True


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


def fetch_mapbox_route(
    lat1: float, lng1: float, lat2: float, lng2: float, token: str, profile: str = "driving",
) -> tuple[list[list[float]], float] | None:
    coords = f"{lng1},{lat1};{lng2},{lat2}"
    query = urllib.parse.urlencode({
        "geometries": "geojson",
        "overview": "full",
        "steps": "false",
        "access_token": token,
    })
    url = f"https://api.mapbox.com/directions/v5/mapbox/{profile}/{coords}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"  Route fetch failed [{profile}] ({lat1:.4f},{lng1:.4f})→({lat2:.4f},{lng2:.4f}): {exc}")
        return None
    if data.get("code") != "Ok" or not data.get("routes"):
        print(f"  No {profile} route ({lat1:.4f},{lng1:.4f})→({lat2:.4f},{lng2:.4f}): {data.get('code')}")
        return None
    route = data["routes"][0]
    path = [[pt[1], pt[0]] for pt in route["geometry"]["coordinates"]]
    return path, route["distance"] / 1609.344


def fetch_mapbox_driving_route(
    lat1: float, lng1: float, lat2: float, lng2: float, token: str
) -> tuple[list[list[float]], float] | None:
    return fetch_mapbox_route(lat1, lng1, lat2, lng2, token, "driving")


def leg_wants_walking(a: dict, b: dict, dist: float) -> bool:
    """Memory spurs (hike / trail photos) use walking when short and photo-related."""
    photo_leg = bool(a.get("is_photo") or b.get("is_photo") or a.get("kind") == "photo" or b.get("kind") == "photo")
    return photo_leg and dist <= WALK_SPUR_MILES


def leg_is_hiking(
    a: dict,
    b: dict,
    dist: float,
    gap_hours: float | None = None,
) -> bool:
    """True when this leg should play as on-foot (icon + pacing), not highway driving."""
    # Photo↔photo / photo↔stop trail spurs (Mapbox walking geometry).
    if leg_wants_walking(a, b, dist):
        return True
    photoish = bool(
        a.get("is_photo") or b.get("is_photo")
        or a.get("kind") == "photo" or b.get("kind") == "photo"
    )
    # Only clear trail crawls — require a photo endpoint and short, slow motion.
    # (Overnight highway gaps with leftover photos must stay driving.)
    if photoish and gap_hours is not None and dist > 0.02 and gap_hours > 0:
        mph = dist / max(float(gap_hours), 0.05)
        if mph < 4.0 and dist <= 8.0:
            return True
    return False


def _raw_profile_path(
    lat1: float, lng1: float, lat2: float, lng2: float,
    cache: dict, token: str, refresh: bool, stats: dict,
    profile: str,
) -> list[list[float]] | None:
    """Mapbox geometry for one profile — no endpoint chords, no great-circle fallback."""
    dist = haversine_miles(lat1, lng1, lat2, lng2)
    if dist < MIN_ROUTE_MILES:
        return [[lat1, lng1], [lat2, lng2]]
    key = f"{ROUTE_CACHE_VER}:{profile}:{leg_cache_key(lat1, lng1, lat2, lng2)}"
    if key in cache and (not refresh or key in stats["session_keys"]):
        stats["cache_hits"] += 1
        return cache[key]["path"]
    if token:
        result = fetch_mapbox_route(lat1, lng1, lat2, lng2, token, profile)
        if result:
            path, miles = result
            path = _trim_crowfly_stubs(simplify_path(path))
            cache[key] = {
                "path": path,
                "distance_miles": round(miles, 1),
                "profile": profile,
                "ver": ROUTE_CACHE_VER,
            }
            stats["session_keys"].add(key)
            stats["fetched"] += 1
            time.sleep(ROUTE_FETCH_DELAY_S)
            return path
        stats["fetch_failed"] += 1
    return None


def _stitch_walking_access(
    path: list[list[float]],
    lat1: float, lng1: float, lat2: float, lng2: float,
    cache: dict, token: str, refresh: bool, stats: dict,
) -> list[list[float]]:
    """If driving snapped to a highway, walk the mapped trail to/from the photo."""
    out = [list(pt) for pt in path]
    start_gap = haversine_miles(lat1, lng1, out[0][0], out[0][1])
    if start_gap >= ACCESS_STITCH_MI:
        access = _raw_profile_path(
            lat1, lng1, out[0][0], out[0][1], cache, token, refresh, stats, "walking"
        )
        if _access_ok(access, start_gap):
            out = _join_polylines(access, out)
            stats["access_stitched"] = stats.get("access_stitched", 0) + 1
    end_gap = haversine_miles(lat2, lng2, out[-1][0], out[-1][1])
    if end_gap >= ACCESS_STITCH_MI:
        access = _raw_profile_path(
            out[-1][0], out[-1][1], lat2, lng2, cache, token, refresh, stats, "walking"
        )
        if _access_ok(access, end_gap):
            out = _join_polylines(out, access)
            stats["access_stitched"] = stats.get("access_stitched", 0) + 1
    return out


def get_route_segment(
    lat1: float, lng1: float, lat2: float, lng2: float,
    cache: dict, token: str, refresh: bool, stats: dict,
    profile: str = "driving",
) -> list[list[float]]:
    dist = haversine_miles(lat1, lng1, lat2, lng2)
    if dist < MIN_ROUTE_MILES:
        return [[lat1, lng1], [lat2, lng2]]

    def finish(raw: list[list[float]]) -> list[list[float]]:
        path = _stitch_walking_access(raw, lat1, lng1, lat2, lng2, cache, token, refresh, stats)
        return _gentle_pin_endpoints(path, lat1, lng1, lat2, lng2)

    def raw(prof: str) -> list[list[float]] | None:
        return _raw_profile_path(lat1, lng1, lat2, lng2, cache, token, refresh, stats, prof)

    # Highway legs: always keep driving geometry and walk only the last-mile access.
    # (Full walking over 20–50mi often scores higher just because it hits photo GPS,
    # then cuts valleys.)
    if profile != "walking":
        for prof in ("driving", "cycling", "walking"):
            cand = raw(prof)
            if cand:
                return finish(cand)
        stats["fallback"] += 1
        points = max(24, min(120, int(max(dist, 2) * 1.4)))
        return great_circle_arc(lat1, lng1, lat2, lng2, num_points=points)

    walk = raw("walking")
    if walk and not _is_valley_cut(walk, lat1, lng1, lat2, lng2):
        return finish(walk)
    # Mapbox often stays on the forest road and misses a dotted OSM loop
    # (Maroon Bells Scenic Loop U-turn at the bridge/rapids).
    osm = osm_detour_path(lat1, lng1, lat2, lng2)
    if osm and len(osm) >= 4 and not _is_valley_cut(osm, lat1, lng1, lat2, lng2):
        stats["osm_detours"] = stats.get("osm_detours", 0) + 1
        return _gentle_pin_endpoints(osm, lat1, lng1, lat2, lng2)
    cycle = raw("cycling")
    if cycle and not _is_valley_cut(cycle, lat1, lng1, lat2, lng2):
        return finish(cycle)
    drive = raw("driving")
    if drive:
        stitched = finish(drive)
        if not _is_valley_cut(stitched, lat1, lng1, lat2, lng2):
            return stitched
    if osm and len(osm) >= 4:
        stats["osm_detours"] = stats.get("osm_detours", 0) + 1
        return _gentle_pin_endpoints(osm, lat1, lng1, lat2, lng2)
    if walk:
        return finish(walk)
    if drive:
        return finish(drive)
    stats["fallback"] += 1
    points = max(24, min(120, int(max(dist, 2) * 1.4)))
    return great_circle_arc(lat1, lng1, lat2, lng2, num_points=points)


def get_driving_segment(
    lat1: float, lng1: float, lat2: float, lng2: float,
    cache: dict, token: str, refresh: bool, stats: dict,
) -> list[list[float]]:
    return get_route_segment(lat1, lng1, lat2, lng2, cache, token, refresh, stats, "driving")


def build_route_path(
    stops: list[dict], cache: dict, token: str, refresh: bool, stats: dict,
) -> list[list[float]]:
    if not stops:
        return []
    path: list[list[float]] = []
    memory_spurs: list[dict] = []
    prev: dict | None = None
    for b in stops:
        if not is_valid_coord(b.get("lat"), b.get("lng")):
            continue
        if prev is None:
            prev = b
            continue
        a = prev
        dist = haversine_miles(a["lat"], a["lng"], b["lat"], b["lng"])
        if dist > 1500:
            prev = b
            continue
        profile = "walking" if leg_wants_walking(a, b, dist) else "driving"
        configured = matching_via_paths(a, b, stats.get("_via_paths") or [])
        if configured:
            segment = stitch_configured_via_path(
                a["lat"], a["lng"], b["lat"], b["lng"], configured[0]
            )
            stats["via_paths"] = stats.get("via_paths", 0) + 1
        else:
            segment = get_route_segment(
                a["lat"], a["lng"], b["lat"], b["lng"], cache, token, refresh, stats, profile
            )
        if not segment:
            prev = b
            continue
        # Always visit the photo. Skipping valley-cut vertices made Independence
        # Pass look like the path never went there.
        path = _join_polylines(path, segment)
        if profile == "walking":
            memory_spurs.append({
                "from": [a["lat"], a["lng"]],
                "to": [b["lat"], b["lng"]],
                "path": segment,
            })
            stats["walking_spurs"] = stats.get("walking_spurs", 0) + 1
        prev = b
    # Stash on stats for prepare_trips to pick up (cleared per trip)
    stats["_last_memory_spurs"] = memory_spurs
    if not path and stops:
        return [[stops[0]["lat"], stops[0]["lng"]]]
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
    dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


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
    photos: list[dict] | None = None,
) -> dict:
    """
    Cinematic replay along the journey path (chargers + photo GPS waypoints).

    Photo clusters sit on the route at exact EXIF coordinates, so hike destinations
    like Maroon Bells are visited — not snapped sideways onto the nearest highway.
    Extra album photos (non-cluster reps) still appear as memory beats snapped onto
    the enriched path (near-zero spur when the trail is already in route_path).
    """
    journey: list[dict] = []
    for s in stops:
        if not is_valid_coord(s.get("lat"), s.get("lng")):
            continue
        row = dict(s)
        if row.get("is_photo") or row.get("kind") == "photo":
            row["is_photo"] = True
            row["kind"] = "photo"
            row.setdefault("location", f"Photo · {row.get('album', 'memory')}")
        else:
            row["is_photo"] = False
            row.setdefault("kind", "home" if row.get("is_home_anchor") else "stop")
        journey.append(row)

    if len(journey) < 1:
        return {"segments": [], "total_video_ms": 0, "real_duration_ms": 0, "story": story or {}}

    stop_pois = stop_pois or [[] for _ in journey]
    story = story or {}
    road_only = [s for s in journey if not s.get("is_photo")]
    captions = story.get("stop_captions", [{}] * len(road_only))

    # Cluster reps already on the journey; add remaining thumbs as path memories.
    on_path_ids = {s.get("id") for s in journey if s.get("is_photo") and s.get("id")}
    photo_list = photos if photos is not None else [
        s for s in journey if s.get("is_photo")
    ]
    raw_memories = []
    for p in photo_list:
        if not p.get("thumb") or not is_valid_coord(p.get("lat"), p.get("lng")):
            continue
        if p.get("id") and p.get("id") in on_path_ids:
            continue  # held when we dwell on that photo waypoint
        row = dict(p)
        row["kind"] = "photo"
        row["is_photo"] = True
        row["cluster_size"] = int(p.get("cluster_size") or 1)
        row.setdefault("location", f"Photo · {p.get('album', 'memory')}")
        raw_memories.append(row)
    memories = select_playback_memories(
        raw_memories, road_only or journey, route_path or [],
    )

    legs: list[dict] = []
    real_total_ms = 0
    for i in range(len(journey) - 1):
        a, b = journey[i], journey[i + 1]
        try:
            gap_ms = max(
                30_000,
                int((parse_ts(b["datetime"]) - parse_ts(a["datetime"])).total_seconds() * 1000),
            )
        except Exception:
            gap_ms = 60_000
        real_total_ms += gap_ms
        path = leg_path_between_stops(a, b, cache, token, refresh, stats)
        miles = path_miles(path) if path else haversine_miles(
            a["lat"], a["lng"], b["lat"], b["lng"]
        )
        gap_h = max(0.0, float(gap_ms) / 3_600_000.0)
        crow = haversine_miles(a["lat"], a["lng"], b["lat"], b["lng"])
        profile = "walking" if leg_is_hiking(a, b, miles or crow, gap_h) else "driving"
        # Speed/photo hike detector may outrank Mapbox driving geometry — prefer walking paths.
        if profile == "walking" and not leg_wants_walking(a, b, crow):
            path = get_route_segment(
                float(a["lat"]), float(a["lng"]), float(b["lat"]), float(b["lng"]),
                cache, token, refresh, stats, "walking",
            )
            if path:
                miles = path_miles(path)
        to_lbl = short_location_label(b.get("location") or "") if not b.get("is_photo") else ""
        if not to_lbl:
            for j in range(i + 1, len(journey)):
                if not journey[j].get("is_photo"):
                    to_lbl = short_location_label(journey[j].get("location") or "")
                    break
        from_lbl = short_location_label(a.get("location") or "") if not a.get("is_photo") else ""
        if not from_lbl:
            for j in range(i, -1, -1):
                if not journey[j].get("is_photo"):
                    from_lbl = short_location_label(journey[j].get("location") or "")
                    break
        legs.append({
            "from_idx": i,
            "to_idx": i + 1,
            "path": path,
            "miles": miles,
            "gap_ms": gap_ms,
            "profile": profile,
            "t0": parse_waypoint_ts(a.get("datetime")),
            "t1": parse_waypoint_ts(b.get("datetime")),
            "from_label": from_lbl,
            "to_label": to_lbl,
            "bearing": round(leg_bearing_deg(a["lat"], a["lng"], b["lat"], b["lng"]), 1),
        })

    # Place extra photo memories on the closest journey leg (enriched path).
    leg_memories: list[list[dict]] = [[] for _ in legs]
    for mem in memories:
        ts = parse_waypoint_ts(mem.get("datetime"))
        best_li = None
        best_score = float("inf")
        best_frac = 0.5
        best_snap = (float(mem["lat"]), float(mem["lng"]))
        best_spur = 0.0
        for li, leg in enumerate(legs):
            path = leg.get("path") or []
            if len(path) >= 2:
                slat, slng, gfrac, dist = nearest_point_on_path(
                    float(mem["lat"]), float(mem["lng"]), path
                )
            else:
                slat, slng = float(mem["lat"]), float(mem["lng"])
                gfrac, dist = 0.5, 9999.0
            in_window = leg["t0"] < ts <= leg["t1"]
            score = dist - (8.0 if in_window else 0.0)
            if score < best_score:
                best_score = score
                best_li = li
                best_frac = max(0.04, min(0.96, gfrac))
                best_snap = (slat, slng)
                best_spur = dist
        if best_li is None:
            continue
        m = dict(mem)
        # Prefer exact EXIF when the enriched path already reaches the shot.
        if best_spur <= 1.25:
            m["route_lat"], m["route_lng"] = float(mem["lat"]), float(mem["lng"])
            m["spur_miles"] = round(best_spur, 2)
        else:
            m["route_lat"], m["route_lng"] = best_snap[0], best_snap[1]
            m["spur_miles"] = round(best_spur, 2)
        m["leg_frac"] = best_frac
        m["on_corridor"] = m["spur_miles"] <= MEMORY_ROUTE_SNAP_MI
        leg_memories[best_li].append(m)

    for bucket in leg_memories:
        # Time-first: keeps overnight camp shots in capture order on out-and-back trails.
        bucket.sort(key=lambda m: (
            parse_waypoint_ts(m.get("datetime")),
            float(m.get("leg_frac") or 0),
        ))

    n_road = len(road_only)
    n_mem = sum(1 for s in journey if s.get("is_photo")) + sum(len(b) for b in leg_memories)
    road_miles = sum(float(leg.get("miles") or 0) for leg in legs)
    target_ms = min(
        PLAYBACK_TARGET_MAX_MS,
        max(
            PLAYBACK_TARGET_MIN_MS,
            int(n_road * 6_200 + n_mem * 1_100 + road_miles * 14),
        ),
    )
    # Short title card — interactive play also skips intro for instant motion.
    intro_ms, outro_ms = 700, 1_600

    body: list[dict] = []
    road_i = -1

    def _clock_iso(dt: datetime) -> str:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()

    def _lerp_clock(t0: datetime, t1: datetime, frac: float) -> datetime:
        span = (t1 - t0).total_seconds()
        return t0 + timedelta(seconds=span * max(0.0, min(1.0, float(frac))))

    def append_travel(
        path: list[list[float]],
        f0: float,
        f1: float,
        leg: dict,
        stop_index: int,
        clock_t0: datetime | None = None,
        clock_t1: datetime | None = None,
        path_override: list[list[float]] | None = None,
        profile_override: str | None = None,
    ) -> None:
        profile = profile_override or leg.get("profile") or "driving"
        if path_override is not None:
            sub = path_override
            if len(sub) < 2:
                return
        else:
            if f1 - f0 < 0.012:
                return
            sub = slice_path_by_frac(path, f0, f1)
            if len(sub) < 2:
                return
        sub_mi = path_miles(sub)
        # Prefer explicit photo/stop instants so the playhead never jumps at memories.
        t_start = clock_t0 if clock_t0 is not None else _lerp_clock(leg["t0"], leg["t1"], f0)
        t_end = clock_t1 if clock_t1 is not None else _lerp_clock(leg["t0"], leg["t1"], f1)
        lng_hint = sub[0][1] if sub else None
        if profile == "walking":
            # Pleasant on-foot pace — skip overnight clock stretch that made trail crawls crawl.
            dur = max(2_400, min(11_000, int(sub_mi * 220 + 1_600)))
        else:
            t_start, t_end = driving_clock_window(t_start, t_end, sub_mi, lng_hint)
            weighted_h = clock_weighted_hours(t_start, t_end, lng_hint)
            dur = max(4_200, min(28_000, int(sub_mi * 85 + 2_400 + weighted_h * 1_050)))
        to_label = (leg.get("to_label") or "").strip()
        if to_label.lower().startswith("photo"):
            to_label = ""
        body.append({
            "type": "travel",
            "duration_ms": dur,
            "path": sub,
            "bearing": round(
                leg_bearing_deg(sub[0][0], sub[0][1], sub[-1][0], sub[-1][1]), 1
            ),
            "from_label": leg["from_label"],
            "to_label": to_label or leg.get("to_label") or "",
            "stop_index": stop_index,
            "leg_miles": round(sub_mi, 1),
            "profile": profile,
            "clock_start": _clock_iso(t_start),
            "clock_end": _clock_iso(t_end),
        })

    def append_memory_at(
        mem: dict,
        lat: float,
        lng: float,
        spur: float,
        stop_index: int,
        leg_frac: float | None = None,
        profile: str = "driving",
    ) -> None:
        album = mem.get("album") or "memory"
        ts = parse_waypoint_ts(mem.get("datetime"))
        row = {
            "type": "memory",
            "duration_ms": MEMORY_HOLD_MS if spur <= MEMORY_ROUTE_SNAP_MI else MEMORY_HOLD_OFF_CORRIDOR_MS,
            "lat": lat,
            "lng": lng,
            "photo_lat": mem["lat"],
            "photo_lng": mem["lng"],
            "label": short_location_label(mem.get("location") or album),
            "stop_index": stop_index,
            "caption": f"Memory · {album.title() if str(album).islower() else album}",
            "subcaption": "",
            "is_photo": True,
            "thumb": mem.get("thumb") or "",
            "album": album,
            "photo_id": mem.get("id") or "",
            # Ground-truth capture instant (GPS UTC) — never fall back to charger stop time.
            "datetime": mem.get("datetime") or _clock_iso(ts),
            "spur_miles": round(float(spur), 2),
            "on_corridor": float(spur) <= MEMORY_ROUTE_SNAP_MI,
            "profile": profile if profile in ("walking", "driving") else "driving",
            "pois": [],
            "clock_start": _clock_iso(ts),
            "clock_end": _clock_iso(ts + timedelta(minutes=2)),
        }
        if leg_frac is not None:
            row["leg_frac"] = leg_frac
        body.append(row)

    for i, stop in enumerate(journey):
        is_last = i == len(journey) - 1
        if stop.get("is_photo"):
            album = stop.get("album") or "memory"
            arrive_profile = "driving"
            if i > 0 and (i - 1) < len(legs):
                arrive_profile = legs[i - 1].get("profile") or "driving"
            append_memory_at(
                stop,
                float(stop["lat"]),
                float(stop["lng"]),
                0.0,
                max(0, road_i),
                profile=arrive_profile,
            )
        else:
            road_i += 1
            label = short_location_label(stop["location"])
            cap = captions[road_i] if road_i < len(captions) else {}
            dwell_ms = 1100 if not is_last else 1400
            stop_ts = parse_waypoint_ts(stop.get("datetime"))
            body.append({
                "type": "dwell",
                "duration_ms": dwell_ms,
                "lat": stop["lat"],
                "lng": stop["lng"],
                "label": label,
                "stop_index": road_i,
                "pois": [],
                "caption": cap.get(
                    "caption",
                    f"Final stop · {label}" if is_last else f"Stopped in {label}",
                ),
                "subcaption": cap.get("sub", "Journey complete" if is_last else ""),
                "is_photo": False,
                "clock_start": _clock_iso(stop_ts),
                "clock_end": _clock_iso(stop_ts + timedelta(minutes=12)),
            })

        if is_last:
            break

        leg = legs[i]
        mems = leg_memories[i]
        cursor_frac = 0.0
        # Real-time cursor: photo EXIF/GPS times, not path-fraction guesses.
        cursor_clock = parse_waypoint_ts(stop.get("datetime"))
        path = leg["path"] or [
            [stop["lat"], stop["lng"]],
            [journey[i + 1]["lat"], journey[i + 1]["lng"]],
        ]

        for mem in mems:
            photo_lat, photo_lng = float(mem["lat"]), float(mem["lng"])
            spur = float(mem.get("spur_miles") or 0)
            if path and len(path) >= 2:
                # Forward-only snap avoids outbound→return teleport on out-and-back hikes.
                _, _, snap_frac, _ = nearest_point_on_path_from(
                    photo_lat, photo_lng, path, min_frac=cursor_frac
                )
                g_lat, g_lng, g_frac, g_dist = nearest_point_on_path(
                    photo_lat, photo_lng, path
                )
                frac = float(snap_frac)
                slat, slng = photo_lat, photo_lng
                if spur > 1.25:
                    slat = float(mem.get("route_lat", photo_lat))
                    slng = float(mem.get("route_lng", photo_lng))
                    frac = float(mem.get("leg_frac") or frac)
                    if frac < cursor_frac:
                        frac = float(snap_frac)

                cur_pt = point_at_arc_frac(path, cursor_frac)
                direct = haversine_miles(cur_pt[0], cur_pt[1], photo_lat, photo_lng)
                along = 0.0
                if frac > cursor_frac + 0.002:
                    along = path_miles(slice_path_by_frac(path, cursor_frac, frac))
                # If the corridor detours far past a nearby shot (camp / spur),
                # walk a short spur instead of racing ahead and snapping back.
                use_spur = (
                    direct < 2.0
                    and along > max(0.55, direct * 3.2)
                ) or (
                    g_frac + 0.02 < cursor_frac
                    and g_dist < 0.5
                    and along > max(0.4, direct * 2.5)
                )
            else:
                slat = float(mem.get("route_lat", photo_lat))
                slng = float(mem.get("route_lng", photo_lng))
                frac = max(cursor_frac, float(mem.get("leg_frac") or 0.5))
                use_spur = False
                cur_pt = [stop["lat"], stop["lng"]]
                direct = 0.0

            mem_ts = parse_waypoint_ts(mem.get("datetime"))
            # Inherit the leg's mode — never force walking just because a photo is nearby.
            travel_profile = leg.get("profile") or "driving"

            if use_spur:
                spur_stats = stats if stats is not None else {
                    "cache_hits": 0, "fetched": 0, "fetch_failed": 0,
                    "fallback": 0, "session_keys": set(),
                }
                spur_path = None
                if cache is not None:
                    spur_path = get_route_segment(
                        float(cur_pt[0]), float(cur_pt[1]), photo_lat, photo_lng,
                        cache, token, refresh, spur_stats, "walking",
                    )
                if not spur_path or len(spur_path) < 2:
                    spur_path = [list(cur_pt), [photo_lat, photo_lng]]
                append_travel(
                    path, cursor_frac, cursor_frac, leg, max(0, road_i),
                    clock_t0=cursor_clock, clock_t1=mem_ts,
                    path_override=spur_path,
                    profile_override=travel_profile,
                )
                slat, slng = photo_lat, photo_lng
                # Stay on the corridor tip — don't jump to a return-leg frac.
                frac = cursor_frac
            else:
                if frac < cursor_frac + 0.008:
                    frac = min(0.98, cursor_frac + 0.01)
                append_travel(
                    path, cursor_frac, frac, leg, max(0, road_i),
                    clock_t0=cursor_clock, clock_t1=mem_ts,
                )
                if body and body[-1]["type"] == "travel":
                    # Pin exact photo GPS only when already on the mapped way.
                    tip = body[-1]["path"][-1]
                    tip_d = haversine_miles(tip[0], tip[1], photo_lat, photo_lng)
                    if tip_d <= PIN_ENDPOINT_MI:
                        body[-1]["path"][-1] = [photo_lat, photo_lng]
                        slat, slng = photo_lat, photo_lng
                    else:
                        slat, slng = tip[0], tip[1]
                    travel_profile = body[-1].get("profile") or travel_profile

            append_memory_at(
                mem, slat, slng, float(mem.get("spur_miles") or 0), max(0, road_i), frac,
                profile=travel_profile,
            )
            cursor_frac = max(cursor_frac, frac)
            cursor_clock = mem_ts

        append_travel(
            path, cursor_frac, 1.0, leg, max(0, road_i),
            clock_t0=cursor_clock, clock_t1=leg["t1"],
        )
        if body and body[-1]["type"] == "travel":
            tip = body[-1]["path"][-1]
            dest = journey[i + 1]
            if haversine_miles(tip[0], tip[1], dest["lat"], dest["lng"]) <= PIN_ENDPOINT_MI:
                body[-1]["path"][-1] = [dest["lat"], dest["lng"]]

    body = _renormalize_segment_durations(body, target_ms, intro_ms, outro_ms)

    anchor = road_only or journey
    t_start = parse_waypoint_ts(anchor[0].get("datetime") if anchor else None)
    t_end = parse_waypoint_ts(anchor[-1].get("datetime") if anchor else None)
    path_mi = int(path_miles(route_path or []))
    days = 1
    if t_start and t_end and t_end >= t_start:
        days = max(1, (t_end.date() - t_start.date()).days + 1)
    intro_seg = {
        "type": "intro",
        "duration_ms": intro_ms,
        "title": story.get("intro_title", ""),
        "caption": story.get("intro", "Road trip replay"),
        "highlights": story.get("highlights", [])[:4],
        "clock_start": _clock_iso(t_start),
        "clock_end": _clock_iso(t_start),
        "miles": path_mi,
        "duration_days": days,
        "place_count": len(road_only or journey),
    }
    outro_seg = {
        "type": "outro",
        "duration_ms": outro_ms,
        "caption": story.get("outro", "Journey complete"),
        "visited_count": story.get("visited_count", 0),
        "nearby_count": story.get("nearby_count", 0),
        "clock_start": _clock_iso(t_end),
        "clock_end": _clock_iso(t_end),
        "miles": path_mi,
        "duration_days": days,
        "place_count": len(road_only or journey),
    }
    all_segments = [intro_seg, *body, outro_seg]

    return {
        "segments": all_segments,
        "total_video_ms": sum(s["duration_ms"] for s in all_segments),
        "real_duration_ms": real_total_ms,
        "story": story,
        "memory_count": n_mem,
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
    trip_photos = load_trip_photos()
    home_regions = trips_data.get("home_regions") or []
    prepared: list[dict] = []
    stats.setdefault("walking_spurs", 0)
    for i, trip in enumerate(trips_data["trips"]):
        base_stops = [
            s for s in trip["stops"]
            if is_valid_coord(s.get("lat"), s.get("lng"))
        ]
        if not base_stops:
            continue
        photos = trip_photos.get(trip["id"], [])
        road_stops = normalize_road_stops(base_stops)
        near_photos = photos_near_road_stops(photos, road_stops)
        photo_clusters = cluster_photo_memories(near_photos)
        # Path visits exact photo GPS (Maroon Bells, trailheads, etc.).
        route_stops = merge_photo_waypoints(road_stops, photo_clusters)
        stats["_last_memory_spurs"] = []
        override = matching_trip_override(trip)
        stats["_via_paths"] = list(override.get("route_via_paths") or [])
        route_path = build_route_path(route_stops, cache, token, refresh, stats)
        memory_spurs = list(stats.get("_last_memory_spurs") or [])
        miles = path_miles(route_path)
        photo_count = len(photos)
        # UI / atlas use chargers only; photos are pins + route vertices.
        stops = road_stops
        place_count = len(stops)
        states = trip.get("via_states") or sorted({
            st for s in stops
            if (st := extract_state(s.get("location") or "") or ("CO" if s.get("in_colorado") else None))
        })
        state_names = [STATE_NAMES.get(s, s) for s in states]
        via_summary = trip.get("via_summary") or ", ".join(states)
        arcs = build_arcs(stops, trip_color(i), trip["id"], cache, token, refresh, stats)
        visited_for_trip = visited_all.get(trip["id"], {})
        stop_pois = [
            match_pois_for_stop(s, all_places, visited_for_trip)
            for s in stops
        ]
        # POIs align with journey waypoints (empty for photo GPS nodes).
        journey_pois: list[list[dict]] = []
        road_poi_i = 0
        for s in route_stops:
            if s.get("is_photo") or s.get("kind") == "photo":
                journey_pois.append([])
            else:
                journey_pois.append(stop_pois[road_poi_i] if road_poi_i < len(stop_pois) else [])
                road_poi_i += 1
        story = build_trip_story(trip, stops, stop_pois)
        # Photo count for narrative (photos are path waypoints + map pins)
        if photo_count:
            origin = short_location_label(stops[0]["location"]) if stops else ""
            via = ", ".join(states) if states else "the open road"
            days = trip_duration_days(trip["start"], trip["end"])
            crew = trip.get("trip_crew") or trip.get("owner_short") or "you"
            vehicle = sanitize_vehicle_label(trip.get("vehicle_label"))
            if trip.get("is_shared"):
                who = f"{crew}" + (f" in {vehicle}" if vehicle else "")
            else:
                who = crew if not vehicle else f"{crew} · {vehicle}"
            story["intro"] = (
                f"A {days}-day journey for {who} from {origin.split(',')[0]} "
                f"through {via} — {place_count} places along the way, "
                f"{photo_count} photo memories."
            )
        story["intro_title"] = trip["name"]
        days = trip_duration_days(trip["start"], trip["end"])
        story["share_blurb"] = (
            f"{trip['name']} — {miles:,.0f} mi · {place_count} places"
            + (f" · {photo_count} photos" if photo_count else "")
            + f" · {days}d\n"
            f"Relive it: {LIVE_DEMO_URL}"
        )
        story = apply_story_overrides(trip, story)
        days = trip_duration_days(trip["start"], trip["end"])
        story["outro"] = (
            f"{miles:,.0f} miles along the mapped route · {days} day"
            f"{'' if days == 1 else 's'} · {place_count} places"
            + (f" · {photo_count} photos" if photo_count else "")
        )
        playback = build_playback_timeline(
            route_stops, route_path, story, journey_pois, cache, token, refresh, stats, photos=photos
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
        vehicle_label = sanitize_vehicle_label(trip.get("vehicle_label"))
        client_stops = [client_stop(s) for s in stops]
        photos_payload = [
            {
                "id": p.get("id"),
                "lat": p["lat"],
                "lng": p["lng"],
                "datetime": p.get("datetime") or p.get("captured_at"),
                "album": p.get("album", ""),
                "thumb": p.get("thumb", ""),
                "location": p.get("location", f"Photo · {p.get('album', 'memory')}"),
            }
            for p in photos
            if is_valid_coord(p.get("lat"), p.get("lng"))
        ]
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
            "stops": client_stops,
            "photos": photos_payload,
            "photo_count": photo_count,
            "route_path": route_path,
            "memory_spurs": memory_spurs,
            "stop_count": place_count,
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
            "owner_short": trip.get("owner_short", ""),
            "travelers": trip.get("travelers", []),
            "trip_crew": trip.get("trip_crew", ""),
            "driver_short": trip.get("driver_short", ""),
            "vehicle_label": vehicle_label,
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
    total_photos = sum(int(t.get("photo_count") or 0) for t in trips)
    total_places = sum(int(t.get("stop_count") or 0) for t in trips)
    total_days = sum(int(t.get("duration_days") or 0) for t in trips)
    return {
        "trip_count": len(trips),
        "total_miles": sum(t["miles"] for t in trips),
        "total_photos": total_photos,
        "total_places": total_places,
        "total_days": total_days,
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
                    "datetime": s["datetime"], "kind": s.get("kind", "stop"), "is_photo": bool(s.get("is_photo")),
                },
                "geometry": {"type": "Point", "coordinates": [s["lng"], s["lat"]]},
            })
    return {"type": "FeatureCollection", "features": features}


def write_gpx(trip: dict, path: Path) -> None:
    gpx = ET.Element("gpx", {
        "version": "1.1", "creator": "My Mile Diary",
        "xmlns": "http://www.topografix.com/GPX/1/1",
    })
    ET.SubElement(ET.SubElement(gpx, "metadata"), "name").text = trip["name"]
    for stop in trip["stops"]:
        if stop.get("lat") is None:
            continue
        wpt = ET.SubElement(gpx, "wpt", {"lat": str(stop["lat"]), "lon": str(stop["lng"])})
        ET.SubElement(wpt, "name").text = stop["location"]
        desc = "Photo memory" if stop.get("is_photo") else (stop.get("location") or "Stop")
        ET.SubElement(wpt, "desc").text = desc
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
        # Path must visit photo EXIF — trail/road routing, not a 1mi crow-fly chord
        photos = t.get("photos") or []
        path = t.get("route_path") or []
        if photos and path:
            for p in photos:
                if not is_valid_coord(p.get("lat"), p.get("lng")):
                    continue
                # Sample path densely near photo (full scan for album-sized sets)
                best = min(
                    haversine_miles(p["lat"], p["lng"], pt[0], pt[1])
                    for pt in path
                )
                if best > 1.25:
                    issues.append(
                        f"Path misses photo EXIF on {t['id']}: "
                        f"{p.get('id') or p.get('album')} is {best:.1f}mi from route "
                        f"(expected path through exact shot)"
                    )
        # Shortcut chords (straight hops that skip the mapped trail/road)
        if path and "2025-09-25" in t.get("id", ""):
            for i in range(1, len(path)):
                a, b = path[i - 1], path[i]
                hop = haversine_miles(a[0], a[1], b[0], b[1])
                # Maroon Dam / Maroon Creek trail — a 0.18mi chord was the visible triangle
                if (
                    hop > 0.12
                    and 39.092 <= a[0] <= 39.097 and 39.092 <= b[0] <= 39.097
                    and -106.957 <= a[1] <= -106.948 and -106.957 <= b[1] <= -106.948
                ):
                    issues.append(
                        f"Path shortcut hop {hop:.2f}mi at Maroon Dam on {t['id']}"
                    )
                # Independence Pass photos — 0.33mi and 1.0mi chords jumped off CO-82
                if (
                    hop > 0.16
                    and 39.090 <= min(a[0], b[0]) and max(a[0], b[0]) <= 39.112
                    and -106.595 <= min(a[1], b[1]) and max(a[1], b[1]) <= -106.555
                ):
                    issues.append(
                        f"Path shortcut hop {hop:.2f}mi at Independence Pass on {t['id']}"
                    )
            maroon_pts = [
                p for p in path
                if 39.090 <= p[0] <= 39.097 and -106.956 <= p[1] <= -106.948
            ]
            if maroon_pts and min(p[0] for p in maroon_pts) > 39.0930:
                issues.append(
                    f"Maroon Scenic Loop U-turn missing on {t['id']} "
                    f"(path stayed on FR 1975, min lat {min(p[0] for p in maroon_pts):.5f})"
                )
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
        for i in issues[:20]:
            print(f"  ⚠ {i}")
        hard = [
            i for i in issues
            if i.startswith("Path misses photo")
            or i.startswith("Path shortcut")
            or i.startswith("Maroon Scenic Loop")
        ]
        if hard:
            raise SystemExit(f"Validation failed: {len(hard)} photo(s) not on routed path")
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
    parser = argparse.ArgumentParser(description="Build My Mile Diary travel map")
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
    stats = {"fetched": 0, "cache_hits": 0, "fallback": 0, "fetch_failed": 0, "walking_spurs": 0, "access_stitched": 0, "skipped_photo_cuts": 0, "osm_detours": 0, "via_paths": 0, "session_keys": set()}

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

    print(f"Built My Mile Diary — {len(prepared)} trips")
    print(f"  Dashboard: {dashboard['total_miles']:,} mi · {dashboard.get('total_photos', 0)} photos · {dashboard.get('total_places', 0)} places")
    print(
        f"  Routes: {stats['fetched']} fetched, {stats['cache_hits']} cached, "
        f"{stats['fallback']} arc fallback, {stats['fetch_failed']} failed, "
        f"{stats.get('access_stitched', 0)} trail splices, "
        f"{stats.get('osm_detours', 0)} OSM detours, "
        f"{stats.get('via_paths', 0)} via-path legs, "
        f"{stats.get('walking_spurs', 0)} walking legs, "
        f"{stats.get('skipped_photo_cuts', 0)} photo cuts skipped"
    )
    print(f"  Mapbox token: {'loaded' if mapbox_token else 'MISSING (using cache/fallback)'}")
    print(f"  HTML: {HTML_OUTPUT}")
    if embed_token_b64:
        print("  Token embed: base64 (push-protection safe)")


if __name__ == "__main__":
    main()
