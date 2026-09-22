# sports-tickets — project context for Claude

Personal project: track Gametime ticket listings over time to learn when
prices bottom out per seat tier, inform Michael's own purchases, and
explore arbitrage/alert ideas. Not work; never store anything in the work
Postgres warehouse.

**Read [STATUS.md](STATUS.md) first** — current state, findings so far, and
open questions. **[DECISIONS.md](DECISIONS.md)** logs why things are the way
they are. Update both at the end of any session that changes them
(WorkOS-style session wrap).

## Mechanics in brief

- `collector.py` snapshots Gametime's unofficial mobile API
  (`mobile.gametime.co/v1`) into Neon Postgres (project
  `mute-brook-17443841`, us-east-2). Schema in `schema.sql`, applied
  idempotently by the collector.
- Runs via GitHub Actions (`.github/workflows/collect.yml`, public repo
  `michaelmorningstar17/sports-tickets`) every 15 min. GitHub's own cron is
  unreliable; the reliable trigger is a Neon schedule trigger → `src/pinger.ts`
  function → `workflow_dispatch` (see neon.ts).
- Snapshot cadence is a grid anchored at each game's start time; per-event
  due-ness computed from the DB (see CADENCE in collector.py).
- Secrets: `.env.local` (gitignored) holds DATABASE_URL and
  GH_WORKFLOW_TOKEN; the GitHub secret DATABASE_URL powers CI runs.
- Local run: `set -a && source .env.local && set +a && .venv/bin/python collector.py`
  (`--dry-run` for grid status without DB).

## Key data facts

- `deal_score = all-in price / V`, where V is Gametime's modeled fair value
  for that seat (derive V as `price_total_cents / deal_score`; stored as
  `value_cents`). Low score = cheap for what it is.
- Filter junk listings with `deal_score > 1.5` (broker placeholders, e.g.
  $1,886 across cleared tiers).
- `section_group` is the venue's own tier name (Climate Pledge: Lower /
  Loge / Upper / *Ice); names differ per venue.
- `lots` = purchasable quantities ("2/4/6"); `seats` usually masked by
  Gametime ('*'), captured when exposed.
- We observe asks, not transactions; disappearance ≈ sold or delisted.
- Prices we store are the API's BASE price. Gametime's apps apply a
  per-user display layer (up to ~9.5%, see `promofee` in listing
  signatures): Michael's app showed $24 where the API said $22 (2026-09-22).
  Trends are consistent on our basis, but consumer-facing quotes and any
  buy-leg profit math should assume display price ≈ base +0-10%.
