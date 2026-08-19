#!/usr/bin/env python3
"""Detect or load home-base configuration for any Tesla charging history."""

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


def resolve_home_config(
    locations: list[str],
    cache: dict,
) -> dict:
    """
    Merge optional owner_config.json with auto-detected home base.
    """
    cfg = load_owner_config()
    if cfg.get("home_bases") and cfg.get("home_lat") is not None and cfg.get("home_lng") is not None:
        return {
            "home_bases": set(cfg["home_bases"]),
            "home_lat": float(cfg["home_lat"]),
            "home_lng": float(cfg["home_lng"]),
            "home_label": cfg.get("home_label") or extract_city(next(iter(cfg["home_bases"]))),
            "home_radius_miles": float(cfg.get("home_radius_miles", DEFAULT_HOME_RADIUS_MILES)),
            "far_from_home_miles": float(cfg.get("far_from_home_miles", DEFAULT_FAR_FROM_HOME_MILES)),
            "source": "config",
        }

    bases, lat, lng, label = detect_home_bases(locations, cache)
    return {
        "home_bases": bases,
        "home_lat": lat,
        "home_lng": lng,
        "home_label": label,
        "home_radius_miles": DEFAULT_HOME_RADIUS_MILES,
        "far_from_home_miles": DEFAULT_FAR_FROM_HOME_MILES,
        "source": "auto",
    }
