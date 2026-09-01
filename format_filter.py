"""Theatre-only BookMyShow wrapper.

Theatre mode uses one canonical BMS cinema URL from BMS_URL. It discovers all
movie/event codes on that cinema page, checks their showtimes, and sends ONE
compact email for the entire theatre scan: movie name + timings only.
"""

import os
import re
from datetime import datetime
from html import escape
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
    if not url:
        return None
    url = unquote(url).strip().rstrip("/")
    match = re.fullmatch(
        r"https?://in\.bookmyshow\.com/cinemas/([^/]+)/([^/]+)/buytickets/([A-Za-z0-9]+)(?:/(\d{8}))?",
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
        "region_slug": city.lower(),
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


def extract_event_codes(text: str) -> list[str]:
    found = []
    for code in re.findall(r"\bET\d{8,}\b", text or "", re.I):
        code = code.upper()
        if code not in found:
            found.append(code)
    return found


def discover_event_codes(theatre_url: str, date_code: str) -> list[str]:
    full_url = theatre_url.rstrip("/")
    if date_code:
        full_url += f"/{date_code}"
    renderer_url = "https://r.jina.ai/" + full_url
    print(f"  🔎 Discovering movies from the cinema page for {date_code}")
    try:
        response = requests.get(
            renderer_url,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"  ⚠️ Renderer request failed: {exc}")
        return []
    if response.status_code != 200:
        print(f"  ⚠️ Renderer HTTP {response.status_code}")
        return []
    codes = extract_event_codes(response.text)
    print(f"  🎬 Renderer found {len(codes)} movie/event code(s)")
    return codes


def send_theatre_email(theatre_name: str, date_text: str, notifications: list[dict]):
    """Send exactly one email containing only movie names and timings."""
    api_key = checker.RESEND_API_KEY.strip()
    to = checker.RESEND_TO_EMAIL.strip()
    sender = checker.RESEND_FROM_EMAIL.strip() or "onboarding@resend.dev"
    if not api_key or not to:
        print("  ⚠️ Skipping email — RESEND_API_KEY or RESEND_TO_EMAIL not set.")
        return

    grouped: dict[str, set[str]] = {}
    for item in notifications:
        movie = (item.get("movie") or "Unknown Movie").strip() or "Unknown Movie"
        times = grouped.setdefault(movie, set())
        for show in item.get("shows", []):
            time = (show.time or "").strip()
            if time:
                times.add(time)

    def time_sort_key(value: str):
        match = re.match(r"^(\d{1,2}):(\d{2})\s*(AM|PM)?$", value.strip(), re.I)
        if not match:
            return (9999, value)
        hour, minute, suffix = match.groups()
        hour = int(hour)
        minute = int(minute)
        suffix = (suffix or "").upper()
        if suffix == "AM":
            hour = 0 if hour == 12 else hour
        elif suffix == "PM":
            hour = 12 if hour == 12 else hour + 12
        return (hour * 60 + minute, value)

    grouped = {
        movie: sorted(times, key=time_sort_key)
        for movie, times in sorted(grouped.items(), key=lambda pair: pair[0].lower())
    }

    subject = f"BMS Alert: {theatre_name} — {date_text}"
    plain_lines = [f"BMS Alert: {theatre_name}", f"Date: {date_text}", ""]
    html_rows = []
    for movie, times in grouped.items():
        joined = ", ".join(times) if times else "No timings found"
        plain_lines.append(f"{movie} — {joined}")
        html_rows.append(
            f'<tr><td style="padding:10px 8px;border-bottom:1px solid #ddd;"><b>{escape(movie)}</b></td>'
            f'<td style="padding:10px 8px;border-bottom:1px solid #ddd;">{escape(joined)}</td></tr>'
        )

    html = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;color:#333;padding:24px;">'
        f'<h2 style="color:#111;margin-bottom:6px;">BMS Theatre Alert</h2>'
        f'<p style="color:#666;margin-top:0;"><b>{escape(theatre_name)}</b> · {escape(date_text)}</p>'
        '<table style="width:100%;border-collapse:collapse;">'
        '<tr><th style="text-align:left;padding:8px;">Movie</th>'
        '<th style="text-align:left;padding:8px;">Timings</th></tr>'
        + "".join(html_rows)
        + '</table></body></html>'
    )

    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": sender,
                "to": [to],
                "subject": subject,
                "text": "\n".join(plain_lines),
                "html": html,
            },
            timeout=15,
        )
        if response.status_code not in (200, 201):
            print(f"  ❌ Resend {response.status_code}: {response.text}")
            return
        print(f"  ✅ ONE theatre email sent to {to}")
    except requests.RequestException as exc:
        print(f"  ❌ Email failed: {exc}")


