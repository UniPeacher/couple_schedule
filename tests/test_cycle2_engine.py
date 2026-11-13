"""M1 tests: v2 engine + data layer. Run: python3 -m unittest discover -s tests -v"""
import json
import os
import sqlite3
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import cycle
import cycle2
import cycle_engine as engine

TODAY = date(2026, 10, 25)


def period(start, end=None):
    return {'start_date': start, 'end_date': end}


class EngineTests(unittest.TestCase):
    def test_no_history_is_honest(self):
        r = engine.predict([], {'prediction_enabled': 1}, TODAY)
        self.assertFalse(r['available'])
        self.assertIn('记下这次经期开始', r['reason'])

    def test_single_record_progress_hint(self):
        recs = [period('2026-10-01', '2026-10-05')]
        r = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        self.assertFalse(r['available'])
        self.assertIn('只有 1 次经期记录', r['reason'])

    def test_weighted_two_gaps_gives_window(self):
        recs = [period('2026-10-01'), period('2026-09-02'), period('2026-08-03')]
        r = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        self.assertTrue(r['available'], r.get('reason'))
        self.assertEqual(r['algorithm'], 'weighted-v2')
        self.assertEqual(r['cycle_length'], 29)   # weighted (30*.8+29)/1.8 -> 29.4
        self.assertEqual(r['estimated_start'], '2026-10-30')
        self.assertLessEqual(r['window'], 7)
        self.assertGreaterEqual(r['window'], 2)

    def test_recent_gap_weighted_heavier_than_old(self):
        # old gaps 28, 29; newest gap 34: weighted mean leans newer (~30.8 -> 31)
        recs = [period('2026-10-01'), period('2026-08-28'),
                period('2026-07-30'), period('2026-07-02')]
        r = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        self.assertTrue(r['available'], r.get('reason'))
        self.assertGreaterEqual(r['cycle_length'], 31)  # pulled toward 34 vs median 29

    def test_absurd_gap_downweighted_not_rejected(self):
        # v1 would refuse outright (max-min > 10); v2 keeps predicting via downweighting
        recs = [period('2026-10-01'), period('2026-09-03'), period('2026-07-20')]
        r = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        self.assertTrue(r['available'], r.get('reason'))

    def test_too_many_absurd_intervals_refuse(self):
        recs = [period('2026-10-01'), period('2026-05-01'), period('2026-02-01')]
        r = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        self.assertFalse(r['available'])
        self.assertIn('暂不预估', r['reason'])

    def test_window_tightens_with_more_steady_samples(self):
        steady = [period('2026-10-01')]
        d = date(2026, 10, 1)
        for i in range(2, 9):
            d = d - timedelta(days=28)
            steady.append(period(d.isoformat()))
        few = engine.predict(steady[:3], {'prediction_enabled': 1}, TODAY)
        many = engine.predict(steady, {'prediction_enabled': 1}, TODAY)
        self.assertTrue(few['available'] and many['available'])
        self.assertGreaterEqual(few['window'], many['window'])
        self.assertEqual(many['confidence'], 'high')

    def test_expired_stops_rolling_forward(self):
        recs = [period('2026-09-01'), period('2026-08-03'), period('2026-07-06')]
        r = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        self.assertFalse(r['available'])
        self.assertTrue(r.get('expired'))
        self.assertIn('待确认', r['reason'])

    def test_disabled_prediction(self):
        recs = [period('2026-10-01'), period('2026-09-03')]
        r = engine.predict(recs, {'prediction_enabled': 0}, TODAY)
        self.assertFalse(r['available'])
        self.assertEqual(r['reason'], '已关闭预估')

    def test_luteal_calibration_shifts_ovulation(self):
        recs = [period('2026-10-01'), period('2026-09-03'), period('2026-08-06')]
        plain = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        cal = engine.predict(recs, {'prediction_enabled': 1}, TODAY, luteal_length=11)
        self.assertEqual(plain['luteal_length'], 14)
        self.assertFalse(plain['luteal_calibrated'])
        self.assertTrue(cal['luteal_calibrated'])
        self.assertEqual((date.fromisoformat(cal['estimated_start'])
                          - date.fromisoformat(cal['ovulation_date'])).days, 11)


