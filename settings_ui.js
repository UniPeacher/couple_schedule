/* Settings paint the last cached payload instantly, then refresh silently in the background;
   an in-progress edit (dirty) is never clobbered by the refresh. */
var Settings=(function(){
'use strict';
var version=0,dirty=false;
// Remove sensitive fields saved by older versions, without dropping calendar data.
try{Object.keys(localStorage).filter(function(k){return /^sched_(week|month)_/.test(k);}).forEach(function(k){try{var x=JSON.parse(localStorage.getItem(k));if(x.settings)delete x.settings.llm_api_key;if(x.users)Object.keys(x.users).forEach(function(uid){delete x.users[uid].wx_uid;});localStorage.setItem(k,JSON.stringify(x));}catch(e){localStorage.removeItem(k);}});}catch(e){}
function h(x){return esc(x);}
function field(label,name,value,attrs){return '<label class="st-field"><span>'+label+'</span><input name="'+name+'" value="'+h(value||'')+'" '+(attrs||'')+'></label>';}
function note(x){return '<p class="st-note">'+x+'</p>';}
function submit(label){return '<div class="st-save"><span class="st-result" role="status"></span><button class="p-btn pri">'+(label||'保存')+'</button></div>';}
function group(id,title,summary,content,open){return '<details class="st-group" id="st-'+id+'"'+(open?' open':'')+'><summary><span><strong>'+title+'</strong><small>'+summary+'</small></span><span class="st-chevron">⌄</span></summary><div class="st-body">'+content+'</div></details>';}
function form(scope,content,label){return '<form data-settings-form="'+scope+'" method="post" action="/api/settings">'+content+submit(label)+'</form>';}
function render(){dirty=false;var n=++version,uid=ME.uid;
var cached=null;try{cached=JSON.parse(localStorage.getItem('sched_settings_v1')||'null');}catch(e){}
setAppHTML(renderTopBar()+'<div class="page-container st-page"><div class="st-head"><span class="st-head-title">⚙️ 设置</span><span class="st-head-sub">外观、通知，以及两个人共用的配置</span></div><div id="settingsContent" aria-live="polite">'+(cached?'':'正在加载最新设置…')+'</div></div>'+bottomNavHTML()+modalsHTML());
if(cached){try{paint(cached);}catch(e){}}
api('/api/settings',{cache:'no-store'}).then(function(s){try{localStorage.setItem('sched_settings_v1',JSON.stringify(s));}catch(e){}if(n!==version||!ME||ME.uid!==uid||CURRENT_VIEW!=='settings'||dirty)return;paint(s);}).catch(function(e){if(n===version&&$('#settingsContent')&&!cached)$('#settingsContent').innerHTML=note(h(e.message))+'<button class="p-btn" data-settings="retry">重试</button>';});
}
function paint(s){var u=s.users,p=s.settings,dark=document.documentElement.classList.contains('dark'),mine=u[ME.uid];
var appearance='<div class="seg" id="themeSeg"><button type="button" class="opt'+(!dark?' on':'')+'" data-act="set-theme" data-theme="light">☀️ 浅色</button><button type="button" class="opt'+(dark?' on':'')+'" data-act="set-theme" data-theme="dark">🌙 深色</button></div>'+note('立即生效，只影响这台设备。');
var notifications='<h4>本机通知</h4>'+note(window.AndroidApp?'已检测到 Android 客户端。能否收到通知仍取决于系统通知权限与后台运行设置。':'当前是网页环境；手机系统通知请安装 Android 客户端。')+'<div class="st-actions"><button type="button" class="p-btn" data-act="test-app-notice">发一条测试通知</button>'+(window.AndroidApp&&window.AndroidApp.requestBatteryOptimization?'<button type="button" class="p-btn" data-act="battery-protect">后台运行设置</button>':'')+'</div><hr><h4>微信推送</h4>'+form('notifications',note('在「虾推啥」「息知」或「Server酱」任一微信推送服务里获取自己的推送 Token，填入后日程变动、留言和手账动态会推送到微信；周期记录不走微信推送。'+(mine.wx_configured?'当前已配置，留空保持不变。':'当前未配置。'))+field('我的推送 Token','wx_token','','type="password" autocomplete="off" placeholder="留空不修改" maxlength="100"')+'<label class="st-check"><input type="checkbox" name="clear_wx">清除我的推送 Token</label>','保存推送设置');
var profile=form('profile','<div class="st-grid">'+field('🐶 '+h(u.a.name)+' 的昵称','name_a',u.a.name,'maxlength="30" required')+field('🐾 '+h(u.b.name)+' 的昵称','name_b',u.b.name,'maxlength="30" required')+'</div>'+note('保存后两个人看到的就是新昵称。'));
var schedule=form('schedule','<div class="st-grid">'+field(h(u.a.name)+' · 第 1 周周一','week1_a',u.a.week1,'type="date"')+field(h(u.b.name)+' · 第 1 周周一','week1_b',u.b.week1,'type="date"')+'</div>'+note('学期课表第一周从哪天开始（选周一），用于教学周和单双周换算；两个人的校历可以不同。')+'<div class="st-grid">'+field('每天统计开始','window_start',p.window_start,'type="time" required')+field('每天统计结束','window_end',p.window_end,'type="time" required')+'</div>'+field('最短共同空闲（分钟）','min_gap',String(p.min_gap),'type="number" min="5" max="180" required')+note('共同空闲只在这个时间段里找，且空档不短于设置的分钟数才算能一起安排的时间。'));
var data='<p class="st-note">把我们两边的日程、留言、纪念日、手账、心愿、旅行清单和我的周期记录打包成一个 JSON 文件下载，用来备份或搬家。文件不含密码、推送 Token 和密钥。</p><div class="st-actions"><a class="p-btn" href="/api/export" download>⬇️ 导出数据文件</a></div>';
var security='<form data-settings-form="password" method="post" action="/api/password">'+field('当前密码','old','','type="password" autocomplete="current-password" required')+field('新密码','new','','type="password" autocomplete="new-password" minlength="6" required')+field('再次输入新密码','repeat','','type="password" autocomplete="new-password" minlength="6" required')+note('只改我自己的登录密码，至少 6 位。')+submit('修改密码')+'</form>';
var advanced=s.is_admin?form('ai',note('AI 简报服务，两个人共用；保存这一组会同时清空旧的简报缓存。')+field('API 基础地址','llm_api_base',p.llm_api_base,'type="url" placeholder="https://…/v1" maxlength="200"')+field('模型名称','llm_model',p.llm_model,'maxlength="60"')+field('新的 API Key','llm_api_key','','type="password" autocomplete="off" placeholder="留空保持现有密钥" maxlength="200"')+note(p.llm_key_configured?'密钥已配置，不回显。':'尚未配置密钥。')+'<label class="st-check"><input type="checkbox" name="clear_llm_key">清除现有 API Key</label>','保存 AI 配置'):note('月度简报的 AI 服务由对方账号统一配置，这里无需设置。');
if(window.AndroidApp&&window.AndroidApp.openServerSettings)advanced+='<hr><button type="button" class="p-btn" data-act="open-server-url">修改客户端服务地址</button>';
$('#settingsContent').innerHTML=group('appearance','🌓 外观','浅色或深色 · 即时生效',appearance,true)
 +group('notifications','🔔 通知',mine.wx_configured?'微信推送已配置':'微信推送未配置 · 点这里设置',notifications)
 +group('profile','🐾 我们的名字',h(u.a.name)+' & '+h(u.b.name),profile)
 +group('schedule','🗓️ 课表与空闲','第 1 周 '+(u.a.week1||'未设置')+' · 每天 '+p.window_start+'–'+p.window_end,schedule)
 +group('data','💾 我们的数据','导出 JSON 备份文件',data)
 +group('security','🔒 登录密码','修改我自己的登录密码',security)
 +group('advanced','🛠️ AI 简报服务',s.is_admin?'模型 '+(p.llm_model||'未设置'):'由对方账号统一配置',advanced)
 +'<div class="st-about"><p>两人日程 · 给两个人一起安排生活的小工具</p><p>数据都在我们自己的服务器上，随时可以在「我们的数据」里导出备份。</p><button type="button" class="p-btn danger" data-settings="logout">退出当前账号</button></div>';
}
document.addEventListener('input',function(e){if(e.target.closest('[data-settings-form]')){dirty=true;var f=e.target.closest('form'),r=f.querySelector('.st-result');if(r){r.textContent='尚未保存';r.dataset.state='dirty';}}});
document.addEventListener('submit',function(e){var f=e.target;if(!f.matches('[data-settings-form]'))return;e.preventDefault();e.stopImmediatePropagation();var scope=f.dataset.settingsForm,data=Object.fromEntries(new FormData(f)),result=f.querySelector('.st-result'),button=f.querySelector('button:not([type="button"])'),url='/api/settings';
if(scope==='password'){if(data.new!==data.repeat){result.textContent='两次输入的新密码不一致';result.dataset.state='error';return;}delete data.repeat;url='/api/password';}else{data.scope=scope;if(scope==='schedule')data.min_gap=Number(data.min_gap);if(scope==='notifications')data.clear_wx=f.elements.clear_wx.checked;if(scope==='ai')data.clear_llm_key=f.elements.clear_llm_key.checked;}
button.disabled=true;result.textContent='正在保存…';result.dataset.state='saving';
api(url,{method:'POST',body:data,cache:'no-store'}).then(function(){
// Read back the exact persisted group before showing success; passwords verified by API.
return scope==='password'?Promise.resolve():api('/api/settings',{cache:'no-store'}).then(function(saved){if(scope==='profile'){ME.name=saved.users[ME.uid].name;document.querySelectorAll('.user-pill').forEach(function(el){el.textContent=(ME.uid==='a'?'🐶 ':'🐾 ')+ME.name;});META={users:['a','b'].map(function(uid){return {uid:uid,name:saved.users[uid].name};})};}
// The collapsed header must show the freshly saved values, not a stale summary.
if(scope==='profile'){$('#st-profile summary small').textContent=saved.users.a.name+' & '+saved.users.b.name;}
else if(scope==='schedule'){$('#st-schedule summary small').textContent='第 1 周 '+(saved.users.a.week1||'未设置')+' · 每天 '+saved.settings.window_start+'–'+saved.settings.window_end;}
else if(scope==='notifications'){$('#st-notifications summary small').textContent=saved.users[ME.uid].wx_configured?'微信推送已配置':'微信推送未配置 · 点这里设置';}
else if(scope==='ai'){$('#st-advanced summary small').textContent='模型 '+(saved.settings.llm_model||'未设置');}
MONTH_DATA=null;try{Object.keys(localStorage).filter(function(k){return /^sched_(week|month)_/.test(k);}).forEach(function(k){localStorage.removeItem(k);});}catch(e){}return api('/api/week?date='+ANCHOR,{cache:'no-store'}).then(function(week){DATA=week;});});
}).then(function(){if(scope==='password')f.reset();f.querySelectorAll('input[type="password"]').forEach(function(x){x.value='';});f.querySelectorAll('input[type="checkbox"]').forEach(function(x){x.checked=false;});result.textContent='已保存';result.dataset.state='saved';dirty=!!document.querySelector('.st-result[data-state="dirty"]');toast(scope==='password'?'密码已更新':'已保存');}).catch(function(err){result.textContent=err.message;result.dataset.state='error';}).finally(function(){button.disabled=false;});
},true);
document.addEventListener('click',function(e){var el=e.target.closest('[data-settings]');if(!el)return;e.preventDefault();e.stopImmediatePropagation();if(el.dataset.settings==='retry')render();else if(el.dataset.settings==='logout'){uiConfirm({icon:'👋',title:'退出当前账号？',message:'退出只结束本次登录，不会删除你们的数据。'+(dirty?'尚未保存的设置会丢失。':''),danger:true,okText:'退出'}).then(function(ok){if(!ok)return;var legacy=document.createElement('button');legacy.dataset.act='logout';document.body.appendChild(legacy);legacy.click();legacy.remove();});}},true);
window.addEventListener('click',function(e){
if(CURRENT_VIEW!=='settings'||!dirty)return;
var target=e.target.closest('[data-act]');if(!target)return;
var act=target.getAttribute('data-act'),navigation=['switch-view','open-anniv-list','open-anniv','open-summary','open-settings'].indexOf(act)>=0;
if(navigation){e.preventDefault();e.stopImmediatePropagation();uiConfirm({icon:'📝',title:'尚未保存',message:'离开后这些修改会丢失，仍要继续吗？',okText:'离开'}).then(function(ok){if(!ok)return;dirty=false;target.click();});}
},true);
window.addEventListener('beforeunload',function(e){if(CURRENT_VIEW==='settings'&&dirty){e.preventDefault();e.returnValue='';}});
return {render:render};
})();
