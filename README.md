# sports-tickets

Tracks Gametime ticket listings for sports events over time, so we can learn
when prices bottom out per seat tier and spot underpriced listings. Currently
configured for Seattle Kraken games; any Gametime performer works via
`PERFORMER_SLUG`.

A GitHub Action wakes every 15 minutes; per event, the collector decides
from the database whether a snapshot is due. Snapshots follow a grid
anchored at each game's start time (start minus k * interval), so one
snapshot in every tier lands exactly at time-of-game and day-over-day
points share the same time of day:

| Time until game       | Snapshot interval |
|-----------------------|-------------------|
| schedule app. to 7d   | daily             |
| 7 days to 24 hours    | every 6 hours     |
| 24 hours to 6 hours   | hourly            |
| 6 hours to T+30 min   | every 15 minutes  |

Each run also refreshes the schedule, upserts `events`, records a daily
min-price row per game (`event_stats`), and for due home games records every
individual listing (`listing_snapshots`): section, row, quantities, pre-fee
and all-in price, Gametime's `deal_score`, and the implied fair value
`V = price / score`. Due-ness is computed from each event's last snapshot in
the database, so cron jitter delays a snapshot slightly but never skips or
doubles one.

## One-time setup (manual steps)

1. **Neon** (free Postgres): create a project at neon.tech, copy the
   connection string (`postgres://...`).
2. **GitHub**: create a private repo, push this folder to it.
3. In the repo: Settings → Secrets and variables → Actions → New repository
   secret, name `DATABASE_URL`, value = the Neon connection string.
4. Actions tab → collect-snapshots → Run workflow (first manual run creates
   the schema and verifies everything).

## Config

Env vars read by `collector.py` (set in `collect.yml` if you want to change
them): `PERFORMER_SLUG` (default `nhlsea`), `HOME_ONLY` (default `true`),
`HORIZON_DAYS` (default `30`).

## Local dry run (no database)

```bash
python3 collector.py --dry-run
```

## Notes

- `deal_score = all-in price / V`, where V is Gametime's modeled fair value
  for that seat at that event. Low score = cheap relative to model.
  We store both; a real buy signal is a low score with stable V.
- Volume: roughly 5-8k listing rows/day in-season, ~1M rows/season. Fits
  Neon's 0.5 GB free tier for a season; prune old preseason snapshots if it
  gets tight.
- The mobile API is unofficial. If it changes or blocks, runs fail loudly
  (GitHub emails on workflow failure); fallback would be a headless browser.
- The workflow makes a tiny "keepalive" commit on the 1st of each month so
  GitHub doesn't auto-disable the cron after 60 days of repo inactivity.