class BbtTests(unittest.TestCase):
    def setUp(self):
        self.starts = ['2026-07-10', '2026-08-07', '2026-09-06', '2026-10-06']
        self.records = [period(d) for d in reversed(self.starts)]

    def feed(self, day, temp):
        return {'date': day, 'temp_c': temp}

    def make_bbts(self, ovu_offset, cycles=None):
        """Synthetic temps: low for days 1..ovu_offset-1 from each start, high after."""
        data = []
        spans = cycles or list(zip(self.starts, self.starts[1:]))
        for a, b in spans:
            d0 = date.fromisoformat(a)
            d1 = date.fromisoformat(b)
            d = d0 + timedelta(days=1)
            while d < d1:
                if (d - d0).days < ovu_offset:
                    data.append(self.feed(d.isoformat(), 36.45))
                elif (d - d0).days == ovu_offset:
                    data.append(self.feed(d.isoformat(), 36.75))
                else:
                    data.append(self.feed(d.isoformat(), 36.85))
                d += timedelta(days=1)
        return data

    def test_shift_detected_and_luteal_individual(self):
        bbt = self.make_bbts(16)  # ovulation ~cycle day 17
        r = engine.luteal_from_bbt(self.records, bbt)
        self.assertIsNotNone(r)
        self.assertTrue(10 <= r <= 16)
        self.assertAlmostEqual(r, 30 - 16 - 1, delta=2)

    def test_sparse_bbt_yields_none(self):
        self.assertIsNone(engine.luteal_from_bbt(self.records, []))
        self.assertIsNone(engine.luteal_from_bbt(self.records,
                                                 [self.feed('2026-09-10', 36.5)]))

    def test_out_of_band_luteal_excluded(self):
        bbt = self.make_bbts(28)  # shift so late luteal < 10, must be excluded
        self.assertIsNone(engine.luteal_from_bbt(self.records, bbt))

    def test_flat_temps_no_shift(self):
        bbt = [self.feed('2026-08-09', 36.4), self.feed('2026-08-20', 36.42),
               self.feed('2026-09-01', 36.41)]
        self.assertIsNone(engine.bbt_shift(bbt, '2026-08-07', '2026-09-06'))


class PhaseTests(unittest.TestCase):
    def setUp(self):
        self.records = [period('2026-10-01', '2026-10-05'), period('2026-09-01'),
                        period('2026-08-02')]
        self.prediction = engine.predict(self.records, {'prediction_enabled': 1}, TODAY)

    def p(self, d):
        return engine.phase_of(d, self.records, self.prediction)

    def test_day_one_is_menstrual(self):
        self.assertEqual(self.p('2026-10-01')['phase'], 'menstrual')

    def test_period_span_until_duration(self):
        self.assertEqual(self.p('2026-10-05')['phase'], 'menstrual')
        self.assertEqual(self.p('2026-10-06')['phase'], 'follicular')

    def test_ovulation_window(self):
        ovu = date.fromisoformat(self.prediction['ovulation_date'])
        self.assertEqual(self.p(ovu.isoformat())['phase'], 'ovulation')
        self.assertEqual(self.p((ovu + timedelta(days=1)).isoformat())['phase'], 'ovulation')
        self.assertEqual(self.p((ovu + timedelta(days=2)).isoformat())['phase'], 'luteal')

    def test_luteal_until_window_start(self):
        self.assertEqual(self.p('2026-10-25')['phase'], 'luteal')
        self.assertEqual(self.p('2026-10-25')['label'], '黄体期')
        self.assertEqual(self.p('2026-10-25')['cycle_day'], 25)

    def test_outside_window_unlabeled(self):
        ws = date.fromisoformat(self.prediction['window_start'])
        self.assertIsNone(self.p(ws.isoformat()))  # inside confidence window, unconfirmed
        self.assertIsNone(engine.phase_of('2026-06-01', self.records, self.prediction))

    def test_open_period_none_duration_not_assumed(self):
        recs = [period('2026-10-20')] + self.records[1:]
        pred = engine.predict(recs, {'prediction_enabled': 1}, TODAY)
        ph = engine.phase_of('2026-10-22', recs, pred)
        self.assertIsNotNone(ph)
        self.assertEqual(ph['phase'], 'menstrual')  # open period still counted by dates given

    def test_month_phases_covers_month(self):
        marks = engine.month_phases('2026-10-01', '2026-10-31', self.records, self.prediction)
        dates = {m['date'] for m in marks}
        self.assertIn('2026-10-03', dates)
        self.assertIn('2026-10-25', dates)
        self.assertNotIn('2026-10-30', dates)  # inside prediction window, unconfirmed
        kinds = {m['phase'] for m in marks}
        self.assertTrue({'menstrual', 'follicular', 'luteal'} <= kinds)


class InsightTests(unittest.TestCase):
    def test_regularity_levels(self):
        steady = [period(('2026-10-01'))]
        d = date(2026, 10, 1)
        for i in range(6):
            d -= timedelta(days=28 + (i % 2))
            steady.append(period(d.isoformat()))
        out = engine.insights(steady, {'estimated_duration': 5})
        self.assertEqual(out['items'][0]['kind'], 'regularity')
        self.assertIn(out['items'][0]['level'], ('good', 'fair'))

    def test_long_periods_flag_watch(self):
        recs = [period('2026-10-01', '2026-10-09'), period('2026-08-30', '2026-09-08'),
                period('2026-07-30', '2026-08-07'), period('2026-06-29', '2026-07-07')]
        out = engine.insights(recs, {})
        self.assertTrue(any(i['kind'] == 'duration' and i['level'] == 'watch'
                            for i in out['items']))
        self.assertIn('咨询医生', json.dumps(out, ensure_ascii=False))

    def test_no_diagnosis_language(self):
        recs = [period('2026-10-01'), period('2026-09-01'), period('2026-08-01')]
        text = json.dumps(engine.insights(recs, {}), ensure_ascii=False)
        for word in ('诊断', '疾病', '不孕', '治疗'):
            self.assertNotIn(word, text.replace('不构成诊断', '').replace('不用于避孕或诊断', ''))


