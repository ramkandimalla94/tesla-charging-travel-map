#!/usr/bin/env python3
"""
Optional Playwright invoice scraper for failed/ambiguous geocodes.

Uses a persistent browser profile so you only need to log in once (including 2FA).
Credentials are read from .env — never hardcoded.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from geopy.geocoders import Nominatim

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
MERGED = DATA_DIR / "merged_charges.csv"
CACHE_FILE = DATA_DIR / "locations_cache.json"
SESSION_DIR = DATA_DIR / "tesla_session"

ADDRESS_PATTERN = re.compile(
    r"(\d{1,6}\s+[\w\s.'#-]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|"
    r"Drive|Dr|Lane|Ln|Way|Court|Ct|Place|Pl|Parkway|Pkwy|Highway|Hwy|Loop)[^,\n]*"
    r",\s*[\w\s]+,\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?)",
    re.IGNORECASE,
)


def load_cache() -> dict:
    if CACHE_FILE.exists():
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_cache(cache: dict) -> None:
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)


def get_failed_sites(cache: dict) -> list[str]:
    return [
        name for name, entry in cache.items()
        if entry.get("lat") is None or entry.get("lng") is None
    ]


def find_invoice_for_site(df: pd.DataFrame, site: str) -> str | None:
    rows = df[df["SiteLocationName"] == site]
    if rows.empty:
        return None
    url = rows.iloc[0].get("Invoice")
    return str(url) if pd.notna(url) else None


def extract_address(page_text: str) -> str | None:
    match = ADDRESS_PATTERN.search(page_text)
    return match.group(1).strip() if match else None


def scrape_invoice_urls(
    sites: list[str],
    invoice_urls: dict[str, str],
    headless: bool = False,
) -> dict[str, str]:
    from playwright.sync_api import sync_playwright

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    addresses: dict[str, str] = {}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(SESSION_DIR),
            headless=headless,
        )
        page = context.pages[0] if context.pages else context.new_page()

        email = os.getenv("TESLA_EMAIL", "")
        password = os.getenv("TESLA_PASSWORD", "")

        page.goto("https://auth.tesla.com/user/reports")
        time.sleep(3)

        if "auth.tesla.com" in page.url and email and password:
            try:
                page.fill('input[type="email"], input[name="identity"]', email)
                page.click('button[type="submit"]')
                time.sleep(2)
                page.fill('input[type="password"]', password)
                page.click('button[type="submit"]')
                print("Submitted credentials — complete 2FA manually if prompted.")
                time.sleep(15)
            except Exception as exc:
                print(f"Auto-login failed ({exc}). Complete login manually in the browser.")

        for site in sites:
            url = invoice_urls.get(site)
            if not url:
                print(f"No invoice URL for '{site}', skipping")
                continue
            print(f"Scraping invoice for: {site}")
            try:
                page.goto(url, wait_until="networkidle", timeout=60000)
                time.sleep(2)
                text = page.inner_text("body")
                address = extract_address(text)
                if address:
                    addresses[site] = address
                    print(f"  Found address: {address}")
                else:
                    print("  No address found in invoice")
            except Exception as exc:
                print(f"  Error: {exc}")

        context.close()

    return addresses


def geocode_address(geolocator: Nominatim, address: str) -> dict | None:
    try:
        location = geolocator.geocode(address, country_codes="us", timeout=10)
        if location:
            return {
                "lat": location.latitude,
                "lng": location.longitude,
                "display_name": location.address,
            }
    except Exception as exc:
        print(f"  Geocode error: {exc}")
    return None


def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(description="Scrape Tesla invoices for failed geocodes")
    parser.add_argument("--headless", action="store_true", help="Run browser headless")
    args = parser.parse_args()

    if not MERGED.exists() or not CACHE_FILE.exists():
        raise FileNotFoundError("Run merge_csvs.py and geocode_locations.py first")

    cache = load_cache()
    failed = get_failed_sites(cache)
    if not failed:
        print("No failed geocodes — nothing to scrape")
        return

    print(f"Found {len(failed)} failed geocodes: {failed}")

    df = pd.read_csv(MERGED)
    invoice_urls = {
        site: find_invoice_for_site(df, site)
        for site in failed
    }

    addresses = scrape_invoice_urls(failed, invoice_urls, headless=args.headless)

    user_agent = os.getenv("NOMINATIM_USER_AGENT", "tesla-charging-history-map")
    geolocator = Nominatim(user_agent=user_agent)

    for site, address in addresses.items():
        print(f"Re-geocoding '{site}' from invoice address...")
        result = geocode_address(geolocator, address)
        if result:
            cache[site] = {
                "site_name": site,
                "lat": result["lat"],
                "lng": result["lng"],
                "display_name": result["display_name"],
                "source": "invoice",
                "query": address,
            }
            print(f"  -> {result['lat']:.5f}, {result['lng']:.5f}")
        time.sleep(1.1)

    save_cache(cache)
    print(f"Updated cache: {CACHE_FILE}")


if __name__ == "__main__":
    main()
