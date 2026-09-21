-- Kraken ticket price tracker schema (Postgres)
-- Applied automatically by collector.py on each run (idempotent).

CREATE TABLE IF NOT EXISTS events (
    event_id        TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    datetime_local  TIMESTAMP NOT NULL,
    datetime_utc    TIMESTAMPTZ NOT NULL,
    venue           TEXT,
    city            TEXT,
    state           TEXT,
    is_home         BOOLEAN NOT NULL,
    category        TEXT,
    first_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at    TIMESTAMPTZ NOT NULL
);

-- One row per event per collector run: the cheap long-horizon price curve
-- for every game on the schedule, even ones we don't snapshot listings for.
CREATE TABLE IF NOT EXISTS event_stats (
    event_id           TEXT NOT NULL REFERENCES events(event_id),
    captured_at        TIMESTAMPTZ NOT NULL,
    min_price_prefee   INTEGER,   -- cents
    min_price_total    INTEGER,   -- cents, all-in
    PRIMARY KEY (event_id, captured_at)
);

-- One row per listing per collector run, for events inside the horizon.
CREATE TABLE IF NOT EXISTS listing_snapshots (
    event_id            TEXT NOT NULL REFERENCES events(event_id),
    captured_at         TIMESTAMPTZ NOT NULL,
    listing_id          TEXT NOT NULL,
    section_group       TEXT,      -- tier: Lower / Loge / Upper / *Ice
    section             TEXT,
    row                 TEXT,
    lots                TEXT,      -- purchasable quantities, e.g. '2/4'
    price_prefee_cents  INTEGER NOT NULL,
    price_total_cents   INTEGER NOT NULL,  -- all-in
    deal_score          REAL,              -- Gametime's score = price / V
    value_cents         INTEGER,           -- V, derived: price_total / deal_score
    source              TEXT,
    delivery_type       TEXT,
    PRIMARY KEY (listing_id, captured_at)
);

-- Seat numbers when Gametime exposes them (often masked as '*'), needed to
-- detect adjacent single-seat listings for pairing analysis.
-- NOTE: comments here must not contain semicolons (see apply_schema).
ALTER TABLE listing_snapshots ADD COLUMN IF NOT EXISTS seats TEXT;

CREATE INDEX IF NOT EXISTS idx_listing_snapshots_event
    ON listing_snapshots (event_id, captured_at);
CREATE INDEX IF NOT EXISTS idx_listing_snapshots_tier
    ON listing_snapshots (event_id, section_group, captured_at);
