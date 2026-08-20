#!/usr/bin/env python3
"""Browser verification for travel map — Playwright screenshots + assertions."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUTPUT = ROOT / "output" / "travel_map.html"
SCREENSHOTS = ROOT / "docs" / "screenshots"
TRIPS_FILE = ROOT / "data" / "trips.json"

CO_BOUNDS = {"lat_min": 37, "lat_max": 41, "lng_min": -109, "lng_max": -102}
CONUS = {"lat_min": 24, "lat_max": 50, "lng_min": -125, "lng_max": -95}


def load_trip_id(keyword: str) -> str:
    data = json.loads(TRIPS_FILE.read_text(encoding="utf-8"))
    for trip in data["trips"]:
        if keyword in trip["id"]:
            return trip["id"]
    raise KeyError(f"No trip matching {keyword!r}")


def in_conus(lat: float, lng: float) -> bool:
    return (
        CONUS["lat_min"] <= lat <= CONUS["lat_max"]
        and CONUS["lng_min"] <= lng <= CONUS["lng_max"]
    )


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
    trip_co = load_trip_id("Henrietta_to_Vernon")
    trip_sea = load_trip_id("Henrietta_to_Yakima")

    console_errors: list[str] = []
    base_url = "http://127.0.0.1:8765/output/travel_map.html"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900})
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)
        page.on("pageerror", lambda err: console_errors.append(str(err)))

        print(f"Loading {base_url}")
        page.goto(base_url, wait_until="networkidle", timeout=90000)
        page.wait_for_selector(".mapboxgl-canvas", timeout=60000)
        page.wait_for_timeout(3000)
        page.evaluate("document.getElementById('splash')?.classList.add('hidden')")
        page.evaluate("document.getElementById('onboarding')?.classList.remove('visible')")
        page.wait_for_timeout(1000)

        # Overview
        overview = page.evaluate(
            """() => ({
              center: map.getCenter(),
              pitch: map.getPitch(),
              zoom: map.getZoom(),
              projection: map.getProjection?.()?.name || 'mercator',
              hasDeck: typeof deck !== 'undefined',
            })"""
        )
        print(f"Overview camera: {overview}")
        page.screenshot(path=str(SCREENSHOTS / "01-overview.png"))

        # Colorado trip
        page.evaluate(f"selectTrip({json.dumps(trip_co)})")
        page.wait_for_timeout(3500)
        co_state = page.evaluate(
            """() => {
              const bounds = { latMin: 37, latMax: 41, lngMin: -109, lngMax: -102 };
              const stops = activeStops();
              const inCo = stops.filter(s =>
                s.lat >= bounds.latMin && s.lat <= bounds.latMax &&
                s.lng >= bounds.lngMin && s.lng <= bounds.lngMax
              );
              const c = map.getCenter();
              return {
                total: stops.length,
                inColorado: inCo.length,
                names: inCo.map(s => s.location.split(',')[0]),
                center: { lat: c.lat, lng: c.lng },
                pitch: map.getPitch(),
                zoom: map.getZoom(),
                projection: map.getProjection?.()?.name || 'mercator',
              };
            }"""
        )
        print(f"Colorado: {co_state}")
        page.screenshot(path=str(SCREENSHOTS / "04-colorado-fixed.png"))

        # Seattle trip
        page.evaluate(f"selectTrip({json.dumps(trip_sea)})")
        page.wait_for_timeout(3500)
        sea_state = page.evaluate(
            """() => {
              const stops = activeStops();
              const c = map.getCenter();
              const wa = stops.filter(s => s.lat >= 45 && s.lat <= 49 && s.lng >= -125 && s.lng <= -116);
              const tx = stops.filter(s => s.lat >= 25 && s.lat <= 37 && s.lng >= -107 && s.lng <= -93);
              return {
                total: stops.length,
                waStops: wa.length,
                txStops: tx.length,
                center: { lat: c.lat, lng: c.lng },
                pitch: map.getPitch(),
                zoom: map.getZoom(),
              };
            }"""
        )
        print(f"Seattle: {sea_state}")
        page.screenshot(path=str(SCREENSHOTS / "05-seattle-fixed.png"))

        browser.close()

    critical_errors = [
        e for e in console_errors
        if "favicon" not in e.lower() and "404" not in e.lower() and "net::ERR" not in e
    ]
    print(f"\nConsole errors ({len(critical_errors)}):")
    for e in critical_errors[:10]:
        print(f"  - {e}")

    ok = True
    if co_state["inColorado"] < 4:
        print(f"FAIL: Expected >=4 CO markers, got {co_state['inColorado']}")
        ok = False
    if not in_conus(co_state["center"]["lat"], co_state["center"]["lng"]):
        print(f"FAIL: CO camera outside CONUS: {co_state['center']}")
        ok = False
    if co_state["pitch"] > 50:
        print(f"FAIL: Pitch too high for CO view: {co_state['pitch']}")
        ok = False
    if overview.get("hasDeck"):
        print("FAIL: deck.gl still loaded")
        ok = False
    if overview.get("projection") == "globe":
        print("FAIL: Still using globe projection")
        ok = False
    if sea_state["waStops"] < 1 and sea_state["txStops"] < 1:
        print(f"FAIL: Seattle trip missing WA/TX stops: {sea_state}")
        ok = False
    if not in_conus(sea_state["center"]["lat"], sea_state["center"]["lng"]):
        print(f"FAIL: Seattle camera outside CONUS: {sea_state['center']}")
        ok = False
    if critical_errors:
        print("WARN: Console errors detected")

    if ok:
        print("\nVerification PASSED")
        return 0
    print("\nVerification FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
