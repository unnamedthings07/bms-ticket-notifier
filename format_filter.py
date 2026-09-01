"""Bengaluru theatre-mode wrapper for the BMS ticket checker.

Theatre mode uses BMS_THEATRE as a theatre name and BMS_MOVIE=ANY as a
wildcard. A canonical BMS cinema URL may be supplied through BMS_THEATRE_URL
to make venue resolution deterministic. For example:
https://in.bookmyshow.com/cinemas/bengaluru/pvr-nexus-formerly-forum-koramangala/buytickets/PVFF/20260903
"""

import os
import re
from urllib.parse import unquote

import requests

import main as checker

SUPPORTED_FORMATS = {
    "imax": ("imax",),
    "4dx": ("4dx",),
    "dolby cinema": ("dolby cinema",),
    "3d": ("3d", "3-d"),
}

HEADERS = dict(checker.HEADERS)


def normalize_formats(raw: str) -> list[str]:
    values = []
    for item in (raw or "").split(","):
        key = item.strip().lower()
        if not key:
            continue
        if key not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported BMS_FORMAT '{item.strip()}'. "
                f"Use: {', '.join(SUPPORTED_FORMATS)}"
            )
        values.append(key)
    return values


def parse_cinema_url(url: str, name: str = ""):
    """Parse the exact BMS cinema URL shape, including /buytickets/CODE/date."""
    if not url:
        return None

    url = unquote(url).strip()
    match = re.search(
        r"https?://in\.bookmyshow\.com/cinemas/([^/]+)/([^/]+)/buytickets/([A-Za-z0-9]{3,})(?:/(\d{8}))?(?:[/?#]|$)",
        url,
        re.I,
    )
    if not match:
        return None

    city, venue_slug, venue_code, date_code = match.groups()
    # Never accept the literal route component as a venue code.
    if venue_code.lower() == "buytickets":
        return None

    return {
        "url": f"https://in.bookmyshow.com/cinemas/{city}/{venue_slug}/buytickets/{venue_code.upper()}",
        "venue_code": venue_code.upper(),
        "venue_slug": venue_slug,
        "name": name,
        "date_code": date_code,
    }


def extract_cinema_urls(text: str):
    pattern = re.compile(
        r"https?://in\.bookmyshow\.com/cinemas/bengaluru/[^\s\"'<>]+",
        re.I,
    )
    return [m.rstrip(".,);]") for m in pattern.findall(unquote(text))]


def discover_bengaluru_theatre(region_slug: str, theatre_filter: str):
    """Resolve a Bengaluru theatre without using BMS's blocked venue directory.

    Preferred path: BMS_THEATRE_URL, which is the canonical cinema page supplied
    by the user. Fallback: derive the slug from BMS_THEATRE and try direct pages,
    then search-engine indexed BMS cinema URLs.
    """
    target = (theatre_filter or "").strip()
    explicit_url = os.getenv("BMS_THEATRE_URL", "").strip()

    if explicit_url:
        info = parse_cinema_url(explicit_url, target)
        if info:
            return info
        print("  ⚠️ BMS_THEATRE_URL is not a valid BookMyShow cinema URL")

    if not target:
        return None

    slug = re.sub(r"^(pvr|inox|cinepolis|miraj|amb cinemas)\s*[:\-]?\s*", "", target, flags=re.I)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", slug.lower()).strip("-")
    candidates = [slug]
    full_slug = re.sub(r"[^a-zA-Z0-9]+", "-", target.lower()).strip("-")
    if full_slug not in candidates:
        candidates.append(full_slug)

    for candidate in candidates:
        for url in (
            f"https://in.bookmyshow.com/cinemas/bengaluru/{candidate}/buytickets/",
            f"https://in.bookmyshow.com/cinemas/bengaluru/{candidate}/",
        ):
            try:
                resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
            except requests.RequestException:
                continue
            info = parse_cinema_url(resp.url, target)
            if info:
                return info
            for found_url in extract_cinema_urls(resp.text):
                info = parse_cinema_url(found_url, target)
                if info:
                    return info

    # Search engines are only a fallback; no BMS venue-list request is made.
    query = f'site:in.bookmyshow.com/cinemas/bengaluru "{target}"'
    for engine in ("https://www.google.com/search?q=", "https://www.bing.com/search?q="):
        try:
            from urllib.parse import quote
            resp = requests.get(
                engine + quote(query),
                headers={"User-Agent": HEADERS["User-Agent"], "Accept-Language": "en-US,en;q=0.9"},
                timeout=15,
            )
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        for found_url in extract_cinema_urls(resp.text):
            info = parse_cinema_url(found_url, target)
            if info:
                return info

    return None


# Patch main.py's theatre resolver only for Bengaluru theatre mode.
if (
    os.getenv("BMS_MODE", "movie").strip().lower() in ("theatre", "cinema")
    and os.getenv("BMS_REGION", "bengaluru").strip().lower() in ("bengaluru", "bangalore")
):
    checker.discover_theatre_page = discover_bengaluru_theatre


original_filter_shows = checker.filter_shows
requested_formats = normalize_formats(os.getenv("BMS_FORMAT", "").strip())


def filter_shows(shows, theatre_filter, time_periods, date_codes):
    filtered = original_filter_shows(shows, theatre_filter, time_periods, date_codes)
    if not requested_formats:
        return filtered

    return [
        show
        for show in filtered
        if any(
            alias in (show.screen_attr or "").strip().lower()
            for fmt in requested_formats
            for alias in SUPPORTED_FORMATS[fmt]
        )
    ]


checker.filter_shows = filter_shows


if __name__ == "__main__":
    checker.main()
