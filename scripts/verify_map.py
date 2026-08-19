#!/usr/bin/env python3
"""Browser verification for travel map — Playwright screenshots + assertions."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output" / "travel_map.html"
SCREENSHOTS = ROOT / "docs" / "screenshots"
TRIPS_FILE = ROOT / "data" / "trips.json"

CO_BOUNDS = {"lat_min": 37, "lat_max": 41, "lng_min": -109, "lng_max": -102}


def load_trip_id(keyword: str) -> str:
    data = json.loads(TRIPS_FILE.read_text(encoding="utf-8"))
    for trip in data["trips"]:
        if keyword in trip["id"]:
            return trip["id"]
    raise KeyError(f"No trip matching {keyword!r}")


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Install playwright: pip install playwright && playwright install chromium")
        return 1

    if not OUTPUT.exists():
        print(f"Missing {OUTPUT} — run build_map.py first")
        return 1

    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    trip_co = load_trip_id("trip_009")
    trip_sea = load_trip_id("trip_011")

    console_errors: list[str] = []
    base_url = "http://127.0.0.1:8765/output/travel_map.html"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)))

        print(f"Loading {base_url}")
        page.goto(base_url, wait_until="networkidle", timeout=90000)

        # Wait for Mapbox map canvas
        page.wait_for_selector(".mapboxgl-canvas", timeout=60000)
        page.wait_for_timeout(5000)  # intro flyover

        # Hide splash if still visible
        page.evaluate("document.getElementById('splash')?.classList.add('hidden')")
        page.wait_for_timeout(1500)

        # a) Overview
        overview_path = SCREENSHOTS / "01-overview.png"
        page.screenshot(path=str(overview_path), full_page=False)
        print(f"Saved {overview_path}")

        # b) Colorado trip
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(3500)
        co_path = SCREENSHOTS / "02-colorado-trip.png"
        page.screenshot(path=str(co_path), full_page=False)

        co_markers = page.evaluate(
            """() => {
              const bounds = { latMin: 37, latMax: 41, lngMin: -109, lngMax: -102 };
              const stops = activeStops();
              const inCo = stops.filter(s =>
                s.lat >= bounds.latMin && s.lat <= bounds.latMax &&
                s.lng >= bounds.lngMin && s.lng <= bounds.lngMax
              );
              return { total: stops.length, inColorado: inCo.length, names: inCo.map(s => s.location.split(',')[0]) };
            }"""
        )
        print(f"Colorado markers: {co_markers}")
        co_path_saved = co_path

        # c) Seattle trip
        page.evaluate(f"selectTrip({json.dumps(trip_sea)})")
        page.wait_for_timeout(3500)
        sea_path = SCREENSHOTS / "03-seattle-trip.png"
        page.screenshot(path=str(sea_path), full_page=False)
        print(f"Saved {sea_path}")

        # Mapbox terrain check
        has_terrain = page.evaluate(
            "() => !!(map.getTerrain && map.getTerrain())"
        )
        print(f"Mapbox terrain active: {has_terrain}")

        # No ALL_ARCS / ArcLayer in page
        has_bad_arcs = page.evaluate(
            "() => typeof ALL_ARCS !== 'undefined' || typeof ArcLayer !== 'undefined'"
        )

        browser.close()

    # Filter ignorable console noise
    critical_errors = [
        e for e in console_errors
        if "favicon" not in e.lower()
        and "404" not in e.lower()
        and "net::ERR" not in e
    ]

    print(f"\nConsole errors ({len(critical_errors)}):")
    for e in critical_errors[:10]:
        print(f"  - {e}")

    ok = True
    if co_markers["inColorado"] < 4:
        print(f"FAIL: Expected >=4 CO markers, got {co_markers['inColorado']}")
        ok = False
    if has_bad_arcs:
        print("FAIL: ALL_ARCS or ArcLayer still present")
        ok = False
    if not has_terrain:
        print("WARN: Mapbox terrain not detected (may load async)")
    if critical_errors:
        print("WARN: Console errors detected — review above")

    if ok:
        print("\nVerification PASSED")
        return 0
    print("\nVerification FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
