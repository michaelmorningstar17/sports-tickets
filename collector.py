#!/usr/bin/env python3
"""Snapshot Gametime ticket listings for tracked games into Postgres.

Discovers the schedule from Gametime's mobile API and snapshots
listing-level data (section, row, price, deal score, implied value V)
on a cadence that tightens as each game approaches. The workflow runs
every 15 minutes; per-event due-ness is decided from the database
(time since that event's last snapshot), so GitHub cron jitter can
delay a snapshot slightly but never skip or double one.

Snapshots follow a grid anchored at each game's start time (start minus
k * interval), so one snapshot of every tier lands exactly at time-of-game:

    schedule appearance to T-7d    daily
    T-7d to T-24h                  every 6 hours
    T-24h to T-6h                  hourly
    T-6h to T+30min                every 15 minutes
    after T+30min                  stop (sales cut off shortly after start)

Usage:
    DATABASE_URL=postgres://... python3 collector.py
    python3 collector.py --dry-run     # no database, no writes; prints the
                                       # schedule and per-event grid status

Config (env vars):
    DATABASE_URL     Postgres connection string (required unless --dry-run)
    PERFORMER_SLUG   Gametime performer slug        (default: nhlsea)
    HOME_ONLY        snapshot listings for home games only (default: true)
"""

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://mobile.gametime.co/v1"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

PERFORMER_SLUG = os.environ.get("PERFORMER_SLUG", "nhlsea")
HOME_ONLY = os.environ.get("HOME_ONLY", "true").lower() != "false"
# Also track every NHL game (any team, any arena) starting within this many
# hours — endgame research across the league. 0 disables.
EXTRA_NHL_HOURS = float(os.environ.get("EXTRA_NHL_HOURS", "0"))
REQUEST_PAUSE_SECS = 1.5

# Snapshot grid, anchored at game start so one snapshot of every tier lands
# exactly at time-of-game: (game is at most this far away, grid interval).
# Intervals nest (15m | 1h | 6h | 24h), so tier transitions share grid points.
CADENCE = [
    (timedelta(hours=6), timedelta(minutes=15)),
    (timedelta(hours=24), timedelta(hours=1)),
    (timedelta(days=7), timedelta(hours=6)),
    (None, timedelta(hours=24)),   # daily from schedule appearance to T-7d
]
POST_START_GRACE = timedelta(minutes=30)
STATS_INTERVAL = timedelta(hours=23)   # schedule-wide min-price curve: daily


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def parse_events(wrappers, always_track=False):
    events = []
    for wrapper in wrappers:
        ev, venue = wrapper["event"], wrapper.get("venue") or {}
        # Season-ticket packages and TBD placeholders have no usable market.
        if ev.get("tbd") or ev.get("date_tbd") or "Season Tickets" in ev.get("name", ""):
            continue
        start = datetime.fromisoformat(ev["datetime_utc"].replace("Z", ""))
        events.append({
            "always_track": always_track,
            "event_id": ev["id"],
            "name": ev["name"],
            "datetime_local": ev["datetime_local"],
            "datetime_utc": ev["datetime_utc"],
            "start": start.replace(tzinfo=timezone.utc),
            "venue": venue.get("name"),
            "city": venue.get("city"),
            "state": venue.get("state"),
            "is_home": venue.get("city") == "Seattle",
            "category": ev.get("category"),
            "min_price_prefee": (ev.get("min_price") or {}).get("prefee"),
            "min_price_total": (ev.get("min_price") or {}).get("total"),
        })
    return events


def fetch_schedule(now):
    data = get_json(f"{API}/events?performer_slug={PERFORMER_SLUG}&per_page=100")
    events = parse_events(data["events"])
    if EXTRA_NHL_HOURS > 0:
        seen = {e["event_id"] for e in events}
        # Sorted by date ascending; first page covers the next several days.
        extra = get_json(f"{API}/events?category=nhl&per_page=100")
        window = timedelta(hours=EXTRA_NHL_HOURS)
        events += [
            e for e in parse_events(extra["events"], always_track=True)
            if e["event_id"] not in seen
            and -POST_START_GRACE <= e["start"] - now <= window
        ]
    return events


def fetch_listings(event_id):
    data = get_json(f"{API}/listings?event_id={event_id}")
    rows = []
    for l in data.get("listings", []):
        total = l["price"]["total"]
        score = l.get("score") or 0
        rows.append({
            "listing_id": l["id"],
            "section_group": l.get("section_group"),
            "section": l.get("section"),
            "row": l.get("row"),
            "lots": "/".join(str(x) for x in l.get("lots", [])),
            "price_prefee_cents": l["price"]["prefee"],
            "price_total_cents": total,
            "deal_score": score if score > 0 else None,
            "value_cents": round(total / score) if score > 0 else None,
            "source": l.get("source"),
            "delivery_type": l.get("delivery_type"),
            "seats": "/".join(l.get("seats") or []) or None,
        })
    return rows


def grid_interval(time_until_start):
    """Grid interval for a game this far away, or None if out of scope."""
    if time_until_start < -POST_START_GRACE:
        return None
    for horizon, interval in CADENCE:
        if horizon is None or time_until_start <= horizon:
            return interval
    return None


def last_grid_point(start, now):
    """Most recent scheduled snapshot time (start - k*interval) at or
    before now, or None if the game is out of scope."""
    import math
    interval = grid_interval(start - now)
    if interval is None:
        return None
    k = math.ceil((start - now) / interval)
    return start - k * interval


