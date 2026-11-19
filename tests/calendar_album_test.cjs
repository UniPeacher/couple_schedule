const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '..', 'web', 'app.js'), 'utf8');
const section = source.slice(source.indexOf('var DIARY_CATEGORIES ='), source.indexOf('function renderApp(){'));
function fixture() {
  let html = '';
  const attrs = {};
  const box = {innerHTML: ''};
  const cell = {getAttribute: () => '2026-05-20', setAttribute(k,v) {attrs[k]=v;}, classList:{toggle() {}}};
  const c = vm.createContext({
    MONTH_ANCHOR:'2026-05', MONTH_SELECTED_DATE:'', MONTH_DATA:null,
    WD:['周一','周二','周三','周四','周五','周六','周日'],
    todayISO:()=> '2026-11-17', pad:n=>String(n).padStart(2,'0'),
    esc:s=>String(s).replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('"','&quot;'),
    renderTopBar:()=>'', diarySegHTML:()=>'', bottomNavHTML:()=>'', modalsHTML:()=>'',
    setAppHTML:s=>{html=s;}, document:{querySelectorAll:()=>[cell]},
    $:s=>s==='#monthDayCard'?box:null,
  });
  vm.runInContext(section,c);
  return {c,box,attrs,html:()=>html};
}
test('loading an uncached month does not lock selection to its first day',()=>{
  const {c}=fixture();
  assert.equal(c.monthSelectedDate(),'2026-05-01');
  assert.equal(c.MONTH_SELECTED_DATE,'');
  c.MONTH_DATA={diaries_by_date:{'2026-05-03':[], '2026-05-20':[]}};
  assert.equal(c.monthSelectedDate(),'2026-05-20');
});
test('current month selects today and existing user selection survives refresh',()=>{
  const {c}=fixture(); c.MONTH_ANCHOR='2026-11'; c.MONTH_DATA={diaries_by_date:{}};
  assert.equal(c.monthSelectedDate(),'2026-11-17');
  c.MONTH_SELECTED_DATE='2026-11-04';
  assert.equal(c.monthSelectedDate(),'2026-11-04');
});
test('month renders accessible date buttons, node counts, full title and cover outside grid',()=>{
  const {c,html}=fixture();
  c.MONTH_DATA={total_count:1,diaries_by_date:{'2026-05-20':[{title:'一起看海 <3',cover_photo:'a.jpg',photo_count:2,content:'记得那阵海风',author_uid:'a'}]},nodes_by_date:{'2026-05-20':[{label:'一周年',kind:'anniversary',icon:'💖',detail:'在一起满1年'},{label:'520',kind:'romantic',icon:'💕',detail:'每年5月20日'}]}};
  c.renderMonthApp(); const s=html();
  assert.equal((s.match(/data-act="select-month-day"/g)||[]).length,31);
  assert.match(s,/一周年<\/span><span class="mday-node-more">\+1/);
  assert.match(s,/一起看海 &lt;3/);
  assert.match(s,/aria-pressed="true"/);
  assert.match(s,/aria-label="记录这一天"><svg[^>]+viewBox="0 0 24 24"/);
  assert.match(s,/aria-label="上一月"><svg/);
  assert.match(s,/aria-label="下一月"><svg/);
  assert(!s.slice(s.indexOf('class="month-grid"'),s.indexOf('class="album-calendar-foot"')).includes('<img'));
  assert(s.indexOf('class="album-cover"')>s.indexOf('id="monthDayCard"'));
});
test('selecting a date updates panel without full render',()=>{
  const {c,box,attrs}=fixture(); c.MONTH_DATA={diaries_by_date:{},nodes_by_date:{}};
  c.selectMonthDay('2026-05-20');
  assert.equal(c.MONTH_SELECTED_DATE,'2026-05-20');
  assert.equal(attrs['aria-pressed'],'true');
  assert.match(box.innerHTML,/5 月 20 日/);
  assert.match(box.innerHTML,/data-date="2026-05-20"/);
  c.selectMonthDay('2026-06-01');
  assert.equal(c.MONTH_SELECTED_DATE,'2026-05-20');
});
