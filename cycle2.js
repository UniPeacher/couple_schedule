/* Cycle v2 UI: phase ring + phase-coloured calendar. Health state stays in memory only. */
var Cycle2 = (function () {
  'use strict';
  var state = null, owner = null, sequence = 0, selectedDate = null;
  var symptoms = ['疲惫', '腹胀', '头痛', '腰酸', '乳房胀痛', '恶心'];
  var PHASES = ['menstrual', 'follicular', 'ovulation', 'luteal'];
  var PHASE_LABEL = {menstrual: '行经期', follicular: '卵泡期', ovulation: '排卵期', luteal: '黄体期'};
  var CONF_LABEL = {high: '可信度较高', medium: '可信度中等', low: '样本较少'};

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
  function nameOf(uid) {
    var u = ((META && META.users) || []).find(function (x) { return x.uid === uid; });
    return u ? u.name : '伴侣';
  }
  function go() { history.pushState({type: 'view', v: 'cycle2'}, ''); CURRENT_VIEW = 'cycle2'; load(); window.scrollTo(0, 0); }
  function emptyPage() {
    $('#app').innerHTML = renderTopBar() + '<div class="page-container c2-page"><div class="page-header-card"><div><div class="page-header-title">🌷 周期</div><div class="page-header-desc">四相周期 · 越记越准 · 不要求每日打卡</div></div></div><div id="cycle2Content" aria-live="polite">正在安全加载…</div></div>' + bottomNavHTML() + modalsHTML();
  }

  /* ---------- ring ---------- */
  function arcPath(cx, cy, r, a0, a1) {
    var p0 = {x: cx + r * Math.cos(a0), y: cy + r * Math.sin(a0)};
    var p1 = {x: cx + r * Math.cos(a1), y: cy + r * Math.sin(a1)};
    var large = (a1 - a0) > Math.PI ? 1 : 0;
    return 'M' + p0.x.toFixed(2) + ' ' + p0.y.toFixed(2) +
      ' A' + r + ' ' + r + ' 0 ' + large + ' 1 ' + p1.x.toFixed(2) + ' ' + p1.y.toFixed(2);
  }

  function ring(s) {
    var t = s.today, cx = 150, cy = 150, r = 118, size = 300;
    var start = '<circle class="c2-track" cx="' + cx + '" cy="' + cy + '" r="' + r + '"/>';
    if (!t) {
      return '<svg class="c2-ring" viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="暂无周期数据">' +
        start + '<text x="' + cx + '" y="' + (cy - 4) + '" text-anchor="middle" class="c2-ring-phase">还没有周期</text>' +
        '<text x="' + cx + '" y="' + (cy + 20) + '" text-anchor="middle" class="c2-ring-day">记录两次经期后开始分期</text></svg>';
    }
    // Partner payloads carry only the phase; without dates draw a single-hue ring.
    if (!t.period_start || !t.next_start) {
      var known = ['menstrual', 'follicular', 'ovulation', 'luteal'].indexOf(t.phase) >= 0 ? t.phase : null;
      return '<svg class="c2-ring" viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="' + h(t.label) + '">' +
        '<circle class="c2-track" cx="' + cx + '" cy="' + cy + '" r="' + r + '"/>' +
        (known ? '<path class="c2-seg c2-' + known + '" d="' +
          arcPath(cx, cy, r, -Math.PI / 2, -Math.PI / 2 + Math.PI * 1.999) + '"/>' : '') +
        '<text x="' + cx + '" y="' + (cy - 6) + '" text-anchor="middle" class="c2-ring-phase">' + h(t.label) + '</text>' +
        '<text x="' + cx + '" y="' + (cy + 16) + '" text-anchor="middle" class="c2-ring-day">第 ' + t.day + ' 天</text>' +
        '<text x="' + cx + '" y="' + (cy + 38) + '" text-anchor="middle" class="c2-ring-sub">周期第 ' + t.cycle_day + ' 天</text></svg>';
    }
    // Day 1 at 12 o'clock, clockwise over the whole cycle length.
    var total = daysBetween(t.period_start, t.next_start);
    var segs = [], starts = {menstrual: 0, follicular: t.duration_used, ovulation: null, luteal: null};
    var ovuDay = daysBetween(t.period_start, t.ovulation);
    starts.ovulation = ovuDay; starts.luteal = ovuDay + 2;
    var spans = [
      ['menstrual', 0, t.duration_used],
      ['follicular', t.duration_used, ovuDay],
      ['ovulation', ovuDay, ovuDay + 2],
      ['luteal', ovuDay + 2, total]
    ];
    spans.forEach(function (sp) {
      var a = (sp[1] / total) * Math.PI * 2 - Math.PI / 2;
      var b = (sp[2] / total) * Math.PI * 2 - Math.PI / 2;
      if (sp[2] > sp[1]) segs.push('<path class="c2-seg c2-' + sp[0] + '" d="' + arcPath(cx, cy, r, a, b) + '"/>');
    });
    // Prediction window: days around the expected start, wrapped onto the ring.
    // Anything past the cycle end belongs at the ring's beginning, not clipped away.
    var ws = daysBetween(t.period_start, (s.prediction && s.prediction.window_start) || t.next_start);
    var we = daysBetween(t.period_start, (s.prediction && s.prediction.window_end) || t.next_start) + 1;
    var windows = [];
    if (we > ws) {
      var from = Math.max(ws, 0), to = we;
      if (from < total) windows.push([from, Math.min(to, total)]);
      if (to > total) windows.push([0, to - total]);
    }
    windows.forEach(function (w) {
      var ma = (w[0] / total) * Math.PI * 2 - Math.PI / 2;
      var mb = (w[1] / total) * Math.PI * 2 - Math.PI / 2;
      if (mb > ma) segs.push('<path class="c2-window" d="' + arcPath(cx, cy, r, ma, mb) + '"/>');
    });
    // Day 1 sits at 12 o'clock, so subtract one before scaling.
    var pa = (Math.min(Math.max(t.cycle_day - 1, 0), total - 1) / total) * Math.PI * 2 - Math.PI / 2;
    var px = cx + r * Math.cos(pa), py = cy + r * Math.sin(pa);
    // Pointer crosses the band so the day marker is readable against every phase colour.
    var pointer = '<line class="c2-pointer" x1="' + (cx + (r - 30) * Math.cos(pa)).toFixed(1) + '" y1="' +
      (cy + (r - 30) * Math.sin(pa)).toFixed(1) + '" x2="' + (cx + (r + 9) * Math.cos(pa)).toFixed(1) +
      '" y2="' + (cy + (r + 9) * Math.sin(pa)).toFixed(1) + '"/>' +
      '<circle class="c2-pointer-dot" cx="' + px.toFixed(1) + '" cy="' + py.toFixed(1) + '" r="4"/>';
    return '<svg class="c2-ring" viewBox="0 0 ' + size + ' ' + size + '" role="img" aria-label="当前' + h(PHASE_LABEL[t.phase]) + '第' + t.day + '天">' +
      start + segs.join('') + pointer +
      '<text x="' + cx + '" y="' + (cy - 6) + '" text-anchor="middle" class="c2-ring-phase">' + h(PHASE_LABEL[t.phase]) + '</text>' +
      '<text x="' + cx + '" y="' + (cy + 16) + '" text-anchor="middle" class="c2-ring-day">第 ' + t.day + ' 天 · 周期第 ' + t.cycle_day + ' 天</text>' +
      '<text x="' + cx + '" y="' + (cy + 38) + '" text-anchor="middle" class="c2-ring-sub">' + h(t.period_start) + ' 起</text></svg>';
  }

  function daysBetween(a, b) {
    if (!a || !b) return 0;
    return Math.round((new Date(b + 'T00:00:00') - new Date(a + 'T00:00:00')) / 86400000);
  }

  function legend() {
    return '<div class="c2-legend">' + PHASES.map(function (p) {
      return '<span><i class="c2-' + p + '"></i>' + PHASE_LABEL[p] + '</span>';
    }).join('') + '<span><i style="background:transparent;border:1px dashed #e0607f"></i>预估窗口</span></div>';
  }

  /* ---------- cards ---------- */
  function predictionCard(s) {
    var p = s.prediction;
    if (!p) return '';
    if (!p.available) {
      return '<section class="c2-card"><h3>🔮 下次经期</h3><p class="c2-muted">' + h(p.reason || '暂无可用预估') + '</p></section>';
    }
    return '<section class="c2-window-strip"><strong>下次预计 ' + h(p.estimated_start) +
      '<span class="c2-conf ' + h(p.confidence) + '">' + h(CONF_LABEL[p.confidence] || '') + '</span></strong>' +
      '<p class="c2-muted">大概率落在 ' + h(p.window_start) + ' ～ ' + h(p.window_end) +
      '（±' + p.window + ' 天，共 ' + p.cycle_length + ' 天周期）<br>' + h(p.basis || '') +
      ' · 预计经期 ' + p.estimated_duration + ' 天</p></section>';
  }

  function insightsCard(s) {
    var ins = s.insights || {items: []};
    if (!ins.items.length) {
      return '<section class="c2-card"><h3>📈 周期洞察</h3><p class="c2-muted">积累 3 个以上周期后，这里会显示规律性与黄体期长度等统计。</p></section>';
    }
    return '<section class="c2-card"><h3>📈 周期洞察</h3><ul class="c2-insights">' +
      ins.items.map(function (i) {
        return '<li class="' + h(i.level) + '"><i class="c2-dot"></i>' + h(i.text) + '</li>';
      }).join('') + '</ul><p class="c2-muted">' + h(ins.disclaimer) + '</p></section>';
  }

  function own(s) {
    var html = '<section class="c2-card"><div class="c2-eyebrow">今天的周期状态</div>' +
      '<div class="c2-ring-wrap">' + ring(s) + legend() + '</div>' + predictionCard(s) + '</section>';

    html += '<section class="c2-card"><h3>🌿 记录 <small>按需填，不必每天</small></h3>' +
      '<div class="c2-actions">' + btn(s.active ? '确认结束日期' : '经期开始了', s.active ? 'finish' : 'start') +
      btn('补录历史', 'add') + btn('记录今天的感受', 'today') + '</div>' +
      '<details class="c2-details" style="margin-top:14px"><summary>身体感受与处理</summary>' +
      '<form id="cycle2DayForm" method="post">' + field('日期', dt('date', todayISO(), true, todayISO())) +
      '<div class="c2-grid">' + field('经量', select('flow', ['极少', '少', '中', '多', '很多'])) +
      field('疼痛', select('pain', ['无', '轻', '中', '重'])) + '</div>' +
      field('是否影响日常活动', select('activity', ['几乎无影响', '轻度影响', '明显影响', '严重影响'])) +
      '<div class="c2-checks">' + symptoms.map(function (x) {
        return '<label class="c2-check"><input type="checkbox" name="symptoms" value="' + x + '"><span>' + x + '</span></label>';
      }).join('') + '</div>' +
      field('心情（选填）', input('mood', '', 'maxlength="20"')) +
      field('缓解方式（选填）', input('relief', '', 'maxlength="50" placeholder="例如：休息、热敷"')) +
      field('用药记录（选填）', input('medication', '', 'maxlength="50" placeholder="只记录事实，不推荐用药"')) +
      field('缓解效果', select('effectiveness', ['有效', '部分有效', '无效'])) +
      field('文字备注', '<textarea name="note" maxlength="500" rows="2" placeholder="默认仅自己可见"></textarea>') +
      check('note_shared', '将这条备注也分享（需已开启身体信息分享）', false) +
      '<div class="c2-actions"><button class="p-btn pri">保存感受</button>' + btn('删除这天的感受', 'delete-day') + '</div></form></details></section>';

    html += bbtCard(s);
    html += insightsCard(s);

    html += '<section class="c2-card"><h3>🗓️ 已确认经期</h3>' + (s.periods.length ? s.periods.map(function (r) {
      return '<div class="c2-history"><div><strong>' + h(r.start_date) + '</strong><p class="c2-muted">至 ' +
        h(r.end_date || '结束待确认') + ' · ' + (r.source && r.source.author_uid !== s.uid ? '伴侣代记，本人已确认' : '本人记录') +
        '</p></div><div class="c2-actions">' + btn('编辑', 'edit', 'data-id="' + r.id + '"') +
        btn('删除', 'delete-period', 'data-id="' + r.id + '"') + '</div></div>';
    }).join('') : '<p class="c2-muted">开始和结束时各记一下就好。</p>') + '</section>';

    return html;
  }

  function bbtCard(s) {
    var temps = s.bbt || [];
    var recent = temps.slice(-7);
    var summary = temps.length ?
      '已记录 ' + temps.length + ' 天，最近 ' + h(recent[recent.length - 1].date) + ' 为 ' +
      recent[recent.length - 1].temp_c + ' ℃' : '还没有基础体温记录。';
    var calibrated = s.insights && s.insights.luteal_calibrated;
    return '<details class="c2-card c2-details"><summary>🌡️ 基础体温 <small>可选，用于校准黄体期</small></summary>' +
      '<p class="c2-muted">' + h(summary) + (calibrated ? '<br>已据体温反推你的黄体期约 ' + calibrated + ' 天，预估已按此校准。' : '') +
      '<br>每天固定时间测得才有意义；不做避孕或排卵确认用途。</p>' +
      '<form id="cycle2BbtForm" method="post">' + field('日期', dt('date', todayISO(), true, todayISO())) +
      field('体温（℃）', input('temp_c', '', 'type="number" step="0.01" min="35" max="38.5" inputmode="decimal" placeholder="例如 36.55"')) +
      '<div class="c2-actions"><button class="p-btn pri">保存体温</button>' + btn('删除这天体温', 'delete-bbt') + '</div></form></details>';
  }

  function partnerCard(s) {
    var p = s.partner;
    if (!p) {
      return '<details class="c2-card c2-details"><summary>🤍 关注对方</summary>' +
        '<p class="c2-muted">由记录者在自己的账号开启分享后，这里才会显示对方的周期。授权前不显示任何身体信息。</p></details>';
    }
    var html = '<section class="c2-card"><div class="c2-eyebrow">双方授权的周期概览</div><h3>🤍 ' + h(nameOf(p.uid)) + ' 的周期</h3>';
    if (p.today) {
      // Recipient gets the same ring affordance, built from the shared phase only.
      html += '<div class="c2-ring-wrap">' + ring({
        today: p.today,
        prediction: p.prediction || null,
        bbt: [],
      }) + legend() + '</div>';
    }
    if (p.prediction) {
      html += '<p>' + (p.prediction.available ?
        '下次预计 ' + h(p.prediction.estimated_start) + ' ～ ' + h(p.prediction.window_end) :
        h(p.prediction.reason || '暂无获授权的预估')) + '</p>';
    }
    if (p.periods) {
      html += '<details class="c2-details"><summary>已确认的日期（' + p.periods.length + '）</summary>' +
        p.periods.map(function (r) {
          return '<p>' + h(r.start_date) + ' ～ ' + h(r.end_date || '结束待确认') +
            (r.start_clipped ? '（按分享起始日裁切）' : '') + '</p>';
        }).join('') + '</details>';
    }
    if (p.can_proxy) {
      html += '<div class="c2-actions">' + btn('帮她记一次，等她确认', 'proxy') + '</div>' +
        (p.editable_periods || []).map(function (r) {
          return '<div class="c2-history"><span>' + h(r.start_date) + ' ～ ' + h(r.end_date || '结束未确认') + '</span>' +
            btn('代记结束 / 更正', 'proxy-edit', 'data-id="' + r.id + '"') + '</div>';
        }).join('') +
        '<p class="c2-muted">代记不会直接成为正式记录，也不参与预估；她可以纠正后确认或拒绝。</p>';
    }
    if (p.days && p.days.length) {
      html += '<details class="c2-details"><summary>身体状态记录（' + p.days.length + '）</summary>' +
        '<p class="c2-muted">' + (p.days.some(function (d) { return d.date === todayISO(); }) ?
          '今天有更新' : '今天未更新，不沿用昨天的状态') + '</p>' +
        p.days.slice(0, 20).map(dailyLine).join('') + '</details>';
    }
    if (p.care) {
      html += '<p>' + h(p.care.message) + '</p><p class="c2-muted">有效至 ' + p.care.expires_on + '</p>' +
        (p.care.acknowledged ? '<p>已回应：收到，我在。</p>' : btn('收到，我在 🤍', 'ack'));
    }
    return html + '</section>';
  }

  function dailyLine(d) {
    var labels = {flow: '经量', pain: '疼痛', mood: '心情', activity: '影响', relief: '缓解', medication: '用药', effectiveness: '效果'};
    return '<article class="c2-day"><strong>' + h(d.date) + '</strong>' +
      Object.keys(labels).map(function (k) {
        return d[k] ? '<p>' + labels[k] + '：' + h(d[k]) + '</p>' : '';
      }).join('') +
      (d.symptoms && d.symptoms.length ? '<p>感受：' + d.symptoms.map(h).join('、') + '</p>' : '') +
      (d.note ? '<p>备注：' + h(d.note) + '</p>' : '') + '</article>';
  }

  function proposals(s) {
    if (!s.proposals || !s.proposals.length) return '';
    return '<section class="c2-card"><h3>📝 代记待确认</h3><p class="c2-muted">不催促、不自动确认；正式周期与统计暂不包含这些记录。</p>' +
      s.proposals.map(function (r) {
        var mine = r.uid === s.uid;
        return '<div class="c2-history"><div><strong>' + h(nameOf(r.author_uid)) + ' 代记</strong><p>' +
          h(r.start_date) + ' ～ ' + h(r.end_date || '结束未填') + '</p></div><div class="c2-actions">' +
          (mine ? btn('确认 / 纠正', 'review', 'data-id="' + r.id + '"') + btn('拒绝', 'reject', 'data-id="' + r.id + '"') :
            btn('撤回代记', 'cancel-proposal', 'data-id="' + r.id + '"')) + '</div></div>';
      }).join('') + '</section>';
  }

  function settingsCard(s) {
    var n = s.preferences || {}, x = s.collaboration || {}, p = s.settings || {};
    return '<section class="c2-card"><h3>🔔 我的提醒偏好</h3>' +
      '<p class="c2-muted">两人独立设置；接收对方提醒还需对方授权。锁屏默认不显示具体健康状态。</p>' +
      '<form id="cycle2PreferencesForm" method="post">' +
      field('我的使用方式', '<select name="role"><option value="recorder"' + (n.role === 'recorder' ? ' selected' : '') +
        '>我来记录身体状态</option><option value="supporter"' + (n.role === 'supporter' ? ' selected' : '') +
        '>我来关注伴侣</option></select>') +
      check('enabled', '接收状态提醒', n.enabled) +
      check('upcoming', '预估经期将至（每周期一次）', n.upcoming) +
      check('pms', '经前期提醒（黄体期尾声，每周期一次）', n.pms !== 0) +
      check('started', '对方确认经期开始', n.started) +
      check('discomfort', '对方当天中度及以上疼痛 / 明显影响活动', n.discomfort) +
      check('pending', '预估区间过去后，一次中性待确认提示', n.pending) +
      field('提前天数', input('advance_days', String(n.advance_days == null ? 2 : n.advance_days),
        'type="number" min="0" max="7" required')) +
      check('notice_details', '允许我的锁屏显示详情（还需记录者允许）', n.notice_details) +
      '<button class="p-btn pri">保存我的提醒</button></form></section>' +

      '<details class="c2-card c2-details"><summary>🔒 我自己的记录：分享与预估</summary>' +
      '<p class="c2-muted">只管理本人数据。伴侣代记须经本人确认，原有记录不会自动扩大分享范围。</p>' +
      '<form id="cycle2SettingsForm" method="post"><div class="c2-grid">' +
      field('典型周期天数（可不填）', input('typical_cycle', p.typical_cycle == null ? '' : p.typical_cycle, 'type="number" min="15" max="60"')) +
      field('典型经期天数（可不填）', input('typical_duration', p.typical_duration == null ? '' : p.typical_duration, 'type="number" min="1" max="30"')) +
      '</div>' + check('prediction_enabled', '开启辅助预估', p.prediction_enabled) +
      check('share_actual', '分享实际日期', p.share_actual) +
      check('share_prediction', '分享预估区间与当前所处阶段', p.share_prediction) +
      field('分享起始日期', dt('share_since', p.share_since || todayISO(), true)) + '<hr>' +
      check('share_details', '分享结构化身体信息', x.share_details) +
      check('allow_proxy', '允许伴侣代记日期，等待我确认', x.allow_proxy) +
      check('allow_notifications', '允许对方向自己手机接收我的状态提醒', x.allow_notifications) +
      check('allow_notice_details', '允许对方在锁屏显示我的具体状态', x.allow_notice_details) +
      '<p class="c2-muted">文字备注默认私密，可按条分享。撤回后停止访问，但已读通知与截图无法收回。</p>' +
      '<div class="c2-actions"><button class="p-btn pri">保存分享与预估</button>' +
      btn('立即停止全部分享', 'revoke-sharing') + '</div></form></details>';
  }

  function paintPage(s) {
    var box = $('#cycle2Content');
    if (!box) return;
    var supporter = s.preferences && s.preferences.role === 'supporter';
    box.innerHTML = (supporter ? partnerCard(s) : own(s)) + proposals(s) +
      (supporter ? '<details class="c2-card c2-details"><summary>管理我自己的记录（可选）</summary>' + own(s) + '</details>' : partnerCard(s)) +
      settingsCard(s) +
      '<details class="c2-card c2-details"><summary>数据导出与删除</summary><div class="c2-actions">' +
      btn('导出我的数据', 'export') + btn('删除我的全部经期数据', 'delete-all') +
      '</div><p class="c2-muted">不影响共同日程或伴侣的正式记录；已导出副本需要另外处理。</p></details>' +
      '<p class="c2-muted" style="padding:0 8px 30px">' + h(s.disclaimer) + '</p>' +
      '<div class="overlay" id="cycle2PeriodModal"><div class="modal" role="dialog" aria-modal="true">' +
      '<h3 id="cycle2PeriodTitle">记录实际经期</h3><form id="cycle2PeriodForm" method="post">' +
      '<input type="hidden" name="id"><input type="hidden" name="mode"><input type="hidden" name="proposal_id">' +
      field('开始日期', dt('start_date', todayISO(), true, todayISO())) +
      field('结束日期（进行中留空）', dt('end_date', '', false, todayISO())) +
      '<p class="c2-muted" id="cycle2PeriodHint">只记录实际发生的日期。</p>' +
      '<div class="c2-actions">' + btn('取消', 'close-period') + '<button class="p-btn pri">保存记录</button></div></form></div></div>';

    var day = selectedDate || todayISO();
    var dayForm = $('#cycle2DayForm');
    if (dayForm) { dayForm.elements.date.value = day; fillDay(day); }
    if (selectedDate) {
      var wrap = dayForm && dayForm.closest('details');
      if (wrap) wrap.open = true;
      if (dayForm) dayForm.scrollIntoView({block: 'center'});
      selectedDate = null;
    }
  }

  function fillDay(date) {
    var f = $('#cycle2DayForm');
    if (!f || !state) return;
    var d = state.notes.find(function (r) { return r.date === date; }) || {};
    ['flow', 'pain', 'activity', 'mood', 'relief', 'medication', 'effectiveness', 'note'].forEach(function (k) {
      if (f.elements[k]) f.elements[k].value = d[k] || '';
    });
    if (f.elements.note_shared) f.elements.note_shared.checked = !!d.note_shared;
    f.querySelectorAll('[name="symptoms"]').forEach(function (x) {
      x.checked = (d.symptoms || []).indexOf(x.value) >= 0;
    });
  }

  function paintCalendar(s) {
    document.querySelectorAll('.c2-cell-mark').forEach(function (x) { x.remove(); });
    (s.calendar || []).forEach(function (m) {
      var cell = document.querySelector('.month-cell[data-date="' + m.date + '"]');
      if (!cell) return;
      var b = document.createElement('button');
      b.type = 'button';
      b.className = 'c2-cell-mark c2-' + m.phase;
      b.dataset.c2 = 'calendar';
      b.dataset.date = m.date;
      b.textContent = PHASE_LABEL[m.phase] || '';
      cell.appendChild(b);
    });
    if (s.prediction && s.prediction.available) {
      addWindowMarks(s.prediction);
    }
  }

  function addWindowMarks(p) {
    var d = new Date(p.window_start + 'T00:00:00');
    var end = new Date(p.window_end + 'T00:00:00');
    while (d <= end) {
      var iso = d.getFullYear() + '-' + String(d.getMonth() + 1).padStart(2, '0') + '-' + String(d.getDate()).padStart(2, '0');
      var cell = document.querySelector('.month-cell[data-date="' + iso + '"]');
      if (cell && !cell.querySelector('.c2-cell-mark')) {
        var b = document.createElement('button');
        b.type = 'button';
        b.className = 'c2-cell-mark c2-window';
        b.dataset.c2 = 'calendar';
        b.dataset.date = iso;
        b.textContent = '预估';
        cell.appendChild(b);
      }
      d.setDate(d.getDate() + 1);
    }
  }

  function paintHome(s) {
    var slot = $('#cycle2Home');
    if (!slot) return;
    var supporter = s.preferences && s.preferences.role === 'supporter';
    var line;
    if (supporter) {
      if (s.partner && s.partner.today) {
        line = '<strong>' + h(nameOf(s.partner.uid)) + '：' + h(s.partner.today.label) +
          ' · 第 ' + s.partner.today.day + ' 天</strong>';
      } else {
        line = '<strong>' + (s.partner ? '已授权，暂无周期数据' : '关注对方的周期状态') + '</strong>';
      }
    } else if (s.today) {
      line = '<strong>' + h(s.today.label) + ' · 第 ' + s.today.day + ' 天</strong>' +
        '<p class="c2-muted">周期第 ' + s.today.cycle_day + ' 天</p>';
    } else {
      line = '<strong>' + (s.periods.length ? '预估待补充记录' : '记录一次经期开始') + '</strong>';
    }
    slot.innerHTML = '<div class="c2-card"><div class="c2-eyebrow">🌷 周期</div>' + line +
      '<div class="c2-actions">' + btn(supporter ? '查看状态 ›' : '查看周期 ›', 'open') + '</div>' +
      '<p class="c2-muted">四相分期 · 预估仅供参考</p></div>';
  }

  function request() {
    var version = ++sequence, uid = ME && ME.uid;
    if (!uid) return Promise.resolve();
    return api('/api/cycle2?month=' + MONTH_ANCHOR, {cache: 'no-store'}).then(function (s) {
      if (version !== sequence || !ME || uid !== ME.uid) return;
      state = s; owner = uid;
      if (CURRENT_VIEW === 'cycle2') paintPage(s);
      paintHome(s); paintCalendar(s);
      document.body.setAttribute('data-cycle2-ready', String(version));
    }).catch(function (e) {
      if (version !== sequence) return;
      var target = $('#cycle2Content') || $('#cycle2Home');
      if (target) target.innerHTML = '<div class="c2-card">' + h(e.message) + ' ' + btn('重新加载', 'retry') + '</div>';
    });
  }

  function mount() {
    if (!ME) { state = null; owner = null; sequence++; return; }
    if (owner !== ME.uid) state = null;
    if (CURRENT_VIEW === 'week' || CURRENT_VIEW === 'month') {
      var target = CURRENT_VIEW === 'week' ? document.querySelector('main') : document.querySelector('.month-cal-wrap');
      if (target && !$('#cycle2Home')) {
        var slot = document.createElement('section');
        slot.id = 'cycle2Home';
        target.insertAdjacentElement('afterend', slot);
      }
      request();
    } else if (CURRENT_VIEW === 'cycle2') request();
  }

  function save(action, data, el) {
    if (el) el.disabled = true;
    document.body.setAttribute('data-cycle2-saving', '1');
    return api('/api/cycle2/' + action, {method: 'POST', body: data, cache: 'no-store'}).then(function () {
      toast(action === 'proposal' ? '已代记，等待本人确认' : '已保存');
      if (['period', 'proposal', 'proposal/confirm', 'bbt'].indexOf(action) >= 0 &&
        history.state && history.state.type === 'modal') { closeAllModals(); history.back(); }
      return request();
    }).catch(function (e) { toast(e.message); }).finally(function () {
      document.body.removeAttribute('data-cycle2-saving');
      if (el) el.disabled = false;
    });
  }

  function edit(r, mode) {
    var f = $('#cycle2PeriodForm');
    if (!f) return;
    f.elements.id.value = r.id || '';
    f.elements.mode.value = mode || 'period';
    f.elements.proposal_id.value = mode === 'confirm' ? r.id : '';
    f.elements.start_date.value = r.start_date || todayISO();
    f.elements.end_date.value = r.end_date || '';
    $('#cycle2PeriodTitle').textContent = mode === 'proxy' ? '帮她代记 · 待本人确认' :
      mode === 'confirm' ? '确认或纠正代记' : '记录实际经期';
    $('#cycle2PeriodHint').textContent = mode === 'proxy' ?
      '仅提交待确认记录，不直接改变正式周期。' :
      mode === 'confirm' ? '可以先纠正日期；确认后成为正式记录并参与周期统计。' : '只记录实际发生的日期。';
    openModal('#cycle2PeriodModal');
    f.elements.start_date.focus();
  }

  document.addEventListener('change', function (e) {
    if (e.target.matches('#cycle2DayForm [name="date"]')) fillDay(e.target.value);
  });

  document.addEventListener('submit', function (e) {
    var f = e.target, id = f.getAttribute('id') || '';
    if (!/^cycle2/.test(id)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var d = Object.fromEntries(new FormData(f).entries()), action;
    if (id === 'cycle2PeriodForm') {
      var mode = d.mode;
      delete d.mode;
      if (mode === 'confirm') { action = 'proposal/confirm'; d.id = Number(d.proposal_id); }
      else {
        action = mode === 'proxy' ? 'proposal' : 'period';
        if (d.id) { if (mode === 'proxy') { d.period_id = Number(d.id); delete d.id; } else d.id = Number(d.id); }
        else delete d.id;
      }
      delete d.proposal_id;
    } else if (id === 'cycle2DayForm') {
      action = 'day';
      d.symptoms = new FormData(f).getAll('symptoms');
      d.note_shared = f.elements.note_shared.checked;
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
      action = 'sharing';
      var p = {}, x = {};
      ['typical_cycle', 'typical_duration'].forEach(function (k) { p[k] = d[k] === '' ? null : Number(d[k]); });
      ['prediction_enabled', 'share_actual', 'share_prediction'].forEach(function (k) { p[k] = f.elements[k].checked; });
      p.share_since = d.share_since;
      ['share_details', 'allow_proxy', 'allow_notifications', 'allow_notice_details'].forEach(function (k) {
        x[k] = f.elements[k].checked;
      });
      if (!confirm('确认按所选范围与当前伴侣共享？备注仍按单条设置，代记必须由你确认。')) return;
      d = {settings: p, collaboration: x};
    } else return;
    save(action, d, f.querySelector('button:not([type="button"])'));
  }, true);

  document.addEventListener('click', function (e) {
    var el = e.target.closest('[data-c2]');
    if (!el) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    var a = el.dataset.c2;
    if (a === 'open' || a === 'calendar') {
      selectedDate = a === 'calendar' ? el.dataset.date : null;
      go();
      return;
    }
    if (a === 'retry') { request(); return; }
    if (!state || !ME || owner !== ME.uid) return;
    if (a === 'start' || a === 'add') edit({});
    else if (a === 'finish') edit(Object.assign({}, state.active, {end_date: todayISO()}));
    else if (a === 'edit') edit(state.periods.find(function (r) { return r.id === Number(el.dataset.id); }));
    else if (a === 'proxy') edit({}, 'proxy');
    else if (a === 'proxy-edit') edit((state.partner.editable_periods || []).find(function (r) { return r.id === Number(el.dataset.id); }), 'proxy');
    else if (a === 'review') edit(state.proposals.find(function (r) { return r.id === Number(el.dataset.id); }), 'confirm');
    else if (a === 'reject' || a === 'cancel-proposal') save(a === 'reject' ? 'proposal/reject' : 'proposal/cancel', {id: Number(el.dataset.id)}, el);
    else if (a === 'close-period') { closeAllModals(); if (history.state && history.state.type === 'modal') history.back(); }
    else if (a === 'today') {
      var dayForm = $('#cycle2DayForm');
      if (!dayForm) return;
      var details = dayForm.closest('details');
      if (details) details.open = true;
      dayForm.elements.date.value = todayISO();
      fillDay(todayISO());
      dayForm.scrollIntoView({behavior: 'smooth', block: 'center'});
    }
    else if (a === 'delete-period' && confirm('删除这条已确认经期？身体感受另行保留。')) save('period/delete', {id: Number(el.dataset.id)}, el);
    else if (a === 'delete-day' && confirm('删除这天的感受与处理记录？')) save('day/delete', {date: $('#cycle2DayForm').elements.date.value}, el);
    else if (a === 'delete-bbt' && confirm('删除这天的体温记录？')) save('bbt/delete', {date: $('#cycle2BbtForm').elements.date.value}, el);
    else if (a === 'revoke-sharing') save('sharing', {
      settings: {share_actual: false, share_prediction: false},
      collaboration: {share_details: false, allow_proxy: false, allow_notifications: false, allow_notice_details: false}
    }, el);
    else if (a === 'ack') save('care/ack', {}, el);
    else if (a === 'delete-all' && confirm('删除本人全部经期数据？不影响对方正式记录。') && confirm('不可从 App 撤销，确定删除？')) {
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

  function scrub() {
    sequence++;
    state = null;
    document.querySelectorAll('.c2-cell-mark').forEach(function (e) { e.remove(); });
    var slot = $('#cycle2Home');
    if (slot) slot.innerHTML = '';
    var box = $('#cycle2Content');
    if (box) box.innerHTML = '<p>私人记录已隐藏，返回后重新加载。</p>';
  }

  setInterval(function () {
    if (!ME || document.hidden || !state || document.body.hasAttribute('data-cycle2-saving') ||
      !$('#cycle2Home') && CURRENT_VIEW !== 'cycle2') return;
    var uid = ME.uid, v = sequence;
    api('/api/cycle2?month=' + MONTH_ANCHOR, {cache: 'no-store'}).then(function (s) {
      if (!ME || uid !== ME.uid || v !== sequence) return;
      var changed = JSON.stringify(state.partner) !== JSON.stringify(s.partner) ||
        JSON.stringify(state.proposals) !== JSON.stringify(s.proposals);
      state.partner = s.partner;
      state.calendar = s.calendar;
      state.proposals = s.proposals;
      var shared = $('#cycle2Partner');
      if (shared) shared.innerHTML = partnerCard(s);
      paintHome(s);
      paintCalendar(s);
      if (changed && CURRENT_VIEW === 'cycle2' && !document.querySelector('#cycle2PeriodModal.show') &&
        !document.querySelector('#cycle2Content :focus')) paintPage(s);
    }).catch(function () { scrub(); });
  }, 15000);

  document.addEventListener('visibilitychange', function () {
    if (document.hidden) scrub(); else if (ME) mount();
  });

  return {mount: mount, renderPage: emptyPage};
})();
