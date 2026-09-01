"""BMS Ticket Checker — CI/Headless mode.

Supports movie mode and theatre mode. Theatre mode accepts a theatre name
through BMS_THEATRE and discovers the matching BookMyShow cinema page before
collecting all movie event codes for the requested date(s).
"""

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from html import escape, unescape
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo

import requests

BMS_URLS_RAW = os.getenv("BMS_URLS", "").strip()
LEGACY_BMS_URL = os.getenv("BMS_URL", "").strip()
CONFIG = {
    "mode": os.getenv("BMS_MODE", "movie").strip().lower(),
    "region": os.getenv("BMS_REGION", "bengaluru").strip().lower(),
    "movie": os.getenv("BMS_MOVIE", "ANY").strip(),
    "urls": BMS_URLS_RAW or LEGACY_BMS_URL,
    "dates": os.getenv("BMS_DATES", "").strip(),
    "theatre": os.getenv("BMS_THEATRE", "").strip(),
    "time_period": os.getenv("BMS_TIME", "").strip(),
}

RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_TO_EMAIL = os.getenv("RESEND_TO_EMAIL", "")
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "onboarding@resend.dev")
STATE_FILE = "bms_state.json"
API_URL = "https://in.bookmyshow.com/api/movies-data/v4/showtimes-by-event/primary-dynamic"

AVAIL_STATUS_MAP = {
    "0": ("SOLD OUT", "🔴"), "1": ("ALMOST FULL", "🟡"),
    "2": ("FILLING FAST", "🟠"), "3": ("AVAILABLE", "🟢"),
}
DATE_STYLE_MAP = {"date-selected": "BOOKABLE", "date-disabled": "NOT_OPEN", "date-default": "AVAILABLE"}
TIME_PERIODS = {"morning": (600, 1200), "afternoon": (1200, 1600), "evening": (1600, 1900), "night": (1900, 2400)}
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
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9", "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    "Referer": "https://in.bookmyshow.com/", "Connection": "keep-alive",
}

@dataclass
class CatInfo:
    name: str; price: str; status: str

@dataclass
class ShowInfo:
    venue_code: str; venue_name: str; session_id: str; date_code: str; time: str; time_code: str; screen_attr: str; categories: list[CatInfo] = field(default_factory=list)

@dataclass
class DateInfo:
    date_code: str; status: str

def split_movie_urls(raw: str) -> list[str]:
    if not raw: return []
    urls = []
    for line in raw.splitlines():
        line = line.strip()
        if line: urls.extend(part.strip() for part in line.split(";") if part.strip())
    return urls

def parse_bms_url(url: str):
    parts = urlparse(url).path.strip("/").split("/")
    result = {"event_code": None, "date_code": None, "region_slug": None}
    for part in parts:
        if re.match(r"^ET\d{8,}$", part): result["event_code"] = part
        elif re.match(r"^\d{8}$", part): result["date_code"] = part
    if "movies" in parts:
        idx = parts.index("movies")
        if idx + 1 < len(parts): result["region_slug"] = parts[idx + 1]
    return result

def resolve_region(slug):
    key = (slug or "").lower().strip()
    return REGION_MAP.get(key, (key.upper()[:6], key, "0", "0", ""))

def fetch_bms(event_code, date_code, region_code, region_slug, lat, lon, geohash):
    headers = {**HEADERS, "Accept": "application/json, text/plain, */*", "x-app-code": "WEB", "x-region-code": region_code, "x-region-slug": region_slug, "x-geohash": geohash, "x-latitude": lat, "x-longitude": lon, "x-location-selection": "manual", "x-lsid": "", "Referer": f"https://in.bookmyshow.com/movies/{region_slug}/buytickets/{event_code}/"}
    params = {"eventCode": event_code, "dateCode": date_code or "", "isDesktop": "true", "regionCode": region_code, "xLocationShared": "false", "memberId": "", "lsId": "", "subCode": "", "lat": lat, "lon": lon}
    try:
        resp = requests.get(API_URL, headers=headers, params=params, timeout=20)
        if resp.status_code == 200: return resp.json()
        print(f"  HTTP {resp.status_code} for {event_code} / {date_code or '(default)'}")
    except requests.RequestException as exc: print(f"  Request failed: {exc}")
    return None

