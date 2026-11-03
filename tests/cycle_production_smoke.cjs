'use strict';
// Production verification is read-only. Token stays in memory, never printed or saved.
const {chromium}=require('playwright');
const {execFileSync}=require('node:child_process');
const assert=require('node:assert/strict');
(async()=>{
  const token=execFileSync('sudo',['-n','docker','exec','sched-share','python','-c',"import app; app.SECRET=app.load_secret(); print(app.make_token('a'))"],{encoding:'utf8'}).trim();
  const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||'/home/uni/.local/bin/chromium',headless:true,args:['--no-sandbox']});
  try{
    const context=await browser.newContext({viewport:{width:390,height:844}});
    await context.addCookies([{name:'sched_sess',value:token,url:'http://127.0.0.1:8795',httpOnly:true,sameSite:'Lax'}]);
    const page=await context.newPage();
    const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.goto('http://127.0.0.1:8795');
    await page.locator('#cycleHome [data-cycle="open"]').waitFor();
    const layout=await page.evaluate(()=>({after:!!(document.querySelector('main').compareDocumentPosition(document.querySelector('#cycleHome'))&Node.DOCUMENT_POSITION_FOLLOWING),below:document.querySelector('#cycleHome').getBoundingClientRect().top>=innerHeight}));
    assert.equal(layout.after,true);assert.equal(layout.below,true);
    await page.locator('#cycleHome [data-cycle="open"]').click();
    await page.locator('#cyclePreferencesForm').waitFor();
    const r=await context.request.get('http://127.0.0.1:8795/api/cycle');
    assert.equal(r.status(),200);assert.ok(r.headers()['cache-control'].includes('no-store'));
    const s=await r.json();assert.equal(s.periods.length,0);assert.equal(s.days.length,0);assert.equal(s.partner,null);
    await page.screenshot({path:'output/cycle-production-empty.png'});
    await page.locator('[data-act="switch-view"][data-v="settings"]').last().click();
    await page.locator('.cy-entry').waitFor();
    await page.locator('.cy-entry').click();
    await page.locator('#cycleDayForm').waitFor();
    assert.deepEqual(errors,[]);
    console.log('PRODUCTION_SMOKE_OK: homepage/settings entrances, authenticated private API, no-store, empty personal records, mobile render; no production writes');
  }finally{await browser.close()}
})().catch(e=>{console.error(e.message);process.exitCode=1});
