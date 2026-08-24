# Multi-movie support helper
# Store multiple BookMyShow URLs in BMS_URL separated by newlines.
# The existing main.py must call this helper for full integration.

import re
from urllib.parse import urlparse


def split_bms_urls(value: str) -> list[str]:
    return [u.strip() for u in re.split(r"[\n,]+", value or "") if u.strip()]


def parse_bms_movie_url(url: str) -> dict[str, str | None]:
    parts = urlparse(url).path.strip("/").split("/")
    result = {"event_code": None, "date_code": None, "region_slug": None}
    for part in parts:
        if re.match(r"^ET\d{8,}$", part):
            result["event_code"] = part
        elif re.match(r"^\d{8}$", part):
            result["date_code"] = part
    if "movies" in parts:
        idx = parts.index("movies")
        if idx + 1 < len(parts):
            result["region_slug"] = parts[idx + 1]
    return result
