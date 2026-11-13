"""Cycle engine v2: weighted prediction, four-phase derivation, BBT calibration, insights.

Pure standard-library functions on plain dicts; no database access.
records: [{'start_date': iso, 'end_date': iso|None}] newest first (v1 convention).
bbt:     [{'date': iso, 'temp_c': float}]
"""
from datetime import date, timedelta
import statistics

LUTEAL_DEFAULT = 14          # calendar fallback assumption (days from ovulation to next start)
LUTEAL_MIN, LUTEAL_MAX = 10, 16   # sanity band for BBT-derived individual luteal length
PHASES = ('menstrual', 'follicular', 'ovulation', 'luteal')
PHASE_LABELS = {'menstrual': '行经期', 'follicular': '卵泡期',
                'ovulation': '排卵期', 'luteal': '黄体期'}
DISCLAIMER = '预估与分期仅供参考，不用于避孕或诊断；未记录不代表月经推迟。'


def _d(value):
    return value if isinstance(value, date) else date.fromisoformat(value)


def weighted_stats(gaps, decay=0.8, outlier_band=7, absurd=(15, 60)):
    """Exponential recency weights; implausible or off-median gaps downweighted, never dropped."""
    if not gaps:
        return None
    med = statistics.median(gaps)
    pairs = []
    for i, g in enumerate(gaps):            # oldest -> newest
        w = decay ** (len(gaps) - 1 - i)
        if not (absurd[0] <= g <= absurd[1]):
            w *= 0.1
        elif abs(g - med) > outlier_band:
            w *= 0.3
        pairs.append((g, w))
    total = sum(w for _, w in pairs)
    if total < 0.5:
        return None                          # too few trustworthy observations
    mean = sum(g * w for g, w in pairs) / total
    var = sum(w * (g - mean) ** 2 for g, w in pairs) / total
    return {'length': mean, 'spread': var ** 0.5, 'weight': total}


def predict(records, prefs, today, luteal_length=None):
    """Weighted next-period estimate with a confidence window that tightens with data."""
    today = _d(today)
    if not prefs.get('prediction_enabled', 1):
        return {'available': False, 'reason': '已关闭预估'}
    starts = sorted(_d(r['start_date']) for r in records)
    starts = starts[-12:]
    gaps = [(b - a).days for a, b in zip(starts, starts[1:])]
    if not starts:
        return {'available': False, 'reason': '记录经期开始日期后，可逐步形成预估'}
    if len(gaps) >= 1:
        stats = weighted_stats(gaps)
        if stats is None:
            return {'available': False,
                    'reason': '近期记录间隔差异较大或含异常间隔，暂不预估；继续记录后可恢复'}
        length = round(stats['length'])
        if len(gaps) == 1:
            margin = 4
            basis = '最近一次周期间隔（样本尚少）'
            confidence = 'low'
        else:
            margin = max(2, min(7, round(stats['spread'] / 1.5) + 2))
            basis = '近期记录加权平均（%d 个周期间隔）' % len(gaps)
            confidence = 'high' if stats['weight'] >= 3 and len(gaps) >= 4 else 'medium'
    elif prefs.get('typical_cycle'):
        length = prefs['typical_cycle']
        margin = 3
        basis = '你填写的典型周期（历史记录不足）'
        confidence = 'low'
    else:
        return {'available': False,
                'reason': '已记录 %d 次经期，再补充 1 次即可形成预估' % len(starts)}
    target = starts[-1] + timedelta(days=length)
    if target + timedelta(days=margin) < today:
        return {'available': False, 'expired': True,
                'estimated_start': target.isoformat(),
                'reason': '上次预估区间已过，周期信息待确认；未记录不代表推迟'}
    durations = [(_d(r['end_date']) - _d(r['start_date'])).days + 1
                 for r in records[:8] if r.get('end_date')]
    if durations:
        duration = round(statistics.median(durations))
    else:
        duration = prefs.get('typical_duration') or 5
    luteal = luteal_length or LUTEAL_DEFAULT
    ovulation = target - timedelta(days=luteal)
    return dict(available=True, algorithm='weighted-v2', basis=basis,
                confidence=confidence, cycle_length=length, window=margin,
                estimated_start=target.isoformat(),
                window_start=(target - timedelta(days=margin)).isoformat(),
                window_end=(target + timedelta(days=margin)).isoformat(),
                estimated_duration=duration,
                ovulation_date=ovulation.isoformat(),
                luteal_length=luteal, luteal_calibrated=bool(luteal_length),
                generated_on=today.isoformat(), disclaimer=DISCLAIMER)


def bbt_shift(bbt, start, nxt):
    """Locate post-ovulation thermal shift inside one cycle. Returns shift day or None.
    Rules: baseline = median of days 1-5; shift = first day (after day 6) at or above
    baseline + 0.3 C followed by 2 days at or above baseline + 0.25 C."""
    inside = sorted(((_d(b['date']), b['temp_c']) for b in bbt
                     if _d(start) < _d(b['date']) < _d(nxt)), key=lambda x: x[0])
    if len(inside) < 7:
        return None
    first5 = [t for _, t in inside[:5]]
    if len(first5) < 3:
        return None
    baseline = statistics.median(first5)
    for i, (day, temp) in enumerate(inside):
        if (day - _d(start)).days < 6:
            continue
        if temp >= baseline + 0.3 and all(t >= baseline + 0.25 for _, t in inside[i + 1:i + 3]) and len(inside) - i >= 3:
            prev = [d for d, t in inside[:i] if t < baseline + 0.3]
            ovu = prev[-1] if prev else day - timedelta(days=1)
            return {'shift_day': day.isoformat(), 'ovulation': ovu.isoformat(),
                    'luteal': (_d(nxt) - ovu).days}
    return None


