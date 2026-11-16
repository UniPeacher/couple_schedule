'use strict';
// 心愿卡片：点开详情、修改后回写。跑在隔离 fixture 上，不碰生产数据。
const {chromium}=require('playwright');
const assert=require('node:assert/strict');
const base='http://127.0.0.1:18798';
(async()=>{
  const browser=await chromium.launch({executablePath:'/home/uni/.local/bin/chromium',headless:true,args:['--no-sandbox']});
  try{
    const ctx=await browser.newContext({viewport:{width:390,height:844}});
    const p=await ctx.newPage();
    const errors=[];
    p.on('pageerror',e=>errors.push(e.message));

    await p.goto(base);
    await p.locator('#loginPw').fill('wishes-test-password');
    await p.locator('#loginForm button').click();
    await p.locator('.bottom-nav').waitFor();

    // fixture 库可能带上一轮留下的心愿，先清空，让断言只依赖本轮
    const existing=await (await p.request.get(base+'/api/wishes')).json();
    for(const w of existing.wishes){
      await p.request.post(base+'/api/wishes/delete',{data:{id:w.id}});
    }

    const created=await p.request.post(base+'/api/wishes',{data:{
      creator_uid:'a', title:'一起去看极光', category:'travel', priority:0, note:'芬兰或冰岛都行'
    }});
    assert.equal(created.status(),200);

    await p.locator('.bottom-nav [data-v="month"]').click();
    await p.locator('.page-container [data-v="wishes"]').click();
    const card=p.locator('.wish-card');
    await card.waitFor();
    assert.equal(await p.locator('.wish-card').count(),1);
    assert.equal(await card.getAttribute('data-act'),'open-wish');

    // 1. 点卡片打开详情
    await card.click();
    await p.locator('#ovWishDetail.show').waitFor();
    const detail=p.locator('#ovWishDetailBox');
    const t1=await detail.textContent();
    assert.ok(t1.includes('一起去看极光'));
    assert.ok(t1.includes('芬兰或冰岛都行'));
    assert.ok(t1.includes('旅行'));
    assert.ok(t1.includes('⏳ 奔赴中'));
    assert.ok(t1.includes('小金毛'));

    // 2. 进入修改
    await p.locator('[data-act="edit-wish-detail"]').click();
    await p.locator('#we_title').waitFor();
    assert.equal(await p.locator('#we_title').inputValue(),'一起去看极光');
    assert.equal(await p.locator('#we_note').inputValue(),'芬兰或冰岛都行');
    assert.equal(await p.locator('#wishEditCatSeg .opt.on').getAttribute('data-v'),'travel');

    // 3. 改内容并保存
    await p.locator('#we_title').fill('一起去冰岛看极光');
    await p.locator('#we_note').fill('寒假，预算两万');
    await p.locator('#wishEditCatSeg .opt[data-v="life"]').click();
    await p.locator('#wishEditPrioSeg .opt[data-v="1"]').click();
    const saved=p.waitForResponse(r=>r.url().endsWith('/api/wishes/update')&&r.request().method()==='POST');
    await p.locator('[data-act="save-edit-wish"]').click();
    assert.equal((await saved).status(),200);

    // 4. 详情回到只读态并显示新值
    await p.locator('#we_title').waitFor({state:'detached'});
    const t2=await detail.textContent();
    assert.ok(t2.includes('一起去冰岛看极光'));
    assert.ok(t2.includes('寒假，预算两万'));
    assert.ok(t2.includes('生活'));
    assert.ok(t2.includes('超级想去'));

    // 5. 列表卡片同步
    const cardText=await p.locator('.wish-card').first().textContent();
    assert.ok(cardText.includes('一起去冰岛看极光'));
    assert.ok(cardText.includes('寒假，预算两万'));
    assert.equal(await p.locator('.wish-card').first().getAttribute('class'),'wish-card priority');

    // 6. 服务端已落库
    const list=await (await p.request.get(base+'/api/wishes')).json();
    assert.equal(list.wishes.length,1);
    assert.equal(list.wishes[0].title,'一起去冰岛看极光');
    assert.equal(list.wishes[0].note,'寒假，预算两万');
    assert.equal(list.wishes[0].category,'life');
    assert.equal(list.wishes[0].priority,1);

    // 7. 刷新后仍在（不是只改了内存）
    await p.reload();
    await p.locator('.bottom-nav [data-v="month"]').click();
    await p.locator('.page-container [data-v="wishes"]').click();
    await p.locator('.wish-card').first().waitFor();
    assert.ok((await p.locator('.wish-card').first().textContent()).includes('一起去冰岛看极光'));

    // 8. 详情里标记实现，状态跟着变
    await p.locator('.wish-card').first().click();
    await p.locator('#ovWishDetail.show').waitFor();
    await p.locator('[data-act="wish-detail-toggle"]').click();
    await p.locator('[data-act="wish-detail-toggle"]').waitFor({state:'detached'});
    assert.ok((await detail.textContent()).includes('🎉 已实现'));

    // 9. 关闭弹窗
    await p.locator('#ovWishDetail [data-act="close"]').click();
    await p.locator('#ovWishDetail.show').waitFor({state:'detached'});

    assert.deepEqual(errors,[]);

    await p.screenshot({path:'output/wishes-cards.png',fullPage:true});
    await p.locator('.wish-card').first().click();
    await p.locator('#ovWishDetail.show').waitFor();
    await p.screenshot({path:'output/wishes-detail.png'});
    await p.locator('[data-act="edit-wish-detail"]').click();
    await p.locator('#we_title').waitFor();
    await p.screenshot({path:'output/wishes-edit.png'});
    console.log('PASS wish detail opens, edit saves to server, list and detail stay in sync, survives reload, toggle works');
  }finally{ await browser.close(); }
})().catch(e=>{console.error(e.message);process.exitCode=1;});