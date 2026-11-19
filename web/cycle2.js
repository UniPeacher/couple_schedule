/* Cycle journal: overview, calendar and daily record. Health state stays in memory only. */
var Cycle2 = (function () {
  'use strict';
  var state = null, owner = null, sequence = 0, selectedDate = null;
  var inflight = null;
  var anchor = null, viewMode = 'calendar', recordTab = null, recordDate = null;
  var PHASES = ['menstrual', 'follicular', 'ovulation', 'luteal'];
  var PHASE_LABEL = {menstrual: '行经期', follicular: '卵泡期', ovulation: '排卵期', luteal: '黄体期'};
  var CONF_LABEL = {high: '记录较充分', medium: '基于近期记录', low: '参考记录较少'};
  var WD = ['一', '二', '三', '四', '五', '六', '日'];
  var WD_FULL = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  var MOODS = ['开心', '平静', '放松', '兴奋', '敏感', '焦虑', '烦躁', '低落'];
  var MOOD_EMOJI = ['🙂', '😌', '😊', '🤩', '🥺', '😰', '😤', '😔'];
  var SYMPTOMS = ['疲劳', '腹胀', '头痛', '腰酸', '乳房胀痛', '恶心', '抽筋', '浮肿',
                  '失眠', '眩晕', '腹泻', '便秘'];
  var SYMPTOM_EMOJI = {疲劳: '😪', 腹胀: '🫧', 头痛: '🤕', 腰酸: '🦴', 乳房胀痛: '😣',
                       恶心: '🤢', 抽筋: '⚡', 浮肿: '💧', 失眠: '🌙', 眩晕: '💫',
                       腹泻: '🚽', 便秘: '🚻'};
  var SEX_OPTIONS = ['无', '保护性行为', '无保护性行为'];
  var SEX_EMOJI = {无: '☀️', 保护性行为: '☂️', 无保护性行为: '🌂'};
  function fmtD(iso) {
    if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso || '';
    var p = iso.split('-');
    return (+p[1]) + '.' + (+p[2]);
  }
  function fmtFull(iso) {
    return iso ? iso.replace(/-/g, '.') : '';
  }
  function fmtRange(a, b) {
    if (!a || !b) return '';
    if (a === b) return fmtD(a);
    return a.slice(0, 4) === b.slice(0, 4) ? fmtD(a) + ' — ' + fmtD(b) : fmtFull(a) + ' — ' + fmtFull(b);
  }
  /* SVG arc between two angles (radians, clockwise), for the phase segments. */
  function arcPath(cx, cy, r, a0, a1) {
    var large = a1 - a0 > Math.PI ? 1 : 0;
    var x0 = cx + r * Math.cos(a0), y0 = cy + r * Math.sin(a0);
    var x1 = cx + r * Math.cos(a1), y1 = cy + r * Math.sin(a1);
    return 'M' + x0.toFixed(1) + ' ' + y0.toFixed(1) +
      ' A' + r + ' ' + r + ' 0 ' + large + ' 1 ' + x1.toFixed(1) + ' ' + y1.toFixed(1);
  }

  function h(x) { return esc(x == null ? '' : x); }
  function btn(label, action, attrs) {
    return '<button type="button" class="p-btn" data-c2="' + action + '" ' + (attrs || '') + '>' + label + '</button>';
  }
  function field(label, input) { return '<label class="c2-field"><span>' + label + '</span>' + input + '</label>'; }
  function input(name, value, attrs) {
    return '<input name="' + name + '" value="' + h(value) + '" ' + (attrs || '') + '>';
  }
  function dt(name, value, required, max) {
    return input(name, value, 'type="date"' + (required ? ' required' : '') + (max ? ' max="' + max + '"' : ''));
  }
  function check(name, label, on) {
    return '<label class="c2-check"><input type="checkbox" name="' + name + '"' + (on ? ' checked' : '') + '><span>' + label + '</span></label>';
  }
  function select(name, items, value) {
    return '<select name="' + name + '"><option value="">未记录</option>' + items.map(function (x) {
      return '<option' + (x === value ? ' selected' : '') + '>' + h(x) + '</option>';
    }).join('') + '</select>';
  }
  /* A pill that wraps one radio/checkbox input; the label mirrors the input state. */
  function opt(name, value, label, emoji, on, type) {
    return '<label class="c2-opt' + (on ? ' on' : '') + '">' +
      '<input type="' + (type || 'radio') + '" name="' + name + '" value="' + h(value) + '"' +
      (on ? ' checked' : '') + '>' +
      (emoji ? '<span class="em">' + emoji + '</span>' : '') +
      '<span>' + h(label || value) + '</span></label>';
  }
  function optRow(name, items, value) {
    return items.map(function (x) { return opt(name, x, x, '', x === value); }).join('');
  }
  function monthAnchor() {
    if (!anchor) anchor = (typeof MONTH_ANCHOR === 'string' && /^\d{4}-\d{2}$/.test(MONTH_ANCHOR))
      ? MONTH_ANCHOR : todayISO().slice(0, 7);
    return anchor;
  }
  function shiftMonth(step) {
    var p = monthAnchor().split('-'), y = +p[0], m = +p[1];
    var d = new Date(y, m - 1 + step, 1);
    anchor = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0');
  }

  /* ---------- dot ring ---------- */
  function daysBetween(a, b) {
    if (!a || !b) return 0;
    return Math.round((new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00')) / 86400000);
  }

  function ringMessage(s, today) {
    var latest = s.periods[0], p = s.prediction;
    if (!latest) return ['还没有经期记录', '记录开始日期后再查看'];
    var days = daysBetween(latest.start_date, today) + 1;
    var detail = '自记录开始第 ' + days + ' 天';
    if (!s.settings.prediction_enabled) return ['预估已关闭', detail];
    if (p.expired) return ['预估区间已过', detail];
    if (p.available && today >= p.window_start) return ['阶段待确认', detail];
    if (s.periods.length === 1 && !s.settings.typical_cycle) return ['需要更多经期记录', detail];
    return ['暂时无法推算阶段', detail];
  }

  function ring(s) {
    var cx = 150, cy = 150, r = 110, t = s.today, today = todayISO();
    var total = t ? daysBetween(t.period_start, t.next_start) : 0;
    var current = t ? t.cycle_day - 1 : -1;
    var angle = function (day) { return day / total * 2 * Math.PI - Math.PI / 2; };
    var shapes = '<circle class="c2-track" cx="150" cy="150" r="' + r + '"/>';
    var message = ringMessage(s, today);
    if (total > 0) {
      var duration = Math.min(t.duration_used, total);
      var ovulation = daysBetween(t.period_start, t.ovulation);
      // Match phase_of(): recorded duration takes precedence when phases overlap.
      var ovulationStart = Math.min(Math.max(ovulation, duration), total);
      var ovulationEnd = Math.min(Math.max(ovulation + 2, ovulationStart), total);
      var segments = [0, duration, ovulationStart, ovulationEnd, total];
      PHASES.forEach(function (phase, i) {
        var start = segments[i], end = segments[i + 1];
        if (start === end) return;
        var cls = 'c2-arc ' + phase;
        shapes += end - start === total
          ? '<circle class="' + cls + '" cx="150" cy="150" r="' + r + '"/>'
          : '<path class="' + cls + '" d="' + arcPath(cx, cy, r, angle(start - 0.5), angle(end - 0.5)) + '"/>';
        for (var day = start; day < end; day++) {
          var a = angle(day), x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
          var state = day === current ? ' is-today' : day > current ? ' future' : '';
          shapes += '<circle class="c2-dot ' + phase + state + '" cx="' + x.toFixed(2) + '" cy="' + y.toFixed(2) + '" r="4"/>';
        }
      });
      message = ['预估' + (PHASE_LABEL[t.phase] || t.label), '周期第 ' + t.cycle_day + ' 天'];
    }
    var weekday = WD_FULL[new Date(today + 'T00:00:00').getDay()];
    var date = '今天 ' + fmtD(today) + ' · ' + weekday;
    return '<svg class="c2-ring" viewBox="0 0 300 300" role="img" aria-label="' +
      h(date + '，' + message.join('，')) + '">' + shapes +
      '<text class="c2-ring-date" x="150" y="115">' + h(date) + '</text>' +
      '<text class="c2-ring-phase" x="150" y="151">' + h(message[0]) + '</text>' +
      '<text class="c2-ring-day" x="150" y="177">' + h(message[1]) + '</text></svg>';
  }

  /* ---------- cycle overview ---------- */
  var MARK_LABEL = {period: '已记录经期', predicted: '经期预估', 'open-start': '经期开始 · 结束待确认'};

  function periodOn(s, day) {
    return s.periods.find(function (r) {
      return r.start_date <= day && day <= (r.end_date || r.start_date);
    });
  }

  function markFor(s, day, period) {
    // Only the recorded start is certain if the end has not been entered.
    if (period) return period.end_date ? 'period' : 'open-start';
    var p = s.prediction;
    if (p.available && p.window_start <= day && day <= p.window_end) return 'predicted';
    return '';
  }

  function legend(ringMode) {
    var items = ringMode
      ? [['period', '行经期'], ['follicular', '卵泡期'], ['ovulation', '排卵期'], ['luteal', '黄体期']]
      : [['period', '已记录经期'], ['predicted', '经期预估'], ['open-start', '结束待确认'], ['note', '有当天记录']];
    return '<div class="c2-legend">' + items.map(function (it) {
      return '<span><i class="' + it[0] + '" aria-hidden="true"></i>' + it[1] + '</span>';
    }).join('') + '</div>';
  }

  function dateLabel(iso) {
    var p = iso.split('-');
    return (+p[0]) + '年' + (+p[1]) + '月' + (+p[2]) + '日';
  }

  function daySummary(s, day) {
    var note = (s.notes || []).some(function (r) { return r.date === day; });
    var temp = (s.bbt || []).some(function (r) { return r.date === day; });
    var period = periodOn(s, day);
    var future = day > todayISO();
    var bits = [MARK_LABEL[markFor(s, day, period)], note ? '已记感受' : '', temp ? '已记体温' : ''].filter(Boolean);
    return '<div class="c2-day-summary" aria-live="polite"><div><strong>' + dateLabel(day) +
      (day === todayISO() ? ' · 今天' : '') + '</strong><span>' +
      (bits.join(' · ') || (future ? '只能记录今天或之前的日期' : '当天还没有记录')) + '</span></div>' +
      (future ? '' : '<div class="c2-day-actions">' +
        (period ? btn('改经期', 'edit', 'data-id="' + period.id + '"') : '') +
        btn(note || temp ? '编辑当天' : '记这一天', 'record-day', 'data-date="' + day + '"') + '</div>') + '</div>';
  }

  function calendarGrid(s) {
    var p = monthAnchor().split('-'), year = +p[0], month = +p[1];
    var lead = (new Date(year, month - 1, 1).getDay() + 6) % 7;
    var days = new Date(year, month, 0).getDate();
    var noted = {}, today = todayISO();
    var isCurrent = today.slice(0, 7) === monthAnchor();
    var picked = selectedDate && selectedDate.slice(0, 7) === monthAnchor()
      ? selectedDate : isCurrent ? today : monthAnchor() + '-01';
    (s.notes || []).concat(s.bbt || []).forEach(function (n) { noted[n.date] = true; });
    var cells = WD.map(function (w) { return '<div class="c2-cal-head" aria-hidden="true">' + w + '</div>'; }).join('');
    for (var i = 0; i < lead; i++) cells += '<div class="c2-cal-blank" aria-hidden="true"></div>';
    for (var d = 1; d <= days; d++) {
      var iso = monthAnchor() + '-' + String(d).padStart(2, '0');
      var kind = markFor(s, iso, periodOn(s, iso));
      var isToday = iso === today;
      var visualKind = isToday && kind === 'open-start' ? '' : kind;
      var label = dateLabel(iso) + '，' + WD_FULL[(lead + d) % 7] +
        (iso === today ? '，今天' : '') + (kind ? '，' + MARK_LABEL[kind] : '') + (noted[iso] ? '，有当天记录' : '');
      cells += '<button type="button" class="c2-cal-cell' +
        (visualKind ? ' c2-day-' + visualKind : '') + '" data-c2="day" data-date="' + iso + '" aria-label="' + h(label) +
        '" aria-pressed="' + (iso === picked) + '"' + (isToday ? ' aria-current="date"' : '') + '>' +
        (isToday ? '<span class="c2-today-tag" aria-hidden="true">今</span>' : '') + '<span class="d">' + d + '</span>' + (noted[iso] ? '<i class="c2-m-note" aria-hidden="true"></i>' : '') + '</button>';
    }
    return '<div class="c2-cal" role="group" aria-label="' + year + '年' + month + '月周期日历">' + cells + '</div>' +
      legend(false) + daySummary(s, picked);
  }

  function calendarCard(s) {
    var p = monthAnchor().split('-');
    return '<section class="c2-card c2-calendar-card" aria-labelledby="c2CalendarTitle">' +
      '<div class="c2-section-head"><div><span class="c2-label" aria-hidden="true">CALENDAR</span>' +
      '<h2 id="c2CalendarTitle">周期日历</h2></div>' +
      '<div class="c2-view-tabs" role="group" aria-label="视图">' +
      ['calendar', 'ring'].map(function (mode) {
        return '<button type="button" data-c2="view-mode" data-mode="' + mode + '" aria-pressed="' +
          (viewMode === mode) + '">' + (mode === 'calendar' ? '月历' : '周期环') + '</button>';
      }).join('') + '</div></div>' +
      (viewMode === 'calendar'
        ? '<div class="c2-month-row"><div class="c2-month-nav">' +
          '<button type="button" class="c2-month-arrow" data-c2="month-prev" aria-label="上个月">‹</button>' +
          '<button type="button" class="c2-month" data-c2="month-pick">' + p[0] + ' 年 ' + (+p[1]) + ' 月 <span aria-hidden="true">⌄</span></button>' +
          '<button type="button" class="c2-month-arrow" data-c2="month-next" aria-label="下个月">›</button></div>' +
          (monthAnchor() !== todayISO().slice(0, 7) ? btn('回到本月', 'month-today') : '') + '</div>' + calendarGrid(s)
        : '<div class="c2-view-ring"><div class="c2-ring-wrap">' + ring(s) + '</div>' +
          (s.today ? legend(true) + '<p class="c2-ring-caption">实心点表示已过去的日期，描边点为今天，空心点为之后的日期。阶段为辅助推算。</p>' : '') + '</div>') + '</section>';
  }

  function hero(s) {
    var today = todayISO(), active = s.active, latest = (s.periods || [])[0];
    var title = '从一次记录开始', desc = '记下经期第一天，慢慢看见自己的节奏。';
    var primary = '记录经期开始', action = 'start';
    if (active) {
      var elapsed = daysBetween(active.start_date, today) + 1;
      title = '结束待确认';
      desc = fmtFull(active.start_date) + ' 开始' + (s.status && s.status.day ? ' · 距开始已 ' + elapsed + ' 天（含当天）' : '') + '。';
      primary = '填写结束日期'; action = 'finish';
    } else if (latest) {
      var onPeriod = latest.start_date <= today && today <= latest.end_date;
      title = onPeriod ? '经期第 ' + (daysBetween(latest.start_date, today) + 1) + ' 天'
        : s.today ? '预估' + (PHASE_LABEL[s.today.phase] || s.today.label) : '今天的周期';
      desc = '最近一次经期 · ' + fmtRange(latest.start_date, latest.end_date);
      if (onPeriod) { primary = '编辑本次经期'; action = 'edit'; }
    }
    var attrs = action === 'edit' ? ' data-id="' + latest.id + '"' : '';
    return '<section class="c2-card c2-hero" aria-labelledby="c2TodayTitle">' +
      '<svg class="c2-hero-art" viewBox="0 0 168 148" fill="none" aria-hidden="true">' +
      '<circle cx="105" cy="73" r="57"/><circle cx="105" cy="73" r="42" stroke-dasharray="2 6"/>' +
      '<path d="M105 109c-16-13-25-24-25-38 0-15 13-24 25-35 12 11 25 20 25 35 0 14-9 25-25 38Z M105 109c0-25 10-41 26-47 M105 109c0-25-10-41-26-47"/>' +
      '<circle cx="64" cy="111" r="4" class="c2-art-dot"/><circle cx="142" cy="33" r="5" class="c2-art-dot"/>' +
      '<path d="M51 34v14m-7-7h14m95 70v12m-6-6h12"/></svg>' +
      '<div class="c2-hero-inner"><div class="c2-hero-meta"><span class="c2-label">周期手账 · 今天 ' + fmtD(today) + '</span>' +
      '<span class="c2-scope">' + (s.shared_record ? '共同记录' : '个人记录') + '</span></div>' +
      '<h1 id="c2TodayTitle">' + h(title) + '</h1><p>' + h(desc) + '</p>' +
      '<div class="c2-actions"><button type="button" class="c2-primary" data-c2="' + action + '"' + attrs + '>' + primary + '</button>' +
      '<button type="button" class="c2-quiet" data-c2="record" data-tab="symptom">记录今天<span aria-hidden="true"> ↗</span></button></div></div></section>';
  }

  function predictionCard(s) {
    var p = s.prediction || {}, count = (s.periods || []).length;
    var title, desc, status;
    if (p.available) {
      status = 'available';
      title = fmtRange(p.window_start, p.window_end);
      desc = (p.basis || '') + ' · 预计持续约 ' + p.estimated_duration + ' 天';
    } else if (!s.settings.prediction_enabled) {
      status = 'off'; title = '预估已关闭'; desc = '照常记录，日历仍会显示实际经期。';
    } else if (p.expired) {
      status = 'expired'; title = '上次预估区间已过'; desc = '周期信息待确认；未记录不代表推迟。';
    } else if (!count) {
      status = 'empty'; title = '等待第一条经期记录'; desc = '有了经期日期，才能逐步估算下次区间。';
    } else if (count === 1 && !s.settings.typical_cycle) {
      status = 'insufficient'; title = '还需要一次经期记录'; desc = '有两次开始日期，就能算出一次实际周期间隔。';
    } else {
      status = 'unavailable'; title = '暂时无法预估'; desc = p.reason || '继续记录日期后再查看。';
    }
    return '<section class="c2-card c2-forecast" data-prediction="' + status + '" aria-labelledby="c2ForecastTitle">' +
      '<div class="c2-forecast-kicker"><span class="c2-label">下次经期预估</span>' +
      (p.available ? '<span class="c2-conf">' + h(CONF_LABEL[p.confidence] || '') + '</span>' : '') + '</div>' +
      '<h2 id="c2ForecastTitle">' + h(title) + '</h2><p>' + h(desc) + '</p>' +
      (p.available ? '<div class="c2-forecast-note">日期为估算，不用于避孕或诊断。</div>' : '') + '</section>';
  }

  function historyCard(s) {
    var recent = (s.periods || []).slice(0, 3);
    return '<section class="c2-card c2-recent" aria-labelledby="c2HistoryTitle">' +
      '<div class="c2-section-head"><div><span class="c2-label" aria-hidden="true">HISTORY</span><h2 id="c2HistoryTitle">近期经期</h2></div>' +
      btn('＋ 补录', 'add') + '</div>' +
      (recent.length ? recent.map(function (r) {
        var text = fmtFull(r.start_date) + (r.end_date && r.start_date !== r.end_date ? ' — ' + fmtD(r.end_date) : '');
        return '<button type="button" class="c2-recent-row" data-c2="edit" data-id="' + r.id + '">' +
          '<i class="' + (r.end_date ? 'period' : 'open-start') + '" aria-hidden="true"></i><span><strong>' + h(text) + '</strong><small>' +
          (r.end_date ? '共 ' + (daysBetween(r.start_date, r.end_date) + 1) + ' 天' : '结束待确认') + '</small></span>' +
          '<span aria-hidden="true">›</span></button>';
      }).join('') + btn('查看全部', 'record', 'data-tab="period"')
        : '<p class="c2-muted">还没有经期记录。有之前的日期，可以直接补录。</p>') + '</section>';
  }

  function insightsCard(s) {
    var ins = s.insights || {items: []};
    var p = s.prediction || {};
    return '<details class="c2-card c2-details" id="c2Insights"><summary>周期洞察</summary>' +
      (p.available && p.fertile_start && p.fertile_end >= todayISO()
        ? '<p>预估易孕区间：' + h(fmtRange(p.fertile_start, p.fertile_end)) + '。仅为参考，不用于避孕。</p>' : '') +
      (!ins.items.length ? '<p class="c2-muted">记录满三个周期间隔后，这里会显示规律性等统计。</p>'
        : '<ul class="c2-insights">' + ins.items.map(function (i) {
          return '<li class="' + h(i.level) + '"><i class="c2-dot" aria-hidden="true"></i>' + h(i.text) + '</li>';
        }).join('') + '</ul><p class="c2-muted">' + h(ins.disclaimer) + '</p>') + '</details>';
  }

  /* ---------- record sheet ---------- */
  var TABS = [['period', '🩸 经期'], ['sex', '💕 性行为'], ['symptom', '🩹 身体症状'], ['mood', '😊 心情记录'], ['more', '📋 更多记录']];

  function tabBar() {
    return '<div class="c2-tabs">' + TABS.map(function (t) {
      return '<button type="button" class="c2-tab' + (recordTab === t[0] ? ' on' : '') +
        '" data-c2="tab" data-tab="' + t[0] + '" aria-pressed="' + (recordTab === t[0]) + '">' + t[1] + '</button>';
    }).join('') + '</div>';
  }

  function panelHead(emoji, title, extra) {
    return '<div class="c2-empty-head"><span class="c2-ico-chip">' + emoji + '</span>' +
      '<h3>' + h(title) + '</h3>' + (extra || '') + '</div>';
  }

  function periodPanel(s) {
    var rows = (s.periods || []).map(function (r) {
      return '<div class="c2-history"><div><strong>' + h(fmtFull(r.start_date)) + '</strong>' +
        '<p class="c2-muted">' + (r.end_date ? '结束于 ' + h(fmtFull(r.end_date)) : '结束待确认') + '</p></div>' +
        '<div class="c2-actions">' + btn('编辑', 'edit', 'data-id="' + r.id + '"') +
        btn('删除', 'delete-period', 'data-id="' + r.id + '"') + '</div></div>';
    }).join('');
    return '<section class="c2-card">' + panelHead('🩸', '经期记录') +
      '<div class="c2-actions" style="margin-top:0">' +
      btn(s.active ? '确认结束日期' : '经期开始了', s.active ? 'finish' : 'start') +
      btn('补录历史', 'add') + '</div>' +
      '<div style="margin-top:8px">' + (rows || '<p class="c2-muted">开始和结束时各记一下就好。</p>') + '</div>' +
      '</section>';
  }

  function symptomPanel(d) {
    return '<section class="c2-card">' + panelHead('🩹', '身体症状') +
      '<div class="c2-group-label">经量</div>' +
      '<div class="c2-chip-grid">' + optRow('flow', ['极少', '少', '中', '多', '很多'], d.flow) + '</div>' +
      '<div class="c2-group-label">疼痛程度</div>' +
      '<div class="c2-chip-grid">' + optRow('pain', ['无', '轻', '中', '重'], d.pain) + '</div>' +
      '<div class="c2-group-label">身体感受 <span style="font-weight:600">可多选</span></div>' +
      '<div class="c2-chip-grid">' + SYMPTOMS.map(function (x) {
        return opt('symptoms', x, x, SYMPTOM_EMOJI[x] || '', (d.symptoms || []).indexOf(x) >= 0, 'checkbox');
      }).join('') + '</div>' +
      '<div class="c2-group-label">对日常活动的影响</div>' +
      '<div class="c2-chip-grid">' + optRow('activity', ['几乎无影响', '轻度影响', '明显影响', '严重影响'], d.activity) + '</div>' +
      '<div class="c2-group-label">处理方式</div>' +
      field('缓解方式（选填）', input('relief', d.relief, 'maxlength="50" placeholder="例如：休息、热敷"')) +
      field('用药记录（选填）', input('medication', d.medication, 'maxlength="50" placeholder="只记录事实，不推荐用药"')) +
      '<div class="c2-group-label">缓解效果</div>' +
      '<div class="c2-chip-grid">' + optRow('effectiveness', ['有效', '部分有效', '无效'], d.effectiveness) + '</div>' +
      '</section>';
  }

  function moodPanel(d) {
    var picked = MOODS.indexOf(d.mood) >= 0;
    return '<section class="c2-card">' + panelHead('😊', '心情') +
      '<div class="c2-chip-grid">' + MOODS.map(function (x, i) {
        return opt('mood', x, x, MOOD_EMOJI[i], d.mood === x);
      }).join('') + '</div>' +
      field('其他心情（选填）', input('mood_other', picked ? '' : (d.mood || ''),
        'maxlength="20" placeholder="用几个字描述"')) +
      '</section>';
  }

  function sexPanel(d) {
    return '<section class="c2-card">' + panelHead('💕', '性行为记录') +
      '<div class="c2-chip-grid">' + SEX_OPTIONS.map(function (x) {
        return opt('sex', x, x, SEX_EMOJI[x], d.sex === x);
      }).join('') + '</div>' +
      '<p class="c2-muted">按天记录有无与保护方式。</p>' +
      '</section>';
  }

  function morePanel(s) {
    var temps = s.bbt || [];
    var last = temps.length ? temps[temps.length - 1] : null;
    var day = recordDate || todayISO();
    var mine = temps.find(function (x) { return x.date === day; });
    return '<section class="c2-card">' +
      panelHead('🌡️', '基础体温', '<small>可选，用于校准黄体期</small>') +
      '<p class="c2-muted">' + (last
        ? '已记录 ' + temps.length + ' 天，最近 ' + h(last.date) + ' 为 ' + last.temp_c + ' ℃'
        : '还没有基础体温记录。') +
      (s.insights && s.insights.luteal_calibrated
        ? '<br>已据体温反推黄体期约 ' + s.insights.luteal_calibrated + ' 天。' : '') +
      '<br>每天固定时间测得才有意义；不做避孕或排卵确认用途。</p>' +
      field('体温（℃）', input('temp_c', mine ? mine.temp_c : '',
        'type="number" step="0.01" min="35" max="38.5" inputmode="decimal" placeholder="例如 36.55"')) +
      '<div class="c2-actions">' + btn('保存体温', 'save-bbt') +
      (mine ? btn('删除这天体温', 'delete-bbt') : '') + '</div>' +
      '</section>' +
      '<section class="c2-card">' + panelHead('📝', '备注') +
      field('想记的话（选填）', '<textarea name="note" maxlength="500" rows="3" placeholder="写给这一天"></textarea>') +
      '<p class="c2-muted">' + (s.shared_record ? '备注属于共同记录，双方都可查看。' : '备注属于当前个人记录。') + '</p>' +
      '</section>';
  }

  function recordPage(s) {
    var day = recordDate || todayISO();
    var d = (s.notes || []).find(function (r) { return r.date === day; }) || {};
    var panel = recordTab === 'period' ? periodPanel(s)
      : recordTab === 'sex' ? sexPanel(d)
      : recordTab === 'mood' ? moodPanel(d)
      : recordTab === 'more' ? morePanel(s)
      : symptomPanel(d);
    return '<header class="c2-record-head"><button type="button" class="c2-back" data-c2="back" aria-label="返回周期总览">‹</button>' +
      '<div><span class="c2-label">' + (day === todayISO() ? '今天 · ' : '') + dateLabel(day) + '</span>' +
      '<h1>' + (recordTab === 'period' ? '经期历史' : '记录这一天') + '</h1></div>' +
      '<span class="c2-scope">' + (s.shared_record ? '共同记录' : '个人记录') + '</span></header>' +
      tabBar() + (recordTab === 'period' ? panel :
        '<form id="cycle2DayForm" method="post"><input type="hidden" name="date" value="' + h(day) + '">' + panel + '</form>' +
        '<div class="c2-savebar"><button type="submit" id="c2SaveBtn" form="cycle2DayForm"' +
          (recordTab === 'more' ? ' formnovalidate>保存备注' : '>保存当天记录') + '</button></div>') +
      modalsHTML();
  }

  function settingsCard(s) {
    var n = s.preferences || {}, p = s.settings || {};
    var source = s.shared
      ? '<p class="c2-muted">周期预估由这份记录的所有者设置。你可以单独调整自己的提醒。</p>'
      : '<form id="cycle2SettingsForm" method="post"><div class="c2-grid">' +
        field('典型周期天数（可不填）', input('typical_cycle', p.typical_cycle, 'type="number" min="15" max="60"')) +
        field('典型经期天数（可不填）', input('typical_duration', p.typical_duration, 'type="number" min="1" max="30"')) +
        '</div>' + check('prediction_enabled', '开启辅助预估', p.prediction_enabled) +
        '<div class="c2-actions"><button class="p-btn pri">保存预估设置</button></div></form>';
    return '<details class="c2-card c2-details" id="c2Settings"><summary>提醒与数据设置</summary>' +
      '<p>' + (s.shared_record ? '这份记录由双方共同查看和编辑。' : '当前显示你自己的周期记录。') +
      '提醒偏好只对当前账号生效。</p>' +
      '<h3>我的提醒</h3><form id="cycle2PreferencesForm" method="post">' +
      check('enabled', '接收周期提醒', n.enabled) +
      check('upcoming', '预估经期将至', n.upcoming) +
      check('pms', '可能临近经前期', n.pms !== 0) +
      check('started', '经期开始时提醒我', n.started) +
      check('discomfort', '中度或以上疼痛，或明显影响活动时提醒我', n.discomfort) +
      check('pending', '预估区间已过，提醒我确认', n.pending) +
      field('提前天数', input('advance_days', String(n.advance_days == null ? 2 : n.advance_days),
        'type="number" min="0" max="7" required')) +
      check('notice_details', '允许通知显示详情', n.notice_details) +
      '<div class="c2-actions"><button class="p-btn pri">保存我的提醒</button></div></form>' +
      '<div class="c2-setting-group"><h3>周期预估</h3>' + source + '</div>' +
      '<div class="c2-setting-group"><h3>数据管理</h3><p class="c2-muted">导出仅包含你名下的数据。' +
      (s.shared_record ? '删除会清空当前共同记录。' : '删除会清空当前个人记录。') +
      '已导出的副本需要另外处理。</p><div class="c2-actions">' + btn('导出我的数据', 'export') +
      btn('删除当前周期数据', 'delete-all') + '</div></div></details>';
  }

  function modalsHTML() {
    return '<div class="overlay" id="cycle2PeriodModal"><div class="modal" role="dialog" aria-modal="true">' +
      '<div class="mhead"><div class="mhead-left"><h3 id="cycle2PeriodTitle">记录实际经期</h3></div></div><form id="cycle2PeriodForm" method="post">' +
      '<input type="hidden" name="id">' +
      field('开始日期', dt('start_date', todayISO(), true, todayISO())) +
      field('结束日期（进行中留空）', dt('end_date', '', false, todayISO())) +
      '<p class="c2-muted" id="cycle2PeriodHint">只记录实际发生的日期。</p>' +
      '<div class="c2-actions foot"><button type="button" class="p-btn" data-c2="close-period">取消</button>' +
      '<button class="p-btn pri">保存记录</button></div></form></div></div>';
  }

  /* ---------- composition ---------- */
  function own(s) {
    return '<div class="c2-dashboard">' + hero(s) + predictionCard(s) + calendarCard(s) +
      '<div class="c2-sidebar">' + historyCard(s) + insightsCard(s) + settingsCard(s) + '</div></div>' +
      '<p class="c2-disclaimer">' + h(s.disclaimer) + '</p>';
  }

  function paintPage(s, savedAction) {
    var box = $('#cycle2Content');
    if (!box) return;
    var shell = document.querySelector('.page-container.c2-page');
    if (shell) shell.classList.toggle('has-savebar', !!recordTab && recordTab !== 'period');
    if (recordTab) {
      /* 记录页横滑页签条随重绘整块重建，会把滚动位置弹回最左：重建前后保持原位 */
      var prevTabs = box.querySelector('.c2-tabs');
      var segX = prevTabs ? prevTabs.scrollLeft : 0;
      var form = $('#cycle2DayForm');
      // Saving either field must leave the other field's draft alone.
      var keep = savedAction === 'day' ? 'temp_c' : /^bbt(?:\/delete)?$/.test(savedAction) ? 'note' : '';
      var draft = keep && form && form.elements.date.value === recordDate && form.elements[keep]
        ? form.elements[keep].value : null;
      box.innerHTML = recordPage(s);
      var newTabs = box.querySelector('.c2-tabs');
      if (newTabs) newTabs.scrollLeft = segX;
      fillDay(recordDate || todayISO());
      if (draft !== null) $('#cycle2DayForm').elements[keep].value = draft;
      bindForms();
      return;
    }
    var open = Array.from(box.querySelectorAll('details[open][id]'), function (el) { return el.id; });
    box.innerHTML = own(s) + modalsHTML();
    open.forEach(function (id) { var el = document.getElementById(id); if (el) el.open = true; });
    bindForms();
  }

  function fillDay(date) {
    var f = $('#cycle2DayForm');
    if (!f || !state) return;
    var d = state.notes.find(function (r) { return r.date === date; }) || {};
    ['flow', 'pain', 'activity', 'mood', 'relief', 'medication', 'effectiveness', 'note'].forEach(function (k) {
      if (f.elements[k] && f.elements[k].type !== 'radio' && f.elements[k].type !== 'checkbox') {
        f.elements[k].value = d[k] || '';
      }
    });
    if (f.elements.mood_other && MOODS.indexOf(d.mood) < 0) f.elements.mood_other.value = d.mood || '';
    f.querySelectorAll('[name="symptoms"]').forEach(function (x) {
      x.checked = (d.symptoms || []).indexOf(x.value) >= 0;
      var lab = x.closest('.c2-opt');
      if (lab) lab.classList.toggle('on', x.checked);
    });
    f.querySelectorAll('[name="sex"]').forEach(function (x) {
      x.checked = x.value === (d.sex || '');
      var lab = x.closest('.c2-opt');
      if (lab) lab.classList.toggle('on', x.checked);
    });
  }

  /* ---------- data ---------- */
  function clearLegacySnapshots() {
    try {
      Object.keys(localStorage).forEach(function (key) {
        if (key.indexOf('sched_cycle2_') === 0) localStorage.removeItem(key);
      });
    } catch (e) {}
  }
  clearLegacySnapshots();

  function request(quiet, savedAction) {
    var uid = ME && ME.uid;
    if (!uid || CURRENT_VIEW !== 'cycle2') return Promise.resolve();
    var month = monthAnchor();
    if (inflight && inflight.uid === uid && inflight.month === month && (quiet || !inflight.quiet)) return inflight.req;
    var version = ++sequence;
    var req = api('/api/cycle2?month=' + month, {cache: 'no-store'}).then(function (s) {
      if (version !== sequence || !ME || uid !== ME.uid || month !== monthAnchor() || CURRENT_VIEW !== 'cycle2') return;
      if (quiet && (recordTab || $('#cycle2PeriodModal.show') || $('#c2Settings :focus') ||
        document.body.hasAttribute('data-cycle2-saving') || JSON.stringify(s) === JSON.stringify(state))) return;
      state = s; owner = uid;
      paintPage(s, savedAction);
      document.body.setAttribute('data-cycle2-ready', String(version));
    }).catch(function (e) {
      if (quiet || version !== sequence || CURRENT_VIEW !== 'cycle2') return;
      var box = $('#cycle2Content');
      if (box) box.innerHTML = '<div class="c2-card c2-loading"><p>' + h(e.message) + '</p>' + btn('重新加载', 'retry') + '</div>';
    }).finally(function () {
      if (inflight && inflight.req === req) inflight = null;
    });
    inflight = {uid: uid, month: month, req: req, quiet: quiet};
    return req;
  }

  function mount() {
    if (!ME || CURRENT_VIEW !== 'cycle2') {
      if (state || inflight) scrub();
      return;
    }
    if (!document.hidden) request();
  }

  function save(action, data, el) {
    if (document.body.hasAttribute('data-cycle2-saving')) return Promise.resolve();
    if (el) el.disabled = true;
    document.body.setAttribute('data-cycle2-saving', '1');
    return api('/api/cycle2/' + action, {method: 'POST', body: data, cache: 'no-store'}).then(function () {
      toast('已保存');
      inflight = null; ++sequence;
      if (action === 'period') closeAllModals();
      return request(false, action);
    }).catch(function (e) { toast(e.message); }).finally(function () {
      document.body.removeAttribute('data-cycle2-saving');
      if (el) el.disabled = false;
    });
  }

  function edit(r, mode) {
    var f = $('#cycle2PeriodForm');
    if (!f || !r) return;
    f.elements.id.value = r.id || '';
    f.elements.start_date.value = r.start_date || (mode === 'add' ? '' : todayISO());
    f.elements.end_date.value = r.end_date || '';
    f.elements.end_date.required = mode === 'finish';
    if (mode === 'finish') f.elements.end_date.setAttribute('aria-required', 'true');
    else f.elements.end_date.removeAttribute('aria-required');
    $('#cycle2PeriodTitle').textContent = {start: '记录经期开始', add: '补录经期', finish: '填写结束日期'}[mode] || '编辑经期';
    $('#cycle2PeriodHint').textContent = mode === 'finish' ? '请填写实际结束的那一天。' :
      '只记录实际发生的日期。结束日期未确认可留空。';
    openModal('#cycle2PeriodModal');
    f.elements[mode === 'finish' ? 'end_date' : 'start_date'].focus();
  }

  document.addEventListener('change', function (e) {
    var el = e.target;
    if (el.matches('#cycle2DayForm [name="date"]')) fillDay(el.value);
    if (el.matches('#cycle2DayForm input[type="radio"]')) {
      document.querySelectorAll('#cycle2DayForm .c2-opt').forEach(function (lab) {
        var inp = lab.querySelector('input');
        if (inp && inp.type === 'radio' && inp.name === el.name) lab.classList.toggle('on', inp.checked);
      });
    } else if (el.matches('#cycle2DayForm input[type="checkbox"]')) {
      var lab = el.closest('.c2-opt');
      if (lab) lab.classList.toggle('on', el.checked);
    }
  });

  function bindForms() {
    ['cycle2PeriodForm', 'cycle2DayForm', 'cycle2PreferencesForm', 'cycle2SettingsForm'].forEach(function (id) {
      var f = document.getElementById(id);
      if (f) f.addEventListener('submit', onSubmit);
    });
  }

  function onSubmit(e) {
    var f = e.currentTarget || e.target;
    var id = f.getAttribute('id') || '';
    if (!/^cycle2/.test(id)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var d = Object.fromEntries(new FormData(f).entries()), action;
    if (id === 'cycle2PeriodForm') {
      // The shared date picker marks its inputs readonly, so required does not block submit.
      if (!d.start_date || (f.elements.end_date.required && !d.end_date)) {
        toast(!d.start_date ? '请选择开始日期' : '请选择实际结束日期');
        return;
      }
      if (d.start_date > todayISO() || d.end_date > todayISO() || (d.end_date && d.end_date < d.start_date)) {
        toast('请填写有效的起止日期，不能是未来日期');
        return;
      }
      action = 'period';
      if (d.id) d.id = Number(d.id); else delete d.id;
    } else if (id === 'cycle2DayForm') {
      action = 'day';
      /* Each tab is a partial view of one row, and the server replaces the whole
         row. Seed from what is stored, then apply only the fields this tab owns. */
      var day = f.elements.date.value;
      var stored = (state && state.notes.find(function (r) { return r.date === day; })) || {};
      var merged = {date: day};
      ['flow', 'pain', 'activity', 'mood', 'relief', 'medication', 'effectiveness', 'sex', 'note'].forEach(function (k) {
        if (f.elements[k]) merged[k] = f.elements[k].value;
        else if (stored[k]) merged[k] = stored[k];
      });
      merged.symptoms = f.querySelector('[name="symptoms"]')
        ? new FormData(f).getAll('symptoms') : (stored.symptoms || []);
      merged.note_shared = !!stored.note_shared;
      if (recordTab === 'mood' && f.elements.mood_other && f.elements.mood_other.value.trim()) {
        merged.mood = f.elements.mood_other.value.trim();
      }
      d = merged;
    } else if (id === 'cycle2PreferencesForm') {
      action = 'preferences';
      ['enabled', 'upcoming', 'pms', 'started', 'discomfort', 'pending', 'notice_details'].forEach(function (k) {
        d[k] = f.elements[k].checked;
      });
      d.advance_days = Number(d.advance_days);
    } else if (id === 'cycle2SettingsForm') {
      /* The settings form is only rendered to the record owner. */
      action = 'settings';
      var p = {};
      ['typical_cycle', 'typical_duration'].forEach(function (k) {
        p[k] = (d[k] === '' || d[k] == null) ? null : Number(d[k]);
      });
      p.prediction_enabled = f.elements.prediction_enabled.checked;
      d = p;
    } else return;
    save(action, d, e.submitter || f.querySelector('button.p-btn.pri'));
  }

  function openRecord(tab) {
    var entering = !recordTab;             // 双击日格/记录行不重复压记录条目
    recordTab = tab || recordTab || 'symptom';
    recordDate = recordDate || todayISO();
    if (state) {
      if (entering) {
        /* 记录页是周期页内的一层子页：压入历史，返回手势/键回到周期主页 */
        history.pushState({type: 'modal', id: 'cycle2-record', day: recordDate, tab: recordTab}, '');
      }
      paintPage(state);
    }
    window.scrollTo(0, 0);
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-c2]');
    if (!el) return;
    var a = el.dataset.c2;
    if (a === 'view-mode') {
      if (!state || viewMode === el.dataset.mode) return;
      viewMode = el.dataset.mode;
      paintPage(state);
      document.querySelector('[data-c2="view-mode"][aria-pressed="true"]').focus({preventScroll: true});
      return;
    }
    if (a === 'back') {
      e.preventDefault();
      /* 返回按钮与打开时压入的历史条目配对，交给 popstate 恢复周期主页 */
      if (history.state && history.state.type === 'modal' && history.state.id === 'cycle2-record') {
        history.back();
        return;
      }
      recordTab = null; recordDate = null;
      if (state) paintPage(state);
      window.scrollTo(0, 0);
      return;
    }
    if (a === 'month-pick') {
      e.preventDefault();
      openMonthPicker(monthAnchor(), function (ym) { anchor = ym; request(); });
      return;
    }
    if (a === 'month-today') {
      e.preventDefault();
      anchor = todayISO().slice(0, 7);
      request();
      return;
    }
    if (a === 'month-prev' || a === 'month-next') {
      e.preventDefault();
      shiftMonth(a === 'month-next' ? 1 : -1);
      request();
      return;
    }
    if (a === 'day' && state && state.month === monthAnchor()) {
      selectedDate = el.dataset.date;
      var prev = document.querySelector('.c2-cal-cell[aria-pressed="true"]');
      if (prev) prev.setAttribute('aria-pressed', 'false');
      el.setAttribute('aria-pressed', 'true');
      $('.c2-day-summary').outerHTML = daySummary(state, selectedDate);
      return;
    }
    if (a === 'tab') {
      recordTab = el.dataset.tab;
      history.replaceState(Object.assign({}, history.state, {tab: recordTab}), '');
      if (state) paintPage(state);
      return;
    }
    if (a === 'record' || a === 'record-day') {
      recordDate = el.dataset.date || todayISO();
      openRecord(el.dataset.tab || 'symptom');
      return;
    }
    if (a === 'retry') { request(); return; }
    if (a === 'save-bbt') {
      e.preventDefault();
      var bf = document.getElementById('cycle2DayForm');
      if (!bf || !bf.elements.temp_c) return;
      var val = bf.elements.temp_c.value;
      if (val === '') { toast('请先填写体温'); return; }
      if (!bf.elements.temp_c.reportValidity()) return;
      save('bbt', {date: recordDate || todayISO(), temp_c: Number(val)}, el);
      return;
    }
    if (!state || !ME || owner !== ME.uid) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (a === 'start' || a === 'add') edit({}, a);
    else if (a === 'finish') edit(state.active, a);
    else if (a === 'edit') edit(state.periods.find(function (r) { return r.id === Number(el.dataset.id); }));
    else if (a === 'close-period') closeAllModals();
    else if (a === 'delete-period') {
      uiConfirm({icon: '🩸', title: '删除这条已确认经期？',
        message: '身体感受另行保留。', danger: true, okText: '删除'}).then(function (ok) {
        if (ok) save('period/delete', {id: Number(el.dataset.id)}, el);
      });
    }
    else if (a === 'delete-bbt') save('bbt/delete', {date: recordDate || todayISO()}, el);
    else if (a === 'delete-all') {
      uiConfirm({icon: '🗑️', title: '删除全部经期数据？',
        message: state.shared_record ? '会清空这份共同记录，双方都无法再查看。' : '会清空当前个人记录。',
        danger: true, okText: '继续'})
        .then(function (ok1) {
          if (!ok1) return;
          return uiConfirm({icon: '⚠️', title: '不可从 App 撤销，确定删除？',
            message: '已导出的副本需要另外处理。', danger: true, okText: '删除'})
            .then(function (ok2) {
              if (ok2) save('delete-all', {confirm: 'DELETE_MY_CYCLE_DATA'}, el);
            });
        });
    }
    else if (a === 'export') {
      api('/api/cycle2/export', {cache: 'no-store'}).then(function (data) {
        var url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
        var link = document.createElement('a');
        link.href = url; link.download = 'my-cycle-data-v2.json'; link.click();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
        toast('文件包含健康信息，请妥善保管');
      }).catch(function (err) { toast(err.message); });
    }
  }, true);

  function emptyPage() {
    recordTab = null;
    recordDate = null;
    setAppHTML(renderTopBar() +
      '<div class="page-container c2-page"><main id="cycle2Content">' +
      '<div class="c2-card c2-loading" role="status">正在读取周期记录…</div></main></div>' + bottomNavHTML());
    if (!document.hidden && state && ME && owner === ME.uid && state.month === monthAnchor()) paintPage(state);
  }

  function scrub() {
    sequence++;
    inflight = null;
    state = null;
    owner = null;
    var box = $('#cycle2Content');
    if (box) {
      if ($('#cycle2PeriodModal.show')) closeAllModals(true);
      box.innerHTML = '<div class="c2-card c2-loading" role="status">周期记录已隐藏，返回后重新加载。</div>';
    } else {
      recordTab = null;
      recordDate = null;
      selectedDate = null;
    }
  }

  setInterval(function () {
    if (!ME || document.hidden || CURRENT_VIEW !== 'cycle2' || !state || recordTab ||
      document.body.hasAttribute('data-cycle2-saving') || $('#cycle2PeriodModal.show')) return;
    request(true);
  }, 15000);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) scrub(); else if (ME) mount();
  });

  return {mount: mount, renderPage: emptyPage,
    reopenRecord: function () {
      recordTab = history.state.tab || 'symptom';
      recordDate = history.state.day || todayISO();
      if (state) paintPage(state); else mount();
    }};
})();
