#!/usr/bin/env python3
"""Crawl data/photos/<album>/* for EXIF GPS + time; write index + hover thumbs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image
from PIL.ExifTags import GPSTAGS, TAGS

ROOT = Path(__file__).resolve().parent.parent
PHOTOS_DIR = ROOT / "data" / "photos"
INDEX_FILE = ROOT / "data" / "photos_index.json"
THUMBS_DIR = ROOT / "output" / "photos" / "thumbs"

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".tif", ".tiff", ".webp"}
THUMB_MAX = 960


def _register_heif() -> None:
    try:
        from pillow_heif import register_heif_opener

        register_heif_opener()
    except ImportError:
        pass


def _ratio_to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        if hasattr(value, "numerator") and hasattr(value, "denominator"):
            den = value.denominator or 1
            return value.numerator / den
        if isinstance(value, (tuple, list)) and len(value) == 2:
            den = float(value[1]) or 1.0
            return float(value[0]) / den
        raise


def _dms_to_decimal(dms, ref: str) -> float | None:
    if not dms or len(dms) < 3:
        return None
    deg = _ratio_to_float(dms[0])
    minutes = _ratio_to_float(dms[1])
    seconds = _ratio_to_float(dms[2])
    decimal = deg + minutes / 60.0 + seconds / 3600.0
    if ref in ("S", "W"):
        decimal = -decimal
    return decimal


def _exif_dict(img: Image.Image) -> dict:
    raw = img.getexif()
    if not raw:
        return {}
    out: dict = {}
    for tag_id, value in raw.items():
        name = TAGS.get(tag_id, tag_id)
        out[name] = value
    gps_ifd = raw.get_ifd(0x8825) if hasattr(raw, "get_ifd") else None
    if gps_ifd:
        gps = {}
        for tag_id, value in gps_ifd.items():
            gps[GPSTAGS.get(tag_id, tag_id)] = value
        out["GPSInfo"] = gps
    return out


def extract_gps(exif: dict) -> tuple[float, float] | None:
    gps = exif.get("GPSInfo") or {}
    if not gps:
        return None
    lat = _dms_to_decimal(gps.get("GPSLatitude"), str(gps.get("GPSLatitudeRef", "N")))
    lng = _dms_to_decimal(gps.get("GPSLongitude"), str(gps.get("GPSLongitudeRef", "E")))
    if lat is None or lng is None:
        return None
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    if lat == 0 and lng == 0:
        return None
    return lat, lng


def _gps_ratio_hours(value) -> float | None:
    """Convert EXIF GPSTimeStamp component (hours/minutes/seconds) to float."""
    try:
        return _ratio_to_float(value)
    except (TypeError, ValueError):
        return None


def extract_gps_datetime(exif: dict) -> datetime | None:
    """
    GPSDateStamp + GPSTimeStamp are true UTC (unlike DateTimeOriginal, which is
    camera local wall-clock with no offset). Prefer these for trip matching.
    """
    gps = exif.get("GPSInfo") or {}
    date_stamp = gps.get("GPSDateStamp")
    time_stamp = gps.get("GPSTimeStamp")
    if not date_stamp or not time_stamp or len(time_stamp) < 3:
        return None
    try:
        date_text = str(date_stamp).strip().replace("-", ":")
        year, month, day = (int(p) for p in date_text.split(":")[:3])
        hour = _gps_ratio_hours(time_stamp[0])
        minute = _gps_ratio_hours(time_stamp[1])
        second = _gps_ratio_hours(time_stamp[2])
        if hour is None or minute is None or second is None:
            return None
        sec_int = int(second)
        micro = int(round((second - sec_int) * 1_000_000))
        return datetime(
            year, month, day, int(hour), int(minute), sec_int, micro, tzinfo=timezone.utc
        )
    except (TypeError, ValueError):
        return None


def extract_datetime(exif: dict, path: Path) -> str | None:
    # Prefer GPS UTC clock — DateTimeOriginal is local wall time without TZ.
    gps_dt = extract_gps_datetime(exif)
    if gps_dt is not None:
        return gps_dt.isoformat()

    for key in ("DateTimeOriginal", "DateTimeDigitized", "DateTime"):
        raw = exif.get(key)
        if not raw:
            continue
        text = str(raw).strip()
        for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S"):
            try:
                # No offset in EXIF — treat as UTC only as a last resort so
                # comparisons stay timezone-aware (same convention as trip CSV).
                dt = datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
                return dt.isoformat()
            except ValueError:
                continue
    # Fallback: file mtime so album photos still cluster into trips when EXIF time missing
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return mtime.isoformat()


def photo_id(album: str, path: Path) -> str:
    digest = hashlib.sha1(f"{album}/{path.name}".encode()).hexdigest()[:12]
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "_", path.stem)[:40].strip("_") or "photo"
    return f"{stem}_{digest}"


def write_thumb(img: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    frame = img.convert("RGB")
    frame.thumbnail((THUMB_MAX, THUMB_MAX), Image.Resampling.LANCZOS)
    frame.save(dest, "JPEG", quality=82, optimize=True)


def iter_album_images(photos_root: Path) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if not photos_root.is_dir():
        return found
    for album_dir in sorted(p for p in photos_root.iterdir() if p.is_dir()):
        if album_dir.name.startswith("."):
            continue
        for path in sorted(album_dir.rglob("*")):
            if not path.is_file():
                continue
            if path.suffix.lower() not in IMAGE_EXTS:
                continue
            found.append((album_dir.name, path))
    return found


def ingest(photos_root: Path, thumbs_root: Path, index_path: Path) -> dict:
    _register_heif()
    photos: list[dict] = []
    skipped_no_gps = 0
    errors = 0

    for album, path in iter_album_images(photos_root):
        try:
            with Image.open(path) as img:
                exif = _exif_dict(img)
                coords = extract_gps(exif)
                if not coords:
                    skipped_no_gps += 1
                    continue
                lat, lng = coords
                captured_at = extract_datetime(exif, path)
                pid = photo_id(album, path)
                thumb_rel = f"photos/thumbs/{album}/{pid}.jpg"
                thumb_abs = thumbs_root / album / f"{pid}.jpg"
                write_thumb(img, thumb_abs)
                photos.append({
                    "id": pid,
                    "album": album,
                    "source_name": path.name,
                    "lat": round(lat, 6),
                    "lng": round(lng, 6),
                    "captured_at": captured_at,
                    "thumb": thumb_rel,
                })
        except Exception as exc:  # noqa: BLE001 — keep ingest resilient per-file
            errors += 1
            print(f"  skip {path}: {exc}")

    photos.sort(key=lambda p: (p.get("captured_at") or "", p["id"]))
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "photo_count": len(photos),
        "skipped_no_gps": skipped_no_gps,
        "errors": errors,
        "photos": photos,
    }
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest album photos from data/photos/<album>/ into photos_index.json + thumbs"
    )
    parser.add_argument(
        "--photos-dir",
        type=Path,
        default=PHOTOS_DIR,
        help="Root folder containing album subfolders (default: data/photos)",
    )
    args = parser.parse_args()
    print(f"Scanning {args.photos_dir} …")
    result = ingest(args.photos_dir, THUMBS_DIR, INDEX_FILE)
    print(
        f"Indexed {result['photo_count']} photos "
        f"(skipped no GPS: {result['skipped_no_gps']}, errors: {result['errors']})"
    )
    print(f"  Index: {INDEX_FILE}")
    print(f"  Thumbs: {THUMBS_DIR}")


if __name__ == "__main__":
    main()
