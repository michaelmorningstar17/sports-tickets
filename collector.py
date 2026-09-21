#!/usr/bin/env python3
"""Snapshot Gametime ticket listings for tracked games into Postgres.

Discovers the schedule from Gametime's mobile API and snapshots
listing-level data (section, row, price, deal score, implied value V)
on a cadence that tightens as each game approaches. The workflow runs
every 15 minutes; per-event due-ness is decided from the database
(time since that event's last snapshot), so GitHub cron jitter can
delay a snapshot slightly but never skip or double one.

Cadence (time until game start -> snapshot interval):
    more than 7 days        daily
    7 days to 24 hours      twice daily
    24 hours to 4 hours     hourly
    4 hours to T+30 min     every run (~15 min)
    after T+30 min          stop (sales cut off shortly after start)

Usage:
    DATABASE_URL=postgres://... python3 collector.py
    python3 collector.py --dry-run     # no database; collects everything
                                       # in horizon and prints a summary

Config (env vars):
    DATABASE_URL     Postgres connection string (required unless --dry-run)
    PERFORMER_SLUG   Gametime performer slug        (default: nhlsea)
    HOME_ONLY        snapshot listings for home games only (default: true)
    HORIZON_DAYS     listing-snapshot window in days       (default: 30)
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
HORIZON_DAYS = int(os.environ.get("HORIZON_DAYS", "30"))
REQUEST_PAUSE_SECS = 1.5

# (game is at most this far away, snapshot interval). Intervals sit a few
# minutes under the nominal tier so a jittery cron run just past the
# boundary still collects instead of waiting a whole extra cycle.
CADENCE = [
    (timedelta(hours=4), timedelta(minutes=13)),
    (timedelta(hours=24), timedelta(minutes=55)),
    (timedelta(days=7), timedelta(hours=11, minutes=30)),
    (timedelta(days=HORIZON_DAYS), timedelta(hours=23)),
]
POST_START_GRACE = timedelta(minutes=30)
STATS_INTERVAL = timedelta(hours=23)   # schedule-wide min-price curve: daily


def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def fetch_schedule():
    data = get_json(f"{API}/events?performer_slug={PERFORMER_SLUG}&per_page=100")
    events = []
    for wrapper in data["events"]:
        ev, venue = wrapper["event"], wrapper.get("venue") or {}
        # Season-ticket packages and TBD placeholders have no usable market.
        if ev.get("tbd") or ev.get("date_tbd") or "Season Tickets" in ev.get("name", ""):
            continue
        start = datetime.fromisoformat(ev["datetime_utc"].replace("Z", ""))
        events.append({
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
        })
    return rows


def required_interval(time_until_start):
    """Snapshot interval for a game this far away, or None if out of scope."""
    if time_until_start < -POST_START_GRACE:
        return None
    for horizon, interval in CADENCE:
        if time_until_start <= horizon:
            return interval
    return None


def listings_due(event, now, last_snapshot):
    if HOME_ONLY and not event["is_home"]:
        return False
    interval = required_interval(event["start"] - now)
    if interval is None:
        return False
    last = last_snapshot.get(event["event_id"])
    return last is None or now - last >= interval


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
        for e in events:
            cur.execute(
                """INSERT INTO events (event_id, name, datetime_local,
                       datetime_utc, venue, city, state, is_home,
                       category, last_seen_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (event_id) DO UPDATE SET
                       name = EXCLUDED.name,
                       datetime_local = EXCLUDED.datetime_local,
                       datetime_utc = EXCLUDED.datetime_utc,
                       last_seen_at = EXCLUDED.last_seen_at""",
                (e["event_id"], e["name"], e["datetime_local"],
                 e["datetime_utc"], e["venue"], e["city"], e["state"],
                 e["is_home"], e["category"], now))
            if e["event_id"] in stats_due_ids:
                cur.execute(
                    """INSERT INTO event_stats VALUES (%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (e["event_id"], now, e["min_price_prefee"],
                     e["min_price_total"]))
        for event_id, listings in listings_by_event.items():
            for l in listings:
                cur.execute(
                    """INSERT INTO listing_snapshots VALUES
                       (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    (event_id, now, l["listing_id"], l["section_group"],
                     l["section"], l["row"], l["lots"],
                     l["price_prefee_cents"], l["price_total_cents"],
                     l["deal_score"], l["value_cents"], l["source"],
                     l["delivery_type"]))
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
    events = fetch_schedule()

    if dry_run:
        conn = None
        last_snapshot, last_stats = {}, {}
    else:
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

    if dry_run:
        for e in targets:
            ls = listings_by_event.get(e["event_id"], [])
            floor = min((l["price_total_cents"] for l in ls), default=0)
            interval = required_interval(e["start"] - now)
            print(f"  {e['datetime_local'][:16]} {e['name'][:45]:45s} "
                  f"{len(ls):4d} listings, floor ${floor/100:.0f}, "
                  f"interval {interval}")
    else:
        write_db(conn, now, events, listings_by_event, stats_due_ids)
        conn.close()
        print("database write complete")

    # Fail the run only if every listings fetch failed (endpoint change/block).
    if failures and not listings_by_event:
        sys.exit(1)


if __name__ == "__main__":
    main()
