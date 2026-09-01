"""Format-filter wrapper for the BMS ticket checker.

Also provides robust Bengaluru theatre discovery for theatre mode. BookMyShow's
public cinema directory can return HTTP 403 from GitHub Actions, so theatre
mode first tries the direct cinema URL and then uses a search-engine fallback
to resolve the requested theatre page. No theatre is hardcoded.

Supported BMS_FORMAT values:
  imax
  4dx
  dolby cinema
  3d

Multiple formats can be comma-separated. An empty BMS_FORMAT keeps all formats.
"""

import os
import re
from html import unescape
from urllib.parse import quote

import requests

import main as checker


SUPPORTED_FORMATS = {
    "imax": ("imax",),
    "4dx": ("4dx",),
    "dolby cinema": ("dolby cinema",),
    "3d": ("3d", "3-d"),
}

HEADERS = {
    **checker.HEADERS,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def normalize_formats(raw: str) -> list[str]:
    values = []
    for item in raw.split(",") if raw else []:
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


def slugify(value: str) -> str:
    value = value.lower()
    value = re.sub(r"[’'`]+", "", value)
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def extract_bms_cinema_url(text: str):
    text = text.replace("\\u002F", "/").replace("\\/", "/").replace("&amp;", "&")
    patterns = [
        r"https?://in\.bookmyshow\.com/cinemas/(?:bengaluru|bang)/[^\s\"'<>]+",
        r"https?://in\.bookmyshow\.com/(?:bengaluru|bang)/cinemas/[^\s\"'<>]+",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, re.I):
            url = match.rstrip(".,);\]")
            if "/cinemas/" in url.lower() and "/buytickets/" in url.lower():
                return url
    return None


def parse_cinema_url(url: str, name: str = ""):
    match = re.search(r"/cinemas/([^/]+)/([^/]+)/buytickets/([A-Za-z0-9]+)(?:/\d{8})?", url, re.I)
    if not match:
        return None
    city, venue_slug, venue_code = match.groups()
    return {
        "url": f"https://in.bookmyshow.com/cinemas/{city}/{venue_slug}/buytickets/{venue_code}",
        "venue_code": venue_code.upper(),
        "venue_slug": venue_slug,
        "name": name,
    }


def fetch_direct_candidate(slug: str):
    if not slug:
        return None
    url = f"https://in.bookmyshow.com/cinemas/bengaluru/{slug}/buytickets/"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True)
    except requests.RequestException:
        return None
    if resp.status_code != 200:
        return None
    info = parse_cinema_url(resp.url)
    if info:
        return info
    found = extract_bms_cinema_url(resp.text)
    return parse_cinema_url(found) if found else None


def search_engine_candidate(theatre: str):
    query = f'site:in.bookmyshow.com/cinemas/ "{theatre}" Bengaluru'
    for engine_url in (
        "https://www.google.com/search?q=" + quote(query),
        "https://www.bing.com/search?q=" + quote(query),
    ):
        try:
            resp = requests.get(
                engine_url,
                headers={"User-Agent": HEADERS["User-Agent"], "Accept-Language": "en-US,en;q=0.9"},
                timeout=15,
            )
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue
        url = extract_bms_cinema_url(resp.text) or extract_bms_cinema_url(unescape(resp.text))
        if url:
            info = parse_cinema_url(url, theatre)
            if info:
                return info
    return None


def discover_bengaluru_theatre(theatre_filter: str):
    target = theatre_filter.strip()
    candidates = [slugify(target)]
    stripped = re.sub(r"^(pvr|inox|cinepolis|miraj|amb cinemas)\s*[:\-]?\s*", "", target, flags=re.I)
    stripped_slug = slugify(stripped)
    if stripped_slug and stripped_slug not in candidates:
        candidates.append(stripped_slug)

    for candidate in candidates:
        info = fetch_direct_candidate(candidate)
        if info:
            info["name"] = target
            return info

    return search_engine_candidate(target)


if os.getenv("BMS_MODE", "movie").strip().lower() in ("theatre", "cinema") and os.getenv("BMS_REGION", "bengaluru").strip().lower() in ("bengaluru", "bangalore"):
    checker.discover_theatre_page = discover_bengaluru_theatre


original_filter_shows = checker.filter_shows
requested_formats = normalize_formats(os.getenv("BMS_FORMAT", "").strip())


def filter_shows(shows, theatre_filter, time_periods, date_codes):
    filtered = original_filter_shows(
        shows, theatre_filter, time_periods, date_codes
    )
    if not requested_formats:
        return filtered

    matches = []
    for show in filtered:
        attr = (show.screen_attr or "").strip().lower()
        if any(alias in attr for fmt in requested_formats for alias in SUPPORTED_FORMATS[fmt]):
            matches.append(show)
    return matches


checker.filter_shows = filter_shows


if __name__ == "__main__":
    checker.main()
