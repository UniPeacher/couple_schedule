'use strict';
// Acceptance tests against isolated tests/browser_server.py only, never production.
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const base=process.env.CYCLE_TEST_URL||'http://127.0.0.1:18796';
assert.ok(base.includes(':18796'),'Do not run destructive fixture tests against production');
const out=path.resolve(__dirname,'../output');
function ago(n){const d=new Date();d.setDate(d.getDate()-n);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;}
async function snap(p){const r=await p.request.get(base+'/api/cycle');assert.equal(r.status(),200);return r.json();}
async function post(p,action,data){const r=await p.request.post(base+'/api/cycle/'+action,{data});assert.equal(r.status(),200,await r.text());return r.json();}
async function refresh(p){const before=await p.locator('body').getAttribute('data-cycle-ready');const response=p.waitForResponse(r=>r.url().includes('/api/cycle?')&&r.status()===200);await p.evaluate(()=>Cycle.mount());await response;await p.waitForFunction(v=>document.body.getAttribute('data-cycle-ready')!==v,before);await p.waitForFunction(()=>!!document.querySelector('#cyclePreferencesForm'));}
async function expand(p,selector){await p.locator(selector).evaluate(el=>{for(let q=el.parentElement;q;q=q.parentElement)if(q.tagName==='DETAILS')q.open=true;});}
async function submit(p,form,action){
  await expand(p,form);
  const response=p.waitForResponse(r=>r.url().endsWith('/api/cycle/'+action)&&r.request().method()==='POST');
  await p.locator(form+' button:not([type="button"])').click();
  const r=await response;assert.equal(r.status(),200,await r.text());
  await p.waitForFunction(()=>!document.body.hasAttribute('data-cycle-saving'));
  await p.locator('#cyclePreferencesForm').waitFor();
}
async function clickSave(p,selector,action){
  await expand(p,selector);const response=p.waitForResponse(r=>r.url().endsWith('/api/cycle/'+action)&&r.request().method()==='POST');
  await p.locator(selector).click();const r=await response;assert.equal(r.status(),200,await r.text());await p.waitForFunction(()=>!document.body.hasAttribute('data-cycle-saving'));
}
(async()=>{
  fs.mkdirSync(out,{recursive:true});const errors=[];
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/home/uni/.local/bin/chromium',headless:true,args:['--no-sandbox']});
  try{
    const a=await(await browser.newContext({viewport:{width:390,height:844}})).newPage();
    const b=await(await browser.newContext({viewport:{width:390,height:844}})).newPage();
    for(const [p,uid] of [[a,'a'],[b,'b']]){
      p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());
      await p.goto(base);await p.locator('[data-act="pick-login"][data-uid="'+uid+'"]').click();await p.locator('#loginPw').fill('cycle-test-password');await p.locator('#loginForm button').click();
      await p.locator('#cycleHome [data-cycle="open"]').waitFor();
      const layout=await p.evaluate(()=>({after:!!(document.querySelector('main').compareDocumentPosition(document.querySelector('#cycleHome'))&Node.DOCUMENT_POSITION_FOLLOWING),top:document.querySelector('#cycleHome').getBoundingClientRect().top,height:innerHeight}));
      assert.equal(layout.after,true);assert.ok(layout.top>=layout.height,JSON.stringify(layout));
      await p.locator('#cycleHome [data-cycle="open"]').click();await p.locator('#cyclePreferencesForm').waitFor();
      await post(p,'delete-all',{confirm:'DELETE_MY_CYCLE_DATA'});await refresh(p);
    }
    console.log('PASS home card is below timetable and below mobile first viewport');
    await a.locator('#cyclePreferencesForm [name="role"]').selectOption('supporter');
    await a.locator('#cyclePreferencesForm [name="enabled"]').check();
    await submit(a,'#cyclePreferencesForm','preferences');
    assert.equal((await snap(a)).preferences.role,'supporter');
    await expand(b,'#cycleSettingsForm');
    for(const name of ['share_actual','share_prediction','share_details','allow_proxy','allow_notifications'])await b.locator('#cycleSettingsForm [name="'+name+'"]').check();
    await b.locator('#cycleSettingsForm [name="share_since"]').fill(ago(100));
    await submit(b,'#cycleSettingsForm','sharing');
    await refresh(a);assert.ok((await snap(a)).partner.can_proxy);
    console.log('PASS recorder consent and independently chosen supporter role/preferences');
    for(const age of [80,52,24]){
      await b.locator('[data-cycle="add"]').click();await b.locator('#cyclePeriodForm [name="start_date"]').fill(ago(age));await b.locator('#cyclePeriodForm [name="end_date"]').fill(ago(age-4));await submit(b,'#cyclePeriodForm','period');
    }
    await refresh(a);assert.equal((await snap(a)).partner.trend.median,28);assert.equal((await snap(a)).partner.trend.spread,0);
    assert.equal((await snap(a)).partner.prediction.available,true);
    await a.locator('[data-cycle="proxy"]').click();await submit(a,'#cyclePeriodForm','proposal');
    assert.equal((await snap(b)).periods.length,3);assert.equal((await snap(a)).proposals.length,1);
    const noticesBefore=await a.request.get(base+'/api/messages');assert.equal((await noticesBefore.json()).messages.length,0);
    await refresh(b);await b.locator('[data-cycle="review"]').click();await submit(b,'#cyclePeriodForm','proposal/confirm');
    let formal=(await snap(b)).periods[0];assert.equal(formal.source.author_uid,'a');assert.equal(formal.source.confirmed_by,'b');
    const notices=await(await a.request.get(base+'/api/messages')).json();assert.equal(notices.messages.length,1);assert.equal(notices.messages[0].content,'身体记录有更新，打开后查看。');
    await refresh(a);assert.equal((await snap(a)).partner.status.day,1);
    console.log('PASS proxy pending excluded from calculations/notifications; recorder confirmation preserves authorship');
    await b.locator('#cycleDayForm [name="pain"]').selectOption('中');await b.locator('#cycleDayForm [name="flow"]').selectOption('中');
    await b.locator('#cycleDayForm [name="symptoms"][value="疲惫"]').check();
    await b.locator('#cycleDayForm [name="medication"]').fill('synthetic medication record');await b.locator('#cycleDayForm [name="relief"]').fill('热敷');
    await b.locator('#cycleDayForm [name="effectiveness"]').selectOption('有缓解');await b.locator('#cycleDayForm [name="note"]').fill('PRIVATE-NOTE <img src=x onerror="window.CYCLE_XSS=true">');
    await submit(b,'#cycleDayForm','day');await refresh(a);
    let shared=(await snap(a)).partner.days[0];assert.equal(shared.pain,'中');assert.equal(shared.medication,'synthetic medication record');assert.equal('note' in shared,false);
    assert.equal((await(await a.request.get(base+'/api/messages')).json()).messages.length,2);
    assert.equal(await b.evaluate(()=>!!window.CYCLE_XSS),false);
    await b.locator('#cycleDayForm [name="note_shared"]').check();await submit(b,'#cycleDayForm','day');
    assert.ok((await snap(a)).partner.days[0].note.includes('PRIVATE-NOTE'));
    assert.equal((await(await a.request.get(base+'/api/messages')).json()).messages.length,2);
    await b.locator('#cycleDayForm [name="note_shared"]').uncheck();await submit(b,'#cycleDayForm','day');
    assert.equal('note' in (await snap(a)).partner.days[0],false);
    console.log('PASS structured details shared, private notes opt-in, moderate pain neutral notification, no duplicates or XSS');
    await refresh(a);await a.locator('[data-cycle="proxy-edit"][data-id="'+formal.id+'"]').click();
    await a.locator('#cyclePeriodForm [name="end_date"]').fill(ago(0));await submit(a,'#cyclePeriodForm','proposal');
    assert.equal((await snap(b)).active.end_date,null);
    await refresh(b);await b.locator('[data-cycle="review"]').click();await submit(b,'#cyclePeriodForm','proposal/confirm');
    assert.equal((await snap(b)).active,null);
    await refresh(a);await a.evaluate(()=>window.scrollTo(0,0));await a.screenshot({path:path.join(out,'cycle-v2-supporter.png')});
    await a.locator('[data-act="toggle-dark"]').click();await a.screenshot({path:path.join(out,'cycle-v2-dark.png')});
    assert.equal(await a.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await a.setViewportSize({width:1280,height:900});await a.screenshot({path:path.join(out,'cycle-v2-desktop.png')});
    console.log('PASS proxy end confirmation, mobile/desktop and dark rendering');
    const current=(await snap(b));
    const month=current.prediction.start.slice(0,7);
    await b.evaluate((month)=>{MONTH_ANCHOR=month;CURRENT_VIEW='month';MONTH_DATA=null;return load();},month);
    await b.locator('.cy-mark.cy-predicted').first().waitFor();
    assert.ok(await b.locator('.cy-mark.cy-predicted').count()>0);
    await b.evaluate(()=>{MONTH_ANCHOR=todayISO().slice(0,7);MONTH_DATA=null;return load();});
    await b.locator('.cy-mark.cy-actual').first().waitFor();
    const chosen=await b.locator('.cy-mark.cy-actual').first().getAttribute('data-date');await b.locator('.cy-mark.cy-actual').first().click();await b.locator('#cycleDayForm').waitFor();
    assert.equal(await b.locator('#cycleDayForm [name="date"]').inputValue(),chosen);
    const leaks=await b.evaluate(()=>Object.values(localStorage).some(v=>v.includes('PRIVATE-NOTE')||v.includes('synthetic medication record')));assert.equal(leaks,false);
    await expand(b,'[data-cycle="export"]');const download=b.waitForEvent('download');await b.locator('[data-cycle="export"]').click();const f=await download;
    const data=JSON.parse(fs.readFileSync(await f.path(),'utf8'));assert.equal(data.version,2);assert.equal(data.periods.length,4);assert.equal(data.days[0].note_shared,0);
    console.log('PASS calendar navigation, actual vs predicted layers, real JSON export and no health localStorage');
    await refresh(b);await clickSave(b,'[data-cycle="revoke-sharing"]','sharing');
    assert.equal((await snap(a)).partner,null);assert.equal((await snap(a)).proposals.length,0);
    assert.equal((await(await a.request.get(base+'/api/messages')).json()).messages.length,0);
    await refresh(a);assert.equal(await a.locator('[data-cycle="proxy"]').count(),0);
    console.log('PASS revoke immediately removes API access, proxy permission and queued health notifications');
    await clickSave(b,'[data-cycle="delete-all"]','delete-all');assert.equal((await snap(b)).periods.length,0);assert.equal((await snap(b)).days.length,0);
    assert.deepEqual(errors,[]);console.log('BROWSER_TESTS_OK: collaborative acceptance scenarios passed, no JavaScript errors');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
