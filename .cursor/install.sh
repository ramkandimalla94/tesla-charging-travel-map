#!/usr/bin/env bash
# Idempotent Cloud Agent setup for the Tesla Charging Travel Map.
# Refreshes the Python virtualenv, installs the Playwright browser used by the
# verification suite, and regenerates the map from the committed trip data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# System packages: venv support + Chromium/Playwright runtime libraries.
sudo apt-get update -qq
sudo apt-get install -y --no-install-recommends python3-venv

# Python virtualenv + pinned project dependencies.
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# Headless Chromium + OS dependencies for scripts/verify_map.py.
sudo .venv/bin/python -m playwright install-deps chromium
.venv/bin/python -m playwright install chromium

# Build the map from committed data/trips.json. build_map.py embeds
# MAPBOX_TOKEN when it is present (secret env var or .env); without it the
# page falls back to prompting for a Mapbox public token in the browser.
.venv/bin/python scripts/build_map.py
