"""
BMS Ticket Checker — CI/Headless mode for GitHub Actions.

Supports one or many BookMyShow movie URLs. Configure multiple URLs in
BMS_URLS, one URL per line. BMS_URL remains supported for backwards
compatibility.
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from html import escape
from urllib.parse import urlparse

import requests

# ──────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────────────
BMS_URLS_RAW = os.getenv("BMS_URLS", "").strip()
LEGACY_BMS_URL = os.getenv("BMS_URL", "").strip()
CONFIG = {
    "urls": BMS_URLS_RAW or LEGACY_BMS_URL,
    "dates": os.getenv("BMS_DATES", ""),          # YYYYMMDD, comma-separated
    "theatre": os.getenv("BMS_THEATRE", ""),      # substring filter, comma-separated
    "time_period": os.getenv("BMS_TIME", ""),     # morning/afternoon/evening/night
}

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_TO_EMAIL = os.getenv("RESEND_TO_EMAIL", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
STATE_FILE = "bms_state.json"

AVAIL_STATUS_MAP = {
    "0": ("SOLD OUT", "🔴"),
    "1": ("ALMOST FULL", "🟡"),
    "2": ("FILLING FAST", "🟠"),
    "3": ("AVAILABLE", "🟢"),
}

DATE_STYLE_MAP = {
    "date-selected": "BOOKABLE",
    "date-disabled": "NOT_OPEN",
    "date-default": "AVAILABLE",
}

TIME_PERIODS = {
    "morning": (600, 1200),
    "afternoon": (1200, 1600),
    "evening": (1600, 1900),
    "night": (1900, 2400),
}

REGION_MAP = {
    "chennai": ("CHEN", "chennai", "13.056", "80.206", "tf3"),
    "mumbai": ("MUMBAI", "mumbai", "19.076", "72.878", "te7"),
    "delhi-ncr": ("NCR", "delhi-ncr", "28.613", "77.209", "ttn"),
    "delhi": ("NCR", "delhi-ncr", "28.613", "77.209", "ttn"),
    "bengaluru": ("BANG", "bengaluru", "12.972", "77.594", "tdr"),
    "bangalore": ("BANG", "bengaluru", "12.972", "77.594", "tdr"),
    "hyderabad": ("HYD", "hyderabad", "17.385", "78.487", "tep"),
    "kolkata": ("KOLK", "kolkata", "22.573", "88.364", "tun"),
    "pune": ("PUNE", "pune", "18.520", "73.856", "te2"),
    "kochi": ("KOCH", "kochi", "9.932", "76.267", "t9z"),
}


@dataclass
class CatInfo:
    name: str
    price: str
    status: str


@dataclass
class ShowInfo:
    venue_code: str
    venue_name: str
    session_id: str
    date_code: str
    time: str
    time_code: str
    screen_attr: str
    categories: list[CatInfo] = field(default_factory=list)


@dataclass
class DateInfo:
    date_code: str
    status: str


# ──────────────────────────────────────────────────────────────────────
# URL / REGION HELPERS
# ──────────────────────────────────────────────────────────────────────
def split_movie_urls(raw: str) -> list[str]:
    """Accept one URL, or multiple URLs separated by newlines/semicolons."""
    if not raw:
        return []
    urls = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        urls.extend(part.strip() for part in line.split(";") if part.strip())
    return urls


def parse_bms_url(url: str):
    path = urlparse(url).path.strip("/")
    parts = path.split("/")
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


def resolve_region(slug):
    key = (slug or "").lower().strip()
    if key in REGION_MAP:
        return REGION_MAP[key]
    return (key.upper()[:6], key, "0", "0", "")


# ──────────────────────────────────────────────────────────────────────
# BMS API
# ──────────────────────────────────────────────────────────────────────
API_URL = "https://in.bookmyshow.com/api/movies-data/v4/showtimes-by-event/primary-dynamic"


def fetch_bms(event_code, date_code, region_code, region_slug, lat, lon, geohash):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/145.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": f"https://in.bookmyshow.com/movies/{region_slug}/buytickets/{event_code}/",
        "sec-ch-ua": '"Chromium";v="145", "Not:A-Brand";v="99"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"macOS"',
        "x-app-code": "WEB",
        "x-region-code": region_code,
        "x-region-slug": region_slug,
        "x-geohash": geohash,
        "x-latitude": lat,
        "x-longitude": lon,
        "x-location-selection": "manual",
        "x-lsid": "",
    }
    params = {
        "eventCode": event_code,
        "dateCode": date_code or "",
        "isDesktop": "true",
        "regionCode": region_code,
        "xLocationShared": "false",
        "memberId": "",
        "lsId": "",
        "subCode": "",
        "lat": lat,
        "lon": lon,
    }
    try:
        resp = requests.get(API_URL, headers=headers, params=params, timeout=15)
        if resp.status_code == 200:
            return resp.json()
        print(f"  HTTP {resp.status_code}")
    except requests.RequestException as exc:
        print(f"  Request failed: {exc}")
    return None


# ──────────────────────────────────────────────────────────────────────
# PARSERS
# ──────────────────────────────────────────────────────────────────────
def parse_movie_info(data):
    info = {"name": "Unknown Movie", "language": ""}
    for widget in data.get("data", {}).get("topStickyWidgets", []):
        if widget.get("type") == "horizontal-text-list":
            for item in widget.get("data", []):
                for row in item.get("leftText", {}).get("data", []):
                    for component in row.get("components", []):
                        text = component.get("text", "")
                        if "•" in text:
                            info["language"] = text.strip()
    bottom = data.get("data", {}).get("bottomSheetData", {})
    for widget in bottom.get("format-selector", {}).get("widgets", []):
        if widget.get("type") == "vertical-text-list":
            for item in widget.get("data", []):
                if item.get("styleId") == "bottomsheet-subtitle":
                    info["name"] = item.get("text", info["name"])
    return info


def parse_dates(data):
    dates = []
    for widget in data.get("data", {}).get("topStickyWidgets", []):
        if widget.get("type") != "horizontal-block-list":
            continue
        for item in widget.get("data", []):
            if len(item.get("data", [])) >= 3:
                dates.append(DateInfo(
                    date_code=item.get("id", ""),
                    status=DATE_STYLE_MAP.get(item.get("styleId", ""), "UNKNOWN"),
                ))
    return dates


def parse_shows(data):
    shows = []
    for widget in data.get("data", {}).get("showtimeWidgets", []):
        if widget.get("type") != "groupList":
            continue
        for group in widget.get("data", []):
            if group.get("type") != "venueGroup":
                continue
            for card in group.get("data", []):
                if card.get("type") != "venue-card":
                    continue
                addl = card.get("additionalData", {})
                venue_name = addl.get("venueName", "Unknown")
                venue_code = addl.get("venueCode", "")
                for showtime in card.get("showtimes", []):
                    sa = showtime.get("additionalData", {})
                    date_code = str(
                        sa.get("showDateCode", "") or sa.get("dateCode", "")
                    ).strip()
                    cutoff = sa.get("cutOffDateTime", "")
                    if not date_code and re.match(r"^\d{8}", cutoff):
                        date_code = cutoff[:8]
                    show = ShowInfo(
                        venue_code=venue_code,
                        venue_name=venue_name,
                        session_id=sa.get("sessionId", ""),
                        date_code=date_code,
                        time=showtime.get("title", ""),
                        time_code=sa.get("showTimeCode", ""),
                        screen_attr=showtime.get("screenAttr", "") or sa.get("attributes", ""),
                    )
                    for category in sa.get("categories", []):
                        status = str(category.get("availStatus", ""))
                        show.categories.append(CatInfo(
                            name=category.get("priceDesc", ""),
                            price=category.get("curPrice", "0"),
                            status=status,
                        ))
                    shows.append(show)
    return shows


# ──────────────────────────────────────────────────────────────────────
# FILTERING
# ──────────────────────────────────────────────────────────────────────
def filter_shows(shows, theatre_filter, time_periods, date_codes):
    result = []
    theatres = [x.strip().lower() for x in theatre_filter.split(",") if x.strip()] if theatre_filter else []
    periods = [x.strip().lower() for x in time_periods.split(",") if x.strip()] if time_periods else []
    dates = {x.strip() for x in date_codes.split(",") if x.strip()} if date_codes else set()

    for show in shows:
        if theatres and not any(k in show.venue_name.lower() for k in theatres):
            continue
        if dates and show.date_code and show.date_code not in dates:
            continue
        if periods:
            try:
                time_code = int(show.time_code)
            except (TypeError, ValueError):
                time_code = 0
            matched = any(
                period in TIME_PERIODS and TIME_PERIODS[period][0] <= time_code < TIME_PERIODS[period][1]
                for period in periods
            )
            if not matched:
                continue
        result.append(show)
    return result


# ──────────────────────────────────────────────────────────────────────
# STATE / CHANGE DETECTION
# ──────────────────────────────────────────────────────────────────────
def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as handle:
        json.dump(state, handle, indent=2)


def build_state(shows, dates):
    show_state = {}
    for show in shows:
        for category in show.categories:
            key = f"{show.venue_code}|{show.session_id}|{show.date_code}|{category.name}"
            show_state[key] = {
                "venue": show.venue_name,
                "time": show.time,
                "date": show.date_code,
                "cat": category.name,
                "price": category.price,
                "status": category.status,
            }
    date_state = {date.date_code: date.status for date in dates}
    return {"shows": show_state, "dates": date_state}


def detect_changes(old_state, new_state):
    changes = []
    old_dates = old_state.get("dates", {})
    new_dates = new_state.get("dates", {})
    for date_code, status in new_dates.items():
        old_status = old_dates.get(date_code)
        if old_status == "NOT_OPEN" and status in ("BOOKABLE", "AVAILABLE"):
            changes.append(f"📅 NEW DATE OPENED: {date_code}")

    old_shows = old_state.get("shows", {})
    new_shows = new_state.get("shows", {})
    for key in set(new_shows) - set(old_shows):
        show = new_shows[key]
        changes.append(
            f"🆕 NEW: {show['venue']} {show['time']} [{show['date']}] — {show['cat']} ₹{show['price']}"
        )
    for key, current in new_shows.items():
        previous = old_shows.get(key)
        if previous and previous["status"] == "0" and current["status"] != "0":
            label, icon = AVAIL_STATUS_MAP.get(current["status"], ("UNKNOWN", "⚪"))
            changes.append(
                f"{icon} BACK: {current['venue']} {current['time']} [{current['date']}] — {current['cat']} → {label}"
            )
    return changes


# ──────────────────────────────────────────────────────────────────────
# EMAIL
# ──────────────────────────────────────────────────────────────────────
def status_label(status):
    return AVAIL_STATUS_MAP.get(status, ("UNKNOWN", ""))[0]


def send_email(subject, changes, shows, movie_info):
    api_key = RESEND_API_KEY.strip()
    to = RESEND_TO_EMAIL.strip()
    sender = RESEND_FROM_EMAIL.strip() or "onboarding@resend.dev"
    if not api_key or not to:
        print("  ⚠️  Skipping email — RESEND_API_KEY or RESEND_TO_EMAIL not set.")
        return

    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p")
    movie_name = movie_info.get("name", "Movie")
    venue_groups = {}
    for show in shows:
        venue_groups.setdefault(show.venue_name, []).append(show)

    change_html = ""
    if changes:
        rows = "".join(
            f'<li style="padding:3px 0;font-size:14px;">{escape(item)}</li>' for item in changes
        )
        change_html = f"""
        <h3 style="margin:0 0 8px 0;font-size:15px;color:#333;">Changes Detected</h3>
        <ul style="margin:0 0 20px 0;padding-left:20px;line-height:1.6;color:#333;">{rows}</ul>
        """

    shows_html = ""
    for venue_name, venue_shows in venue_groups.items():
        rows = ""
        for show in venue_shows:
            cats = " | ".join(
                f"{escape(cat.name)} Rs.{escape(cat.price)} ({status_label(cat.status)})"
                for cat in show.categories
            )
            screen = f" [{escape(show.screen_attr)}]" if show.screen_attr else ""
            rows += (
                "<tr>"
                f'<td style="padding:5px 8px;border-bottom:1px solid #ddd;font-size:13px;">{escape(show.time)}{screen}</td>'
                f'<td style="padding:5px 8px;border-bottom:1px solid #ddd;font-size:13px;">{cats}</td>'
                "</tr>"
            )
        shows_html += f"""
        <p style="margin:14px 0 4px 0;font-size:14px;font-weight:bold;color:#333;">{escape(venue_name)}</p>
        <table style="width:100%;border-collapse:collapse;">
          <tr style="background:#f5f5f5;">
            <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ddd;">Time</th>
            <th style="padding:5px 8px;text-align:left;border-bottom:1px solid #ddd;">Categories</th>
          </tr>
          {rows}
        </table>
        """

    html = f"""<!doctype html>