if checker.CONFIG["mode"] in ("theatre", "cinema"):
    configured_url = (
        os.getenv("BMS_URL", "").strip()
        or os.getenv("BMS_THEATRE_URL", "").strip()
        or os.getenv("BMS_THEATRE", "").strip()
    )
    cinema = parse_cinema_url(configured_url)
    if not cinema:
        raise SystemExit(
            "❌ Theatre mode requires BMS_URL to be a canonical BookMyShow cinema URL."
        )

    checker.CONFIG["theatre"] = cinema["name"]
    checker.CONFIG["movie"] = "ANY"
    checker.CONFIG["region"] = (
        "bengaluru"
        if cinema["region_slug"] in ("bengaluru", "bangalore")
        else cinema["region_slug"]
    )
    if not checker.CONFIG["dates"] and cinema["date_code"]:
        checker.CONFIG["dates"] = cinema["date_code"]

    def discover_theatre_from_url(region_slug, theatre_filter):
        print(f"  🔗 Using BMS cinema URL directly: {configured_url}")
        return cinema

    checker.discover_theatre_page = discover_theatre_from_url

    def discover_events_from_url(theatre_base_url, date_code):
        return discover_event_codes(theatre_base_url, date_code)

    checker.discover_event_codes_from_theatre_page = discover_events_from_url

    original_filter = checker.filter_shows
    requested_formats = normalize_formats(os.getenv("BMS_FORMAT", ""))

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
            if requested_formats:
                attrs = (show.screen_attr or "").strip().lower()
                if not any(
                    alias in attrs
                    for fmt in requested_formats
                    for alias in SUPPORTED_FORMATS[fmt]
                ):
                    continue
            result.append(show)
        return result

    checker.filter_shows = filter_shows_by_url

    # Collect every movie result. Do not send an email from run_movie().
    theatre_notifications: list[dict] = []

    def collect_email(subject, changes, shows, movie_info):
        if not changes:
            return
        theatre_notifications.append(
            {
                "movie": movie_info.get("name", "Unknown Movie"),
                "shows": list(shows),
            }
        )

    checker.send_email = collect_email
    original_main_theatre = checker.main_theatre_mode

    def theatre_main_once():
        old_state = checker.load_state()
        old_movies = old_state.get("movies", {}) if isinstance(old_state, dict) else {}
        new_movies, any_ok = original_main_theatre(old_movies)

        if not any_ok:
            print("\n  ❌ No usable movie/showtime data was produced.")
            return

        checker.save_state({"version": 3, "movies": new_movies})
        if not theatre_notifications:
            print("\n  ✅ No theatre changes — no email sent.")
            return

        date_text = ", ".join(checker.CONFIG["dates"].split(",")) if checker.CONFIG["dates"] else "requested date"
        print(f"\n  📧 Sending ONE theatre email for {len(theatre_notifications)} movie result(s)")
        send_theatre_email(cinema["name"], date_text, theatre_notifications)
        print(f"\n✅ Done. Checked {len(new_movies)} movie(s) in one theatre search.")

    checker.main = theatre_main_once


if __name__ == "__main__":
    checker.main()
