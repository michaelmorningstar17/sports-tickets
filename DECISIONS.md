# DECISIONS — sports-tickets

Newest first. One line of context, one of why.

- **2026-09-22: WorkOS-style state files in-repo** (CLAUDE.md / STATUS.md /
  DECISIONS.md). Why: a new Claude session had mechanics from code but no
  strategy/findings; repo is the right home since sessions start in-folder.
- **2026-09-22: Neon schedule trigger + pinger function as the real scheduler.**
  Why: GitHub's cron fired 2 of 28 expected times; workflow_dispatch runs
  execute in seconds. GitHub cron left on as free redundancy. Token is a
  fine-grained PAT (Actions read/write, this repo only) in function env.
- **2026-09-22: League-wide endgame window (EXTRA_NHL_HOURS=8), temporary.**
  Why: needed many endgame curves before Thursday's purchase; endgame-only
  to cap storage. Revisit after 9/24.
- **2026-09-22: capture `seats` column.** Why: pairing-arbitrage analysis
  needs adjacency; usually masked but free to store when exposed.
- **2026-09-21: snapshot grid anchored at game start** (daily / 6h / 1h / 15m
  tiers; one snapshot of every tier lands exactly at time-of-game). Why:
  cross-game comparability in time-until-game coordinates; jitter-proof
  state-driven due-ness; Michael's spec.
- **2026-09-21: full-schedule daily coverage (no 30-day horizon).** Why:
  "can't recover what we don't collect"; storage projection acceptable.
- **2026-09-21: repo made public.** Why: unlimited free Actions minutes for
  a 15-min cron; no secrets in code.
- **2026-09-21: batched DB writes (executemany).** Why: row-by-row inserts
  to remote Postgres timed out (~9k rows > 5 min → seconds).
- **2026-09-21: Neon (us-east-2, project mute-brook-17443841) + GitHub
  Actions + plain Postgres schema.** Why: free, serverless, no lock-in
  (pg_dump moves it anywhere); cloud collector so laptop sleep can't hole
  the time series. Work warehouse explicitly off-limits.
- **2026-09-21: Gametime unofficial mobile API as source.** Why: unauthenticated
  JSON with listing-level section/row/price/lots/score; website HTML is
  bot-gated; official APIs lack listing granularity. Risk: could break or
  block any time — collector fails loudly, fallback is headless browser.
- **2026-09-21: track Kraken home games at listing level; away games
  event-stats only.** Why: Michael attends in Seattle; storage discipline.
