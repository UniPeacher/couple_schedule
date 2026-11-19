'use strict';
// Cycle v2 acceptance run. Isolated fixture only (tests/cycle2_browser_server.py).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.CYCLE2_TEST_URL || 'http://127.0.0.1:18805';
assert.ok(/:188\d\d$/.test(base), 'Refusing to run against a non-fixture port');
const out = path.resolve(__dirname, '../output');

function dayOffset(n) {
  const d = new Date();
  d.setDate(d.getDate() - n);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

async function snap(p) {
  const r = await p.request.get(base + '/api/cycle2');
  assert.equal(r.status(), 200, await r.text());
  return r.json();
}
async function post(p, action, data) {
  const r = await p.request.post(base + '/api/cycle2/' + action, {data});
  assert.equal(r.status(), 200, await r.text());
  return r.json();
}
async function login(p, uid) {
  await p.goto(base);
  await p.locator('[data-act="pick-login"][data-uid="' + uid + '"]').click();
  await p.locator('#loginPw').fill('cycle2-test-password');
  await p.locator('#loginForm button').click();
  // 底栏改版后周期入口是「周期」标签页（98c08d9），进入后等周期页容器渲染
  await p.locator('.bottom-nav [data-act="switch-view"][data-v="cycle2"]').click();
  await p.locator('#cycle2Content .c2-card').first().waitFor({state: 'visible'});
}
function bInnerIncludes(text, marker) { return text.indexOf(marker) >= 0; }
async function openCycle(p, opts) {
  const before = await p.locator('body').getAttribute('data-cycle2-ready');
  await p.evaluate(() => { CURRENT_VIEW = 'cycle2'; Cycle2.renderPage(); Cycle2.mount(); });
  await p.waitForFunction(v => document.body.getAttribute('data-cycle2-ready') !== v, before);
  await p.locator('#cycle2Content .c2-card').first().waitFor();
  if (!opts || opts.ring !== false) await showRing(p);
}
/* The month card opens on the calendar; the ring lives behind the view toggle. */
async function showRing(p) {
  const ring = p.locator('.c2-ring').first();
  if (!(await ring.isVisible().catch(() => false))) {
    await p.locator('[data-c2="toggle-view"]').first().click();
  }
  await ring.waitFor({state: 'visible'});
}

(async () => {
  fs.mkdirSync(out, {recursive: true});
  const errors = [];
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
    headless: true,
    args: ['--no-sandbox'],
  });
  try {
    const a = await (await browser.newContext({viewport: {width: 390, height: 844}})).newPage();
    const b = await (await browser.newContext({viewport: {width: 390, height: 844}})).newPage();
    for (const [p, uid] of [[a, 'a'], [b, 'b']]) {
      p.on('pageerror', e => errors.push(uid + ': ' + e.message));
      p.on('dialog', d => d.accept());
      await login(p, uid);
    }

    // Start from a clean slate: the fixture server is long-lived across runs.
    await post(a, 'delete-all', {confirm: 'DELETE_MY_CYCLE_DATA'});
    await post(b, 'delete-all', {confirm: 'DELETE_MY_CYCLE_DATA'});
    await post(a, 'settings', {share_actual: false, share_prediction: false});
    await post(a, 'collaboration', {allow_notifications: false, share_details: false,
      allow_proxy: false, allow_notice_details: false});
    await post(b, 'preferences', {role: 'recorder', enabled: false});

    // No history must render the honest empty ring.
    await openCycle(a);
    const emptyText = await a.locator('.c2-ring').first().textContent();
    assert.ok(/还没有周期|记录两次|请记录经期/.test(emptyText), 'empty state must be honest, got: ' + emptyText);
    console.log('PASS empty state is honest');

    // The calendar is the default face of the month card and marks nothing without data.
    await a.locator('[data-c2="toggle-view"]').first().click();
    const calCells = await a.locator('.c2-cal-cell').count();
    assert.ok(calCells >= 28, 'calendar must fill a month, got ' + calCells);
    assert.equal(await a.locator('.c2-cal-cell .m.period').count(), 0,
      'no period dots before any record');
    console.log('PASS empty calendar has no marks');
    await a.locator('[data-c2="toggle-view"]').first().click();

    const shot0 = path.join(out, 'cycle2-empty.png');
    await a.screenshot({path: shot0, fullPage: true});
    assert.ok(fs.statSync(shot0).size > 5000);

    // Three cycles of history -> ring shows a real phase.
    // Four cycles give three intervals, the minimum for a regularity insight.
    for (const [s, e] of [[dayOffset(22), dayOffset(18)], [dayOffset(52), dayOffset(48)],
      [dayOffset(82), dayOffset(78)], [dayOffset(112), dayOffset(108)]]) {
      await post(a, 'period', {start_date: s, end_date: e});
    }
    await openCycle(a);
    const snap1 = await snap(a);
    assert.ok(snap1.today, 'phase must resolve with three cycles, got ' + JSON.stringify(snap1.today));
    assert.ok(['menstrual', 'follicular', 'ovulation', 'luteal'].includes(snap1.today.phase));
    assert.ok(snap1.prediction.available, 'prediction must be available');
    const ringText = await a.locator('.c2-ring').first().textContent();
    assert.ok(/第 \d+ 天/.test(ringText), 'ring centre must show the day count, got: ' + ringText);
    console.log('PASS ring renders phase + day: ' + ringText.replace(/\n/g, ' / '));

    // Legend must name all four phases.
    const legend = await a.locator('.c2-legend').textContent();
    for (const label of ['行经期', '卵泡期', '排卵期', '黄体期']) {
      assert.ok(legend.includes(label), 'legend missing ' + label + ': ' + legend);
    }
    console.log('PASS four-phase legend present');

    await a.screenshot({path: path.join(out, 'cycle2-ring.png'), fullPage: true});

    // Insights must appear once 3 cycles exist, in neutral language.
    const insightText = await a.locator('.c2-insights').innerText().catch(() => '');
    assert.ok(insightText.length > 0, 'insights should render with 3 cycles');
    assert.ok(!/诊断|避孕/.test(insightText.replace('不构成诊断', '')), 'no diagnosis language');
    console.log('PASS insights render: ' + insightText.slice(0, 40).replace(/\n/g, ' '));

    // Prediction confidence badge is present.
    const strip = await a.locator('.c2-window-strip').innerText();
    assert.ok(/大概率落在/.test(strip), 'window sentence missing: ' + strip);
    assert.ok(/样本较少|可信度/.test(strip), 'confidence badge missing: ' + strip);
    console.log('PASS prediction window + confidence');

    // Month calendar cells get phase colouring. Render the real month view, not a stub.
    await a.evaluate(() => {
      CURRENT_VIEW = 'month';
      if (typeof renderMonthApp === 'function') renderMonthApp();
    });
    await a.waitForSelector('.month-cell', {timeout: 5000}).catch(() => {});
    await a.evaluate(() => Cycle2.mount());
    await a.waitForFunction(() => document.querySelectorAll('.c2-cell-mark').length > 0,
      null, {timeout: 8000}).catch(() => {});
    const marksAfter = await a.evaluate(() => document.querySelectorAll('.c2-cell-mark').length);
    assert.ok(marksAfter > 0, 'month calendar must be phase-coloured, got ' + marksAfter);
    const phases = await a.evaluate(() => [...new Set([...document.querySelectorAll('.c2-cell-mark')]
      .map(el => el.className.split(' ').pop()))]);
    console.log('PASS month calendar phase marks: ' + marksAfter + ' (' + phases.join(',') + ')');
    await a.screenshot({path: path.join(out, 'cycle2-month.png'), fullPage: false});

    // Turning sharing on puts both accounts on the same record and the same page.
    await post(a, 'settings', {share_actual: true, share_prediction: true, share_since: dayOffset(200)});
    await openCycle(b);
    const bSnap = await snap(b);
    assert.ok(bSnap.today, 'the other account must resolve a phase once shared: ' +
      JSON.stringify(bSnap).slice(0, 200));
    assert.equal(bSnap.uid, (await snap(a)).uid, 'both accounts must read one owner');
    const bText = await b.locator('#cycle2Content').innerText();
    await openCycle(a);
    const aInner = await a.locator('#cycle2Content').innerText();
    for (const marker of ['生理周期', '行经期', '下次预计']) {
      assert.ok(bInnerIncludes(bText, marker), 'other account page missing ' + marker + ': ' + bText.slice(0, 300));
      assert.ok(bInnerIncludes(aInner, marker), 'owner page missing ' + marker);
    }
    assert.ok(!/User|Admin/.test(bText.replace(/[^\u4e00-\u9fa5]/g, '')),
      'no per-person framing left in the shared page');
    console.log('PASS both accounts render the same page');

    await b.screenshot({path: path.join(out, 'cycle2-shared.png'), fullPage: true});

    // A write from the other account lands in the shared record.
    await post(b, 'period', {start_date: dayOffset(9), end_date: dayOffset(5)});
    const seenByA = (await snap(a)).periods.map(r => r.start_date);
    assert.ok(seenByA.includes(dayOffset(9)),
      'owner must see the other account\'s write: ' + JSON.stringify(seenByA));
    console.log('PASS writes are shared both ways');

    // Backfill through the modal must actually persist (v1's /^cycle/ handler used
    // to swallow cycle2 forms, so this is the regression guard).
    await post(a, 'preferences', {role: 'recorder', enabled: false});
    await openCycle(a);
    await a.evaluate(() => window.scrollTo(0, 400));
    await a.locator('[data-c2="add"]').click();
    await a.locator('#cycle2PeriodModal.show').waitFor();
    const beforeSave = (await snap(a)).periods.length;
    await a.locator('#cycle2PeriodForm [name="start_date"]').fill(dayOffset(150));
    await a.locator('#cycle2PeriodForm [name="end_date"]').fill(dayOffset(146));
    const saveResp = a.waitForResponse(r => r.url().includes('/api/cycle2/period') &&
      r.request().method() === 'POST');
    await a.locator('#cycle2PeriodForm button.p-btn.pri').click();
    const sr = await saveResp;
    assert.equal(sr.status(), 200, await sr.text());
    await a.waitForTimeout(600);
    const afterSave = (await snap(a)).periods.length;
    assert.equal(afterSave, beforeSave + 1, 'backfill must persist exactly one new period');
    assert.equal(await a.locator('#cycle2PeriodModal.show').count(), 0, 'modal must close after save');

    // With two intervals the prediction becomes available and the ring is real.
    await openCycle(a);
    const p2 = (await snap(a)).prediction;
    assert.ok(p2.available, 'two intervals should unlock prediction: ' + JSON.stringify(p2));
    console.log('PASS backfill persists and unlocks prediction');

    // BBT accepts a plausible value and rejects an implausible one.
    const okTemp = await b.request.post(base + '/api/cycle2/bbt', {
      data: {date: dayOffset(2), temp_c: 36.55},
    });
    assert.equal(okTemp.status(), 200);
    const badTemp = await b.request.post(base + '/api/cycle2/bbt', {
      data: {date: dayOffset(1), temp_c: 41},
    });
    assert.equal(badTemp.status(), 400);
    console.log('PASS BBT validation');

    // Day notes live in the shared record, so both accounts read the same row.
    await post(a, 'day', {date: dayOffset(1), flow: '中', pain: '轻', symptoms: ['疲劳'],
      note: 'shared-note', note_shared: false});
    const noteOnB = (await snap(b)).notes.find(n => n.date === dayOffset(1));
    assert.ok(noteOnB, 'the other account must see the shared day note');
    assert.equal(noteOnB.note, 'shared-note');
    console.log('PASS day notes are shared');

    // Saving one record tab must not wipe fields owned by the other tabs.
    await openCycle(a);
    await a.locator('[data-c2="record"][data-tab="symptom"]').first().click();
    await a.locator('.c2-tabs').waitFor();
    await a.locator('.c2-opt').filter({hasText: /^多$/}).click();
    await a.locator('.c2-opt').filter({hasText: /^中$/}).nth(1).click();
    await a.locator('.c2-opt').filter({hasText: /疲劳$/}).click();
    let saved = a.waitForResponse(r => r.url().includes('/api/cycle2/day') &&
      r.request().method() === 'POST');
    await a.locator('#c2SaveBtn').click();
    assert.equal((await saved).status(), 200);
    await a.waitForTimeout(400);

    await a.locator('[data-c2="tab"][data-tab="more"]').click();
    await a.locator('#cycle2DayForm [name="note"]').waitFor();
    await a.locator('#cycle2DayForm [name="note"]').fill('tabs must not clobber');
    saved = a.waitForResponse(r => r.url().includes('/api/cycle2/day') &&
      r.request().method() === 'POST');
    await a.locator('#c2SaveBtn').click();
    assert.equal((await saved).status(), 200);
    await a.waitForTimeout(400);

    const kept = (await snap(a)).notes.find(n => n.date === dayOffset(0));
    assert.ok(kept, 'today record must exist');
    assert.equal(kept.flow, '多', 'flow must survive a note-tab save: ' + JSON.stringify(kept));
    assert.equal(kept.pain, '中', 'pain must survive: ' + JSON.stringify(kept));
    assert.deepEqual(kept.symptoms, ['疲劳'], 'symptoms must survive: ' + JSON.stringify(kept));
    assert.equal(kept.note, 'tabs must not clobber');
    console.log('PASS record tabs merge instead of overwriting');

    // Dark mode must still render the ring legibly. The app keys off `html.dark`,
    // set at load time, so the preference must be stored before the page boots.
    await a.evaluate(() => localStorage.setItem('sched_dark_mode', '1'));
    await a.reload();
    await a.waitForFunction(() => document.documentElement.classList.contains('dark'));
    await a.locator('.bottom-nav [data-act="switch-view"][data-v="cycle2"]').waitFor({state: 'visible'});
    await openCycle(a);
    const darkFill = await a.evaluate(() => {
      const dot = document.querySelector('.c2-ring .c2-dot');
      const bg = getComputedStyle(document.querySelector('.c2-page')).getPropertyValue('--c2-track');
      return {dot: dot && dot.getAttribute('class'), track: bg.trim()};
    });
    assert.ok(/c2-dot/.test(darkFill.dot), 'ring must render in dark mode: ' + JSON.stringify(darkFill));
    assert.ok(darkFill.track, 'dark tokens must resolve');
    await a.screenshot({path: path.join(out, 'cycle2-dark.png'), fullPage: true});
    // Leave the context light again so the capture above is the only dark frame.
    await a.evaluate(() => localStorage.setItem('sched_dark_mode', '0'));
    console.log('PASS dark mode renders');

    assert.deepEqual(errors, [], 'page errors: ' + errors.join(' | '));
    console.log('ALL CYCLE2 BROWSER CHECKS PASSED');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
