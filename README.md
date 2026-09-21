# kraken-tickets

Tracks Gametime ticket listings for Seattle Kraken games over time, so we can
learn when prices bottom out per seat tier and spot underpriced listings.

Every 6 hours a GitHub Action:

1. Pulls the full Kraken schedule from Gametime's mobile API and records each
   game's minimum price (`event_stats`), the cheap long-horizon curve.
2. For home games within 30 days, records every individual listing
   (`listing_snapshots`): section, row, quantities, pre-fee and all-in price,
   Gametime's `deal_score`, and the implied fair value `V = price / score`.

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