<html><head><meta charset="utf-8"></head>
<body style="margin:0;padding:24px;font-family:Arial,Helvetica,sans-serif;color:#333;background:#fff;">
<h2 style="margin:0 0 4px 0;color:#111;">BMS Alert: {escape(movie_name)}</h2>
<p style="margin:0 0 20px 0;color:#666;font-size:13px;">{escape(now_str)}</p>
<hr style="border:none;border-top:1px solid #ddd;margin:0 0 20px 0;">
{change_html}
<h3 style="margin:0 0 8px 0;font-size:15px;color:#333;">Current Showtimes</h3>
{shows_html}
<p style="margin:24px 0 0 0;font-size:12px;color:#999;">This is an automated alert from BMS Ticket Notifier.</p>
</body></html>"""

    plain = [subject, "", f"Checked at: {now_str}", ""]
    if changes:
        plain.append("Changes Detected:")
        plain.extend(f"  - {item}" for item in changes)
        plain.append("")
    plain.append("Current Showtimes:")
    for venue_name, venue_shows in venue_groups.items():
        plain.append(f"\n{venue_name}")
        for show in venue_shows:
            cats = " | ".join(
                f"{cat.name} Rs.{cat.price} ({status_label(cat.status)})" for cat in show.categories
            )
            screen = f" [{show.screen_attr}]" if show.screen_attr else ""
            plain.append(f"  {show.time}{screen} - {cats}")
    plain.append("\nThis is an automated alert from BMS Ticket Notifier.")

    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": sender,
                "to": [to],
                "subject": subject,
                "text": "\n".join(plain),
                "html": html,
            },
            timeout=15,
        )
        if resp.status_code not in (200, 201):
            print(f"  ❌ Resend {resp.status_code}: {resp.text}")
            sys.exit(1)
        print(f"  ✅ Email sent to {to}")
    except requests.RequestException as exc:
        print(f"  ❌ Email failed: {exc}")
        sys.exit(1)


# ──────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────
def main():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now_str}] BMS Ticket Checker — CI mode")

    urls = split_movie_urls(CONFIG["urls"])
    if not urls:
        print("  ❌ No BMS_URLS/BMS_URL configured.")
        sys.exit(1)

    old_state = load_state()
    old_movies = old_state.get("movies", {}) if isinstance(old_state, dict) else {}
    new_movies = {}
    any_movie_ok = False

    for index, url in enumerate(urls, start=1):
        parsed = parse_bms_url(url)
        event_code = parsed["event_code"]
        region_slug = parsed["region_slug"]
        url_date = parsed.get("date_code", "")

        print(f"\n🎬 Movie {index}/{len(urls)}")
        if not event_code or not region_slug:
            print("  ❌ Invalid BMS URL. Could not extract event/region — skipping.")
            continue

        region_code, region_slug_resolved, lat, lon, geohash = resolve_region(region_slug)
        raw_dates = CONFIG["dates"].strip()
        if raw_dates:
            date_list = [d.strip() for d in raw_dates.split(",") if d.strip()]
        elif url_date:
            date_list = [url_date]
        else:
            date_list = [""]

        print(f"  Event: {event_code}  Region: {region_code}  Dates: {date_list}")

        all_shows = []
        all_dates = []
        movie_info = {"name": "Unknown Movie", "language": ""}

        for date_code in date_list:
            data = fetch_bms(
                event_code,
                date_code,
                region_code,
                region_slug_resolved,
                lat,
                lon,
                geohash,
            )
            if not data:
                print(f"  ⚠️  No data for date {date_code or '(default)'}")
                continue
            if movie_info["name"] == "Unknown Movie":
                movie_info = parse_movie_info(data)
            all_dates.extend(parse_dates(data))
            all_shows.extend(parse_shows(data))

        if not all_shows:
            print("  ❌ No showtimes found — skipping state update for this movie.")
            continue

        any_movie_ok = True
        filtered = filter_shows(
            all_shows,
            CONFIG["theatre"],
            CONFIG["time_period"],
            CONFIG["dates"],
        )
        print(f"  🍿 {movie_info['name']}  {movie_info['language']}")
        print(f"  📊 {len(filtered)} showtime(s) after filters")

        new_movie_state = build_state(filtered, all_dates)
        new_movies[event_code] = new_movie_state

        previous_movie_state = old_movies.get(event_code)
        if previous_movie_state is None and index == 1 and not old_movies:
            # Backwards compatibility with the original single-movie state file.
            if isinstance(old_state, dict) and "shows" in old_state and "dates" in old_state:
                previous_movie_state = old_state

        changes = detect_changes(previous_movie_state or {}, new_movie_state)
        if changes:
            print(f"  ⚡ {len(changes)} change(s) detected:")
            for change in changes:
                print(f"     {change}")
            send_email(
                f"BMS Alert: {movie_info['name']} - {len(changes)} change(s)",
                changes,
                filtered,
                movie_info,
            )
        else:
            print("  ✅ No changes since last check.")

        print(f"  Current status ({len(filtered)} shows):")
        for show in filtered:
            cats = ", ".join(
                f"{cat.name}=₹{cat.price}({status_label(cat.status)})" for cat in show.categories
            )
            screen = f"|{show.screen_attr}" if show.screen_attr else ""
            print(f"    {show.venue_name} — {show.time}{screen} [{show.date_code}] — {cats}")

    if not any_movie_ok:
        print("\n  ❌ No configured movie produced usable showtime data.")
        sys.exit(0)

    save_state({
        "version": 2,
        "movies": new_movies,
    })
    print(f"\n✅ Done. Checked {len(new_movies)} movie(s).")


if __name__ == "__main__":
    main()