def parse_movie_info(data):
    info = {"name": "Unknown Movie", "language": ""}
    for widget in data.get("data", {}).get("topStickyWidgets", []):
        if widget.get("type") == "horizontal-text-list":
            for item in widget.get("data", []):
                for row in item.get("leftText", {}).get("data", []):
                    for component in row.get("components", []):
                        text = component.get("text", "")
                        if "•" in text: info["language"] = text.strip()
    bottom = data.get("data", {}).get("bottomSheetData", {})
    for widget in bottom.get("format-selector", {}).get("widgets", []):
        if widget.get("type") == "vertical-text-list":
            for item in widget.get("data", []):
                if item.get("styleId") == "bottomsheet-subtitle": info["name"] = item.get("text", info["name"])
    return info

def parse_dates(data):
    dates = []
    for widget in data.get("data", {}).get("topStickyWidgets", []):
        if widget.get("type") != "horizontal-block-list": continue
        for item in widget.get("data", []):
            if len(item.get("data", [])) >= 3: dates.append(DateInfo(item.get("id", ""), DATE_STYLE_MAP.get(item.get("styleId", ""), "UNKNOWN")))
    return dates

def parse_shows(data):
    shows = []
    for widget in data.get("data", {}).get("showtimeWidgets", []):
        if widget.get("type") != "groupList": continue
        for group in widget.get("data", []):
            if group.get("type") != "venueGroup": continue
            for card in group.get("data", []):
                if card.get("type") != "venue-card": continue
                addl = card.get("additionalData", {}); venue_name = addl.get("venueName", "Unknown"); venue_code = addl.get("venueCode", "")
                for showtime in card.get("showtimes", []):
                    sa = showtime.get("additionalData", {}); date_code = str(sa.get("showDateCode", "") or sa.get("dateCode", "")).strip(); cutoff = sa.get("cutOffDateTime", "")
                    if not date_code and re.match(r"^\d{8}", cutoff): date_code = cutoff[:8]
                    show = ShowInfo(venue_code, venue_name, sa.get("sessionId", ""), date_code, showtime.get("title", ""), sa.get("showTimeCode", ""), showtime.get("screenAttr", "") or sa.get("attributes", ""))
                    for category in sa.get("categories", []): show.categories.append(CatInfo(category.get("priceDesc", ""), category.get("curPrice", "0"), str(category.get("availStatus", ""))))
                    shows.append(show)
    return shows

def filter_shows(shows, theatre_filter, time_periods, date_codes):
    result = []; theatres = [x.strip().lower() for x in theatre_filter.split(",") if x.strip()] if theatre_filter else []; periods = [x.strip().lower() for x in time_periods.split(",") if x.strip()] if time_periods else []; dates = {x.strip() for x in date_codes.split(",") if x.strip()} if date_codes else set()
    for show in shows:
        if theatres and not any(k in show.venue_name.lower() for k in theatres): continue
        if dates and show.date_code and show.date_code not in dates: continue
        if periods:
            try: time_code = int(show.time_code)
            except (TypeError, ValueError): time_code = 0
            if not any(period in TIME_PERIODS and TIME_PERIODS[period][0] <= time_code < TIME_PERIODS[period][1] for period in periods): continue
        result.append(show)
    return result

def load_state():
    try:
        with open(STATE_FILE, encoding="utf-8") as handle: return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError): return {}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as handle: json.dump(state, handle, indent=2)

def build_state(shows, dates):
    show_state = {}
    for show in shows:
        for category in show.categories:
            key = f"{show.venue_code}|{show.session_id}|{show.date_code}|{category.name}"; show_state[key] = {"venue": show.venue_name, "time": show.time, "date": show.date_code, "cat": category.name, "price": category.price, "status": category.status}
    return {"shows": show_state, "dates": {date.date_code: date.status for date in dates}}

