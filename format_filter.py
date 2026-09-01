"""Entry point for the BMS ticket checker.

Theatre mode is driven by a canonical BookMyShow cinema URL. In theatre mode,
put the full cinema URL in BMS_URL (or BMS_THEATRE_URL), for example:
https://in.bookmyshow.com/cinemas/bengaluru/pvr-nexus-formerly-forum-koramangala/buytickets/PVFF/20260903

The URL supplies the exact venue code and date. Movie/event codes are discovered
from the cinema page through a text-rendering fallback because GitHub Actions may
receive HTTP 403 from the direct BMS HTML page. Ticket availability is then read
from BookMyShow's showtime API.
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


def parse_cinema_url(url: str):
    """Parse /cinemas/city/slug/buytickets/CODE/YYYYMMDD exactly."""
    if not url:
        return None
    url = unquote(url).strip().rstrip("/")
    match = re.search(
        r"^https?://in\.bookmyshow\.com/cinemas/([^/]+)/([^/]+)/buytickets/([A-Za-z0-9]+)(?:/(\d{8}))?$",
        url,
        re.I,
    )
    if not match:
        return None
    city, slug, code, date_code = match.groups()
    if code.lower() == "buytickets":
        return None
    return {
        "url": f"https://in.bookmyshow.com/cinemas/{city}/{slug}/buytickets/{code.upper()}",
        "venue_code": code.upper(),
        "venue_slug": slug,
        "name": slug.replace("-", " ").title(),
        "date_code": date_code or "",
        "region_slug": city,
    }


def normalize_formats(raw: str) -> list[str]:
    result = []
    for item in (raw or "").split(","):
        key = item.strip().lower()
        if not key:
            continue
        if key not in SUPPORTED_FORMATS:
            raise ValueError(
                f"Unsupported BMS_FORMAT '{item.strip()}'. "
                f"Use: {', '.join(SUPPORTED_FORMATS)}"
            )
        result.append(key)
    return result


def _extract_event_codes(text: str) -> list[str]:
    found = []
    for code in re.findall(r"\bET\d{8,}\b", text or "", re.I):
        code = code.upper()
        if code not in found:
            found.append(code)
    return found


def _fetch_event_codes_via_renderer(url: str) -> list[str]:
    """Fetch a BMS page through a text renderer when GitHub gets BMS HTTP 403."""
    renderer_url = "https://r.jina.ai/" + url
    try:
        response = requests.get(
            renderer_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
        if response.status_code == 200:
            return _extract_event_codes(response.text)
        print(f"  ⚠️ Renderer HTTP {response.status_code}: {renderer_url}")
    except requests.RequestException as exc:
        print(f"  ⚠️ Renderer request failed: {exc}")
    return []


if checker.CONFIG["mode"] in ("theatre", "cinema"):
    configured_url = (
        os.getenv("BMS_URL", "").strip()
        or os.getenv("BMS_THEATRE_URL", "").strip()
        or os.getenv("BMS_THEATRE", "").strip()
    )
    cinema = parse_cinema_url(configured_url)

    if cinema:
        # The cinema URL itself is authoritative. In particular, a Bengaluru
        # cinema URL must always use the existing Bengaluru BMS API configuration
        # even when BMS_REGION was left blank in GitHub Actions variables.
        if cinema["region_slug"].lower() in ("bengaluru", "bangalore"):
            checker.CONFIG["region"] = "bengaluru"
        elif not checker.CONFIG["region"]:
            checker.CONFIG["region"] = cinema["region_slug"]

        if not checker.CONFIG["dates"] and cinema["date_code"]:
            checker.CONFIG["dates"] = cinema["date_code"]

        checker.CONFIG["theatre"] = cinema["name"]

        def discover_theatre_from_url(region_slug, theatre_filter):
            print(f"  🔗 Using BMS cinema URL directly: {configured_url}")
            return cinema

        checker.discover_theatre_page = discover_theatre_from_url

        def discover_events_from_url(theatre_base_url, date_code):
            full_url = theatre_base_url.rstrip("/")
            if date_code:
                full_url += f"/{date_code}"

            print(f"  🔎 Discovering movies from the cinema page for {date_code}")
            codes = _fetch_event_codes_via_renderer(full_url)
            if codes:
                print(f"  🎬 Renderer found {len(codes)} movie/event code(s)")
            else:
                print("  ⚠️ Renderer found no movie event codes.")
            return codes

        checker.discover_event_codes_from_theatre_page = discover_events_from_url

        original_filter = checker.filter_shows

        def filter_shows_by_url(shows, theatre_filter, time_periods, date_codes):
            dates = {x.strip() for x in (date_codes or "").split(",") if x.strip()}
            periods = (time_periods or "").strip()
            result = []
            for show in shows:
                if show.venue_code.upper() != cinema["venue_code"]:
                    continue
                if dates and show.date_code and show.date_code not in dates:
                    continue
                if periods and not original_filter([show], "", periods, date_codes):
                    continue
                result.append(show)
            return result

        checker.filter_shows = filter_shows_by_url

requested_formats = normalize_formats(os.getenv("BMS_FORMAT", "").strip())
if requested_formats:
    previous_filter = checker.filter_shows

    def filter_shows_with_format(shows, theatre_filter, time_periods, date_codes):
        filtered = previous_filter(shows, theatre_filter, time_periods, date_codes)
        return [
            show
            for show in filtered
            if any(
                alias in (show.screen_attr or "").strip().lower()
                for fmt in requested_formats
                for alias in SUPPORTED_FORMATS[fmt]
            )
        ]

    checker.filter_shows = filter_shows_with_format


if __name__ == "__main__":
    checker.main()
