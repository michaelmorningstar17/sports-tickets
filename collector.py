#!/usr/bin/env python3
"""Snapshot Gametime ticket listings for Kraken games into Postgres.

Discovers the schedule from Gametime's mobile API, records a min-price
stat row for every game each run, and full listing-level snapshots
(section, row, price, deal score, implied value V) for games inside
HORIZON_DAYS.

Usage:
    DATABASE_URL=postgres://... python3 collector.py
    python3 collector.py --dry-run     # no database; prints a summary

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
        events.append({
            "event_id": ev["id"],
            "name": ev["name"],
            "datetime_local": ev["datetime_local"],
            "datetime_utc": ev["datetime_utc"],
            "venue": venue.get("name"),
            "city": venue.get("city"),
            "state": venue.get("state"),
            # Home = the performer is listed first in the event name's "X at Y"
            # convention only for the away team; venue city is the robust test.
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


def in_horizon(event, now):
    dt = datetime.fromisoformat(event["datetime_utc"].replace("Z", ""))
    dt = dt.replace(tzinfo=timezone.utc)
    return now <= dt <= now + timedelta(days=HORIZON_DAYS)


def collect(now):
    events = fetch_schedule()
    targets = [e for e in events
               if in_horizon(e, now) and (e["is_home"] or not HOME_ONLY)]
    listings_by_event, failures = {}, []
    for e in targets:
        time.sleep(REQUEST_PAUSE_SECS)
        try:
            listings_by_event[e["event_id"]] = fetch_listings(e["event_id"])
        except Exception as exc:  # keep going; one bad event shouldn't kill the run
            failures.append((e["event_id"], e["name"], str(exc)))
    return events, listings_by_event, failures


def write_db(now, events, listings_by_event):
    import psycopg
    schema = (Path(__file__).parent / "schema.sql").read_text()
    # Tolerate common paste artifacts in the secret: surrounding quotes
    # or a full "DATABASE_URL=..." line copied from an env file.
    dsn = os.environ["DATABASE_URL"].strip()
    if dsn.startswith("DATABASE_URL="):
        dsn = dsn.split("=", 1)[1].strip()
    dsn = dsn.strip("'\"")
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            for statement in schema.split(";"):
                if statement.strip():
                    cur.execute(statement)
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


def main():
    dry_run = "--dry-run" in sys.argv
    now = datetime.now(timezone.utc).replace(microsecond=0)
    events, listings_by_event, failures = collect(now)

    n_listings = sum(len(v) for v in listings_by_event.values())
    print(f"schedule: {len(events)} events; "
          f"listing snapshots: {len(listings_by_event)} events, {n_listings} listings")
    for event_id, name, err in failures:
        print(f"FAILED {event_id} ({name}): {err}", file=sys.stderr)

    if dry_run:
        for e in events:
            if e["event_id"] in listings_by_event:
                ls = listings_by_event[e["event_id"]]
                floor = min((l["price_total_cents"] for l in ls), default=0)
                best = min((l["deal_score"] for l in ls if l["deal_score"]),
                           default=None)
                print(f"  {e['datetime_local'][:10]} {e['name'][:50]:50s} "
                      f"{len(ls):4d} listings, floor ${floor/100:.0f}, "
                      f"best score {best}")
    else:
        write_db(now, events, listings_by_event)
        print("database write complete")

    # Fail the run only if every listings fetch failed (endpoint change/block).
    if failures and not listings_by_event:
        sys.exit(1)


if __name__ == "__main__":
    main()
