/* Cycle v2 UI. Layout follows the period screen: month card with a calendar/ring
   toggle, legend, summary row, then record rows and a full record sheet.
   Health state stays in memory only. */
var Cycle2 = (function () {
  'use strict';
  var state = null, owner = null, sequence = 0, selectedDate = null;
  var stateMonth = null, dirty = false, inflight = null, inflightMonth = null;
  var anchor = null, viewMode = 'calendar', recordTab = null, recordDate = null;
  var PHASES = ['menstrual', 'follicular', 'ovulation', 'luteal'];
  var PHASE_LABEL = {menstrual: '行经期', follicular: '卵泡期', ovulation: '排卵期', luteal: '黄体期'};
  var CONF_LABEL = {high: '可信度较高', medium: '可信度中等', low: '样本较少'};
  var WD = ['日', '一', '二', '三', '四', '五', '六'];
  var WD_FULL = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
  var MOODS = ['开心', '平静', '放松', '兴奋', '敏感', '焦虑', '烦躁', '低落'];
  var MOOD_EMOJI = ['🙂', '😌', '😊', '🤩', '🥺', '😰', '😤', '😔'];
  var SYMPTOMS = ['疲劳', '腹胀', '头痛', '腰酸', '乳房胀痛', '恶心', '抽筋', '浮肿',
                  '失眠', '眩晕', '腹泻', '便秘'];
  var SYMPTOM_EMOJI = {疲劳: '😪', 腹胀: '🫧', 头痛: '🤕', 腰酸: '🦴', 乳房胀痛: '😣',
                       恶心: '🤢', 抽筋: '⚡', 浮肿: '💧', 失眠: '🌙', 眩晕: '💫',
                       腹泻: '🚽', 便秘: '🚻'};
  function fmtD(iso) {
    if (!iso || !/^\d{4}-\d{2}-\d{2}$/.test(iso)) return iso || '';
    var p = iso.split('-');
    return (+p[1]) + '月' + (+p[2]) + '日';
  }
  function fmtRange(a, b) {
    if (!a || !b) return '';
    var pa = a.split('-'), pb = b.split('-');
    if (pa[0] !== pb[0]) return fmtD(a) + ' ～ ' + fmtD(b);
    return (+pa[1]) + '月' + (+pa[2]) + '日 ～ ' + (+pb[1]) + '月' + (+pb[2]) + '日';
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
    return '<input name="' + name + '" value="' + h(value || '') + '" ' + (attrs || '') + '>';
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
  function nameOf(uid) {
    var u = ((META && META.users) || []).find(function (x) { return x.uid === uid; });
    return u ? u.name : '伴侣';
  }
  function go() { history.pushState({type: 'view', v: 'cycle2'}, ''); CURRENT_VIEW = 'cycle2'; load(); window.scrollTo(0, 0); }
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

  /* ---------- page chrome ---------- */
  /* 与其他页面统一的页头卡（汪汪信箱/纪念日等同款 page-header-card），顶栏由 renderTopBar 提供。 */
  function c2HeaderHTML(title, desc, img) {
    return '<div class="page-header-card"><div>' +
      '<div class="page-header-title">' + title + '</div>' +
      '<div class="page-header-desc">' + desc + '</div></div>' +
      '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/' + img + '" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>';
  }

  /* ---------- dot ring ---------- */
  function daysBetween(a, b) {
    if (!a || !b) return 0;
    return Math.round((new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00')) / 86400000);
  }

  function phaseForFraction(f, t) {
    if (!t || !t.period_start || !t.next_start) return 'luteal';
    var total = daysBetween(t.period_start, t.next_start) || 1;
    var day = f * total;
    var dur = t.duration_used || 5;
    var ovu = daysBetween(t.period_start, t.ovulation);
    if (day < dur) return 'menstrual';
    if (day < ovu) return 'follicular';
    if (day < ovu + 2) return 'ovulation';
    return 'luteal';
  }

  function ring(s) {
    var cx = 150, cy = 150, r = 112, size = 300;
    var t = s.today;
    var empty = !t;
    var total = 0, dayIdx = 0;
    if (!empty && t.period_start && t.next_start) {
      total = daysBetween(t.period_start, t.next_start);
      dayIdx = Math.min(Math.max(t.cycle_day - 1, 0), Math.max(total - 1, 0));
    }
    var count = total >= 20 && total <= 60 ? total : 40;
    /* A shared payload carries the phase but no dates, so the ring cannot be sliced
       by day. Fill it with the single known phase instead of leaving it blank. */
    var sharedPhase = !empty && !total && PHASES.indexOf(t.phase) >= 0 ? t.phase : null;
    var els = '<defs><filter id="c2BadgeShadow" x="-40%" y="-40%" width="180%" height="180%">' +
      '<feDropShadow dx="0" dy="2" stdDeviation="3.5" flood-opacity="0.35"/></filter></defs>' +
      '<circle class="c2-track" cx="' + cx + '" cy="' + cy + '" r="' + r + '"/>';
    if (total) {
      /* Phase segments under the dots: the same four colours as the calendar legend,
         dimmed so the day dots and the today badge stay the loudest elements. */
      var dur = t.duration_used || 5;
      var ovu = Math.min(Math.max(daysBetween(t.period_start, t.ovulation), dur), total);
      var segs = [['menstrual', 0, dur], ['follicular', dur, ovu],
                  ['ovulation', ovu, Math.min(ovu + 2, total)],
                  ['luteal', Math.min(ovu + 2, total), total]];
      segs.forEach(function (sg) {
        if (sg[2] - sg[1] < 0.5) return;
        if (sg[2] - sg[1] >= total - 0.01) {
          els += '<circle class="c2-arc ' + sg[0] + '" cx="' + cx + '" cy="' + cy + '" r="' + r + '"/>';
        } else {
          var a0 = (sg[1] / total) * Math.PI * 2 - Math.PI / 2;
          var a1 = (sg[2] / total) * Math.PI * 2 - Math.PI / 2;
          els += '<path class="c2-arc ' + sg[0] + '" d="' + arcPath(cx, cy, r, a0, a1) + '"/>';
        }
      });
    }
    var dots = '';
    for (var i = 0; i < count; i++) {
      var a = (i / count) * Math.PI * 2 - Math.PI / 2;
      var x = cx + r * Math.cos(a), y = cy + r * Math.sin(a);
      var cls = 'c2-dot';
      if (total) cls += ' ' + phaseForFraction(i / count, t) + (i > dayIdx ? ' future' : '');
      else if (sharedPhase) cls += ' ' + sharedPhase;
      dots += '<circle class="' + cls + '" cx="' + x.toFixed(1) + '" cy="' + y.toFixed(1) + '" r="4.6"/>';
    }
    els += dots;
    var badge = '';
    if (!empty && total) {
      var ba = (dayIdx / total) * Math.PI * 2 - Math.PI / 2;
      var bx = cx + r * Math.cos(ba), by = cy + r * Math.sin(ba);
      var now = new Date();
      badge = '<g filter="url(#c2BadgeShadow)"><circle class="c2-badge-bg" cx="' + bx.toFixed(1) + '" cy="' + by.toFixed(1) + '" r="25"/></g>' +
        '<text class="c2-badge-wd" x="' + bx.toFixed(1) + '" y="' + (by - 4).toFixed(1) + '">' + WD_FULL[now.getDay()] + '</text>' +
        '<text class="c2-badge-d" x="' + bx.toFixed(1) + '" y="' + (by + 15).toFixed(1) + '">' + now.getDate() + '</text>';
    }
    var centre;
    if (empty) {
      centre = '<text class="c2-ring-phase" x="' + cx + '" y="' + (cy - 8) + '">还没有周期数据</text>' +
        '<text class="c2-ring-day" x="' + cx + '" y="' + (cy + 16) + '">请记录经期</text>';
    } else {
      var eta = '';
      if (t.next_start) {
        var left = daysBetween(todayISO(), t.next_start);
        if (left > 0) eta = '<text class="c2-ring-eta" x="' + cx + '" y="' + (cy + 38) + '">距下次经期 ' + left + ' 天</text>';
        else if (left === 0) eta = '<text class="c2-ring-eta" x="' + cx + '" y="' + (cy + 38) + '">预估今天到期</text>';
      }
      centre = '<text class="c2-ring-phase ' + h(t.phase) + '" x="' + cx + '" y="' + (cy - 12) + '">' +
          h(PHASE_LABEL[t.phase] || t.label) + '</text>' +
        '<text class="c2-ring-day" x="' + cx + '" y="' + (cy + 12) + '">周期第 ' + t.cycle_day + ' 天 · 经期第 ' + t.day + ' 天</text>' +
        eta;
    }
    return '<svg class="c2-ring" viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="' +
      (empty ? '暂无周期数据' : h(PHASE_LABEL[t.phase]) + '第' + t.day + '天') + '">' +
      els + badge + centre + '</svg>';
  }

  /* The legend names only what the visible face actually draws. */
  function legend(kind) {
    var items = kind === 'ring'
      ? [['period', '行经期'], ['ovulation', '排卵期'], ['follicular', '卵泡期'], ['luteal', '黄体期']]
      : [['period', '经期'], ['predicted pill-dash', '预测经期'], ['fertile pill-fill', '排卵期'],
         ['ovulation gear', '排卵日'], ['note', '有记录']];
    return '<div class="c2-legend">' + items.map(function (it) {
      return '<span><i class="' + it[0] + '">' + (it[0].indexOf('gear') >= 0 ? '❋' : '') + '</i>' + it[1] + '</span>';
    }).join('') + '</div>';
  }

  /* ---------- month calendar ---------- */
  function calendarGrid(s) {
    var p = monthAnchor().split('-'), year = +p[0], month = +p[1];
    var first = new Date(year, month - 1, 1);
    var days = new Date(year, month, 0).getDate();
    var lead = first.getDay();
    var marks = {}, noted = {};
    (s.month_marks || []).forEach(function (m) { marks[m.date] = m.kind; });
    (s.notes || []).forEach(function (n) { noted[n.date] = 1; });
    var todayStr = todayISO();
    /* Huawei-style day chains: consecutive period / predicted / fertile days are
       joined into one scalloped capsule by drawRunChains(); the cell only tags
       which run it belongs to. */
    function runOf(kind) {
      return kind === 'fertile' || kind === 'ovulation' ? 'fertile'
        : kind === 'period' ? 'period'
        : kind === 'predicted' ? 'pred' : '';
    }
    var cells = WD.map(function (w) { return '<div class="c2-cal-head">' + w + '</div>'; }).join('');
    for (var i = 0; i < lead; i++) cells += '<div class="c2-cal-cell" aria-hidden="true"></div>';
    for (var d = 1; d <= days; d++) {
      var iso = year + '-' + String(month).padStart(2, '0') + '-' + String(d).padStart(2, '0');
      var kind = marks[iso], run = runOf(kind);
      var marker = kind === 'ovulation' ? '<span class="m ovu-gear">❋</span>' : '';
      cells += '<button type="button" class="c2-cal-cell' + (iso === todayStr ? ' today' : '') +
        (noted[iso] ? ' has-note' : '') + (kind === 'ovulation' ? ' ovu-day' : '') +
        (run ? ' c2-run c2-run-' + run : '') + '"' +
        (run ? ' data-run="' + run + '"' : '') +
        ' data-c2="day" data-date="' + iso + '">' +
        '<span class="d">' + d + '</span>' + marker + '</button>';
    }
    return '<div class="c2-cal">' + cells + '</div>';
  }

  /* ---------- scalloped day chains (SVG overlay) ---------- */
  /* One blob per day with adjacent blobs overlapping: the union reads as a single
     scalloped capsule. Runs never cross a week row, so each row segment gets its
     own path; the boundary is a run of ellipse arcs (large=0, sweep=1). */
  function chainPath(run, xs, cy, pitch) {
    var rx = pitch / 2 + 3.5, ry = 21;
    var k = xs.length;
    function arc(x, y) {
      return 'A' + rx + ' ' + ry + ' 0 0 1 ' + x.toFixed(1) + ' ' + y.toFixed(1);
    }
    if (k === 1) {
      return '<ellipse class="c2-run-shape ' + run + '" cx="' + xs[0].toFixed(1) +
        '" cy="' + cy.toFixed(1) + '" rx="' + rx.toFixed(1) + '" ry="' + ry + '"/>';
    }
    var S = xs[1] - xs[0];
    var hf = ry * Math.sqrt(Math.max(0, 1 - (S / (2 * rx)) * (S / (2 * rx))));
    var d = 'M' + (xs[0] - rx).toFixed(1) + ' ' + cy.toFixed(1) + arc(xs[0] + S / 2, cy - hf);
    for (var i = 1; i < k; i++) d += arc(xs[i] + S / 2, cy - hf);
    d += arc(xs[k - 1] + rx, cy) + arc(xs[k - 1] - S / 2, cy + hf);
    for (i = k - 2; i >= 1; i--) d += arc(xs[i] - S / 2, cy + hf);
    d += arc(xs[0] - rx, cy) + 'Z';
    return '<path class="c2-run-shape ' + run + '" d="' + d + '"/>';
  }

  function drawRunChains() {
    var cal = document.querySelector('.c2-view-cal:not([hidden]) .c2-cal');
    if (!cal || !cal.clientWidth) return;
    var svg = cal.querySelector('.c2-cal-svg');
    if (!svg) {
      svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
      svg.setAttribute('class', 'c2-cal-svg');
      svg.setAttribute('aria-hidden', 'true');
      cal.insertBefore(svg, cal.firstChild);
    }
    svg.setAttribute('viewBox', '0 0 ' + cal.clientWidth + ' ' + cal.clientHeight);
    var calRect = cal.getBoundingClientRect();
    var pitch = cal.clientWidth / 7;
    var cells = cal.querySelectorAll('.c2-cal-cell[data-run]');
    var html = '', i = 0;
    while (i < cells.length) {
      var run = cells[i].dataset.run, row = cells[i].offsetTop, xs = [], cy = 0, j = i;
      while (j < cells.length && cells[j].dataset.run === run && cells[j].offsetTop === row) {
        var r = cells[j].querySelector('.d').getBoundingClientRect();
        xs.push(r.left + r.width / 2 - calRect.left);
        cy = r.top + r.height / 2 - calRect.top;
        j++;
      }
      html += chainPath(run, xs, cy, pitch);
      i = j;
    }
    svg.innerHTML = html;
  }

  function monthLabel() {
    var p = monthAnchor().split('-');
    return String(p[0]).slice(2) + '年' + (+p[1]) + '月';
  }

  function mainCard(s) {
    var p = s.prediction || {};
    var lengthText = p.available
      ? '<div class="c2-length"><b>' + p.cycle_length + '</b><span>天 · 周期长度</span></div>'
      : '<div class="c2-length empty"><b>--</b><span>天 · 周期长度</span></div>';
    var metrics = [['🩸', '最近一次经期', '暂无记录']];
    if (s.periods && s.periods.length) {
      var r = s.periods[0];
      metrics[0][2] = fmtRange(r.start_date, r.end_date) + (r.end_date ? '' : ' · 进行中');
    }
    if (p.available) {
      var left = daysBetween(todayISO(), p.estimated_start);
      metrics.push(['🔮', '下次经期预估',
        fmtD(p.estimated_start) + (left > 0 ? ' · ' + left + ' 天后' : '')]);
    } else if (s.today) {
      metrics.push(['🌷', '当前阶段',
        (PHASE_LABEL[s.today.phase] || '') + ' · 周期第 ' + s.today.cycle_day + ' 天']);
    }
    return '<section class="c2-card">' +
      '<div class="c2-head">' +
        '<div style="display:flex;align-items:center;gap:0">' +
          '<button type="button" class="c2-month-arrow" data-c2="month-prev" aria-label="上个月">‹</button>' +
          '<button type="button" class="c2-month" data-c2="month-pick">' + monthLabel() + ' <span class="mp-caret">▾</span></button>' +
          '<button type="button" class="c2-month-arrow" data-c2="month-next" aria-label="下个月">›</button>' +
        '</div>' +
        '<button type="button" class="c2-toggle" data-c2="toggle-view">' +
          '<i></i>' + (viewMode === 'calendar' ? '周期视图' : '日历视图') + '</button>' +
      '</div>' +
      lengthText +
      '<div class="c2-view-cal"' + (viewMode === 'calendar' ? '' : ' hidden') + '>' + calendarGrid(s) + '</div>' +
      '<div class="c2-view-ring"' + (viewMode === 'ring' ? '' : ' hidden') + '>' +
        '<div class="c2-ring-wrap">' + ring(s) + '</div>' +
      '</div>' +
      legend(viewMode) +
      '<div class="c2-metrics">' + metrics.map(function (m) {
        return '<div class="c2-metric"><span class="k"><i>' + m[0] + '</i>' + h(m[1]) + '</span><b>' + h(m[2]) + '</b></div>';
      }).join('') + '</div>' +
      '<button type="button" class="c2-link" data-c2="insight">查看周期洞察 ›</button>' +
      '</section>';
  }

  function predictionCard(s) {
    var p = s.prediction;
    if (!p) return '';
    if (!p.available) {
      var n = (s.periods || []).length;
      if (n === 0) {
        return '<section class="c2-card c2-window-strip"><div class="c2-empty-head"><span class="c2-ico-chip">🌱</span>' +
          '<h3>从这一次开始</h3></div>' +
          '<p>记下最近一次经期开始的日期。再记一次，就能算出周期长度与下次预估。</p>' +
          '<div class="c2-actions">' + btn('记录经期开始', 'start') +
          btn('补录之前的日期', 'add') + '</div></section>';
      }
      return '<section class="c2-card c2-window-strip"><div class="c2-empty-head"><span class="c2-ico-chip">🌱</span>' +
        '<h3>还差一次记录</h3></div>' +
        '<p>现在只有 1 条经期记录，看不出周期长度。</p>' +
        '<p class="c2-muted">再记一次经期开始；之前那次没记的话，用「补录历史」倒填就行。</p>' +
        '<div class="c2-actions">' + btn('补录之前的日期', 'add') + '</div></section>';
    }
    var fertile = '';
    /* Only surface the fertile estimate while it is still ahead: a window that has
       already closed says nothing about the coming days. */
    if (p.fertile_start && p.fertile_end >= todayISO()) {
      fertile = '<br>预估易孕期 ' + fmtRange(p.fertile_start, p.fertile_end) +
        '，仅供参考，不作为避孕依据';
    }
    return '<section class="c2-card c2-window-strip c2-pred">' +
      '<div class="c2-empty-head"><span class="c2-ico-chip">📅</span><h3>下次预计</h3>' +
        '<span class="c2-conf ' + h(p.confidence) + '">' + h(CONF_LABEL[p.confidence] || '') + '</span></div>' +
      '<div class="c2-pred-date">' + fmtD(p.estimated_start) + '</div>' +
      '<div class="c2-pred-row">大概率落在 <b>' + fmtRange(p.window_start, p.window_end) +
        '</b>（±' + p.window + ' 天，共 ' + p.cycle_length + ' 天周期）</div>' +
      '<div class="c2-pred-row c2-muted">预计经期 ' + p.estimated_duration + ' 天 · ' + h(p.basis || '') + fertile + '</div></section>';
  }

  function insightsCard(s) {
    var ins = s.insights || {items: []};
    if (!ins.items.length) {
      return '<section class="c2-card" id="c2Insights">' + panelHead('📈', '周期洞察') +
        '<p class="c2-muted">积累 3 个以上周期后，这里会显示规律性与黄体期长度等统计。</p></section>';
    }
    return '<section class="c2-card" id="c2Insights">' + panelHead('📈', '周期洞察') + '<ul class="c2-insights">' +
      ins.items.map(function (i) {
        return '<li class="' + h(i.level) + '"><i class="c2-dot"></i>' + h(i.text) + '</li>';
      }).join('') + '</ul><p class="c2-muted">' + h(ins.disclaimer) + '</p></section>';
  }

  /* ---------- one-tap rows ---------- */
  function actionRows(s) {
    var active = s.active;
    var head;
    if (active) {
      var elapsed = daysBetween(active.start_date, todayISO()) + 1;
      head = '<button type="button" class="c2-row" data-c2="finish">' +
        '<span class="ico">🩸</span><span class="label">本次第 ' + elapsed + ' 天' +
        (active.end_date ? ' · 已记录结束' : ' · 结束尚未确认') + '</span><span class="chev">›</span></button>';
    } else {
      head = '<div class="c2-row" role="group">' +
        '<span class="ico">🩸</span><span class="label">经期开始了吗？</span>' +
        '<span class="c2-switch" data-c2="start-toggle" role="switch" aria-checked="false" tabindex="0"><b></b></span>' +
      '</div>';
    }
    return '<section class="c2-card c2-rows">' + head +
      '<button type="button" class="c2-row" data-c2="record" data-tab="symptom">' +
        '<span class="ico violet">🩹</span><span class="label">身体症状</span><span class="chev">›</span></button>' +
      '<button type="button" class="c2-row" data-c2="record" data-tab="mood">' +
        '<span class="ico warm">😊</span><span class="label">心情</span><span class="chev">›</span></button>' +
      '<button type="button" class="c2-row" data-c2="add">' +
        '<span class="ico teal">📋</span><span class="label">更多记录 · 补录历史</span><span class="chev">›</span></button>' +
      '</section>';
  }

  /* ---------- record sheet ---------- */
  var TABS = [['period', '🩸 经期'], ['symptom', '🩹 身体症状'], ['mood', '😊 心情记录'], ['more', '📋 更多记录']];

  function tabBar() {
    return '<div class="c2-tabs">' + TABS.map(function (t) {
      return '<button type="button" class="c2-tab' + (recordTab === t[0] ? ' on' : '') +
        '" data-c2="tab" data-tab="' + t[0] + '">' + t[1] + '</button>';
    }).join('') + '</div>';
  }

  function panelHead(emoji, title, extra) {
    return '<div class="c2-empty-head"><span class="c2-ico-chip">' + emoji + '</span>' +
      '<h3>' + h(title) + '</h3>' + (extra || '') + '</div>';
  }

  function periodPanel(s) {
    var rows = (s.periods || []).map(function (r) {
      return '<div class="c2-history"><div><strong>' + h(r.start_date) + '</strong>' +
        '<p class="c2-muted">至 ' + h(r.end_date || '结束待确认') + ' · ' +
        (r.source && r.source.author_uid !== s.uid ? '伴侣代记，本人已确认' : '本人记录') + '</p></div>' +
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
      '<p class="c2-muted">心情记在自己名下，是否分享由「设置」里的授权决定。</p>' +
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
      '<div class="c2-actions">' + btn('保存体温', 'save-bbt') + btn('删除这天体温', 'delete-bbt') + '</div>' +
      '</section>' +
      '<section class="c2-card">' + panelHead('📝', '备注') +
      field('想记的话（选填）', '<textarea name="note" maxlength="500" rows="3" placeholder="默认仅自己可见"></textarea>') +
      check('note_shared', '将这条备注也分享（需已开启身体信息分享）', false) +
      '</section>';
  }

  function recordPage(s) {
    var day = recordDate || todayISO();
    var d = (s.notes || []).find(function (r) { return r.date === day; }) || {};
    var panel = recordTab === 'period' ? periodPanel(s)
      : recordTab === 'mood' ? moodPanel(d)
      : recordTab === 'more' ? morePanel(s)
      : symptomPanel(d);
    return '<div style="display:flex;align-items:center;gap:10px;margin:2px 0 14px">' +
        '<button type="button" class="p-btn icon-btn" data-c2="back" aria-label="返回" title="返回">‹</button>' +
        '<div style="flex:1;min-width:0">' + c2HeaderHTML('📝 记录今天', '选择今日感受与处理', 'dog-add.png') + '</div>' +
      '</div>' +
      tabBar() +
      '<form id="cycle2DayForm" method="post">' +
        '<input type="hidden" name="date" value="' + h(day) + '">' +
        panel +
      '</form>' +
      modalsHTML() +
      '<div class="c2-savebar"><button type="submit" id="c2SaveBtn" form="cycle2DayForm">完成</button></div>';
  }

  function settingsCard(s) {
    var n = s.preferences || {}, x = s.collaboration || {}, p = s.settings || {};
    return '<details class="c2-card c2-details" id="c2Settings"><summary>🔔 提醒偏好与共同记录</summary>' +
      '<p class="c2-muted">两人共用同一份周期记录，看到和编辑的都是同一份数据；提醒只发给自己。锁屏默认不显示具体健康状态。</p>' +
      '<form id="cycle2PreferencesForm" method="post">' +
      check('enabled', '接收状态提醒', n.enabled) +
      check('upcoming', '预估经期将至（每周期一次）', n.upcoming) +
      check('pms', '经前期提醒（黄体期尾声，每周期一次）', n.pms !== 0) +
      check('started', '经期开始时提醒我', n.started) +
      check('discomfort', '当天中度及以上疼痛 / 明显影响活动时提醒我', n.discomfort) +
      check('pending', '预估区间过去后，一次中性待确认提示', n.pending) +
      field('提前天数', input('advance_days', String(n.advance_days == null ? 2 : n.advance_days),
        'type="number" min="0" max="7" required')) +
      check('notice_details', '允许我的锁屏显示详情', n.notice_details) +
      '<div class="c2-actions"><button class="p-btn pri">保存我的提醒</button></div></form>' +

      '<details class="c2-details" style="margin-top:16px"><summary>🔒 周期数据与预估</summary>' +
      '<p class="c2-muted">这份记录两人都能看、都能改。</p>' +
      '<form id="cycle2SettingsForm" method="post"><div class="c2-grid">' +
      field('典型周期天数（可不填）', input('typical_cycle', p.typical_cycle == null ? '' : p.typical_cycle, 'type="number" min="15" max="60"')) +
      field('典型经期天数（可不填）', input('typical_duration', p.typical_duration == null ? '' : p.typical_duration, 'type="number" min="1" max="30"')) +
      '</div>' + check('prediction_enabled', '开启辅助预估', p.prediction_enabled) +
      '<div class="c2-actions"><button class="p-btn pri">保存</button></div></form>' +
      '<hr style="border:0;border-top:1px solid var(--line-subtle);margin:16px 0">' +
      '<div class="c2-actions" style="margin-top:0">' + btn('导出数据', 'export') +
      btn('删除全部经期数据', 'delete-all') + '</div>' +
      '<p class="c2-muted">删除会清空两人共用的这份记录；不影响共同日程。已导出副本需要另外处理。</p>' +
      '</details></details>';
  }

  function modalsHTML() {
    return '<div class="overlay" id="cycle2PeriodModal"><div class="modal" role="dialog" aria-modal="true">' +
      '<h3 id="cycle2PeriodTitle">记录实际经期</h3><form id="cycle2PeriodForm" method="post">' +
      '<input type="hidden" name="id">' +
      field('开始日期', dt('start_date', todayISO(), true, todayISO())) +
      field('结束日期（进行中留空）', dt('end_date', '', false, todayISO())) +
      '<p class="c2-muted" id="cycle2PeriodHint">只记录实际发生的日期。</p>' +
      '<div class="c2-actions"><button type="button" class="p-btn" data-c2="close-period">取消</button>' +
      '<button class="p-btn pri">保存记录</button></div></form></div></div>';
  }

  /* ---------- composition ---------- */
  function own(s) {
    return mainCard(s) + predictionCard(s) + actionRows(s) + insightsCard(s) +
      settingsCard(s) +
      '<p class="c2-muted" style="padding:0 8px 12px">' + h(s.disclaimer) + '</p>';
  }

  function paintPage(s) {
    var box = $('#cycle2Content');
    if (!box) return;
    var shell = document.querySelector('.page-container.c2-page');
    if (shell) shell.classList.toggle('has-savebar', !!recordTab);
    if (recordTab) {
      box.innerHTML = recordPage(s);
      fillDay(recordDate || todayISO());
      bindForms();
      return;
    }
    /* Both accounts render the same page: they share one cycle record. */
    box.innerHTML = c2HeaderHTML('🌷 生理周期', '四相分期 · 越记越准', 'dog-couple.png') +
      own(s) + modalsHTML();
    bindForms();
    drawRunChains();
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
    if (f.elements.note_shared) f.elements.note_shared.checked = !!d.note_shared;
    if (f.elements.mood_other && MOODS.indexOf(d.mood) < 0) f.elements.mood_other.value = d.mood || '';
    f.querySelectorAll('[name="symptoms"]').forEach(function (x) {
      x.checked = (d.symptoms || []).indexOf(x.value) >= 0;
      var lab = x.closest('.c2-opt');
      if (lab) lab.classList.toggle('on', x.checked);
    });
  }

  function paintCalendar(s) {
    document.querySelectorAll('.c2-cell-mark').forEach(function (x) { x.remove(); });
    (s.calendar || []).forEach(function (m) {
      var cell = document.querySelector('.month-cell[data-date="' + m.date + '"]');
      if (!cell) return;
      var b = document.createElement('span');
      b.className = 'c2-cell-mark c2-' + m.phase;
      b.dataset.c2 = 'calendar';
      b.dataset.date = m.date;
      b.title = PHASE_LABEL[m.phase] || '';
      cell.appendChild(b);
    });
  }

  /* ---------- data ---------- */
  /* 每月快照落 localStorage，挂载即先用缓存绘制（stale-while-revalidate），
     后台再向网络校准；跨境往返是主要耗时，二次进入与切月不再白等。 */
  function cacheKey(uid, month) { return 'sched_cycle2_' + uid + '_' + month; }
  function readCache(uid, month) {
    try {
      var s = JSON.parse(localStorage.getItem(cacheKey(uid, month)) || 'null');
      return s && s.periods ? s : null;
    } catch (e) { return null; }
  }
  function writeCache(uid, month, s) {
    try { localStorage.setItem(cacheKey(uid, month), JSON.stringify(s)); } catch (e) {}
  }
  function dropCache(uid, month) {
    try { localStorage.removeItem(cacheKey(uid, month)); } catch (e) {}
  }

  function applySnapshot(s, uid, month, version, fromCache) {
    state = s; owner = uid; stateMonth = month; dirty = false;
    /* 日历上的相位标记会打开当天记录页；缓存先行绘制不消费该标记，
       等网络数据到达再打开，避免拿旧数据开记录页。 */
    if (selectedDate && !fromCache) {
      var picked = selectedDate;
      selectedDate = null;
      /* 网络快照到达才打开当天记录页（缓存先行绘制不消费该标记）；
         只在周期页上消费，避免异步回包时在别的视图压入空条目。 */
      if (CURRENT_VIEW === 'cycle2') {
        recordDate = picked;
        recordTab = recordTab || 'symptom';
        history.pushState({type: 'modal', id: 'cycle2-record'}, '');
      }
    }
    if (CURRENT_VIEW === 'cycle2' || recordTab) paintPage(s);
    paintCalendar(s);
    if (!fromCache) document.body.setAttribute('data-cycle2-ready', String(version));
  }

  function request() {
    var uid = ME && ME.uid;
    if (!uid) return Promise.resolve();
    var month = monthAnchor();
    var cached = (!dirty && state && owner === uid && stateMonth === month)
      ? state : readCache(uid, month);
    if (cached) applySnapshot(cached, uid, month, null, true);
    if (inflight && inflightMonth === month) return inflight;
    var version = ++sequence;
    var req = api('/api/cycle2?month=' + month, {cache: 'no-store'}).then(function (s) {
      if (version === sequence && ME && uid === ME.uid && month === monthAnchor()) {
        applySnapshot(s, uid, month, version, false);
        writeCache(uid, month, s);
      }
    }).catch(function (e) {
      if (version !== sequence || cached) return;
      var target = $('#cycle2Content');
      if (target) target.innerHTML = '<div class="c2-card">' + h(e.message) + ' ' + btn('重新加载', 'retry') + '</div>';
    });
    inflight = req; inflightMonth = month;
    req.then(function () { if (inflight === req) { inflight = null; inflightMonth = null; } },
             function () { if (inflight === req) { inflight = null; inflightMonth = null; } });
    return req;
  }

  function mount() {
    if (!ME) { state = null; owner = null; stateMonth = null; dirty = false; sequence++; recordTab = null; inflight = null; inflightMonth = null; return; }
    if (owner !== ME.uid) { state = null; stateMonth = null; dirty = false; recordTab = null; }
    monthAnchor();
    /* 手账月历的相位标记与周期页本身靠这里拉数据；课表不注入任何周期内容。 */
    if (CURRENT_VIEW === 'month' || CURRENT_VIEW === 'cycle2' || recordTab) request();
  }

  function save(action, data, el) {
    if (el) el.disabled = true;
    document.body.setAttribute('data-cycle2-saving', '1');
    return api('/api/cycle2/' + action, {method: 'POST', body: data, cache: 'no-store'}).then(function () {
      toast('已保存');
      dirty = true; dropCache(ME && ME.uid, monthAnchor());
      if (action === 'period') closeAllModals();
      return request();
    }).catch(function (e) { toast(e.message); }).finally(function () {
      document.body.removeAttribute('data-cycle2-saving');
      if (el) el.disabled = false;
    });
  }

  function edit(r) {
    var f = $('#cycle2PeriodForm');
    if (!f) return;
    f.elements.id.value = r.id || '';
    f.elements.start_date.value = r.start_date || todayISO();
    f.elements.end_date.value = r.end_date || '';
    $('#cycle2PeriodTitle').textContent = '记录实际经期';
    $('#cycle2PeriodHint').textContent = '只记录实际发生的日期，两人都看得到。';
    openModal('#cycle2PeriodModal');
    f.elements.start_date.focus();
  }

  /* Pills are labels; mirror the underlying input onto the label's selected class. */
  function syncPills(form) {
    form.querySelectorAll('.c2-opt').forEach(function (lab) {
      var inp = lab.querySelector('input');
      if (inp) lab.classList.toggle('on', inp.checked);
    });
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
    ['cycle2PeriodForm', 'cycle2DayForm', 'cycle2BbtForm',
     'cycle2PreferencesForm', 'cycle2SettingsForm'].forEach(function (id) {
      var f = document.getElementById(id);
      if (!f || f.dataset.c2Bound === '1') return;
      f.dataset.c2Bound = '1';
      f.addEventListener('submit', onSubmit);
    });
    var saveBtn = document.getElementById('c2SaveBtn');
    if (saveBtn && saveBtn.dataset.c2Bound !== '1') {
      saveBtn.dataset.c2Bound = '1';
      saveBtn.addEventListener('click', function (e) {
        var f = document.getElementById('cycle2DayForm');
        if (!f) return;
        e.preventDefault();
        onSubmit({currentTarget: f, target: f,
          preventDefault: function () {}, stopImmediatePropagation: function () {}});
      });
    }
  }

  function onSubmit(e) {
    var f = e.currentTarget || e.target;
    var id = f.getAttribute('id') || '';
    if (!/^cycle2/.test(id)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var d = Object.fromEntries(new FormData(f).entries()), action;
    if (id === 'cycle2PeriodForm') {
      action = 'period';
      if (d.id) d.id = Number(d.id); else delete d.id;
    } else if (id === 'cycle2DayForm') {
      action = 'day';
      /* Each tab is a partial view of one row, and the server replaces the whole
         row. Seed from what is stored, then apply only the fields this tab owns. */
      var day = f.elements.date.value;
      var stored = (state && state.notes.find(function (r) { return r.date === day; })) || {};
      var merged = {date: day};
      ['flow', 'pain', 'activity', 'mood', 'relief', 'medication', 'effectiveness', 'note'].forEach(function (k) {
        if (f.elements[k]) merged[k] = f.elements[k].value;
        else if (stored[k]) merged[k] = stored[k];
      });
      merged.symptoms = f.querySelector('[name="symptoms"]')
        ? new FormData(f).getAll('symptoms') : (stored.symptoms || []);
      merged.note_shared = f.elements.note_shared ? f.elements.note_shared.checked
        : !!stored.note_shared;
      if (recordTab === 'mood' && f.elements.mood_other && f.elements.mood_other.value.trim()) {
        merged.mood = f.elements.mood_other.value.trim();
      }
      d = merged;
    } else if (id === 'cycle2BbtForm') {
      action = 'bbt';
      d.temp_c = d.temp_c === '' ? null : Number(d.temp_c);
    } else if (id === 'cycle2PreferencesForm') {
      action = 'preferences';
      ['enabled', 'upcoming', 'pms', 'started', 'discomfort', 'pending', 'notice_details'].forEach(function (k) {
        d[k] = f.elements[k].checked;
      });
      d.advance_days = Number(d.advance_days);
    } else if (id === 'cycle2SettingsForm') {
      /* Only the record's own settings; both accounts edit the same row. */
      action = 'settings';
      var p = {};
      ['typical_cycle', 'typical_duration'].forEach(function (k) {
        p[k] = (d[k] === '' || d[k] == null) ? null : Number(d[k]);
      });
      p.prediction_enabled = f.elements.prediction_enabled.checked;
      d = p;
    } else return;
    save(action, d, f.querySelector('button.p-btn.pri'));
  }

  function openRecord(tab) {
    recordTab = tab || 'symptom';
    recordDate = recordDate || todayISO();
    if (state) {
      /* 记录页是周期页内的一层子页：压入历史，返回手势/键回到周期主页 */
      history.pushState({type: 'modal', id: 'cycle2-record'}, '');
      paintPage(state);
    }
    window.scrollTo(0, 0);
  }

  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-c2]');
    if (!el) return;
    var a = el.dataset.c2;
    if (a === 'calendar') {
      e.preventDefault();
      go();
      /* go() 的整页重绘会清掉选中标记，放到 go() 之后再挂，
         等网络快照到达时自动打开当天的记录页 */
      selectedDate = el.dataset.date;
      return;
    }
    if (a === 'toggle-view') {
      e.preventDefault();
      viewMode = viewMode === 'calendar' ? 'ring' : 'calendar';
      if (state) paintPage(state);
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
    if (a === 'month-prev' || a === 'month-next') {
      e.preventDefault();
      shiftMonth(a === 'month-next' ? 1 : -1);
      request();
      return;
    }
    if (a === 'insight') {
      e.preventDefault();
      var ins = document.getElementById('c2Insights');
      if (ins) ins.scrollIntoView({behavior: 'smooth', block: 'start'});
      return;
    }
    if (a === 'day') { e.preventDefault(); recordDate = el.dataset.date; openRecord('symptom'); return; }
    if (a === 'tab') { e.preventDefault(); recordTab = el.dataset.tab; if (state) paintPage(state); return; }
    if (a === 'record') { e.preventDefault(); recordDate = todayISO(); openRecord(el.dataset.tab || 'symptom'); return; }
    if (a === 'retry') { e.preventDefault(); request(); return; }
    if (a === 'start-toggle') { e.preventDefault(); if (state) edit({}); return; }
    if (a === 'save-bbt') {
      e.preventDefault();
      var bf = document.getElementById('cycle2DayForm');
      if (!bf || !bf.elements.temp_c) return;
      var val = bf.elements.temp_c.value;
      if (val === '') { toast('请先填写体温'); return; }
      save('bbt', {date: recordDate || todayISO(), temp_c: Number(val)}, el);
      return;
    }
    if (!state || !ME || owner !== ME.uid) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (a === 'start' || a === 'add') edit({});
    else if (a === 'finish') edit(Object.assign({}, state.active, {end_date: todayISO()}));
    else if (a === 'edit') edit(state.periods.find(function (r) { return r.id === Number(el.dataset.id); }));
    else if (a === 'close-period') closeAllModals();
    else if (a === 'delete-period' && confirm('删除这条已确认经期？身体感受另行保留。')) save('period/delete', {id: Number(el.dataset.id)}, el);
    else if (a === 'delete-bbt') save('bbt/delete', {date: recordDate || todayISO()}, el);
    else if (a === 'delete-all' && confirm('删除全部经期数据？两人共用这一份记录，删除后双方都看不到。') && confirm('不可从 App 撤销，确定删除？')) {
      save('delete-all', {confirm: 'DELETE_MY_CYCLE_DATA'}, el);
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
    /* Entering the page always lands on the main card, never a stale record sheet. */
    recordTab = null;
    recordDate = null;
    selectedDate = null;
    setAppHTML(renderTopBar() +
      '<div class="page-container c2-page"><div id="cycle2Content" aria-live="polite"></div></div>' +
      bottomNavHTML());
  }

  function scrub() {
    sequence++;
    state = null;
    recordTab = null;
    document.querySelectorAll('.c2-cell-mark').forEach(function (e) { e.remove(); });
    var box = $('#cycle2Content');
    if (box) box.innerHTML = '<p>私人记录已隐藏，返回后重新加载。</p>';
  }

  setInterval(function () {
    if (!ME || document.hidden || !state || recordTab ||
      document.body.hasAttribute('data-cycle2-saving') ||
      CURRENT_VIEW !== 'cycle2' && CURRENT_VIEW !== 'month') return;
    var uid = ME.uid, v = sequence;
    api('/api/cycle2?month=' + monthAnchor(), {cache: 'no-store'}).then(function (s) {
      if (!ME || uid !== ME.uid || v !== sequence) return;
      var changed = JSON.stringify(state.partner) !== JSON.stringify(s.partner) ||
        JSON.stringify(state.proposals) !== JSON.stringify(s.proposals);
      state.partner = s.partner;
      state.calendar = s.calendar;
      state.month_marks = s.month_marks;
      state.proposals = s.proposals;
      paintCalendar(s);
      if (changed && CURRENT_VIEW === 'cycle2' && !document.querySelector('#cycle2PeriodModal.show') &&
        !document.querySelector('#cycle2Content :focus')) paintPage(s);
    }).catch(function () { scrub(); });
  }, 15000);

  /* The chains are measured off the live grid, so re-measure when it moves. */
  var runResize = 0;
  window.addEventListener('resize', function () {
    if (!state || recordTab || CURRENT_VIEW !== 'cycle2') return;
    clearTimeout(runResize);
    runResize = setTimeout(drawRunChains, 150);
  });

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) scrub(); else if (ME) mount();
  });

  return {mount: mount, renderPage: emptyPage,
    /* 前进手势回到记录页的历史条目时，从缓存的周期数据恢复表单 */
    reopenRecord: function () {
      recordTab = recordTab || 'symptom';
      recordDate = recordDate || todayISO();
      if (state) paintPage(state);
    }};
})();
