"""Entry point for the BMS ticket checker.

Theatre mode can now be driven directly by a canonical BookMyShow cinema URL.
In theatre mode put the full cinema URL in BMS_URL (or BMS_THEATRE_URL), for
example:
https://in.bookmyshow.com/cinemas/bengaluru/pvr-nexus-formerly-forum-koramangala/buytickets/PVFF/20260903

The URL supplies the theatre code and date. The checker then discovers every
movie listed on that cinema page for that date and uses the existing BMS
showtime API to determine whether tickets are released/available.
"""

import os
import re
from urllib.parse import unquote

import main as checker

SUPPORTED_FORMATS = {
    "imax": ("imax",),
    "4dx": ("4dx",),
    "dolby cinema": ("dolby cinema",),
    "3d": ("3d", "3-d"),
}


def parse_cinema_url(url: str):
    """Parse BMS /cinemas/city/slug/buytickets/CODE/YYYYMMDD exactly."""
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


# ---------------------------------------------------------------------------
# Theatre mode: URL is the source of truth. No cinema-directory discovery.
# ---------------------------------------------------------------------------
if checker.CONFIG["mode"] in ("theatre", "cinema"):
    configured_url = (
        os.getenv("BMS_URL", "").strip()
        or os.getenv("BMS_THEATRE_URL", "").strip()
        or os.getenv("BMS_THEATRE", "").strip()
    )
    cinema = parse_cinema_url(configured_url)

    if cinema:
        # The canonical URL contains the exact venue code and optionally date.
        # Put the date from the URL into CONFIG unless the user explicitly set
        # BMS_DATES. This avoids any ambiguity about which day is being checked.
        if not checker.CONFIG["dates"] and cinema["date_code"]:
            checker.CONFIG["dates"] = cinema["date_code"]

        # Keep the human-readable theatre variable for logging only.
        checker.CONFIG["theatre"] = cinema["name"]

        def discover_theatre_from_url(region_slug, theatre_filter):
            print(
                f"  🔗 Using BMS cinema URL directly: {configured_url}"
            )
            return cinema

        checker.discover_theatre_page = discover_theatre_from_url

        # Filter by the exact BMS venue code from the supplied URL. This is
        # safer than matching a display-name string.
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
                # Reuse the normal time-period filtering without reapplying its
                # theatre-name filter.
                if periods:
                    kept = original_filter([show], "", periods, date_codes)
                    if not kept:
                        continue
                result.append(show)
            return result

        checker.filter_shows = filter_shows_by_url


# Format filtering remains optional and works with URL-based theatre mode too.
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