def luteal_from_bbt(records, bbt):
    """Median individual luteal length across cycles with a confirmed thermal shift."""
    starts = sorted(_d(r['start_date']) for r in records)
    values = []
    for a, b in zip(starts, starts[1:]):
        found = bbt_shift(bbt, a.isoformat(), b.isoformat())
        if found and LUTEAL_MIN <= found['luteal'] <= LUTEAL_MAX:
            values.append(found['luteal'])
    if len(values) < 2:
        return None
    return round(statistics.median(values))


def cycle_for(today, records, prediction):
    """(cycle_start, next_start, next_is_real) bounding `today`, or None when unresolvable."""
    today = _d(today)
    starts = sorted(_d(r['start_date']) for r in records)
    past = [s for s in starts if s <= today]
    if not past:
        return None
    start = past[-1]
    after = [s for s in starts if s > today]
    if after:
        return start, after[0], True
    if prediction.get('available'):
        return start, _d(prediction['estimated_start']), False
    return None


def phase_of(today, records, prediction, bbt=None):
    """Which phase `today` sits in, with day counts. Returns None without a resolvable cycle.

    Days inside the confidence window before the predicted start are deliberately
    unlabeled: whether the period already started early is unknown until confirmed.
    """
    today = _d(today)
    bounds = cycle_for(today, records, prediction)
    if bounds is None:
        return None
    start, nxt, real = bounds
    if not real and prediction.get('window_start') and today >= _d(prediction['window_start']):
        return None
    luteal = prediction.get('luteal_length') or LUTEAL_DEFAULT
    active = next((r for r in records if _d(r['start_date']) == start), None)
    if active and not active.get('end_date'):
        duration = None
    elif active and active.get('end_date'):
        duration = (_d(active['end_date']) - start).days + 1
    else:
        duration = prediction.get('estimated_duration')
    duration = duration or prediction.get('estimated_duration') or 5
    ovu = nxt - timedelta(days=luteal)
    if today <= nxt - timedelta(days=1):
        if today <= start + timedelta(days=duration - 1):
            phase, days_in = 'menstrual', (today - start).days + 1
        elif today < ovu:
            phase, days_in = 'follicular', (today - (start + timedelta(days=duration))).days + 1
        elif today <= ovu + timedelta(days=1):
            phase, days_in = 'ovulation', (today - ovu).days + 1
        else:
            phase, days_in = 'luteal', (today - (ovu + timedelta(days=1))).days + 1
    else:
        phase, days_in = None, None
    if phase is None:
        return None
    return dict(phase=phase, label=PHASE_LABELS[phase], day=days_in,
                cycle_day=(today - start).days + 1,
                period_start=start.isoformat(), next_start=nxt.isoformat(),
                ovulation=ovu.isoformat(), duration_used=duration)


def month_phases(month_first, month_last, records, prediction, bbt=None):
    """Day-by-day phase map for a calendar month; days outside any cycle stay unlabeled."""
    marks = []
    day = _d(month_first)
    end = _d(month_last)
    while day <= end:
        p = phase_of(day, records, prediction, bbt)
        if p:
            marks.append(dict(date=day.isoformat(), phase=p['phase'],
                              label=p['label'], cycle_day=p['cycle_day']))
        day += timedelta(days=1)
    return marks


def insights(records, prediction, bbt=None):
    """Conservative health insights (no diagnosis language)."""
    starts = sorted(_d(r['start_date']) for r in records)[-12:]
    gaps = [(b - a).days for a, b in zip(starts, starts[1:])]
    out = []
    if len(gaps) >= 3:
        spread = max(gaps) - min(gaps)
        if spread <= 4:
            out.append({'kind': 'regularity', 'level': 'good',
                        'text': '近 %d 个周期间隔波动 %d 天内，周期规律。' % (len(gaps), spread)})
        elif spread <= 7:
            out.append({'kind': 'regularity', 'level': 'fair',
                        'text': '近 %d 个周期间隔波动 %d 天，属常见范围。' % (len(gaps), spread)})
        else:
            out.append({'kind': 'regularity', 'level': 'watch',
                        'text': '近 %d 个周期间隔波动 %d 天，波动偏大；如持续如此可咨询医生。'
                               % (len(gaps), spread)})
    luteal = luteal_from_bbt(records, bbt) if bbt else None
    if luteal is not None:
        if luteal < 11:
            out.append({'kind': 'luteal', 'level': 'watch',
                        'text': '基础体温提示黄体期约 %d 天，偏短；如有备孕或不适考虑咨询医生。' % luteal})
        else:
            out.append({'kind': 'luteal', 'level': 'good',
                        'text': '基础体温提示你的黄体期约 %d 天，预估已按此校准。' % luteal})
    durations = [(_d(r['end_date']) - _d(r['start_date'])).days + 1
                 for r in records[:8] if r.get('end_date')]
    if len(durations) >= 3:
        med = statistics.median(durations)
        if med >= 8:
            out.append({'kind': 'duration', 'level': 'watch',
                        'text': '经期中位数约 %d 天，偏长；出血量大或持续如此建议咨询医生。' % med})
        elif med <= 2:
            out.append({'kind': 'duration', 'level': 'watch',
                        'text': '经期中位数约 %d 天，偏短；如明显变化可咨询医生。' % med})
    return {'items': out, 'luteal_calibrated': luteal,
            'disclaimer': '健康洞察基于你的记录做统计描述，不构成诊断。'}
