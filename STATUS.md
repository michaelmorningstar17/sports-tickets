# STATUS — sports-tickets

_Last updated: 2026-09-22 (evening)_

## Current state

- Pipeline fully operational and unattended: Neon trigger → pinger →
  GitHub Actions → collector → Neon Postgres, every 15 minutes.
  Since 2026-09-21 23:45 PT: 67/67 successful runs, ~64 expected fires.
- Database: ~72k listing rows, 26 MB of Neon's 500 MB free tier.
- Coverage: all Kraken home games through Apr 2027 (grid cadence: daily →
  6-hourly at T-7d → hourly at T-24h → 15-min at T-6h → stop T+30m, all
  anchored to game start time). Plus every NHL game league-wide entering
  8h before start (`EXTRA_NHL_HOURS: "8"` in collect.yml) — temporary
  calibration window; revisit after 2026-09-24 (storage cost ~70k rows/day
  while preseason slate is full).
- Tonight (Tue 9/22): 10 preseason games auto-tracked, first full-resolution
  endgame curves with no manual bridge.

## Findings so far (from Mon 9/21 games, n=7, preseason)

- Three demand regimes, readable in advance from listing depth + median:
  - Soft (deep supply, modest median): floor sags into and past puck drop;
    minimum typically T-40m..T+20m. 5 of 7 games.
  - Hot (thin supply, high median — Chicago): market clears; brief dip
    ~T-25m is the only window; post-start only junk remains.
  - Rising (Winnipeg): cheap upper seats absorbed early; floor rose $21→$31.
- Tier matters: game-level floor ≈ upper-deck floor. Lower-bowl floors
  tightened earlier in 2 of 3 well-covered games (Dallas Plaza $25→$45 by
  T-40m even while the game floor fell).
- Group buys (5-6 together, lower bowl): scarcity is earlier and sharper.
  Dallas: options 15→3 while cheapest fell $59→$33; safe sweet spot was
  ~T-60m..T-25m. Chicago: zero group options existed at any observed time.
- Gametime deal_score reverse-engineered: score = price / V (V = their
  per-seat fair-value model). Buy signal concept: low score with stable V;
  falling V = market deflating, wait.

## Thursday 9/24 plan (Kraken vs Flames preseason, Michael wants 5 lower-bowl)

- Profile as of 9/22: deep soft market — 46 lower-bowl listings seat 5-6,
  cheapest $17/seat (falling). Advice: do NOT buy early; check T-2h..T-1h
  Thursday (4:40–5:40 PM PT).
- Tripwires to buy immediately: 5-6-capable count < 20, or cheapest group
  price rises across two consecutive snapshots.

## Open questions / next steps

- Validate regime rules on tonight's 10 full-resolution curves.
- After Thursday: decide EXTRA_NHL_HOURS (keep 8 / shrink / 0).
- Build junk-filtered analysis views (`tier_floor_by_hours_out`) once a few
  regular-season weeks accumulate.
- Paper-trade arbitrage rule (buy ≥40-50% below row-band comps to clear
  ~35% round-trip fees) before any real buying-to-resell.
- Pairing arbitrage (adjacent singles): parked — seats usually masked;
  detector-only via same-section-row lots=1 pairs.
- Longer term: alert service (rung 1), affiliate links, cross-marketplace
  (TickPick shows seat numbers) — see conversation of 2026-09-21/22.
