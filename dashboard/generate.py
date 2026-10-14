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
               s.section_group, s.price_total_cents, s.lots, s.deal_score, s.section
        FROM events e JOIN listing_snapshots s USING (event_id)
        WHERE s.deal_score IS NOT NULL AND s.deal_score <= 1.5
    """).fetchall()
    games = defaultdict(lambda: defaultdict(list))
    meta = {}
    for eid, name, venue, dt_local, finished, dow, mins, tier, cents, lots, score, sec in rows:
        qtys = {int(x) for x in (lots or '').split('/') if x.isdigit()}
        games[eid][int(mins)].append((cents / 100, tier, qtys, sec))
        meta[eid] = {'name': name, 'venue': venue, 'dow': int(dow),
                     'date': str(dt_local)[:16], 'finished': bool(finished)}
    return games, meta


def pass_stats(listings):
    floor = min(p for p, *_ in listings)
    lower = [p for p, t, *_ in listings if is_lowerish(t)]
    g = lambda q: [p for p, t, qs, *_ in listings if is_lowerish(t) and q in qs]
    out = {'floor': floor, 'lower_floor': min(lower) if lower else None,
           'n': len(listings)}
    for q in (2, 3, 4, 5, 6):
        vals = g(q)
        out[f'g{q}'] = min(vals) if vals else None
    return out


def analyze_league(games, meta, now_utc, eids=None):
    per_game = {}
    for eid, passes in games.items():
        if eids is not None and eid not in eids:
            continue
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

    # median dollar floor by quantity (lower-type tiers) at each grid point
    fq = []
    for h in HOURS_GRID:
        row = {'h': h}
        for f in ('g2', 'g3', 'g4', 'g5', 'g6'):
            vals = []
            for g in per_game.values():
                target = h * 60
                near = min(g['pts'], key=lambda t: abs(t[0] - target))
                if abs(near[0] - target) <= 45 and near[1][f] is not None:
                    vals.append(near[1][f])
            row[f] = round(statistics.median(vals)) if vals else None
            row['n' + f[1]] = len(vals)
        fq.append(row)
    agg['floor_by_qty'] = fq

    # 'waiting backfires' risk: by hour h, the block is gone OR >=20% pricier
    # than the same game's T-6h block price. Only games that had a block at T-6h.
    for f in ('g2', 'g3', 'g4', 'g5', 'g6'):
        out = []
        for h in HOURS_GRID:
            gone = spike = tot = 0
            for g in per_game.values():
                ref = g['ref'][f]
                if ref is None:
                    continue
                target = h * 60
                near = min(g['pts'], key=lambda t: abs(t[0] - target))
                if abs(near[0] - target) > 45:
                    continue
                tot += 1
                v = near[1][f]
                if v is None:
                    gone += 1
                elif v >= ref * 1.2:
                    spike += 1
            out.append({'h': h,
                        'gone': round(gone / tot * 100) if tot else None,
                        'spike': round(spike / tot * 100) if tot else None,
                        'n': tot})
        agg['regret_' + f] = out

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
            for p, t, *_ in ls:
                tiers[t].append(p)
            if tiers.get('Lower') and tiers.get('Loge'):
                ratios['loge_vs_lower'].append(min(tiers['Loge']) / min(tiers['Lower']))
            if tiers.get('Lower') and tiers.get('Upper'):
                ratios['upper_vs_lower'].append(min(tiers['Upper']) / min(tiers['Lower']))
    k['tier_ratio'] = {kk: round(statistics.median(v), 2) for kk, v in ratios.items()}
    return k


def build_map_data(games, meta, sec_floor_source):
    """Per Kraken home game: thinned per-section price frames for the replay,
    plus each section's best price and when it occurred."""
    here = Path(__file__).parent
    pos = json.loads((here / 'cpa_sections.json').read_text())
    sec_ids = sorted(pos.keys())
    out = {'sections': [{'id': k, 'tier': pos[k]['tier'],
                         'x': pos[k]['x'], 'y': pos[k]['y']} for k in sec_ids],
           'games': []}
    for eid, m in sorted(meta.items(), key=lambda kv: kv[1]['date']):
        if m['venue'] != KRAKEN_VENUE or 'Season Tickets' in m['name']:
            continue
        passes = sec_floor_source.get(eid)
        if not passes or len(passes) < 8:
            continue
        if not m['finished']:
            cutoff = (datetime.now(timezone.utc) + __import__('datetime').timedelta(days=30)).strftime('%Y-%m-%d')
            if m['date'][:10] > cutoff:
                continue
        ordered = sorted(passes.items(), key=lambda t: -t[0])
        # thin to at most 36 frames, always keeping first and last
        n = len(ordered)
        if n > 36:
            idx = sorted({round(i * (n - 1) / 35) for i in range(36)})
            ordered_f = [ordered[i] for i in idx]
        else:
            ordered_f = ordered
        frames = [{'m': mins, 'p': [secp.get(k) for k in sec_ids]}
                  for mins, secp in ordered_f]
        best = []
        for k in sec_ids:
            cands = [(secp[k], mins) for mins, secp in ordered
                     if secp.get(k) is not None and mins >= -5]
            best.append({'p': min(cands)[0], 'm': min(cands, key=lambda c: c[0])[1]}
                        if cands else None)
        out['games'].append({'name': m['name'].replace(' at Seattle Kraken', ''),
                             'date': m['date'], 'finished': m['finished'],
                             'frames': frames, 'best': best,
                             '_eid': eid})

    # season average: completed regular-season home games only
    reg = [(g, meta[g['_eid']]) for g in out['games']
           if g['finished'] and 'Preseason' not in meta[g['_eid']]['name']]
    if reg:
        grid = [4320, 2880, 1440, 720, 480, 360, 300, 240, 180, 120,
                90, 60, 45, 30, 15, 0]
        ordered_by_game = []
        for g, m in reg:
            passes = sec_floor_source[g['_eid']]
            ordered_by_game.append(sorted(passes.items(), key=lambda t: -t[0]))
        avg_frames = []
        for gm in grid:
            tol = max(12, gm * 0.25)
            per_sec = {k: [] for k in sec_ids}
            hit = False
            for ordered in ordered_by_game:
                near = min(ordered, key=lambda t: abs(t[0] - gm))
                if abs(near[0] - gm) > tol:
                    continue
                hit = True
                for k in sec_ids:
                    v = near[1].get(k)
                    if v is not None:
                        per_sec[k].append(v)
            if hit:
                avg_frames.append({'m': gm, 'p': [
                    round(statistics.median(per_sec[k])) if per_sec[k] else None
                    for k in sec_ids]})
        avg_best = []
        for i, k in enumerate(sec_ids):
            vals = [g['best'][i] for g, _ in reg if g['best'][i]]
            avg_best.append({'p': round(statistics.median([v['p'] for v in vals])),
                             'm': round(statistics.median([v['m'] for v in vals]))}
                            if vals else None)
        out['games'].insert(0, {
            'name': f'Season average ({len(reg)} completed regular-season home games)',
            'date': '', 'finished': True,
            'frames': avg_frames, 'best': avg_best})
    for g in out['games']:
        g.pop('_eid', None)
    return out