def detect_changes(old_state, new_state):
    changes = []; old_dates = old_state.get("dates", {}); new_dates = new_state.get("dates", {})
    for date_code, status in new_dates.items():
        if old_dates.get(date_code) == "NOT_OPEN" and status in ("BOOKABLE", "AVAILABLE"): changes.append(f"📅 NEW DATE OPENED: {date_code}")
    old_shows = old_state.get("shows", {}); new_shows = new_state.get("shows", {})
    for key in set(new_shows) - set(old_shows):
        show = new_shows[key]; changes.append(f"🆕 NEW: {show['venue']} {show['time']} [{show['date']}] — {show['cat']} ₹{show['price']}")
    for key, current in new_shows.items():
        previous = old_shows.get(key)
        if previous and previous["status"] == "0" and current["status"] != "0":
            label, icon = AVAIL_STATUS_MAP.get(current["status"], ("UNKNOWN", "⚪")); changes.append(f"{icon} BACK: {current['venue']} {current['time']} [{current['date']}] — {current['cat']} → {label}")
    return changes

def status_label(status): return AVAIL_STATUS_MAP.get(status, ("UNKNOWN", ""))[0]

def send_email(subject, changes, shows, movie_info):
    api_key = RESEND_API_KEY.strip(); to = RESEND_TO_EMAIL.strip(); sender = RESEND_FROM_EMAIL.strip() or "onboarding@resend.dev"
    if not api_key or not to: print("  ⚠️ Skipping email — RESEND_API_KEY or RESEND_TO_EMAIL not set."); return
    now_str = datetime.now().strftime("%d %b %Y, %I:%M %p"); movie_name = movie_info.get("name", "Movie"); venue_groups = {}
    for show in shows: venue_groups.setdefault(show.venue_name, []).append(show)
    rows = "".join(f'<li style="padding:3px 0;font-size:14px;">{escape(item)}</li>' for item in changes); change_html = f'<h3>Changes Detected</h3><ul style="line-height:1.6;">{rows}</ul>' if changes else ""
    shows_html = ""
    for venue_name, venue_shows in venue_groups.items():
        table_rows = ""
        for show in venue_shows:
            cats = " | ".join(f"{escape(cat.name)} Rs.{escape(cat.price)} ({status_label(cat.status)})" for cat in show.categories); screen = f" [{escape(show.screen_attr)}]" if show.screen_attr else ""; table_rows += f'<tr><td style="padding:5px 8px;border-bottom:1px solid #ddd;">{escape(show.time)}{screen}</td><td style="padding:5px 8px;border-bottom:1px solid #ddd;">{cats}</td></tr>'
        shows_html += f'<p><b>{escape(venue_name)}</b></p><table style="width:100%;border-collapse:collapse;"><tr><th style="text-align:left;padding:5px 8px;">Time</th><th style="text-align:left;padding:5px 8px;">Categories</th></tr>{table_rows}</table>'
    html = f'<!doctype html><html><body style="font-family:Arial,sans-serif;color:#333;padding:24px;"><h2 style="color:#111;">BMS Alert: {escape(movie_name)}</h2><p style="color:#666;">{escape(now_str)}</p><hr>{change_html}<h3>Current Showtimes</h3>{shows_html}</body></html>'
    plain = [subject, "", f"Checked at: {now_str}", ""] + (["Changes Detected:"] + [f"  - {x}" for x in changes] + [""] if changes else []) + ["Current Showtimes:"]
    for venue_name, venue_shows in venue_groups.items():
        plain.append(venue_name)
        for show in venue_shows:
            cats = " | ".join(f"{cat.name} Rs.{cat.price} ({status_label(cat.status)})" for cat in show.categories); screen = f" [{show.screen_attr}]" if show.screen_attr else ""; plain.append(f"  {show.time}{screen} - {cats}")
    try:
        resp = requests.post("https://api.resend.com/emails", headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, json={"from": sender, "to": [to], "subject": subject, "text": "\n".join(plain), "html": html}, timeout=15)
        if resp.status_code not in (200, 201): print(f"  ❌ Resend {resp.status_code}: {resp.text}"); sys.exit(1)
        print(f"  ✅ Email sent to {to}")
    except requests.RequestException as exc: print(f"  ❌ Email failed: {exc}"); sys.exit(1)

def clean_html_text(value):
    value = re.sub(r"<[^>]+>", " ", value); value = unescape(value); return re.sub(r"\s+", " ", value).strip()

