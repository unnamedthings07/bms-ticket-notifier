"""Theatre-specific BMS monitoring wrapper.

Theatre mode is driven by one canonical BookMyShow cinema URL. The URL supplies
venue code/date, movie event codes are discovered from the cinema page, and
showtime data is checked through BMS's showtime API. Theatre mode sends at most
ONE email per workflow run, grouped by movie name with timings only.
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


def _extract_event_codes(text: str) -> list[str]:
    found = []
    for code in re.findall(r"\bET\d{8,}\b", text or "", re.I):
        code = code.upper()
        if code not in found:
            found.append(code)
    return found


def _fetch_event_codes_via_renderer(url: str) -> list[str]:
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


def _send_theatre_email(theatre_name: str, date_code: str, notifications: list[dict]):
    """Send exactly one compact theatre email: movie name + timings only."""
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

    # Keep stable alphabetical movie ordering and chronological times.
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

    date_display = date_code or "requested date"
    now = datetime.now().strftime("%d %b %Y, %I:%M %p")
    subject = f"BMS Alert: {theatre_name} — {date_display}"

    plain_lines = [
        f"BMS Alert: {theatre_name}",
        f"Date: {date_display}",
        "",
        "Movies / Timings",
        "",
    ]
    html_rows = []
    for movie, times in grouped.items():
        joined = ", ".join(times) if times else "No timings found"
        plain_lines.append(f"{movie} — {joined}")
        html_rows.append(
            f'<tr><td style="padding:10px 8px;border-bottom:1px solid #ddd;">'
            f'<b>{escape(movie)}</b></td>'
            f'<td style="padding:10px 8px;border-bottom:1px solid #ddd;">'
            f'{escape(joined)}</td></tr>'
        )

    html = (
        '<!doctype html><html><body style="font-family:Arial,sans-serif;color:#333;padding:24px;">'
        f'<h2 style="color:#111;margin-bottom:6px;">BMS Alert: {escape(theatre_name)}</h2>'
        f'<p style="color:#666;margin-top:0;">{escape(date_display)} · {escape(now)}</p>'
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
        print(f"  ✅ One theatre email sent to {to}")
    except requests.RequestException as exc:
        print(f"  ❌ Email failed: {exc}")


# ---------------- Theatre mode ------------------------------------------------
if checker.CONFIG["mode"] in ("theatre", "cinema"):
    configured_url = (
        os.getenv("BMS_URL", "").strip()
        or os.getenv("BMS_THEATRE_URL", "").strip()
        or os.getenv("BMS_THEATRE", "").strip()
    )
    cinema = parse_cinema_url(configured_url)

    if cinema:
        # The full URL is authoritative for theatre + date + Bengaluru region.
        if cinema["region_slug"] in ("bengaluru", "bangalore"):
            checker.CONFIG["region"] = "bengaluru"
        elif not checker.CONFIG["region"]:
            checker.CONFIG["region"] = cinema["region_slug"]

        if not checker.CONFIG["dates"] and cinema["date_code"]:
            checker.CONFIG["dates"] = cinema["date_code"]

        # Movie selection is deliberately ignored in theatre mode.
        checker.CONFIG["theatre"] = cinema["name"]
        checker.CONFIG["movie"] = "ANY"

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

        # Filter by exact BMS venue code from the supplied URL.
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

        # Theatre mode is one search / one notification.
        original_send_email = checker.send_email
        theatre_notifications: list[dict] = []

        def collect_email(subject, changes, shows, movie_info):
            theatre_notifications.append(
                {
                    "changes": list(changes),
                    "shows": list(shows),
                    "movie": movie_info.get("name", "Unknown Movie"),
                }
            )

        checker.send_email = collect_email

        def theatre_main_once():
            old_state = checker.load_state()
            old_movies = old_state.get("movies", {}) if isinstance(old_state, dict) else {}

            new_movies, any_ok = checker.main_theatre_mode(old_movies)

            if not any_ok:
                print("\n  ❌ No usable movie/showtime data was produced.")
                return

            checker.save_state({"version": 3, "movies": new_movies})

            if theatre_notifications:
                date_text = ", ".join(checker.CONFIG["dates"].split(",")) if checker.CONFIG["dates"] else "requested date"
                print(
                    f"\n  📧 Sending ONE theatre email "
                    f"for {len(theatre_notifications)} movie result(s)"
                )
                _send_theatre_email(
                    cinema["name"],
                    date_text,
                    theatre_notifications,
                )
            else:
                print("\n  ✅ No theatre changes — no email sent.")

            print(
                f"\n✅ Done. Checked {len(new_movies)} movie(s) "
                "within one theatre search."
            )

        checker.main = theatre_main_once


# Optional format filter for theatre mode. It applies to the selected theatre
# shows and still results in only one aggregated email.
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