def main():
    import psycopg
    import time as _time
    conn = None
    for attempt in range(4):
        try:
            conn = psycopg.connect(clean_dsn(os.environ['DATABASE_URL']),
                                   connect_timeout=30)
            break
        except psycopg.OperationalError as e:
            if attempt == 3:
                raise
            print(f'connect attempt {attempt + 1} failed ({e}); retrying...')
            _time.sleep(15)
    games, meta = fetch(conn)
    now = datetime.now(timezone.utc)
    kraken_eids = {eid for eid, m in meta.items()
                   if m['venue'] == KRAKEN_VENUE and m['finished']}
    sec_floors = {}
    for eid, m in meta.items():
        if m['venue'] != KRAKEN_VENUE:
            continue
        per_pass = {}
        for mins, ls in games[eid].items():
            secp = {}
            for price, tier, qtys, sec in ls:
                if sec and (sec not in secp or price < secp[sec]):
                    secp[sec] = round(price)
            per_pass[mins] = secp
        sec_floors[eid] = per_pass
    data = {
        'generated': now.strftime('%B %d, %Y %H:%M UTC'),
        'league': analyze_league(games, meta, now),
        'kraken_agg': analyze_league(games, meta, now, eids=kraken_eids),
        'kraken': analyze_kraken(games, meta, conn),
        'map': build_map_data(games, meta, sec_floors),
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
