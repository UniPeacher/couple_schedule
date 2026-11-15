'use strict';
// Nav-history acceptance: every push layer pairs with one history entry, so the
// phone back gesture always lands exactly one level up. Landing spots are
// asserted via history.state (history.length never shrinks on back, so it is
// only meaningful for "a push adds exactly one entry").
// Isolated fixture only (tests/cycle2_browser_server.py).
const {chromium} = require('playwright');
const assert = require('node:assert/strict');

const base = process.env.NAV_TEST_URL || 'http://127.0.0.1:18797';
assert.ok(/:18\d\d\d$/.test(base), 'Refusing to run against a non-fixture port');

async function login(p) {
  await p.goto(base);
  await p.locator('[data-act="pick-login"][data-uid="a"]').click();
  await p.locator('#loginPw').fill('cycle2-test-password');
  await p.locator('#loginForm button').click();
  await p.locator('.bottom-nav').waitFor();
}
const state = p => p.evaluate(() => history.state);
const view = p => p.evaluate(() => CURRENT_VIEW);
async function goBackExpect(p, want, name) {
  await p.goBack();
  await p.waitForFunction(w => JSON.stringify(history.state) === JSON.stringify(w), want,
    {timeout: 5000});
  console.log('PASS back -> ' + name);
}
let p; // 主页面句柄，helper 在 IIFE 内赋值后使用
const WEEK_FAB = () => p.locator('.fab[data-act="open-add"]');
const CYCLE_MAIN = () => p.locator('[data-c2="record"][data-tab="symptom"]').first();
const RECORD_SHEET = () => p.locator('#cycle2DayForm');

