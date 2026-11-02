"""One-shot merge of the Supabase outage buffer back into Neon.

Streams each table buffer -> temp table -> INSERT ... ON CONFLICT DO NOTHING,
so it is idempotent and safe to re-run (e.g. once more after the GitHub
secret flips back, to catch rows written in between).

Usage: set -a && source .env.local && set +a && .venv/bin/python merge_buffer.py
"""
import os
import psycopg

TABLES = [
    ('events', 'event_id'),
    ('event_stats', 'event_id, captured_at'),
    ('listing_snapshots', 'listing_id, captured_at'),
]


def main():
    src = psycopg.connect(os.environ['BUFFER_DATABASE_URL'], connect_timeout=30)
    dst = psycopg.connect(os.environ['DATABASE_URL'], connect_timeout=30)
    src.execute("SET statement_timeout = 0")
    src.commit()
    dst.execute("SET statement_timeout = 0")
    dst.commit()
    for table, conflict in TABLES:
        cols = [r[0] for r in dst.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = %s ORDER BY ordinal_position", (table,))]
        collist = ', '.join(f'"{c}"' for c in cols)
        dst.execute(f'CREATE TEMP TABLE tmp_merge (LIKE {table})')
        if table == 'listing_snapshots':
            weeks = [r[0] for r in src.execute(
                "SELECT DISTINCT date_trunc('week', captured_at) "
                'FROM listing_snapshots ORDER BY 1')]
        else:
            weeks = [None]
        for wk in weeks:
            cond = '' if wk is None else \
                f" WHERE date_trunc('week', captured_at) = '{wk.isoformat()}'"
            with src.cursor().copy(
                    f'COPY (SELECT {collist} FROM {table}{cond}) TO STDOUT') as out, \
                 dst.cursor().copy(f'COPY tmp_merge ({collist}) FROM STDIN') as into:
                for chunk in out:
                    into.write(chunk)
            if wk is not None:
                print(f'  copied week of {wk.date()}')
        cur = dst.execute(
            f'INSERT INTO {table} ({collist}) SELECT {collist} FROM tmp_merge '
            f'ON CONFLICT ({conflict}) DO NOTHING')
        dst.execute('DROP TABLE tmp_merge')
        dst.commit()
        total = dst.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
        print(f'{table}: inserted {cur.rowcount:,} new rows; now {total:,} total')
    src.close()
    dst.close()


if __name__ == '__main__':
    main()
