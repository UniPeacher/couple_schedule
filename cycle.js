/* Health state stays in memory: no localStorage, shared events or AI summaries. */
var Cycle = (function () {
  'use strict';
  var state=null, owner=null, sequence=0, selectedDate=null;
  var symptoms=['疲惫','腹胀','头痛','腰酸','乳房胀痛','恶心'];
  function h(x){return esc(x);}
  function btn(label,action,attrs){return '<button type="button" class="p-btn" data-cycle="'+action+'" '+(attrs||'')+'>'+label+'</button>';}
  function field(label,input){return '<label class="cy-field"><span>'+label+'</span>'+input+'</label>';}
  function input(name,value,attrs){return '<input name="'+name+'" value="'+h(value||'')+'" '+(attrs||'')+'>';}
  function dt(name,value,required,max){return input(name,value,'type="date"'+(required?' required':'')+(max?' max="'+max+'"':''));}
  function check(name,label,on){return '<label class="cy-check"><input type="checkbox" name="'+name+'"'+(on?' checked':'')+'><span>'+label+'</span></label>';}
  function select(name,items,value){return '<select name="'+name+'"><option value="">未记录</option>'+items.map(function(x){return '<option'+(x===value?' selected':'')+'>'+h(x)+'</option>';}).join('')+'</select>';}
  function name(uid){var u=((META&&META.users)||[]).find(function(x){return x.uid===uid;});return u?u.name:'伴侣';}
  function go(){history.pushState({type:'view',v:'cycle'},'');CURRENT_VIEW='cycle';load();window.scrollTo(0,0);}
  function emptyPage(){
    $('#app').innerHTML=renderTopBar()+'<div class="page-container cy-page"><div class="page-header-card"><div><div class="page-header-title">🌷 周期与双人关注</div><div class="page-header-desc">她轻松记下，你心里有数 · 不要求每日打卡</div></div></div><div id="cycleContent" aria-live="polite">正在安全加载…</div></div>'+bottomNavHTML()+modalsHTML();
  }
  function trend(t){
    if(!t||!t.intervals.length)return '补充更多已确认历史后，展示周期间隔与波动。';
    return '近期周期 '+t.minimum+'～'+t.maximum+' 天 · 中位数 '+t.median+' 天 · 波动跨度 '+t.spread+' 天（记录统计，非医学判断）';
  }
  function status(s){return s.status?s.status.label:'暂无已确认记录';}
  function prediction(p){return !p?'暂无获授权的预估':p.available?'下次预计 '+p.start+' ～ '+p.end:(p.reason||'暂无可用预估');}
  function daily(d){
    var labels={flow:'经量',pain:'疼痛',mood:'心情',activity:'日常活动',relief:'缓解方式',medication:'用药记录',effectiveness:'效果'};
    return '<article class="cy-day"><strong>'+h(d.date)+' 的记录</strong><p class="cy-muted">'+(d.date===todayISO()?'今天更新':'历史感受，不代表今天仍然存在')+(d.updated_at?' · '+h(d.updated_at.slice(0,16).replace('T',' ')):'')+'</p>'+Object.keys(labels).map(function(k){return d[k]?'<p>'+labels[k]+'：'+h(d[k])+'</p>':'';}).join('')+(d.symptoms.length?'<p>身体感受：'+d.symptoms.map(h).join('、')+'</p>':'')+(d.note?'<p>她分享的备注：'+h(d.note)+'</p>':'')+'</article>';
  }
  function partner(s,compact){
    var p=s.partner;
    if(!p)return '<section class="cy-card"><h3>🤍 关注对方</h3><p class="cy-muted">由记录者在自己的账号开启双人分享。授权前，不会显示对方的身体信息。</p></section>';
    var html='<section class="cy-card cy-hero"><div class="cy-eyebrow">双方授权的周期概览</div><h3>🤍 '+h(name(p.uid))+' 的周期</h3><p><strong>'+h(status(p))+'</strong></p><p class="cy-muted">'+h(trend(p.trend))+'</p>';
    if(!compact){
      html+='<p>'+h(prediction(p.prediction))+'</p>';
      if(p.days){html+='<details class="cy-details"><summary>身体状态与处理记录（'+p.days.length+'）</summary><p class="cy-muted">'+(p.days.some(function(d){return d.date===todayISO();})?'今天有更新':'今天未更新，不沿用昨天的疼痛程度')+'</p>'+(p.days.length?p.days.map(daily).join(''):'<p class="cy-muted">分享范围内暂无感受记录。</p>')+'</details>';}
      if(p.periods){html+='<details class="cy-details"><summary>查看已确认的日期历史</summary>'+p.periods.map(function(r){return '<p>'+h(r.start_date)+' ～ '+h(r.end_date||'结束待确认')+(r.start_clipped?'（已按分享起始日裁切）':'')+'</p>';}).join('')+'</details>';}
      if(p.can_proxy){html+='<div class="cy-actions">'+btn('帮她记一次，等她确认','proxy')+'</div>'+((p.editable_periods||[]).map(function(r){return '<div class="cy-history"><span>'+h(r.start_date)+' ～ '+h(r.end_date||'结束未确认')+'</span>'+btn('代记结束 / 更正','proxy-edit','data-id="'+r.id+'"')+'</div>';}).join(''))+'<p class="cy-muted">代记不会直接成为实际记录，也不会参与预估；她可以纠正后确认或拒绝。</p>';}
      if(p.care){html+='<p>'+h(p.care.message)+'</p><p class="cy-muted">有效至 '+p.care.expires_on+'</p>'+(p.care.acknowledged?'<p>已回应：收到，我在。</p>':btn('收到，我在 🤍','ack'));}
    }
    return html+'</section>';
  }
  function proposals(s){
    if(!s.proposals.length)return '';
    return '<section class="cy-card"><h3>📝 代记待确认</h3><p class="cy-muted">仅在这里轻提示，不催促、不自动确认；正式周期和统计暂不包含这些记录。</p>'+s.proposals.map(function(r){
      var mine=r.uid===s.uid;
      return '<div class="cy-history"><div><strong>'+h(name(r.author_uid))+' 代记 · 待确认</strong><p>'+h(r.start_date)+' ～ '+h(r.end_date||'结束未填')+'</p></div><div class="cy-actions">'+(mine?btn('确认 / 纠正','review','data-id="'+r.id+'"')+btn('拒绝','reject','data-id="'+r.id+'"'):btn('撤回代记','cancel-proposal','data-id="'+r.id+'"'))+'</div></div>';
    }).join('')+'</section>';
  }
  function own(s){
    return '<section class="cy-card cy-hero"><div class="cy-eyebrow">本人已确认记录</div><h2>'+h(status(s))+'</h2><p class="cy-muted">'+h(trend(s.trend))+'</p><p>'+h(prediction(s.prediction))+'</p><div class="cy-actions">'+btn(s.active?'确认结束日期':'经期开始了',s.active?'finish':'start')+btn('补录历史','add')+btn('按需记录感受','today')+'</div></section>'+
      '<section class="cy-card"><h3>🌿 感受与处理 <small>按需填，不必每天记录</small></h3><p class="cy-muted">开启身体信息分享后，结构化项目对伴侣可见；备注默认私密，可单条分享。用药仅作事实记录，不提供用药建议。</p><form id="cycleDayForm" method="post" action="/api/cycle/day">'+
      field('日期',dt('date',todayISO(),true,todayISO()))+'<div class="cy-grid">'+field('经量',select('flow',['少','中','多']))+field('疼痛',select('pain',['无','轻','中','重']))+'</div>'+field('是否影响日常活动',select('activity',['不影响','有些影响','明显影响']))+
      '<div class="cy-checks">'+symptoms.map(function(x){return '<label class="cy-check"><input type="checkbox" name="symptoms" value="'+x+'"><span>'+x+'</span></label>';}).join('')+'</div>'+field('心情（选填，不由系统推断）',input('mood','','maxlength="30"'))+field('缓解方式（选填）',input('relief','','maxlength="300" placeholder="例如：休息、热敷"'))+field('用药记录（选填）',input('medication','','maxlength="300" placeholder="只记录实际情况，不用于推荐用药"'))+field('缓解效果',select('effectiveness',['未尝试','无改善','有缓解','明显缓解']))+field('文字备注','<textarea name="note" maxlength="1000" rows="2" placeholder="默认仅自己可见"></textarea>')+check('note_shared','将这条备注也分享（需已开启身体信息分享）',false)+'<div class="cy-actions"><button class="p-btn pri">保存感受</button>'+btn('删除这天的感受','delete-day')+'</div></form></section>'+
      '<section class="cy-card"><h3>🗓️ 已确认经期历史</h3>'+(s.periods.length?s.periods.map(function(r){return '<div class="cy-history"><div><strong>'+h(r.start_date)+'</strong><p class="cy-muted">至 '+h(r.end_date||'结束待确认')+' · '+(r.source.author_uid===s.uid?'本人记录':'伴侣代记，本人已确认')+'</p></div><div class="cy-actions">'+btn('编辑','edit','data-id="'+r.id+'"')+btn('删除','delete-period','data-id="'+r.id+'"')+'</div></div>';}).join(''):'<p class="cy-muted">开始和结束时记一下就好。</p>')+'</section>';
  }
  function settings(s){var p=s.settings,x=s.collaboration,n=s.preferences;
    return '<section class="cy-card"><h3>🔔 我的提醒偏好</h3><p class="cy-muted">两个人独立设置；接收对方状态提醒还需要对方授权。默认锁屏不显示具体健康状态。</p><form id="cyclePreferencesForm" method="post">'+field('我的使用方式', '<select name="role"><option value="recorder"'+(n.role==='recorder'?' selected':'')+'>我来记录身体状态</option><option value="supporter"'+(n.role==='supporter'?' selected':'')+'>我来关注伴侣</option></select>')+check('enabled','接收状态提醒',n.enabled)+check('upcoming','预估经期将至（每周期一次）',n.upcoming)+check('started','对方确认经期开始',n.started)+check('discomfort','对方当天中度及以上疼痛 / 明显影响日常活动',n.discomfort)+check('pending','预估区间过去后，一次中性信息待确认提示',n.pending)+field('提前天数',input('advance_days',String(n.advance_days),'type="number" min="0" max="7" required'))+check('notice_details','允许我的锁屏显示详情（还需记录者允许）',n.notice_details)+'<button class="p-btn pri">保存我的提醒</button></form></section>'+
      '<details class="cy-card cy-details"><summary>🔒 我自己的记录：分享、协作与预估</summary><p class="cy-muted">只管理本人数据；不能替对方开启授权。伴侣代记须经本人确认，原有记录不会自动扩大分享范围。</p><form id="cycleSettingsForm" method="post"><div class="cy-grid">'+field('典型周期天数（可不填）',input('typical_cycle',p.typical_cycle,'type="number" min="15" max="60"'))+field('典型经期天数（可不填）',input('typical_duration',p.typical_duration,'type="number" min="1" max="30"'))+'</div>'+check('prediction_enabled','开启辅助预估',p.prediction_enabled)+check('share_actual','分享实际日期及周期波动统计',p.share_actual)+check('share_prediction','分享预估区间',p.share_prediction)+field('分享起始日期',dt('share_since',p.share_since||todayISO(),true))+'<hr>'+check('share_details','分享结构化身体信息（经量、疼痛、症状、心情、缓解方式、用药与效果）',x.share_details)+check('allow_proxy','允许伴侣代记日期，等待我确认',x.allow_proxy)+check('allow_notifications','允许对方向自己的手机接收我的状态提醒',x.allow_notifications)+check('allow_notice_details','允许对方选择在锁屏显示我的具体状态',x.allow_notice_details)+'<p class="cy-muted">文字备注默认私密，可按条分享。不自动向外部微信服务发送健康内容。撤回后停止访问，但已读通知、截图无法收回。</p><div class="cy-actions"><button class="p-btn pri">保存分享与协作</button>'+btn('立即停止全部分享','revoke-sharing')+'</div></form></details>';
  }
  function paintPage(s){var box=$('#cycleContent');if(!box)return;
    var supporter=s.preferences.role==='supporter';
    box.innerHTML=(supporter?'<div id="cyclePartner">'+partner(s,false)+'</div>':own(s))+proposals(s)+(supporter?'<details class="cy-card cy-details"><summary>管理我自己的记录（可选）</summary>'+own(s)+'</details>':'<div id="cyclePartner">'+partner(s,false)+'</div>')+
      '<details class="cy-card cy-details"><summary>🤍 主动发送关怀需求（可选）</summary><form id="cycleCareForm" method="post">'+field('想告诉对方',input('message',s.care?s.care.message:'','maxlength="200"'))+field('有效至',dt('expires_on',s.care?s.care.expires_on:todayISO(),true,shiftISO(todayISO(),7)))+'<div class="cy-actions"><button class="p-btn pri">发送需求</button>'+btn('撤回需求','revoke-care')+'</div></form></details>'+settings(s)+
      '<details class="cy-card cy-details"><summary>数据导出与删除</summary><div class="cy-actions">'+btn('导出我的数据','export')+btn('删除我的全部经期数据','delete-all')+'</div><p class="cy-muted">不影响共同日程或伴侣的正式记录；已导出、旧备份副本需要另外处理。</p></details><p class="cy-muted cy-disclaimer">'+h(s.disclaimer)+' 结束未确认时不自动结束，也不无限显示第 N 天。持续不适请及时就医。</p>'+
      '<div class="overlay" id="cyclePeriodModal"><div class="modal" role="dialog" aria-modal="true" aria-labelledby="cyclePeriodTitle"><h3 id="cyclePeriodTitle">记录实际经期</h3><form id="cyclePeriodForm" method="post" action="/api/cycle/period"><input type="hidden" name="id"><input type="hidden" name="mode"><input type="hidden" name="proposal_id">'+field('开始日期',dt('start_date',todayISO(),true,todayISO()))+field('结束日期（进行中留空）',dt('end_date','',false,todayISO()))+'<p class="cy-muted" id="cyclePeriodHint">只记录实际发生的日期。</p><div class="cy-actions">'+btn('取消','close-period')+'<button class="p-btn pri">保存记录</button></div></form></div></div>';
    var day=selectedDate||todayISO();$('#cycleDayForm').elements.date.value=day;fillDay(day);
    if(selectedDate){var ownDetails=$('#cycleDayForm').closest('details');if(ownDetails)ownDetails.open=true;$('#cycleDayForm').scrollIntoView({block:'center'});selectedDate=null;}
  }
  function fillDay(date){var f=$('#cycleDayForm');if(!f||!state)return;var d=state.days.find(function(r){return r.date===date;})||{};
    ['flow','pain','activity','mood','relief','medication','effectiveness','note'].forEach(function(k){f.elements[k].value=d[k]||'';});f.elements.note_shared.checked=!!d.note_shared;
    f.querySelectorAll('[name="symptoms"]').forEach(function(x){x.checked=(d.symptoms||[]).indexOf(x.value)>=0;});
  }
  function paintCalendar(s){document.querySelectorAll('.cy-mark').forEach(function(x){x.remove();});var layer=s.calendar.slice();s.days.forEach(function(d){if(d.date.slice(0,7)===s.month)layer.push({date:d.date,kind:'daily',uid:s.uid});});layer.forEach(function(m){var cell=document.querySelector('.month-cell[data-date="'+m.date+'"]');if(!cell)return;var b=document.createElement('button');b.type='button';b.className='cy-mark '+(m.kind==='predicted'?'cy-predicted':m.kind==='daily'?'cy-daily':'cy-actual');b.dataset.cycle='calendar';b.dataset.date=m.date;b.textContent=(m.uid===s.uid?'我 · ':'TA · ')+(m.kind==='predicted'?'预估开始':m.kind==='daily'?'● 感受':m.open_start?'已记开始':'实际经期');cell.appendChild(b);});}
  function paintHome(s){var slot=$('#cycleHome');if(!slot)return;var supporter=s.preferences.role==='supporter';slot.innerHTML='<div class="cy-home-heading">🌷 周期与双人关注</div>'+(supporter?partner(s,true):'<div class="cy-card"><strong>'+h(status(s))+'</strong><p class="cy-muted">'+h(trend(s.trend))+'</p></div>')+'<div class="cy-actions">'+btn(supporter?'查看状态与历史 ›':'记录与管理 ›','open')+'</div><p class="cy-muted">按需记录 · 预估仅供参考'+(CURRENT_VIEW==='month'?' · 实线为实际，虚线为预估':'')+'</p>';}
  function request(){var version=++sequence,uid=ME&&ME.uid;if(!uid)return Promise.resolve();return api('/api/cycle?month='+MONTH_ANCHOR,{cache:'no-store'}).then(function(s){if(version!==sequence||!ME||uid!==ME.uid)return;state=s;owner=uid;if(CURRENT_VIEW==='cycle')paintPage(s);paintHome(s);paintCalendar(s);document.body.setAttribute('data-cycle-ready',String(version));}).catch(function(e){if(version!==sequence)return;var target=$('#cycleContent')||$('#cycleHome');if(target)target.innerHTML='<div class="cy-card">'+h(e.message)+' '+btn('重新加载','retry')+'</div>';});}
  function mount(){if(!ME){state=null;owner=null;sequence++;return;}if(owner!==ME.uid)state=null;
    if(CURRENT_VIEW==='week'||CURRENT_VIEW==='month'){var target=CURRENT_VIEW==='week'?document.querySelector('main'):document.querySelector('.month-cal-wrap');if(target&&!$('#cycleHome')){var slot=document.createElement('section');slot.id='cycleHome';target.insertAdjacentElement('afterend',slot);}request();}else if(CURRENT_VIEW==='cycle')request();}
  function save(action,data,el){if(el)el.disabled=true;document.body.setAttribute('data-cycle-saving','1');return api('/api/cycle/'+action,{method:'POST',body:data,cache:'no-store'}).then(function(){toast(action==='proposal'?'已代记，等待本人确认':'已保存');if(['period','proposal','proposal/confirm'].indexOf(action)>=0&&history.state&&history.state.type==='modal'){closeAllModals();history.back();}return request();}).catch(function(e){toast(e.message);}).finally(function(){document.body.removeAttribute('data-cycle-saving');if(el)el.disabled=false;});}
  function edit(r,mode){var f=$('#cyclePeriodForm');f.elements.id.value=r.id||'';f.elements.mode.value=mode||'period';f.elements.proposal_id.value=mode==='confirm'?r.id:'';f.elements.start_date.value=r.start_date||todayISO();f.elements.end_date.value=r.end_date||'';$('#cyclePeriodTitle').textContent=mode==='proxy'?'帮她代记 · 待本人确认':mode==='confirm'?'确认或纠正代记':'记录实际经期';$('#cyclePeriodHint').textContent=mode==='proxy'?'仅提交待确认记录，不直接改变正式周期或触发已确认开始提醒。':mode==='confirm'?'可以先纠正日期；确认后成为正式记录并参与周期统计。':'只记录实际发生的日期，不自动补结束。';openModal('#cyclePeriodModal');f.elements.start_date.focus();}
  document.addEventListener('change',function(e){if(e.target.matches('#cycleDayForm [name="date"]'))fillDay(e.target.value);});
  /* v1 forms only: /^cycle/ would also swallow the v2 cycle2* forms. */
  document.addEventListener('submit',function(e){var f=e.target,id=f.getAttribute('id')||'';if(!/^cycle(?!2)/.test(id))return;e.preventDefault();e.stopImmediatePropagation();var d=Object.fromEntries(new FormData(f).entries()),action;
    if(id==='cyclePeriodForm'){var mode=d.mode;delete d.mode;if(mode==='confirm'){action='proposal/confirm';d.id=Number(d.proposal_id);}else{action=mode==='proxy'?'proposal':'period';if(d.id){if(mode==='proxy'){d.period_id=Number(d.id);delete d.id;}else d.id=Number(d.id);}else delete d.id;}delete d.proposal_id;}
    else if(id==='cycleDayForm'){action='day';d.symptoms=new FormData(f).getAll('symptoms');d.note_shared=f.elements.note_shared.checked;}
    else if(id==='cyclePreferencesForm'){action='preferences';['enabled','upcoming','started','discomfort','pending','notice_details'].forEach(function(k){d[k]=f.elements[k].checked;});d.advance_days=Number(d.advance_days);if(d.notice_details&&!state.preferences.notice_details&&!confirm('允许这台账号的锁屏显示健康详情？对方的状态还需要对方单独同意。'))return;}
    else if(id==='cycleSettingsForm'){action='sharing';var p={},x={};['typical_cycle','typical_duration'].forEach(function(k){p[k]=d[k]===''?null:Number(d[k]);});['prediction_enabled','share_actual','share_prediction'].forEach(function(k){p[k]=f.elements[k].checked;});p.share_since=d.share_since;['share_details','allow_proxy','allow_notifications','allow_notice_details'].forEach(function(k){x[k]=f.elements[k].checked;});if((x.share_details||x.allow_proxy||x.allow_notifications||x.allow_notice_details||p.share_actual||p.share_prediction)&&!confirm('确认按所选范围与当前伴侣共享？结构化身体信息可能包含用药；备注仍按单条设置，代记必须由你确认。'))return;d={settings:p,collaboration:x};}
    else if(id==='cycleCareForm'){action='care';if(!d.message.trim()){toast('请填写需求，或点击撤回');return;}}
    else return;save(action,d,f.querySelector('button:not([type="button"])'));
  },true);
  document.addEventListener('click',function(e){var el=e.target.closest('[data-cycle]');if(!el)return;e.preventDefault();e.stopImmediatePropagation();var a=el.dataset.cycle;
    if(a==='open'||a==='calendar'){selectedDate=a==='calendar'?el.dataset.date:null;go();return;}if(a==='retry'){request();return;}if(!state||!ME||owner!==ME.uid)return;
    if(a==='start'||a==='add')edit({});else if(a==='finish')edit(Object.assign({},state.active,{end_date:todayISO()}));else if(a==='edit')edit(state.periods.find(function(r){return r.id===Number(el.dataset.id);}));else if(a==='proxy')edit({},'proxy');else if(a==='proxy-edit')edit(state.partner.editable_periods.find(function(r){return r.id===Number(el.dataset.id);}),'proxy');else if(a==='review')edit(state.proposals.find(function(r){return r.id===Number(el.dataset.id);}),'confirm');
    else if(a==='reject'||a==='cancel-proposal')save(a==='reject'?'proposal/reject':'proposal/cancel',{id:Number(el.dataset.id)},el);
    else if(a==='close-period'){closeAllModals();if(history.state&&history.state.type==='modal')history.back();}
    else if(a==='today'){var f=$('#cycleDayForm'),details=f.closest('details');if(details)details.open=true;f.elements.date.value=todayISO();fillDay(todayISO());f.scrollIntoView({behavior:'smooth',block:'center'});}
    else if(a==='delete-period'&&confirm('删除这条已确认经期？身体感受另行保留。'))save('period/delete',{id:Number(el.dataset.id)},el);
    else if(a==='delete-day'&&confirm('删除这天的感受与处理记录？'))save('day/delete',{date:$('#cycleDayForm').elements.date.value},el);
    else if(a==='revoke-sharing')save('sharing',{settings:{share_actual:false,share_prediction:false},collaboration:{share_details:false,allow_proxy:false,allow_notifications:false,allow_notice_details:false}},el);
    else if(a==='ack')save('care/ack',{},el);else if(a==='revoke-care')save('care',{message:''},el);
    else if(a==='delete-all'&&confirm('删除本人全部经期数据、授权、提醒偏好及相关代记？不影响对方正式记录。')&&confirm('不可从 App 撤销，确定删除？'))save('delete-all',{confirm:'DELETE_MY_CYCLE_DATA'},el);
    else if(a==='export')api('/api/cycle/export',{cache:'no-store'}).then(function(data){var url=URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:'application/json'})),link=document.createElement('a');link.href=url;link.download='my-cycle-data.json';link.click();setTimeout(function(){URL.revokeObjectURL(url);},1000);toast('文件包含健康信息，请妥善保管');}).catch(function(e){toast(e.message);});
  },true);
  function scrub(){sequence++;state=null;document.querySelectorAll('.cy-mark').forEach(function(e){e.remove();});var slot=$('#cycleHome');if(slot)slot.innerHTML='';var box=$('#cycleContent');if(box)box.innerHTML='<p>私人记录已隐藏，返回后重新加载。</p>';}
  setInterval(function(){if(!ME||document.hidden||!state||document.body.hasAttribute('data-cycle-saving')||!$('#cycleHome')&&CURRENT_VIEW!=='cycle')return;var uid=ME.uid,v=sequence;api('/api/cycle?month='+MONTH_ANCHOR,{cache:'no-store'}).then(function(s){if(!ME||uid!==ME.uid||v!==sequence)return;var changed=JSON.stringify(state.partner)!==JSON.stringify(s.partner)||JSON.stringify(state.proposals)!==JSON.stringify(s.proposals);state.partner=s.partner;state.calendar=s.calendar;state.proposals=s.proposals;var shared=$('#cyclePartner');if(shared)shared.innerHTML=partner(s,false);paintHome(s);paintCalendar(s);if(changed&&CURRENT_VIEW==='cycle'&&!document.querySelector('#cyclePeriodModal.show')&&!document.querySelector('#cycleContent :focus'))paintPage(s);}).catch(function(){scrub();});},15000);
  document.addEventListener('visibilitychange',function(){if(document.hidden)scrub();else if(ME)mount();});
  return {mount:mount,renderPage:emptyPage};
})();
