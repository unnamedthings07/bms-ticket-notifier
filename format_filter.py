"""Format-filter wrapper for the BMS ticket checker.

Supported BMS_FORMAT values:
  imax
  4dx
  dolby cinema
  3d

Multiple formats can be comma-separated. An empty BMS_FORMAT keeps all formats.
The underlying main.py remains responsible for movie/date/theatre/time filtering.
"""

import os

import main as checker


SUPPORTED_FORMATS = {
    "imax": ("imax",),
    "4dx": ("4dx",),
    "dolby cinema": ("dolby cinema",),
    "3d": ("3d", "3-d"),
}


# BookMyShow currently returns HTTP 403 for the Bengaluru cinema directory
# from GitHub Actions. Use the stable direct cinema page for this theatre
# instead of requiring a directory scrape.
DIRECT_CINEMA_PAGES = {
    "pvr: superplex forum mall, kanakapura road": {
        "url": "https://in.bookmyshow.com/cinemas/bengaluru/pvr-superplex-forum-mall-kanakapura-road/buytickets/PSPR",
        "venue_code": "PSPR",
        "venue_slug": "pvr-superplex-forum-mall-kanakapura-road",
        "name": "PVR: Superplex Forum Mall, Kanakapura Road",
    },
    "pvr superplex forum mall, kanakapura road": {
        "url": "https://in.bookmyshow.com/cinemas/bengaluru/pvr-superplex-forum-mall-kanakapura-road/buytickets/PSPR",
        "venue_code": "PSPR",
        "venue_slug": "pvr-superplex-forum-mall-kanakapura-road",
        "name": "PVR: Superplex Forum Mall, Kanakapura Road",
    },
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


# Override theatre discovery with direct BMS pages where known.
original_discover_theatre_page = checker.discover_theatre_page


def discover_theatre_page(region_slug, theatre_filter):
    key = " ".join(theatre_filter.lower().strip().split())
    direct = DIRECT_CINEMA_PAGES.get(key)
    if direct:
        return direct
    return original_discover_theatre_page(region_slug, theatre_filter)


checker.discover_theatre_page = discover_theatre_page


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