(async () => {
  const errors = [];
  const browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_PATH || undefined,
    headless: true,
    args: ['--no-sandbox'],
  });
  try {
    const ctx = await browser.newContext({viewport: {width: 390, height: 844}});
    p = await ctx.newPage();
    p.on('pageerror', e => errors.push(e.message));
    p.on('dialog', d => d.accept());
    await login(p);
    const api = (path, data) => p.request.post(base + path, {data})
      .then(r => { assert.equal(r.status(), 200, path + ': ' + r.status()); return r.json(); });

    /* ---- 1. 周期页点某天 -> 记录页 -> 返回 = 周期主页（主诉场景） ---- */
    await p.locator('.bottom-nav [data-act="switch-view"][data-v="cycle2"]').click();
    await p.locator('#cycle2Content .c2-card').first().waitFor();
    assert.deepEqual(await state(p), {type: 'view', v: 'cycle2'});
    const d0 = new Date(), ymd = [d0.getFullYear(), String(d0.getMonth() + 1).padStart(2, '0'),
      String(d0.getDate()).padStart(2, '0')].join('-');
    await p.locator('.c2-cal-cell[data-date="' + ymd + '"]').click();
    await RECORD_SHEET().waitFor();
    assert.deepEqual(await state(p), {type: 'modal', id: 'cycle2-record'});
    console.log('PASS record sheet pushes its own entry');
    await goBackExpect(p, {type: 'view', v: 'cycle2'}, 'cycle main from record sheet');
    await CYCLE_MAIN().waitFor({state: 'visible', timeout: 5000});
    await RECORD_SHEET().waitFor({state: 'detached', timeout: 5000});
    assert.equal(await view(p), 'cycle2');

    // 记录行 -> 记录页 -> 完成 保存后停留记录页 -> 返回仍回周期主页
    await CYCLE_MAIN().click();
    await RECORD_SHEET().waitFor();
    await p.locator('#c2SaveBtn').click();
    await p.waitForSelector('text=已保存');
    assert.deepEqual(await state(p), {type: 'modal', id: 'cycle2-record'});
    await goBackExpect(p, {type: 'view', v: 'cycle2'}, 'cycle main after 完成');
    await RECORD_SHEET().waitFor({state: 'detached', timeout: 5000});
    await CYCLE_MAIN().waitFor({timeout: 5000});
    // 周期主页再返回 = 上一视图（课表）
    await goBackExpect(p, null, 'boot level');
    await WEEK_FAB().waitFor({state: 'visible', timeout: 5000});
    assert.equal(await view(p), 'week');
    console.log('PASS back from cycle main -> week');

    /* ---- 2. 记新日程：保存后不留弹窗历史条目 ---- */
    await WEEK_FAB().click();
    await p.locator('#ovAdd.show').waitFor();
    assert.equal((await state(p)).type, 'modal');
    await p.locator('#f_title').fill('导航测试日程');
    await p.locator('#addForm button.p-btn.pri').click();
    await p.waitForSelector('text=已记下啦');
    await p.waitForFunction(() => history.state === null, null, {timeout: 5000});
    await WEEK_FAB().waitFor({state: 'visible', timeout: 5000});
    console.log('PASS add event save consumes the modal entry');

    /* ---- 3. 详情 -> 编辑：历史条目被顶替，返回一层即回课表 ---- */
    await p.locator('.ev[data-act="detail"]').first().click();
    await p.locator('#ovDetail.show').waitFor();
    assert.equal((await state(p)).id, '#ovDetail');
    await p.locator('[data-act="edit-event"]').first().click();
    await p.locator('#ovAdd.show').waitFor();
    assert.equal((await state(p)).id, '#ovAdd', 'edit modal replaces the detail entry');
    console.log('PASS edit-from-detail replaces the entry');
    await goBackExpect(p, null, 'boot level after edit modal');
    await WEEK_FAB().waitFor({state: 'visible', timeout: 5000});
    assert.equal(await view(p), 'week');

    /* ---- 4. 详情 -> 删除：确认框手势返回只关确认框；删除后回到课表 ---- */
    await p.locator('.ev[data-act="detail"]').first().click();
    await p.locator('#ovDetail.show').waitFor();
    await p.locator('[data-act="del-event"]').first().click();
    await p.locator('.overlay.uconfirm.show').waitFor();
    await goBackExpect(p, {type: 'modal', id: '#ovDetail'}, 'detail behind dismissed confirm');
    await p.locator('.overlay.uconfirm.show').waitFor({state: 'detached', timeout: 3000});
    await p.locator('#ovDetail.show').waitFor();
    console.log('PASS gesture back dismisses confirm, detail untouched');
    await p.locator('[data-act="del-event"]').first().click();
    await p.locator('.overlay.uconfirm.show').waitFor();
    await p.locator('.confirm-btn.ok').click();
    await p.waitForSelector('text=已删除日程');
    await p.waitForFunction(() => history.state === null, null, {timeout: 5000});
    await WEEK_FAB().waitFor({state: 'visible', timeout: 5000});
    console.log('PASS delete-event lands on week with clean history');

    /* ---- 5. 旅行清单下钻：返回 = 列表；页签重进 = 列表层 ---- */
    const trip = await api('/api/trips', {action: 'create', destination: '导航测试',
      start_date: '2027-01-10', end_date: '2027-01-14', use_template: false});
    await p.locator('.bottom-nav [data-act="switch-view"][data-v="month"]').click();
    await p.locator('.seg [data-act="switch-view"][data-v="trips"]').click();
    await p.locator('[data-act="trip-new-toggle"]').waitFor();
    assert.deepEqual(await state(p), {type: 'view', v: 'trips'});
    await p.locator('[data-act="trip-open"]').first().click();
    await p.locator('[data-act="trip-back"]').waitFor();
    assert.equal((await state(p)).trip, trip.id, 'trip detail carries the trip id');
    await goBackExpect(p, {type: 'view', v: 'trips'}, 'trip list from detail');
    await p.locator('[data-act="trip-new-toggle"]').waitFor({state: 'visible', timeout: 5000});
    assert.equal(await p.evaluate(() => TRIP_ID), null);
    console.log('PASS back from trip detail -> list');
    // 页签切走再切回，必须落在列表层
    await p.locator('[data-act="trip-open"]').first().click();
    await p.locator('[data-act="trip-back"]').waitFor();
    await p.locator('.seg [data-act="switch-view"][data-v="wishes"]').click();
    await p.locator('.fab[data-act="open-add-wish"]').waitFor();
    await p.locator('.seg [data-act="switch-view"][data-v="trips"]').click();
    await p.locator('[data-act="trip-new-toggle"]').waitFor();
    assert.equal(await p.evaluate(() => TRIP_ID), null, 'tab re-entry lands on list level');
    console.log('PASS trips tab re-entry shows list');
    await api('/api/trips', {action: 'delete', id: trip.id});

    /* ---- 6. 心愿 -> 记手账：返回回心愿页 ---- */
    const wish = await api('/api/wishes', {creator_uid: 'a', title: '导航测试心愿',
      category: 'food', priority: 'low', note: ''});
    await api('/api/wishes/toggle', {id: wish.id});
    await p.locator('.seg [data-act="switch-view"][data-v="wishes"]').click();
    await p.locator('[data-act="wish-to-diary"]').first().waitFor();
    // 取消按钮路径：关弹窗落在手账页，一次返回回心愿页
    await p.locator('[data-act="wish-to-diary"]').first().click();
    await p.locator('#ovAddDiary.show').waitFor();
    assert.equal(await view(p), 'month');
    assert.deepEqual(await state(p), {type: 'modal', id: '#ovAddDiary'});
    await p.locator('#ovAddDiary [data-act="close"]').click();
    await p.waitForFunction(() => history.state && history.state.v === 'month', null,
      {timeout: 5000});
    await p.locator('.fab[data-act="open-add-diary"]').waitFor({state: 'visible', timeout: 5000});
    await goBackExpect(p, {type: 'view', v: 'wishes'}, 'wishes from month');
    // 手势返回路径：重开一次，返回关弹窗落手账页，再一次返回回心愿页
    await p.locator('[data-act="wish-to-diary"]').first().click();
    await p.locator('#ovAddDiary.show').waitFor();
    await goBackExpect(p, {type: 'view', v: 'month'}, 'month view behind diary modal');
    await p.locator('.fab[data-act="open-add-diary"]').waitFor({state: 'visible', timeout: 5000});
    await goBackExpect(p, {type: 'view', v: 'wishes'}, 'wishes again');
    await p.locator('.fab[data-act="open-add-wish"]').waitFor({state: 'visible', timeout: 5000});
    console.log('PASS wish-to-diary round trip');
    await api('/api/wishes/delete', {id: wish.id});

    /* ---- 7. 设置页有未保存修改：返回手势先询问 ---- */
    await p.locator('.brand').click(); // 回到课表，设置页的上一层是课表
    await WEEK_FAB().waitFor({state: 'visible', timeout: 5000});
    await p.locator('.topbar [data-act="switch-view"][data-v="settings"]').click();
    await p.locator('#st-profile').waitFor();
    await p.locator('#st-profile summary').click();
    await p.locator('#st-profile input[name="name_a"]').waitFor({state: 'visible'});
    await p.locator('#st-profile input[name="name_a"]').fill('导航金毛');
    await p.goBack();
    await p.locator('.overlay.uconfirm.show').waitFor({timeout: 5000});
    assert.equal(await view(p), 'settings', 'guard keeps the user on settings');
    console.log('PASS dirty settings back asks first');
    await p.locator('.confirm-btn.ok').click(); // 离开
    await WEEK_FAB().waitFor({state: 'visible', timeout: 5000});
    assert.equal(await view(p), 'week');
    console.log('PASS leaving dirty settings lands on week');

    assert.deepEqual(errors, [], 'page errors: ' + errors.join(' | '));
    console.log('ALL NAV BROWSER CHECKS PASSED');
  } finally {
    await browser.close();
  }
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
