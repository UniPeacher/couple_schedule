"""One-off backfill of the period history transcribed from the app's history screen.

The source screen prints each cycle as (period start – day before the next start)
plus its length; the period itself is the seven red pills, so ends are derived as
start + 6 and every gap is checked against the printed length before writing.
Existing rows are skipped, so re-running is a no-op.
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, __file__.rpartition('/')[0] or '.')
import app
import cycle2

# (period start, cycle length printed next to the row)
CYCLES = [
    ('2026-01-30', 36),
    ('2026-03-07', 35),
    ('2026-04-11', 32),
    ('2026-05-13', 32),
    ('2026-06-14', 41),
    ('2026-07-25', 35),
    ('2026-08-29', 31),
    ('2026-09-29', 25),
    ('2026-10-24', None),      # current cycle, already recorded
]
PERIOD_DAYS = 7


def verify():
    """The transcribed lengths must chain exactly through the starts."""
    for (start, printed), nxt in zip(CYCLES, CYCLES[1:]):
        if printed is None:
            continue
        gap = (date.fromisoformat(nxt[0]) - date.fromisoformat(start)).days
        if gap != printed:
            raise SystemExit('%s prints %d days but the next start is %d days away'
                             % (start, printed, gap))


def main():
    verify()
    conn = app.get_connection()
    today = date.today()
    for start, _ in CYCLES:
        if conn.execute('SELECT 1 FROM cycle2_periods WHERE uid=? AND start_date=?',
                        ('b', start)).fetchone():
            print('skip %s (already recorded)' % start)
            continue
        begin = date.fromisoformat(start)
        end = (begin + timedelta(days=PERIOD_DAYS - 1)).isoformat()
        cycle2.mutate(conn, 'b', 'period',
                      {'start_date': start, 'end_date': end}, today)
        print('added %s .. %s' % (start, end))
    conn.commit()

    rows = conn.execute('SELECT start_date,end_date FROM cycle2_periods WHERE uid=? '
                        'ORDER BY start_date', ('b',)).fetchall()
    print('\ntotal periods for b: %d' % len(rows))
    starts = [date.fromisoformat(r['start_date']) for r in rows]
    print('cycle lengths: %s' % [(b - a).days for a, b in zip(starts, starts[1:])])


if __name__ == '__main__':
    main()
