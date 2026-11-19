const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const start = source.indexOf('function detailHTML(');
const end = source.indexOf('function refreshDetail(', start);
function render(overrides = {}) {
  const context = vm.createContext({
    esc: s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),
    WD: ['周一','周二','周三','周四','周五','周六','周日'],
    curDetail: {id: 1, date: '2026-11-17'}, ME: {uid: 'a'},
  });
  vm.runInContext(source.slice(start, end), context);
  return context.detailHTML({event: {id:1, title:'课程 <主题>', repeat:'weekly', weekday:1,
    tstart:'09:00', tend:'10:30', location:'A & B', note:'记得带书', week_spec:'1-16'},
    owner:{uid:'a',name:'小金毛'}, comments:[], ...overrides});
}
test('schedule detail separates schedule, messages and actions with escaped content', () => {
  const html = render();
  for (const cls of ['detail-head','detail-schedule','detail-messages','detail-foot']) assert.match(html, new RegExp(cls));
  assert.match(html, /课程 &lt;主题&gt;/);
  assert.match(html, /A &amp; B/);
  assert.match(html, /每周二 · 1-16周/);
  assert.match(html, /aria-label="关闭弹窗"/);
  assert.match(html, /aria-label="情侣留言"/);
  assert.match(html, /<button type="button" class="phrase-pill"/);
  for (const action of ['edit-week','send-cmt','edit-event','del-event','close']) assert.match(html, new RegExp('data-act="' + action + '"'));
});
test('adjusted occurrence shows actual time, location and undo action', () => {
  const html = render({occurrence:{occ_status:'moved_in', moved_from:'2026-11-16', tstart:'15:00', tend:'16:00', location:'新教室', note:'带实验报告'}});
  assert.match(html, /15:00/); assert.match(html, /新教室/); assert.match(html, /11-16/);
  assert.match(html, /data-act="undo-week"/); assert.doesNotMatch(html, /data-act="edit-week"/);
});
test('single shared event keeps date, owner and missing-location state', () => {
  const html = render({event:{id:2,title:'约会',repeat:'once',date:'2026-11-17',tstart:'18:00',tend:'20:00'},owner:{uid:'both',name:'我们'}});
  assert.match(html, /两人共同/); assert.match(html, /单次日程 · 2026-11-17/);
  assert.match(html, /暂未填写地点/); assert.doesNotMatch(html, /data-act="edit-week"/);
});
