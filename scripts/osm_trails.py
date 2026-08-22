#!/usr/bin/env python3
"""OSM footways for hike U-turns Mapbox Directions does not know about.

Mapbox walking at Maroon Bells follows FR 1975 (Maroon Creek Road) and skips
the Scenic Loop Trail U-turn at the bridge/rapids. Independence Pass hairpins
are on CO-82 (Mapbox driving) — do not even-sample those away.

Overpass is best-effort. Prefer owner_config.route_via_paths when Overpass is
down (GitHub Actions). Cache hits in data/osm_trails_cache.json.
"""

from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CACHE_FILE = ROOT / "data" / "osm_trails_cache.json"
CACHE_VER = "v1"
OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "mymilediary/1.0 (https://github.com/ramkandimalla94/mymilediary)"

# Short photo legs only — never query OSM for highway charges.
MAX_CROW_MI = 2.0
PAD_MI = 0.35
SNAP_START_MI = 0.12
SNAP_END_MI = 0.18
MIN_BULGE_MI = 0.05
MAX_DETROUR_MI = 2.5
MAX_U_TURN_EXTRA_MI = 0.40


def _haversine(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3959.0
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def _cross_track_mi(
    lat: float, lng: float, lat1: float, lng1: float, lat2: float, lng2: float
) -> float:
    """Approximate distance from a point to the start–end chord."""
    d13 = _haversine(lat1, lng1, lat, lng)
    d12 = _haversine(lat1, lng1, lat2, lng2)
    d23 = _haversine(lat, lng, lat2, lng2)
    if d12 < 1e-6:
        return d13
    # Triangle excess vs the chord — 0 on the line, grows with a U-turn bulge.
    return max(0.0, d13 + d23 - d12)


def _load_cache() -> dict:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def _bbox(lat1: float, lng1: float, lat2: float, lng2: float) -> tuple[float, float, float, float]:
    # 1 deg lat ~ 69 mi; pad in degrees.
    pad = PAD_MI / 69.0
    pad_lng = PAD_MI / max(20.0, 69.0 * math.cos(math.radians((lat1 + lat2) / 2)))
    south = min(lat1, lat2) - pad
    north = max(lat1, lat2) + pad
    west = min(lng1, lng2) - pad_lng
    east = max(lng1, lng2) + pad_lng
    return south, west, north, east


def _fetch_ways(south: float, west: float, north: float, east: float) -> list[dict]:
    q = (
        f"[out:json][timeout:25];\n"
        f'(way["highway"~"path|footway|track|steps|bridleway"]({south:.5f},{west:.5f},{north:.5f},{east:.5f}););\n'
        f"out tags geom;"
    )
    req = urllib.request.Request(
        OVERPASS_URL,
        data=q.encode("utf-8"),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read())
    ways = []
    for el in data.get("elements") or []:
        geom = el.get("geometry") or []
        if len(geom) < 3:
            continue
        pts = [[float(g["lat"]), float(g["lon"])] for g in geom]
        tags = el.get("tags") or {}
        ways.append({
            "id": el.get("id"),
            "name": tags.get("name") or "",
            "highway": tags.get("highway") or "",
            "pts": pts,
        })
    return ways


def _ways_for_leg(lat1: float, lng1: float, lat2: float, lng2: float) -> list[dict]:
    south, west, north, east = _bbox(lat1, lng1, lat2, lng2)
    key = f"{CACHE_VER}:{south:.4f},{west:.4f},{north:.4f},{east:.4f}"
    cache = _load_cache()
    if key in cache:
        return cache[key].get("ways") or []
    try:
        ways = _fetch_ways(south, west, north, east)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        print(f"  OSM trail fetch failed: {exc}")
        return []
    cache[key] = {"ways": ways}
    _save_cache(cache)
    return ways


def _along(pts: list[list[float]], a: int, b: int) -> list[list[float]]:
    if a <= b:
        return [list(p) for p in pts[a:b + 1]]
    return [list(p) for p in reversed(pts[b:a + 1])]


def _way_detour(
    pts: list[list[float]], lat1: float, lng1: float, lat2: float, lng2: float
) -> tuple[list[list[float]], float] | None:
    if len(pts) < 4:
        return None
    i1, d1 = min(
        ((i, _haversine(lat1, lng1, p[0], p[1])) for i, p in enumerate(pts)),
        key=lambda x: x[1],
    )
    i2, d2 = min(
        ((i, _haversine(lat2, lng2, p[0], p[1])) for i, p in enumerate(pts)),
        key=lambda x: x[1],
    )
    if d1 > SNAP_START_MI or d2 > SNAP_END_MI or i1 == i2:
        return None

    def miles(path: list[list[float]]) -> float:
        if len(path) < 2:
            return 0.0
        return sum(_haversine(a[0], a[1], b[0], b[1]) for a, b in zip(path, path[1:]))

    short = _along(pts, i1, i2)
    short_mi = miles(short)
    best_path = short
    best_bulge = max(
        (_cross_track_mi(p[0], p[1], lat1, lng1, lat2, lng2) for p in short),
        default=0.0,
    )
    # Prefer a short spur past the photo snaps (U-turn). Ignore the far end of
    # a long trail that only scores high because it is far from the chord.
    for i, p in enumerate(pts):
        bulge = _cross_track_mi(p[0], p[1], lat1, lng1, lat2, lng2)
        if bulge < MIN_BULGE_MI:
            continue
        via = _along(pts, i1, i) + _along(pts, i, i2)[1:]
        extra = miles(via) - short_mi
        if extra < 0.02 or extra > MAX_U_TURN_EXTRA_MI:
            continue
        if bulge > best_bulge:
            best_bulge = bulge
            best_path = via
    path = best_path
    bulge_d = best_bulge
    if len(path) < 4:
        return None
    path_mi = miles(path)
    if path_mi > MAX_DETROUR_MI or bulge_d < MIN_BULGE_MI:
        return None
    return path, bulge_d


def osm_detour_path(
    lat1: float, lng1: float, lat2: float, lng2: float,
) -> list[list[float]] | None:
    """Mapped OSM footpath that bulges (U-turn / loop) off the crow-fly.

    Returns None when Mapbox's short road is the only mapped way, or Overpass
    is unreachable. Caller should still try Mapbox walking / driving.
    """
    crow = _haversine(lat1, lng1, lat2, lng2)
    if crow < 0.04 or crow > MAX_CROW_MI:
        return None
    best: tuple[float, list[list[float]]] | None = None
    for way in _ways_for_leg(lat1, lng1, lat2, lng2):
        found = _way_detour(way["pts"], lat1, lng1, lat2, lng2)
        if not found:
            continue
        path, bulge = found
        if best is None or bulge > best[0]:
            best = (bulge, path)
    if not best:
        return None
    path = [list(p) for p in best[1]]
    # Pin photo GPS when already next to the trail — never a long chord.
    if _haversine(lat1, lng1, path[0][0], path[0][1]) <= 0.04:
        path[0] = [lat1, lng1]
    else:
        path = [[lat1, lng1]] + path
    if _haversine(lat2, lng2, path[-1][0], path[-1][1]) <= 0.04:
        path[-1] = [lat2, lng2]
    else:
        path = path + [[lat2, lng2]]
    return path
