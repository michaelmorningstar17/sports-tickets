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

## Findings from Kraken-Flames 9/24 endgame (soft market; n 87→57)

- Michael's 6x Lower 22 rJ @ $32 VALIDATED: quality-row 6-blocks never beat
  $31 after purchase (usually $37-44). Group-quality never goes on sale.
- Get-in floor CONVERGES across group sizes late: even 6 could enter for
  $19-20 (back rows) at T-60..T-45m. Quality-row premium DIVERGES by size.
- Pairs rule v1 (soft games): watch from T-3h, buy on a quality-row wave
  (a $19 quality pair appeared T-30m), fallback $19-22 back row T-45m.
  Regular-season recalibration pending.

## Findings from Tue 9/22 games (n=10, FULL 15-min resolution)

- Floor minimum typically lands T-4h..T-1.5h, NOT at puck drop (only 1/10
  bottomed post-start). Monday's "min at T+20m" was sparse-sampling
  artifact. After the bottom, floors drift UP as inventory absorbs.
- Listing counts roughly halve over the final 8h in every game.
- Groups of 5 in lower tiers: options collapse or reprice up in the final
  1-3h in 7/9 games (MSG: 8 options T-6h → 0 by T-1h). Only the softest
  markets let groups wait. GROUP BUY WINDOW = T-6h..T-2h.
- Quality-at-budget arrives in perishable waves (Kraken sec 21 row J $28
  appeared overnight 9/21→9/22, bought by Tue noon). Hunt waves, don't
  time a fixed hour. Gametime's V underweights row depth vs human prefs.

## Findings from Mon 9/21 games (n=7, sparse endgame coverage on 4 East games)

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

## Thursday 9/24 plan (Kraken vs Flames preseason, Michael wants 5 seats,
## TRUE Lower only — Loge excluded per Michael 9/22; Loge runs ~25-30%
## below comparable Lower at every snapshot, the standing value play)

- Lower-only profile as of 9/22 eve: floor pinned at $20-22 for 26h (only
  the median compresses, $52→$41); heavy churn (cheap Lower sells, doesn't
  sit). ~26 Lower listings seat 5-6; best: Lower 24 row W $22/seat
  (lots 3-8), Lower 11 row X $23 (6), Lower 17 row Z $25.
- REVISED after Tue full-resolution data: buy Thursday AFTERNOON
  (~12:40–4:40 PM PT, T-6h..T-2h) — group options collapse in the final
  1-3h across the league, and floors bottom T-4h..T-1.5h anyway. Watch for
  a perishable good-row wave (≤ row M under ~$30) during the burst window;
  fallback Lower 24 row W ($22-24, lots 3-8) if no wave appears by ~4:40 PM.
- Tripwires to buy immediately (any day): Lower 5-6-capable count < 12, or
  Lower group floor rises across two consecutive snapshots.

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
