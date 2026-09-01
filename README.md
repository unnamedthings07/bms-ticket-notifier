# BMS Ticket Notifier

Automatically monitors BookMyShow for ticket availability and sends an email alert when something changes.

Runs every 30 minutes via GitHub Actions. Scheduled runs can be a little late because GitHub Actions cron schedules are not exact.

## Modes

### Movie mode — existing behavior

Monitor one or more specific BookMyShow movie URLs and optionally filter by theatre, date, time and format.

Set:

```text
BMS_MODE=movie
BMS_URLS=<one BMS movie URL per line>
BMS_THEATRE=PVR
```

`BMS_URLS` takes priority over the legacy `BMS_URL` variable.

### Theatre mode — any movie

You can now monitor a theatre without specifying a movie.

Example:

```text
BMS_MODE=theatre
BMS_REGION=bengaluru
BMS_THEATRE=PVR Superplex
BMS_MOVIE=ANY
```

The checker finds the matching cinema on BookMyShow, discovers the movie/event codes currently listed there, and then uses the existing showtime API to check those movies. New movies appearing at the theatre are automatically picked up on the next run.

`BMS_MOVIE=ANY` (or `*`) means every movie. You can instead enter a movie-name substring to limit theatre mode to matching movies.

## Configuration

Go to **Settings → Secrets and variables → Actions → Variables**.

| Variable | Description | Example |
|---|---|---|
| `BMS_MODE` | `movie` for the original URL-based mode or `theatre` for theatre-only monitoring | `theatre` |
| `BMS_REGION` | BookMyShow city/region slug used in theatre mode | `bengaluru` |
| `BMS_MOVIE` | Movie filter in theatre mode. Use `ANY` for all movies | `ANY` |
| `BMS_URLS` | One BookMyShow movie URL per line for movie mode | `https://in.bookmyshow.com/...` |
| `BMS_URL` | Legacy single movie URL | `https://in.bookmyshow.com/...` |
| `BMS_DATES` | Dates to monitor, `YYYYMMDD` comma-separated. Empty in theatre mode checks today | `20260901,20260902` |
| `BMS_THEATRE` | Theatre-name substring filter | `PVR Superplex` |
| `BMS_TIME` | `morning`, `afternoon`, `evening`, `night`, comma-separated | `evening,night` |
| `BMS_FORMAT` | `imax`, `4dx`, `dolby cinema`, `3d`, comma-separated | `imax,3d` |

### Theatre mode examples

Monitor every movie at PVR Superplex:

```text
BMS_MODE=theatre
BMS_REGION=bengaluru
BMS_THEATRE=PVR Superplex
BMS_MOVIE=ANY
```

Monitor only IMAX shows at that theatre:

```text
BMS_MODE=theatre
BMS_REGION=bengaluru
BMS_THEATRE=PVR Superplex
BMS_MOVIE=ANY
BMS_FORMAT=imax
```

Monitor only one movie at the theatre:

```text
BMS_MODE=theatre
BMS_REGION=bengaluru
BMS_THEATRE=PVR Superplex
BMS_MOVIE=Hanuman Ansh
```

## Filters

`BMS_THEATRE` is a case-insensitive substring match, so `PVR Superplex` can match the full BookMyShow name `PVR: Superplex Forum Mall, Kanakapura Road`.

Time periods:

- `morning` — 06:00–12:00
- `afternoon` — 12:00–16:00
- `evening` — 16:00–19:00
- `night` — 19:00–24:00

Formats supported by the format wrapper:

- `imax`
- `4dx`
- `dolby cinema`
- `3d`

## Notifications

You receive an email when:

- A new showtime is added
- A date opens for booking
- A previously sold-out category becomes available

Emails show the current filtered showtimes grouped by theatre.

## Setup

### 1. Fork this repository

### 2. Set GitHub Secrets

Go to **Settings → Secrets and variables → Actions** and add:

| Secret | Description |
|---|---|
| `RESEND_API_KEY` | API key from Resend |
| `RESEND_FROM_EMAIL` | Email address to send notifications. `onboarding@resend.dev` can be used for testing |
| `RESEND_TO_EMAIL` | Email address to receive notifications |

### 3. Set GitHub Variables

Choose either movie mode or theatre mode using the examples above.

### 4. Trigger the workflow

Go to **Actions → BMS Ticket Checker → Run workflow**, or wait for the automatic 30-minute schedule.

The workflow stores the last state in `bms_state.json` so only detected changes trigger emails.