def listings_due(event, now, last_snapshot):
    if HOME_ONLY and not event["is_home"] and not event["always_track"]:
        return False
    grid_point = last_grid_point(event["start"], now)
    if grid_point is None:
        return False
    last = last_snapshot.get(event["event_id"])
    return last is None or last < grid_point


def clean_dsn(raw):
    """Rebuild the connection URL from the secret, neutralizing paste
    artifacts: quotes, an env-file prefix, stray whitespace/newlines,
    and malformed query parameters."""
    import re
    m = re.search(r"postgres(?:ql)?://[^\s'\"]+", raw)
    if not m:
        raise ValueError("DATABASE_URL secret does not contain a postgres:// URL")
    url = m.group(0)
    base, _, query = url.partition("?")
    params = [p for p in query.split("&") if p.count("=") == 1 and p.strip()]
    return base + ("?" + "&".join(params) if params else "")


def load_last_times(conn):
    last_snapshot = dict(conn.execute(
        "SELECT event_id, max(captured_at) FROM listing_snapshots GROUP BY 1"
    ).fetchall())
    last_stats = dict(conn.execute(
        "SELECT event_id, max(captured_at) FROM event_stats GROUP BY 1"
    ).fetchall())
    return last_snapshot, last_stats


def write_db(conn, now, events, listings_by_event, stats_due_ids):
    with conn.cursor() as cur:
        cur.executemany(
            """INSERT INTO events (event_id, name, datetime_local,
                   datetime_utc, venue, city, state, is_home,
                   category, last_seen_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (event_id) DO UPDATE SET
                   name = EXCLUDED.name,
                   datetime_local = EXCLUDED.datetime_local,
                   datetime_utc = EXCLUDED.datetime_utc,
                   last_seen_at = EXCLUDED.last_seen_at""",
            [(e["event_id"], e["name"], e["datetime_local"],
              e["datetime_utc"], e["venue"], e["city"], e["state"],
              e["is_home"], e["category"], now) for e in events])
        stats_rows = [(e["event_id"], now, e["min_price_prefee"],
                       e["min_price_total"])
                      for e in events if e["event_id"] in stats_due_ids]
        if stats_rows:
            cur.executemany(
                "INSERT INTO event_stats VALUES (%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING", stats_rows)
        listing_rows = [
            (event_id, now, l["listing_id"], l["section_group"],
             l["section"], l["row"], l["lots"], l["price_prefee_cents"],
             l["price_total_cents"], l["deal_score"], l["value_cents"],
             l["source"], l["delivery_type"], l["seats"])
            for event_id, listings in listings_by_event.items()
            for l in listings]
        if listing_rows:
            cur.executemany(
                """INSERT INTO listing_snapshots
                   (event_id, captured_at, listing_id, section_group,
                    section, row, lots, price_prefee_cents,
                    price_total_cents, deal_score, value_cents, source,
                    delivery_type, seats)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT DO NOTHING""", listing_rows)
    conn.commit()


def apply_schema(conn):
    schema = (Path(__file__).parent / "schema.sql").read_text()
    with conn.cursor() as cur:
        for statement in schema.split(";"):
            if statement.strip():
                cur.execute(statement)
    conn.commit()


def main():
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc).replace(microsecond=0)
    events = fetch_schedule(now)

    if dry_run:
        for e in events:
            if HOME_ONLY and not e["is_home"] and not e["always_track"]:
                continue
            gp = last_grid_point(e["start"], now)
            iv = grid_interval(e["start"] - now)
            print(f"  {e['datetime_local'][:16]} {e['name'][:45]:45s} "
                  f"interval {iv}, last grid point "
                  f"{gp:%Y-%m-%d %H:%M}Z" if gp else
                  f"  {e['datetime_local'][:16]} {e['name'][:45]:45s} out of scope")
        return

    import psycopg
    conn = psycopg.connect(clean_dsn(os.environ["DATABASE_URL"]))
    apply_schema(conn)
    last_snapshot, last_stats = load_last_times(conn)

    targets = [e for e in events if listings_due(e, now, last_snapshot)]
    stats_due_ids = {
        e["event_id"] for e in events
        if e["event_id"] in {t["event_id"] for t in targets}
        or e["event_id"] not in last_stats
        or now - last_stats[e["event_id"]] >= STATS_INTERVAL
    }

    listings_by_event, failures = {}, []
    for e in targets:
        time.sleep(REQUEST_PAUSE_SECS)
        try:
            listings_by_event[e["event_id"]] = fetch_listings(e["event_id"])
        except Exception as exc:  # keep going; one bad event shouldn't kill the run
            failures.append((e["event_id"], e["name"], str(exc)))

    n_listings = sum(len(v) for v in listings_by_event.values())
    print(f"schedule: {len(events)} events; due: {len(targets)}; "
          f"captured: {len(listings_by_event)} events, {n_listings} listings; "
          f"stats rows: {len(stats_due_ids)}")
    for event_id, name, err in failures:
        print(f"FAILED {event_id} ({name}): {err}", file=sys.stderr)

    write_db(conn, now, events, listings_by_event, stats_due_ids)
    conn.close()
    print("database write complete")

    # Fail the run only if every listings fetch failed (endpoint change/block).
    if failures and not listings_by_event:
        sys.exit(1)


if __name__ == "__main__":
    main()
