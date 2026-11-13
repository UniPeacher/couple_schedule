'use strict';
// Cycle v2 acceptance run. Isolated fixture only (tests/cycle2_browser_server.py).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const base = process.env.CYCLE2_TEST_URL || 'http://127.0.0.1:18799';
assert.ok(/:(1879[7-9])$/.test(base), 'Refusing to run against a non-fixture port');
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
  await p.locator('#cycle2Home [data-c2="open"]').waitFor({state: 'visible'});
}
async function openCycle(p, opts) {
  const before = await p.locator('body').getAttribute('data-cycle2-ready');
  await p.evaluate(() => { CURRENT_VIEW = 'cycle2'; Cycle2.renderPage(); Cycle2.mount(); });
  await p.waitForFunction(v => document.body.getAttribute('data-cycle2-ready') !== v, before);
  await p.locator('#cycle2Content .c2-card').first().waitFor();
  if (!opts || opts.ring !== false) await p.locator('.c2-ring').first().waitFor({state: 'attached'});
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
    assert.ok(/还没有周期|记录两次/.test(emptyText), 'empty state must be honest, got: ' + emptyText);
    console.log('PASS empty state is honest');

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

    // Recorder grants sharing; partner sees phase but never statistics.
    await post(a, 'settings', {share_actual: true, share_prediction: true, share_since: dayOffset(200)});
    await post(a, 'collaboration', {allow_notifications: true});
    await post(b, 'preferences', {role: 'supporter', enabled: true, upcoming: true, pms: true});
    await openCycle(b);
    const partnerText = await b.locator('.c2-card').first().innerText();
    const partnerJson = await snap(b);
    assert.ok(partnerJson.partner, 'partner snapshot must exist: ' + JSON.stringify(partnerJson).slice(0, 200));
    assert.ok(partnerJson.partner.today, 'partner must receive the shared phase');
    assert.ok(/第 \d+ 天|黄体期|卵泡期|行经期|排卵期/.test(partnerText),
      'partner must see the phase, got: ' + partnerText);
    for (const leaked of ['basis', 'cycle_length', 'confidence', 'luteal_length']) {
      assert.ok(!(leaked in partnerJson.partner.prediction),
        'partner must not receive ' + leaked);
    }
    console.log('PASS partner sees phase, not statistics');

    await b.screenshot({path: path.join(out, 'cycle2-partner.png'), fullPage: true});

    // Revoking prediction removes the phase immediately.
    await post(a, 'settings', {share_actual: true, share_prediction: false});
    await openCycle(b, {ring: false});
    const after = await b.locator('.c2-card').first().innerText();
    assert.ok(!/黄体期|卵泡期|排卵期/.test(after), 'revoked phase must disappear: ' + after);
    console.log('PASS revoking prediction hides phase');

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

    // Day note round-trips and stays private from the partner.
    await post(a, 'day', {date: dayOffset(1), flow: '中', pain: '轻', symptoms: ['疲惫'],
      note: 'private-note-should-not-leak', note_shared: false});
    const leakText = JSON.stringify(await snap(b));
    assert.ok(!leakText.includes('private-note-should-not-leak'),
      'private note leaked to partner');
    console.log('PASS private note not leaked');

    // Dark mode must still render the ring legibly.
    await a.emulateMedia({colorScheme: 'dark'});
    await openCycle(a);
    await a.locator('.c2-ring').first().waitFor();
    await a.screenshot({path: path.join(out, 'cycle2-dark.png'), fullPage: true});
    console.log('PASS dark mode renders');

    assert.deepEqual(errors, [], 'page errors: ' + errors.join(' | '));
    console.log('ALL CYCLE2 BROWSER CHECKS PASSED');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