def discover_theatre_page(region_slug, theatre_filter):
    """Discover any BookMyShow cinema from the city venue list using BMS_THEATRE."""
    target = theatre_filter.lower().strip()
    candidates = []
    # /venue-list is the public BMS cinema directory. Keep /cinemas as a fallback.
    for directory in (f"https://in.bookmyshow.com/{region_slug}/venue-list", f"https://in.bookmyshow.com/{region_slug}/cinemas"):
        try:
            resp = requests.get(directory, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                print(f"  ⚠️ Cinema directory HTTP {resp.status_code}: {directory}")
                continue
            html = resp.text
        except requests.RequestException as exc:
            print(f"  ⚠️ Cinema directory request failed: {exc}")
            continue

        anchor_re = re.compile(r'<a\b[^>]*href=["\']([^"\']*(?:/cinemas/|/buytickets/)[^"\']*)["\'][^>]*>(.*?)</a>', re.I | re.S)
        for href, body in anchor_re.findall(html):
            text = clean_html_text(body); full_href = urljoin(directory, href)
            if "/cinemas/" not in full_href or (target and target not in text.lower()): continue
            candidates.append((text, full_href))
        if candidates: break

        # Some BMS pages contain the venue name and URL in serialized JSON rather than anchors.
        if target:
            for match in re.finditer(r'(.{0,800}/cinemas/[^"\']+.{0,800})', html, re.I | re.S):
                chunk = match.group(1)
                if target not in clean_html_text(chunk).lower(): continue
                hrefs = re.findall(r'(?:https?:)?[^"\'\s]+/cinemas/[^"\'\s]+', chunk, re.I)
                for href in hrefs: candidates.append((target, urljoin(directory, href)))
        if candidates: break

    # Match requested text against both visible name and URL slug.
    if not candidates:
        slug_target = re.sub(r"[^a-z0-9]+", "-", target).strip("-")
        for directory in (f"https://in.bookmyshow.com/{region_slug}/venue-list", f"https://in.bookmyshow.com/{region_slug}/cinemas"):
            try:
                resp = requests.get(directory, headers=HEADERS, timeout=20)
                if resp.status_code != 200: continue
                for href in re.findall(r'href=["\']([^"\']*/cinemas/[^"\']+)["\']', resp.text, re.I):
                    full_href = urljoin(directory, href)
                    if slug_target and slug_target in full_href.lower(): candidates.append((full_href, full_href))
            except requests.RequestException: pass
            if candidates: break

    for text, href in candidates:
        match = re.search(r"/cinemas/([^/]+)/([^/]+)/buytickets/([A-Za-z0-9]+)(?:/\d{8})?", href)
        if match:
            city, venue_slug, venue_code = match.groups()
            return {"url": f"https://in.bookmyshow.com/cinemas/{city}/{venue_slug}/buytickets/{venue_code}", "venue_code": venue_code, "venue_slug": venue_slug, "name": text}
    return None

def discover_event_codes_from_theatre_page(theatre_base_url, date_code):
    url = theatre_base_url.rstrip("/") + (f"/{date_code}" if date_code else "")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200: print(f"  ❌ Theatre page HTTP {resp.status_code}: {url}"); return []
        html = resp.text
    except requests.RequestException as exc: print(f"  ❌ Theatre page request failed: {exc}"); return []
    events = []
    for href in re.findall(r'href=["\']([^"\']*?/movies/[^"\']*?ET\d{8,}[^"\']*)["\']', html, re.I):
        for code in re.findall(r"ET\d{8,}", href, re.I):
            code = code.upper()
            if code not in events: events.append(code)
    if not events:
        for code in re.findall(r"\bET\d{8,}\b", html, re.I):
            code = code.upper()
            if code not in events: events.append(code)
    return events

def theatre_dates():
    if CONFIG["dates"]: return [x.strip() for x in CONFIG["dates"].split(",") if x.strip()]
    return [datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d")]

def movie_name_matches(movie_name):
    wanted = CONFIG["movie"].strip()
    return not wanted or wanted.upper() == "ANY" or wanted == "*" or wanted.lower() in movie_name.lower()

def run_movie(event_code, region_slug, date_list, previous_state, label=""):
    region_code, region_slug_resolved, lat, lon, geohash = resolve_region(region_slug); all_shows, all_dates = [], []; movie_info = {"name": "Unknown Movie", "language": ""}
    for date_code in date_list:
        data = fetch_bms(event_code, date_code, region_code, region_slug_resolved, lat, lon, geohash)
        if not data: continue
        if movie_info["name"] == "Unknown Movie": movie_info = parse_movie_info(data)
        all_dates.extend(parse_dates(data)); all_shows.extend(parse_shows(data))
    if movie_info["name"] == "Unknown Movie" and not all_shows: return None
    if not movie_name_matches(movie_info["name"]): return None
    filtered = filter_shows(all_shows, CONFIG["theatre"], CONFIG["time_period"], CONFIG["dates"]); new_state = build_state(filtered, all_dates); changes = detect_changes(previous_state or {}, new_state)
    print(f"  🍿 {movie_info['name']}  {movie_info['language']} — {len(filtered)} showtime(s)")
    if changes:
        print(f"  ⚡ {len(changes)} change(s) detected"); [print(f"     {change}") for change in changes]; prefix = f"{label} — " if label else ""; send_email(f"BMS Alert: {prefix}{movie_info['name']} - {len(changes)} change(s)", changes, filtered, movie_info)
    else: print("  ✅ No changes since last check.")
    return new_state

def main_movie_mode(old_movies):
    urls = split_movie_urls(CONFIG["urls"])
    if not urls: print("  ❌ No BMS_URLS/BMS_URL configured."); return {}, False
    new_movies = {}; any_ok = False
    for index, url in enumerate(urls, start=1):
        parsed = parse_bms_url(url); event_code, region_slug, url_date = parsed["event_code"], parsed["region_slug"], parsed.get("date_code", ""); print(f"\n🎬 Movie {index}/{len(urls)}")
        if not event_code or not region_slug: print("  ❌ Invalid BMS URL — skipping."); continue
        date_list = [d.strip() for d in CONFIG["dates"].split(",") if d.strip()] if CONFIG["dates"] else ([url_date] if url_date else [""])
        state = run_movie(event_code, region_slug, date_list, old_movies.get(event_code), "")
        if state is not None: new_movies[event_code] = state; any_ok = True
    return new_movies, any_ok

def main_theatre_mode(old_movies):
    theatre = CONFIG["theatre"]
    if not theatre: print("  ❌ BMS_THEATRE is required in theatre mode."); return {}, False
    region_slug = "bengaluru" if CONFIG["region"] == "bangalore" else CONFIG["region"]
    print(f"\n🏢 Theatre mode — {theatre} | Region: {region_slug} | Movie: {CONFIG['movie']}")
    theatre_info = discover_theatre_page(region_slug, theatre)
    if not theatre_info: print("  ❌ Could not find that theatre on BookMyShow."); return {}, False
    print(f"  🎦 Matched: {theatre_info['name'] or theatre_info['venue_slug']} [{theatre_info['venue_code']}]")
    event_codes = []
    date_list = theatre_dates()
    for date_code in date_list:
        for code in discover_event_codes_from_theatre_page(theatre_info["url"], date_code):
            if code not in event_codes: event_codes.append(code)
    if not event_codes: print("  ⚠️ No movie event codes found on the theatre page."); return {}, False
    print(f"  🎬 Found {len(event_codes)} movie/event code(s)")
    new_movies = {}; any_ok = False
    for index, event_code in enumerate(event_codes, start=1):
        print(f"\n  Movie {index}/{len(event_codes)} — {event_code}"); state = run_movie(event_code, region_slug, date_list, old_movies.get(event_code), theatre)
        if state is not None: new_movies[event_code] = state; any_ok = True
    return new_movies, any_ok

def main():
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] BMS Ticket Checker — CI mode"); print(f"  Mode: {CONFIG['mode']}"); old_state = load_state(); old_movies = old_state.get("movies", {}) if isinstance(old_state, dict) else {}
    new_movies, any_ok = main_theatre_mode(old_movies) if CONFIG["mode"] in ("theatre", "cinema") else main_movie_mode(old_movies)
    if not any_ok: print("\n  ❌ No usable movie/showtime data was produced."); return
    save_state({"version": 3, "movies": new_movies}); print(f"\n✅ Done. Checked {len(new_movies)} movie(s).")

if __name__ == "__main__": main()
