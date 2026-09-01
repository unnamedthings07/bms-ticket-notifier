"""Format-filter wrapper for the BMS ticket checker.

Provides Bengaluru theatre discovery for theatre mode without hardcoding a
specific theatre. The wrapper accepts the same two-argument signature used by
main.py: (region_slug, theatre_filter).

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
from urllib.parse import quote, unquote_plus

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
    "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
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
    """Extract a BMS cinema URL from search-result HTML or text."""
    text = unescape(text)
    text = unquote_plus(text)
    text = text.replace("\\u002F", "/").replace("\\/", "/")
    patterns = [
        r"https?://in\.bookmyshow\.com/cinemas/(?:bengaluru|bang)/[^\s\"'<>]+",
        r"https?://in\.bookmyshow\.com/(?:bengaluru|bang)/cinemas/[^\s\"'<>]+",
    ]
    for pattern in patterns:
        for match in re.findall(pattern, text, re.I):
            url = match.rstrip(".,);]")
            if "/cinemas/" in url.lower():
                return url
    return None


def parse_cinema_url(url: str, name: str = ""):
    """Parse a BMS cinema URL and return its venue code.

    Supported forms:
      /cinemas/<city>/<venue>/buytickets/<CODE>/<date>
      /cinemas/<city>/<venue>/<CODE>/<date>

    The second pattern explicitly excludes the literal ``buytickets`` so a
    page URL ending in ``/buytickets/`` can never be mistaken for a venue code.
    """
    if not url:
        return None

    match = re.search(
        r"/cinemas/([^/]+)/([^/]+)/buytickets/([A-Za-z0-9]+)(?:/\d{8})?(?:[/?#]|$)",
        url,
        re.I,
    )
    if match:
        city, venue_slug, venue_code = match.groups()
    else:
        match = re.search(
            r"/cinemas/([^/]+)/([^/]+)/((?!buytickets(?:/|$))[A-Za-z0-9]{3,})(?:/\d{8})?(?:[/?#]|$)",
            url,
            re.I,
        )
        if not match:
            return None
        city, venue_slug, venue_code = match.groups()

    return {
        "url": f"https://in.bookmyshow.com/cinemas/{city}/{venue_slug}/buytickets/{venue_code.upper()}",
        "venue_code": venue_code.upper(),
        "venue_slug": venue_slug,
        "name": name,
    }


def fetch_direct_candidate(slug: str):
    """Try both BMS URL layouts for a directly derived Bengaluru slug."""
    if not slug:
        return None

    urls = (
        f"https://in.bookmyshow.com/cinemas/bengaluru/{slug}/buytickets/",
        f"https://in.bookmyshow.com/cinemas/bengaluru/{slug}/",
    )

    for url in urls:
        try:
            resp = requests.get(
                url,
                headers=HEADERS,
                timeout=20,
                allow_redirects=True,
            )
        except requests.RequestException:
            continue

        # Only accept a URL when it actually contains a venue code. In
        # particular, do not treat /buytickets/ as the code "BUYTICKETS".
        info = parse_cinema_url(resp.url)
        if info:
            return info

        if resp.status_code != 200:
            continue

        found = extract_bms_cinema_url(resp.text)
        info = parse_cinema_url(found) if found else None
        if info:
            return info

    return None


def search_engine_candidate(theatre: str):
    """Resolve the theatre from indexed BMS pages."""
    query = f'site:in.bookmyshow.com/cinemas/bengaluru "{theatre}"'
    engines = (
        "https://www.google.com/search?q=" + quote(query),
        "https://www.bing.com/search?q=" + quote(query),
    )

    for engine_url in engines:
        try:
            resp = requests.get(
                engine_url,
                headers={
                    "User-Agent": HEADERS["User-Agent"],
                    "Accept-Language": "en-US,en;q=0.9",
                    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                },
                timeout=15,
            )
        except requests.RequestException:
            continue
        if resp.status_code != 200:
            continue

        candidates = [resp.text, unescape(resp.text), unquote_plus(resp.text)]
        for page in candidates:
            url = extract_bms_cinema_url(page)
            if not url:
                continue
            info = parse_cinema_url(url, theatre)
            if info:
                return info

    return None


def discover_bengaluru_theatre(region_slug: str, theatre_filter: str):
    """Resolve a requested Bengaluru theatre using the main.py signature."""
    target = (theatre_filter or "").strip()
    if not target:
        return None

    candidates = [slugify(target)]
    stripped = re.sub(
        r"^(pvr|inox|cinepolis|miraj|amb cinemas)\s*[:\-]?\s*",
        "",
        target,
        flags=re.I,
    )
    stripped_slug = slugify(stripped)
    if stripped_slug and stripped_slug not in candidates:
        candidates.append(stripped_slug)

    for candidate in candidates:
        info = fetch_direct_candidate(candidate)
        if info:
            info["name"] = target
            return info

    return search_engine_candidate(target)


if (
    os.getenv("BMS_MODE", "movie").strip().lower() in ("theatre", "cinema")
    and os.getenv("BMS_REGION", "bengaluru").strip().lower()
    in ("bengaluru", "bangalore")
):
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
        if any(
            alias in attr
            for fmt in requested_formats
            for alias in SUPPORTED_FORMATS[fmt]
        ):
            matches.append(show)
    return matches


checker.filter_shows = filter_shows


if __name__ == "__main__":
    checker.main()
