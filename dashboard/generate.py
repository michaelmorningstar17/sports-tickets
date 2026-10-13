#!/usr/bin/env python3
"""Build the 'when to buy Kraken tickets' dashboard.

Queries the tracker database, computes the aggregate analyses, and renders
two static pages from the templates in this directory:

    site/index.html   plain-advice version (casual reader)
    site/deep.html    methodology and distributions (data-curious reader)

Run:  DATABASE_URL=postgres://... python3 dashboard/generate.py
"""

import json
import os
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from collector import clean_dsn  # noqa: E402

LOWERISH = ('Lower', 'Plaza', 'Platinum', 'Gold', 'Club', 'Loge', '100')
HOURS_GRID = [8, 6, 4, 3, 2, 1.5, 1, 0.75, 0.5, 0.25]
KRAKEN_VENUE = 'Climate Pledge Arena'


def is_lowerish(tier):
    return any(k in (tier or '') for k in LOWERISH)


def quantile(xs, q):
    xs = sorted(xs)
    if not xs:
        return None
    i = (len(xs) - 1) * q
    lo, hi = int(i), min(int(i) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (i - lo)


def fetch(conn):
    """One scan: per game, per pass, floors and group floors."""
    rows = conn.execute("""
        SELECT e.event_id, e.name, e.venue, e.datetime_local,
               e.datetime_utc < now() AS finished,
               extract(dow FROM e.datetime_local) AS dow,
               round(EXTRACT(epoch FROM (e.datetime_utc - s.captured_at))/60) AS mins_before,
               s.section_group, s.price_total_cents, s.lots, s.deal_score
        FROM events e JOIN listing_snapshots s USING (event_id)
        WHERE s.deal_score IS NOT NULL AND s.deal_score <= 1.5
    """).fetchall()
    games = defaultdict(lambda: defaultdict(list))
    meta = {}
    for eid, name, venue, dt_local, finished, dow, mins, tier, cents, lots, score in rows:
        qtys = {int(x) for x in (lots or '').split('/') if x.isdigit()}
        games[eid][int(mins)].append((cents / 100, tier, qtys))
        meta[eid] = {'name': name, 'venue': venue, 'dow': int(dow),
                     'date': str(dt_local)[:16], 'finished': bool(finished)}
    return games, meta


def pass_stats(listings):
    floor = min(p for p, _, _ in listings)
    lower = [p for p, t, _ in listings if is_lowerish(t)]
    g = lambda q: [p for p, t, qs in listings if is_lowerish(t) and q in qs]
    return {
        'floor': floor,
        'lower_floor': min(lower) if lower else None,
        'n': len(listings),
        'g2': min(g(2)) if g(2) else None,
        'g4': min(g(4)) if g(4) else None,
        'g6': min(g(6)) if g(6) else None,
    }


def analyze_league(games, meta, now_utc):
    per_game = {}
    for eid, passes in games.items():
        pts = sorted(((m, pass_stats(ls)) for m, ls in passes.items()),
                     key=lambda t: -t[0])
        pre = [(m, st) for m, st in pts if m >= -5]
        if len(pre) < 8 or pre[0][0] < 240:
            continue
        ref = next((st for m, st in pre if m <= 420), None)
        if not ref:
            continue
        per_game[eid] = {'pts': pre, 'ref': ref}

    agg = {'n_games': len(per_game)}

    # bottom timing distribution
    bottoms, drops = [], []
    by_day = {'weekday': [], 'weekend': []}
    day_drop = {'weekday': [], 'weekend': []}
    day_level = {'weekday': [], 'weekend': []}
    for eid, g in per_game.items():
        mn, mnst = min(g['pts'], key=lambda t: t[1]['floor'])
        bottoms.append(mn)
        d = (g['ref']['floor'] - mnst['floor']) / g['ref']['floor'] * 100
        drops.append(d)
        key = 'weekend' if meta[eid]['dow'] in (5, 6, 0) else 'weekday'
        by_day[key].append(mn)
        day_drop[key].append(d)
        day_level[key].append(g['ref']['floor'])

    buckets = [('>4h out', 241, 10**9), ('2-4h', 121, 240), ('1-2h', 61, 120),
               ('30-60m', 31, 60), ('<30m or after start', -10**9, 30)]
    agg['bottom_buckets'] = [
        {'label': lab, 'n': sum(1 for m in bottoms if lo <= m <= hi)}
        for lab, lo, hi in buckets]
    agg['median_drop_pct'] = round(statistics.median(drops))
    agg['drop_p25_p75'] = [round(quantile(drops, .25)), round(quantile(drops, .75))]
    agg['dow'] = {k: {'n': len(v),
                      'median_bottom_min': round(statistics.median(v)) if v else None,
                      'median_drop_pct': round(statistics.median(day_drop[k])) if day_drop[k] else None,
                      'median_t6_floor': round(statistics.median(day_level[k])) if day_level[k] else None}
                  for k, v in by_day.items()}

    # normalized floor trajectory (median across games), overall and lower-tier
    def traj(field):
        out = []
        for h in HOURS_GRID:
            vals = []
            for g in per_game.values():
                target = h * 60
                near = min(g['pts'], key=lambda t: abs(t[0] - target))
                if abs(near[0] - target) <= 45 and near[1][field] and g['ref'][field]:
                    vals.append(near[1][field] / g['ref'][field])
            out.append({'h': h, 'median': round(statistics.median(vals), 3) if vals else None,
                        'p25': round(quantile(vals, .25), 3) if vals else None,
                        'p75': round(quantile(vals, .75), 3) if vals else None,
                        'n': len(vals)})
        return out
    agg['traj_floor'] = traj('floor')
    agg['traj_lower'] = traj('lower_floor')

    # group availability decay: share of games with any lowerish qty-N listing
    def avail(field):
        out = []
        for h in HOURS_GRID:
            have, tot = 0, 0
            for g in per_game.values():
                target = h * 60
                near = min(g['pts'], key=lambda t: abs(t[0] - target))
                if abs(near[0] - target) <= 45:
                    tot += 1
                    if near[1][field] is not None:
                        have += 1
            out.append({'h': h, 'pct': round(have / tot * 100) if tot else None, 'n': tot})
        return out
    agg['avail_g4'] = avail('g4')
    agg['avail_g6'] = avail('g6')

    # group premium: median g4/g2 and g6/g2 ratio at T-6h and T-1h
    for label, hrs in (('t6', 6), ('t1', 1)):
        r4, r6 = [], []
        for g in per_game.values():
            target = hrs * 60
            near = min(g['pts'], key=lambda t: abs(t[0] - target))
            st = near[1]
            if abs(near[0] - target) <= 45 and st['g2']:
                if st['g4']: r4.append(st['g4'] / st['g2'])
                if st['g6']: r6.append(st['g6'] / st['g2'])
        agg[f'premium_{label}'] = {
            'g4': round(statistics.median(r4), 2) if r4 else None,
            'g6': round(statistics.median(r6), 2) if r6 else None,
            'n4': len(r4), 'n6': len(r6)}
    return agg


def analyze_kraken(games, meta, conn):
    k = {'completed': [], 'upcoming': []}
    for eid, m in meta.items():
        if m['venue'] != KRAKEN_VENUE:
            continue
        passes = games[eid]
        pts = sorted(((mm, pass_stats(ls)) for mm, ls in passes.items()),
                     key=lambda t: -t[0])
        latest_min, latest = min(pts, key=lambda t: t[0]) if pts else (None, None)
        finished = m['finished']
        series = [{'m': mm, 'floor': st['floor'], 'lower': st['lower_floor'],
                   'g2': st['g2'], 'g4': st['g4'], 'g6': st['g6'], 'n': st['n']}
                  for mm, st in pts]
        rec = {'name': m['name'], 'date': m['date'], 'dow': m['dow']}
        if finished:
            pre = [(mm, st) for mm, st in pts if mm >= -5]
            if pre:
                mn, mnst = min(pre, key=lambda t: t[1]['floor'])
                rec.update({'bottom_min': mn, 'bottom_floor': mnst['floor'],
                            'series': [s for s in series if s['m'] >= -35]})
                k['completed'].append(rec)
        elif latest is not None and latest_min is not None and latest_min < 60 * 24 * 40:
            rec.update({
                'hours_out': round(latest_min / 60),
                'floor': latest['floor'], 'lower_floor': latest['lower_floor'],
                'g2': latest['g2'], 'g4': latest['g4'], 'g6': latest['g6'],
                'depth': latest['n']})
            k['upcoming'].append(rec)
    k['completed'].sort(key=lambda r: r['date'])
    k['upcoming'].sort(key=lambda r: r['date'])
    k['upcoming'] = k['upcoming'][:8]

    # tier premium at Climate Pledge from all passes of all home games
    ratios = defaultdict(list)
    for eid, m in meta.items():
        if m['venue'] != KRAKEN_VENUE:
            continue
        for ls in games[eid].values():
            tiers = defaultdict(list)
            for p, t, _ in ls:
                tiers[t].append(p)
            if tiers.get('Lower') and tiers.get('Loge'):
                ratios['loge_vs_lower'].append(min(tiers['Loge']) / min(tiers['Lower']))
            if tiers.get('Lower') and tiers.get('Upper'):
                ratios['upper_vs_lower'].append(min(tiers['Upper']) / min(tiers['Lower']))
    k['tier_ratio'] = {kk: round(statistics.median(v), 2) for kk, v in ratios.items()}
    return k


def main():
    import psycopg
    conn = psycopg.connect(clean_dsn(os.environ['DATABASE_URL']))
    games, meta = fetch(conn)
    now = datetime.now(timezone.utc)
    data = {
        'generated': now.strftime('%B %d, %Y %H:%M UTC'),
        'league': analyze_league(games, meta, now),
        'kraken': analyze_kraken(games, meta, conn),
        'n_games_total': len(games),
        'n_rows': conn.execute('SELECT count(*) FROM listing_snapshots').fetchone()[0],
    }
    conn.close()

    here = Path(__file__).parent
    out = here.parent / 'site'
    out.mkdir(exist_ok=True)
    payload = json.dumps(data, separators=(',', ':'))
    for page in ('index', 'deep'):
        tmpl = (here / f'{page}.tmpl.html').read_text()
        html = tmpl.replace('/*__DATA__*/null', payload) \
                   .replace('__GENERATED__', data['generated'])
        (out / f'{page}.html').write_text(html)
    print(f"generated site/ from {data['league']['n_games_total'] if 'n_games_total' in data['league'] else data['league']['n_games']} qualified games, "
          f"{data['n_rows']:,} rows; {data['generated']}")


if __name__ == '__main__':
    main()
