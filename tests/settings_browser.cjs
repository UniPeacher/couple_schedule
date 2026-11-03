'use strict';
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const base='http://127.0.0.1:18796';
async function get(p){const r=await p.request.get(base+'/api/settings');assert.equal(r.status(),200);return r.json();}
async function open(p,scope){await p.locator('#st-'+scope+'>summary').click();}
async function save(p,scope){const f=p.locator('[data-settings-form="'+scope+'"]');const r=p.waitForResponse(r=>r.url().endsWith('/api/settings')&&r.request().method()==='POST');await f.locator('button:not([type=button])').click();assert.equal((await r).status(),200);await f.locator('.st-result[data-state="saved"]').waitFor();}
(async()=>{fs.mkdirSync('output',{recursive:true});const errors=[];const browser=await chromium.launch({executablePath:'/home/uni/.local/bin/chromium',headless:true,args:['--no-sandbox']});try{
const ctx=await browser.newContext({viewport:{width:390,height:844}});const p=await ctx.newPage();p.on('pageerror',e=>errors.push(e.message));p.on('dialog',d=>d.accept());
await p.addInitScript(()=>localStorage.setItem('sched_week_legacy',{toString:()=>JSON.stringify({settings:{llm_api_key:'LEGACY_KEY'},users:{a:{wx_uid:'LEGACY_TOKEN'}}})}.toString()));
await p.goto(base);await p.locator('#loginPw').fill('cycle-test-password');await p.locator('#loginForm button').click();await p.locator('[data-v="settings"]').last().click();await p.locator('#st-profile').waitFor();
assert.equal(await p.locator('.st-group').count(),7);assert.equal(await p.locator('#st-advanced').getAttribute('open'),null);assert.equal(await p.locator('#st-security').getAttribute('open'),null);
assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
assert.equal(await p.evaluate(()=>Object.values(localStorage).some(v=>v.includes('LEGACY_KEY')||v.includes('LEGACY_TOKEN'))),false);
await p.screenshot({path:'output/settings-mobile.png',fullPage:true});
console.log('PASS seven clear groups, advanced/security collapsed, mobile width, legacy-secret cache cleanup');
let before=await get(p);await open(p,'profile');await p.locator('[name="name_a"]').fill('测试昵称 A');await p.locator('[name="name_b"]').fill('测试昵称 B');await save(p,'profile');let after=await get(p);assert.equal(after.users.a.name,'测试昵称 A');assert.deepEqual(after.settings,before.settings);assert.equal(await p.locator('[name="old"]').inputValue(),'');
console.log('PASS profile group saved and read back independently');
await open(p,'schedule');await p.locator('[name="week1_a"]').fill('2026-08-31');await p.locator('[name="week1_b"]').fill('2026-09-07');await p.locator('[name="window_start"]').fill('09:00');await p.locator('[name="window_end"]').fill('21:00');await p.locator('[name="min_gap"]').fill('35');await save(p,'schedule');after=await get(p);assert.equal(after.settings.min_gap,35);assert.equal(after.users.a.name,'测试昵称 A');
await p.locator('[name="window_end"]').fill('08:00');const failed=p.waitForResponse(r=>r.url().endsWith('/api/settings')&&r.request().method()==='POST');await p.locator('[data-settings-form=schedule] button').click();assert.equal((await failed).status(),400);await p.locator('[data-settings-form=schedule] .st-result[data-state=error]').waitFor();assert.equal((await get(p)).settings.window_end,'21:00');await p.locator('[name="window_end"]').fill('21:00');await save(p,'schedule');
console.log('PASS schedule independent save, invalid-window error and no accidental writes');
await open(p,'notifications');await p.locator('[name=wx_token]').fill('TEST_TOKEN_SECRET');await save(p,'notifications');assert.equal((await get(p)).users.a.wx_configured,true);assert.equal(await p.locator('[name=wx_token]').inputValue(),'');
await save(p,'notifications');assert.equal((await get(p)).users.a.wx_configured,true);
await p.locator('[name=clear_wx]').check();await save(p,'notifications');assert.equal((await get(p)).users.a.wx_configured,false);
console.log('PASS personal push token replace/keep/clear with no value echo');
await open(p,'advanced');await p.locator('[name=llm_api_base]').fill('https://example.invalid/v1');await p.locator('[name=llm_model]').fill('fixture-model');await p.locator('[name=llm_api_key]').fill('TEST_API_SECRET');await save(p,'ai');assert.equal((await get(p)).settings.llm_key_configured,true);assert.equal(await p.locator('[name=llm_api_key]').inputValue(),'');await save(p,'ai');assert.equal((await get(p)).settings.llm_key_configured,true);
for(const route of ['/api/settings','/api/week','/api/month?month=2026-10','/']){const r=await p.request.get(base+route);const text=await r.text();assert.equal(text.includes('TEST_API_SECRET'),false);assert.equal(text.includes('TEST_TOKEN_SECRET'),false);}
console.log('PASS admin AI group replace/keep secrets, no credential leak through SSR/week/month/settings');
await open(p,'security');await p.locator('[name=old]').fill('cycle-test-password');await p.locator('[name=new]').fill('new-fixture-password');await p.locator('[name=repeat]').fill('mismatch-password');await p.locator('[data-settings-form=password] button').click();await p.locator('[data-settings-form=password] .st-result[data-state=error]').waitFor();await p.locator('[name=repeat]').fill('new-fixture-password');const pw=p.waitForResponse(r=>r.url().endsWith('/api/password'));await p.locator('[data-settings-form=password] button').click();assert.equal((await pw).status(),200);await p.locator('[data-settings-form=password] .st-result[data-state=saved]').waitFor();assert.equal(await p.locator('[name=new]').inputValue(),'');const login=await p.request.post(base+'/api/login',{data:{uid:'a',password:'new-fixture-password'}});assert.equal(login.status(),200);
console.log('PASS password form separate, repeat validation, real login with new password');
await p.locator('[data-act=set-theme][data-theme=dark]').click();assert.equal(await p.evaluate(()=>document.documentElement.classList.contains('dark')),true);await p.screenshot({path:'output/settings-dark.png',fullPage:true});await p.setViewportSize({width:1280,height:900});await p.screenshot({path:'output/settings-desktop.png',fullPage:true});
await p.locator('#st-features [data-cycle=open]').click();await p.locator('#cyclePreferencesForm').waitFor();await p.locator('[data-v=settings]').last().click();await p.locator('#st-features').waitFor();await p.locator('#st-features [data-act=open-anniv-list]').click();await p.locator('#annivForm').waitFor();
console.log('PASS themes and functional management navigation');
const b=await(await browser.newContext({viewport:{width:390,height:844}})).newPage();b.on('pageerror',e=>errors.push(e.message));await b.goto(base);await b.locator('[data-act=pick-login][data-uid=b]').click();await b.locator('#loginPw').fill('cycle-test-password');await b.locator('#loginForm button').click();await b.locator('[data-v=settings]').last().click();await b.locator('#st-advanced').waitFor();assert.equal(await b.locator('[data-settings-form=ai]').count(),0);const forbidden=await b.request.post(base+'/api/settings',{data:{scope:'ai',llm_api_base:'https://example.invalid'}});assert.equal(forbidden.status(),403);
console.log('PASS second-account permissions and actual backend authorization');
assert.deepEqual(errors,[]);console.log('SETTINGS_BROWSER_OK: all acceptance scenarios passed, no JavaScript errors');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