class DataLayerTests(unittest.TestCase):
    def setUp(self):
        self.c = sqlite3.connect(':memory:')
        self.c.row_factory = sqlite3.Row
        self.c.executescript(app.SCHEMA + cycle.SCHEMA + cycle2.SCHEMA_V2)

    def tearDown(self):
        self.c.close()

    def write(self, action, data, uid='a'):
        return cycle2.mutate(self.c, uid, action, data, TODAY)

    def test_schema_idempotent(self):
        self.c.executescript(cycle2.SCHEMA_V2)  # second run must not raise

    def test_period_crud_and_overlap(self):
        r = self.write('period', {'start_date': '2026-10-01', 'end_date': '2026-10-05'})
        self.assertTrue(r['ok'])
        with self.assertRaises(cycle2.Error):
            self.write('period', {'start_date': '2026-10-04', 'end_date': '2026-10-06'})
        self.write('period', {'id': r['id'], 'start_date': '2026-10-01', 'end_date': '2026-10-04'})
        self.write('period', {'start_date': '2026-10-05'})  # now allowed
        self.write('period/delete', {'id': r['id']})
        self.assertEqual(len(cycle2.periods(self.c, 'a')), 1)

    def test_period_validation(self):
        for body in [{'start_date': '2026-10-26'}, {'start_date': '2026-10-01', 'end_date': '2026-09-30'},
                     {'start_date': '2026-10-01', 'end_date': '2026-10-30'}]:
            with self.assertRaises(cycle2.Error):
                self.write('period', body)

    def test_isolation_between_uids(self):
        self.write('period', {'start_date': '2026-10-01'})
        with self.assertRaises(cycle2.Error) as e:
            self.write('period', {'id': 1, 'start_date': '2026-10-01'}, uid='b')
        self.assertEqual(e.exception.status, 404)

    def test_bbt_range_and_readback(self):
        self.write('bbt', {'date': '2026-10-15', 'temp_c': 36.6})
        self.assertEqual(cycle2.bbt(self.c, 'a')[0]['temp_c'], 36.6)
        for temp in (34.9, 39.0, 'x', None):
            with self.assertRaises(cycle2.Error):
                self.write('bbt', {'date': '2026-10-16', 'temp_c': temp})
        self.write('bbt', {'date': '2026-10-15', 'temp_c': 36.55})  # replace same day
        self.assertEqual(len(cycle2.bbt(self.c, 'a')), 1)
        self.write('bbt/delete', {'date': '2026-10-15'})
        self.assertEqual(cycle2.bbt(self.c, 'a'), [])

    def test_day_note_validation(self):
        self.write('day', {'date': '2026-10-20', 'flow': '中', 'pain': '重',
                           'symptoms': ['疲惫'], 'note': 'ok'})
        with self.assertRaises(cycle2.Error):
            self.write('day', {'date': '2026-10-21', 'flow': '洪水'})
        with self.assertRaises(cycle2.Error):
            self.write('day', {'date': '2026-10-22', 'symptoms': ['s' * 40]})
        with self.assertRaises(cycle2.Error):
            self.write('day', {'date': '2026-10-26'})  # future

    def test_snapshot_phase_card_and_calendar(self):
        self.write('period', {'start_date': '2026-10-01', 'end_date': '2026-10-05'})
        self.write('period', {'start_date': '2026-09-01', 'end_date': '2026-09-04'})
        self.write('period', {'start_date': '2026-08-02', 'end_date': '2026-08-06'})
        s = cycle2.snapshot(self.c, 'a', '2026-10', TODAY)
        self.assertEqual(s['today']['phase'], 'luteal')
        self.assertEqual(s['today']['label'], '黄体期')
        self.assertTrue(s['prediction']['available'])
        self.assertIn('2026-10-25', {m['date'] for m in s['calendar']})
        self.assertIn('不构成诊断', s['insights']['disclaimer'] +
                      json.dumps(s['insights'], ensure_ascii=False))
        self.assertIn('不用于避孕', s['disclaimer'])

    def test_snapshot_without_history_is_honest(self):
        s = cycle2.snapshot(self.c, 'a', '2026-10', TODAY)
        self.assertIsNone(s['today'])
        self.assertFalse(s['prediction']['available'])

    def test_settings_roundtrip(self):
        self.write('settings', {'typical_cycle': 32, 'typical_duration': 6})
        row = cycle2.settings(self.c, 'a')
        self.assertEqual(row['typical_cycle'], 32)
        with self.assertRaises(cycle2.Error):
            self.write('settings', {'typical_cycle': 90})

    def test_export_contains_no_v1_leak(self):
        self.write('period', {'start_date': '2026-10-01'})
        self.write('bbt', {'date': '2026-10-10', 'temp_c': 36.5})
        out = cycle2.export_data(self.c, 'a')
        self.assertEqual(len(out['periods']), 1)
        self.assertEqual(out['bbt'][0]['temp_c'], 36.5)


if __name__ == '__main__':
    unittest.main()
