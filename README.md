# BMS Ticket Notifier

Automatically monitors [BookMyShow](https://in.bookmyshow.com) for ticket availability and sends you an email alert when something changes.

Runs every 30 minutes via GitHub Actions. Scheduled runs can be a little late because GitHub Actions cron schedules are not exact.

## How It Works

1. Fetches showtimes from the BookMyShow API for one or more movie URLs.
2. Compares results with the previous check (stored in `bms_state.json`).
3. Sends an HTML email via [Resend](https://resend.com) if anything changed (new shows, dates opening, availability updates).
4. Stores each movie's state separately, keyed by its BookMyShow event code.

## Setup

### 1. Fork this repository

### 2. Set GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|--------|-------------|
| `RESEND_API_KEY` | API key from [resend.com](https://resend.com) |
| `RESEND_FROM_EMAIL` | Email address to send notifications. `onboarding@resend.dev` can be used for testing. |
| `RESEND_TO_EMAIL` | Email address to receive notifications |

### 3. Set GitHub Variables

Go to **Settings → Secrets and variables → Actions → Variables**.

| Variable | Description | Example |
|----------|-------------|---------|
| `BMS_URLS` | One BookMyShow movie ticket URL per line. Use this for multiple movies. | See below |
| `BMS_URL` | Single BookMyShow movie URL. Kept for backwards compatibility with the old setup. | `https://in.bookmyshow.com/movies/bengaluru/.../ET00378770` |
| `BMS_DATES` | Dates to monitor (YYYYMMDD, comma-separated). Leave empty to use the date from each URL. | `20260829,20260830` |
| `BMS_THEATRE` | Filter by theatre name (substring match, comma-separated). Leave empty for all theatres. | `Manasa RGB Laser ATMOS: Konanakunte,PVR` |
| `BMS_TIME` | Filter by time period (comma-separated). Leave empty for all showtimes. | `evening,night` |

`BMS_URLS` takes priority over `BMS_URL`. For multiple movies, put each URL on its own line in the **same variable**:

```text
https://in.bookmyshow.com/movies/bengaluru/toxic-a-fairy-tale-for-grown-ups/buytickets/ET00378770/20260829?etCodes=ET00378770&language=kannada&refEventCode=ET00378770
https://in.bookmyshow.com/movies/bengaluru/another-movie/buytickets/ET00123456/20260829
```

The same `BMS_DATES`, `BMS_THEATRE`, and `BMS_TIME` filters apply to every movie in `BMS_URLS`.

**Time periods:** `morning` (06–12), `afternoon` (12–16), `evening` (16–19), `night` (19–24).

### 4. Trigger the workflow

Go to **Actions → BMS Ticket Checker** and click **Run workflow**, or wait for the automatic 30-minute schedule.

You will get a separate email for each movie that has a detected change.

## Local Usage

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --frozen

export BMS_URLS='https://in.bookmyshow.com/movies/bengaluru/movie-1/buytickets/ET00111111/20260829
https://in.bookmyshow.com/movies/bengaluru/movie-2/buytickets/ET00222222/20260829'
export BMS_DATES='20260829,20260830'
export BMS_THEATRE='Manasa RGB Laser ATMOS: Konanakunte'
export BMS_TIME='evening,night'
export RESEND_API_KEY='re_...'
export RESEND_FROM_EMAIL='onboarding@resend.dev'
export RESEND_TO_EMAIL='you@example.com'

uv run main.py
```

`BMS_URL` can still be used instead of `BMS_URLS` when monitoring only one movie.

## Notifications

You'll receive an email when:
- A new showtime is added
- A date opens for booking
- Seat availability changes (for example, sold out → available)

Emails show a summary of what changed and the current status of monitored shows, grouped by theatre.
