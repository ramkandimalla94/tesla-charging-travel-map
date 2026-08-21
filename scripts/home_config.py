#!/usr/bin/env python3
"""Detect or load home-base configuration for travel diary home hubs."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / "data" / "owner_config.json"

DEFAULT_HOME_RADIUS_MILES = 120
DEFAULT_FAR_FROM_HOME_MILES = 350


def haversine_miles(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    r = 3959.0
    p = math.pi / 180
    a = (
        math.sin((lat2 - lat1) * p / 2) ** 2
        + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2
    )
    return 2 * r * math.asin(math.sqrt(min(1.0, a)))


def extract_city(location: str) -> str:
    m = re.match(r"^([^,]+)", location)
    return m.group(1).strip() if m else location


def load_owner_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    with open(CONFIG_FILE, encoding="utf-8") as f:
        return json.load(f)


def detect_home_bases(
    locations: list[str],
    cache: dict,
    *,
    min_count: int = 3,
) -> tuple[set[str], float, float, str]:
    """
    Infer home charging locations from frequency + geographic clustering.
    Returns (home_base_names, home_lat, home_lng, home_label).
    """
    counts = Counter(locations)
    candidates: list[tuple[str, int, float, float]] = []
    for loc, count in counts.most_common(15):
        if count < min_count:
            continue
        entry = cache.get(loc, {})
        lat, lng = entry.get("lat"), entry.get("lng")
        if lat is None or lng is None:
            continue
        candidates.append((loc, count, float(lat), float(lng)))

    if not candidates:
        return set(), 39.0, -98.0, "Home"

    primary_loc, _count, home_lat, home_lng = candidates[0]
    home_bases = {primary_loc}
    for loc, count, lat, lng in candidates[1:4]:
        if haversine_miles(home_lat, home_lng, lat, lng) <= 35:
            home_bases.add(loc)

    home_label = extract_city(primary_loc)
    return home_bases, home_lat, home_lng, home_label


def detect_home_regions(
    locations: list[str],
    cache: dict,
    *,
    min_cluster_charges: int = 6,
    cluster_radius_miles: float = 45,
) -> list[dict]:
    """
    Detect one or more home charging regions (e.g. Dallas + Seattle after a move).
    Returns regions sorted by charge volume, each with center, radius, and member locations.
    """
    counts = Counter(locations)
    points: list[dict] = []
    for loc, count in counts.items():
        entry = cache.get(loc, {})
        lat, lng = entry.get("lat"), entry.get("lng")
        if lat is None or lng is None:
            continue
        points.append({"loc": loc, "lat": float(lat), "lng": float(lng), "count": count})

    if not points:
        return []

    clusters: list[dict] = []
    for p in sorted(points, key=lambda x: -x["count"]):
        placed = False
        for cl in clusters:
            if haversine_miles(p["lat"], p["lng"], cl["lat"], cl["lng"]) <= cluster_radius_miles:
                total = cl["weight"] + p["count"]
                cl["lat"] = (cl["lat"] * cl["weight"] + p["lat"] * p["count"]) / total
                cl["lng"] = (cl["lng"] * cl["weight"] + p["lng"] * p["count"]) / total
                cl["weight"] = total
                cl["charge_count"] += p["count"]
                cl["locs"].append(p["loc"])
                placed = True
                break
        if not placed:
            clusters.append({
                "lat": p["lat"],
                "lng": p["lng"],
                "weight": p["count"],
                "charge_count": p["count"],
                "locs": [p["loc"]],
                "label": extract_city(p["loc"]),
            })

    clusters.sort(key=lambda c: -c["charge_count"])
    regions: list[dict] = []
    for cl in clusters:
        if cl["charge_count"] < min_cluster_charges and len(regions) >= 1:
            continue
        if cl["charge_count"] < 4:
            continue
        regions.append({
            "lat": cl["lat"],
            "lng": cl["lng"],
            "radius_miles": 55,
            "label": cl["label"],
            "bases": set(cl["locs"]),
            "charge_count": cl["charge_count"],
        })
        if len(regions) >= 3:
            break
    return regions


def resolve_home_config(
    locations: list[str],
    cache: dict,
) -> dict:
    """
    Merge optional owner_config.json with auto-detected home base(s).
    """
    cfg = load_owner_config()
    if cfg.get("home_bases") and cfg.get("home_lat") is not None and cfg.get("home_lng") is not None:
        region = {
            "lat": float(cfg["home_lat"]),
            "lng": float(cfg["home_lng"]),
            "radius_miles": float(cfg.get("home_radius_miles", DEFAULT_HOME_RADIUS_MILES)),
            "label": cfg.get("home_label") or extract_city(next(iter(cfg["home_bases"]))),
            "bases": set(cfg["home_bases"]),
            "charge_count": sum(1 for loc in locations if loc in cfg["home_bases"]),
            "canonical_location": cfg.get("home_canonical") or next(
                (b for b in cfg["home_bases"] if cfg.get("home_label", "") in b),
                next(iter(cfg["home_bases"])),
            ),
        }
        extra_regions = []
        all_bases = set(cfg["home_bases"])
        if cfg.get("secondary_regions"):
            for sr in cfg["secondary_regions"]:
                bases = set(sr.get("bases", []))
                all_bases.update(bases)
                extra_regions.append({
                    "lat": float(sr["lat"]),
                    "lng": float(sr["lng"]),
                    "radius_miles": float(sr.get("radius_miles", 55)),
                    "label": sr.get("label", "Home"),
                    "bases": bases,
                    "charge_count": sum(1 for loc in locations if loc in bases),
                    "canonical_location": sr.get("canonical_location")
                    or next(iter(bases), sr.get("label", "Home")),
                })
        return {
            "home_bases": all_bases,
            "home_lat": float(cfg["home_lat"]),
            "home_lng": float(cfg["home_lng"]),
            "home_label": region["label"],
            "home_radius_miles": region["radius_miles"],
            "far_from_home_miles": float(cfg.get("far_from_home_miles", DEFAULT_FAR_FROM_HOME_MILES)),
            "home_regions": [region, *extra_regions],
            "source": "config",
        }

    regions = detect_home_regions(locations, cache)
    if not regions:
        bases, lat, lng, label = detect_home_bases(locations, cache)
        regions = [{
            "lat": lat, "lng": lng, "radius_miles": DEFAULT_HOME_RADIUS_MILES,
            "label": label, "bases": bases, "charge_count": 0,
        }]

    primary = regions[0]
    all_bases: set[str] = set()
    for r in regions:
        all_bases.update(r["bases"])

    return {
        "home_bases": all_bases,
        "home_lat": primary["lat"],
        "home_lng": primary["lng"],
        "home_label": primary["label"],
        "home_radius_miles": primary["radius_miles"],
        "far_from_home_miles": DEFAULT_FAR_FROM_HOME_MILES,
        "home_regions": regions,
        "source": "auto",
    }
