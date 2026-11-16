"use strict";
var splashStart = Date.now();
function dismissSplash(){
  var el = document.getElementById("splashScreen");
  if (!el || el.classList.contains("fade-out")) return;
  var elapsed = Date.now() - splashStart;
  var minShow = 450;
  var delay = Math.max(0, minShow - elapsed);
  setTimeout(function(){
    el.classList.add("fade-out");
    setTimeout(function(){
      if (el && el.parentNode) el.parentNode.removeChild(el);
    }, 500);
  }, delay);
}
setTimeout(dismissSplash, 2500);

var WD = ["周一","周二","周三","周四","周五","周六","周日"];
var ME = null, DATA = null, ANCHOR = null, META = null;

var CURRENT_VIEW = "week"; // "week" | "month" | "summary" | "messages" | "wishes" | "trips" | "anniv" | "settings" | "cycle2"
var MONTH_ANCHOR = todayISO().slice(0, 7); // YYYY-MM
var MONTH_DATA = null;
var UNREAD_COUNT = 0;
var MESSAGES_DATA = [];
var curMsgFilter = "all";
var WISHES_DATA = null;
var curWishFilter = "all";
var addWishUid = "a";
var addWishCat = "travel";
var addWishPrio = 0;
var diaryPhotosToUpload = []; // base64 list
var curEditingDiary = null;
var curEditingEventId = null;
var activeDiaryDate = "";
var addUid = "a", addRep = "weekly", loginUid = "a", annivType = "love";

function setUnreadCount(count){
  var n = Math.max(0, parseInt(count) || 0);
  if (n === UNREAD_COUNT) return; /* 未变化不写 DOM：轮询每 15s 一次，写入会触发确认弹窗的"重渲染即取消"监听 */
  UNREAD_COUNT = n;
  document.querySelectorAll("#bUnreadBadge, .b-badge").forEach(function(badge){
    if (UNREAD_COUNT > 0){
      badge.textContent = UNREAD_COUNT > 99 ? "99+" : UNREAD_COUNT;
      badge.style.display = "inline-flex";
    } else {
      badge.style.display = "none";
      badge.textContent = "";
    }
  });
  var unreadTab = document.querySelector('#msgFilterSeg .opt[data-v="unread"]');
  if (unreadTab){
    unreadTab.textContent = '未读' + (UNREAD_COUNT > 0 ? ' (' + UNREAD_COUNT + ')' : '');
  }
}

function $(s){ return document.querySelector(s); }
function esc(s){ return String(s==null?"":s).replace(/[&<>"']/g, function(c){
  return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]; }); }
function pad(n){ return n<10 ? "0"+n : ""+n; }
function todayISO(){ var d=new Date(); return d.getFullYear()+"-"+pad(d.getMonth()+1)+"-"+pad(d.getDate()); }
function fmtDateCN(dtStr){
  var p = String(dtStr || "").split("-");
  if (p.length !== 3) return dtStr;
  var d = new Date(+p[0], +p[1] - 1, +p[2]);
  var s = (+p[1]) + "月" + (+p[2]) + "日 · " + WD[(d.getDay() + 6) % 7];
  if (+p[0] !== new Date().getFullYear()) s = p[0] + "年" + s;
  return s;
}
function fmtRecordTime(created, dtStr){
  var parts = String(created || "").split(" ");
  var hm = (parts[1] || "").slice(0, 5);
  if (!hm) return created;
  return (parts[0] === dtStr) ? hm : fmtDateCN(parts[0]) + " " + hm;
}
function shiftISO(iso, days){ var p=iso.split("-"), t=new Date(+p[0], +p[1]-1, +p[2]);
  t.setDate(t.getDate()+days); return t.getFullYear()+"-"+pad(t.getMonth()+1)+"-"+pad(t.getDate()); }
function t2m(t){ var p=t.split(":"); return (+p[0])*60 + (+p[1]); }
function m2t(m){ return pad(Math.floor(m/60))+":"+pad(m%60); }
function durTxt(a,b){ var m=b-a, h=Math.floor(m/60);
  return h>0 ? h+"小时"+(m%60?(m%60)+"分":"") : m+"分钟"; }

function calcAnniv(item, todayStr){
  var today = new Date(todayStr + "T00:00:00");
  var tdate = new Date(item.date + "T00:00:00");
  var diffDays = Math.round((today - tdate) / 86400000);
  var icon = "💕", text = "";
  if (item.target_type === "love"){
    icon = "💖";
    if (diffDays >= 0){
      text = esc(item.title) + " 第 " + (diffDays + 1) + " 天";
    } else {
      text = esc(item.title) + " 还有 " + (-diffDays) + " 天";
    }
  } else if (item.target_type === "birthday"){
    icon = "🎂";
    var curYear = today.getFullYear();
    var nextBday = new Date(curYear, tdate.getMonth(), tdate.getDate());
    if (nextBday < today){
      nextBday = new Date(curYear + 1, tdate.getMonth(), tdate.getDate());
    }
    var daysLeft = Math.round((nextBday - today) / 86400000);
    text = (daysLeft === 0) ? (esc(item.title) + " 今天生日快乐！🎉") : (esc(item.title) + " 还有 " + daysLeft + " 天");
  } else {
    icon = "🎯";
    if (diffDays < 0){
      text = esc(item.title) + " 倒计时 " + (-diffDays) + " 天";
    } else if (diffDays === 0){
      text = esc(item.title) + " 就是今天啦！✨";
    } else {
      text = esc(item.title) + " 已过去 " + diffDays + " 天";
    }
  }
  return { icon: icon, text: text, type: item.target_type, diffDays: diffDays };
}
function toast(msg, type){ var t=$("#toast"); t.textContent=msg; t.classList.remove("ok","err"); if(type) t.classList.add(type); t.classList.add("show");
  clearTimeout(t._h); t._h=setTimeout(function(){ t.classList.remove("show"); }, 2600); }

/* 状态栏着色跟随深浅色（head 里的 IIFE 首次同步，这里在切换时更新） */
function syncThemeColor(){
  var tc = document.querySelector('meta[name="theme-color"]');
  if (tc) tc.content = document.documentElement.classList.contains("dark") ? "#15181e" : "#faf6f0";
  var csm = document.querySelector('meta[name="color-scheme"]');
  if (csm) csm.content = document.documentElement.classList.contains("dark") ? "dark" : "light";
}

/* 外观模式："1"固定深色 / "0"固定浅色 / 未存过=跟随系统；跟随系统时随手机深浅色实时切换 */
function themeMode(){
  var v = null;
  try { v = localStorage.getItem("sched_dark_mode"); } catch(e){}
  return v === "1" ? "dark" : v === "0" ? "light" : "system";
}
var systemThemeMQ = window.matchMedia ? window.matchMedia("(prefers-color-scheme: dark)") : null;
function applyThemeMode(){
  var dark = themeMode() === "dark" || (themeMode() === "system" && systemThemeMQ && systemThemeMQ.matches);
  document.documentElement.classList.toggle("dark", dark);
  syncThemeColor();
  var btn = $("#darkToggleBtn");
  if (btn){
    btn.innerHTML = dark ? '☀️<span class="btn-txt"> 浅色</span>' : '🌙<span class="btn-txt"> 深色</span>';
  }
  document.querySelectorAll("#themeSeg .opt").forEach(function(o){
    o.classList.toggle("on", o.getAttribute("data-theme") === themeMode());
  });
}
if (systemThemeMQ && systemThemeMQ.addEventListener) {
  systemThemeMQ.addEventListener("change", function(){ if (themeMode() === "system") applyThemeMode(); });
}

/* 萌系确认弹窗：替代原生 confirm。resolve(true)=确认，resolve(false)=取消。
   自带一条历史条目：手势返回经 popstate 关闭；按钮/Esc 关闭时消费条目，
   不留空条目导致下一次返回"失灵"。skipNextPopRender 屏蔽主动回退那次 popstate。 */
var skipNextPopRender = false;
var activeConfirm = null;
function uiConfirm(opts){
  /* 已有确认框在弹着时，连点同一按钮只算一次 */
  if (activeConfirm) return activeConfirm;
  opts = opts || {};
  activeConfirm = new Promise(function(resolve){
    var settled = false;
    history.pushState({type:"uconfirm"}, "");
    var ov = document.createElement("div");
    ov.className = "overlay uconfirm";
    ov.innerHTML = '<div class="modal confirm-modal">' +
      '<div class="confirm-ico">' + (opts.icon || "🐾") + '</div>' +
      '<h3 class="confirm-title">' + esc(opts.title || "确定吗？") + '</h3>' +
      '<p class="confirm-msg">' + esc(opts.message || "") + '</p>' +
      '<div class="confirm-foot">' +
        '<button type="button" class="confirm-btn cancel">' + (opts.cancelText || "再想想") + '</button>' +
        '<button type="button" class="confirm-btn ok' + (opts.danger ? " danger" : "") + '">' + (opts.okText || "确定") + '</button>' +
      '</div></div>';
    function finish(val){
      if (settled) return;
      settled = true;
      activeConfirm = null;
      document.removeEventListener("keydown", onKey);
      if (mo) mo.disconnect();
      ov.classList.remove("show");
      if (!document.querySelector(".overlay.show")) document.body.style.overflow = "";
      setTimeout(function(){ if (ov && ov.parentNode) ov.parentNode.removeChild(ov); }, 220);
      /* 按钮/Esc 关闭：消费自己压入的历史条目；手势返回已在 popstate 弹掉，无需再退 */
      if (history.state && history.state.type === "uconfirm"){
        skipNextPopRender = true;
        history.back();
      }
      resolve(val);
    }
    function onKey(e){ if (e.key === "Escape") finish(false); }
    var mo = null;
    ov.addEventListener("click", function(e){
      if (e.target === ov){ finish(false); return; }
      var b = e.target.closest(".confirm-btn");
      if (b) finish(b.classList.contains("ok"));
    });
    document.addEventListener("keydown", onKey);
    ov._dismiss = function(){ finish(false); };
    // 视图重渲染即视为取消，避免确认弹窗残留在新视图上。
    // 只监听 #app 直接子节点变化（整页重绘才会触发）；
    // subtree 会把未读徽标等小更新误判成重渲染，导致确认框自己消失。
    mo = new MutationObserver(function(){ finish(false); });
    mo.observe(document.getElementById("app"), {childList:true});
    document.body.appendChild(ov);
    ov.classList.add("show");
    try { (opts.danger ? ov.querySelector(".confirm-btn.cancel") : ov.querySelector(".confirm-btn.ok")).focus(); } catch(e){}
  });
  return activeConfirm;
}

/* 输入校验失败反馈：红框抖动 + 聚焦 */
function markInputErr(el){
  if (!el) return;
  el.classList.add("input-err");
  try { el.focus(); } catch(e){}
  setTimeout(function(){ el.classList.remove("input-err"); }, 700);
}

function clearDataCaches(){
  try {
    localStorage.removeItem("sched_wishes");
    localStorage.removeItem("sched_messages");
    localStorage.removeItem("sched_settings_v1");
  } catch(e){}
  WISHES_DATA = null;
  MESSAGES_DATA = null;
}

/* localStorage 持久缓存统一封装（隐私模式等场景写入失败静默忽略） */
function saveLS(key, val){
  try { localStorage.setItem(key, JSON.stringify(val)); } catch(e){}
}
function loadLS(key){
  try { return JSON.parse(localStorage.getItem(key) || "null"); } catch(e){ return null; }
}
function applyWeek(j){
  DATA = j;
  saveLS("sched_week_" + ANCHOR, j);
  if (j && j.unread_count !== undefined) setUnreadCount(j.unread_count);
}
function applyMonth(j){
  MONTH_DATA = j;
  saveLS("sched_month_" + MONTH_ANCHOR, j);
  if (j && j.unread_count !== undefined) setUnreadCount(j.unread_count);
}

function api(path, opts){
  opts = opts || {};
  opts.headers = Object.assign({"Content-Type":"application/json"}, opts.headers||{});
  if (opts.body && typeof opts.body !== "string") opts.body = JSON.stringify(opts.body);
  return fetch(path, opts).then(function(r){
    return r.json().catch(function(){ return {}; }).then(function(j){
      if (!r.ok){
        if (r.status === 401 && path !== "/api/login"){
          try {
            localStorage.removeItem("sched_me");
            localStorage.removeItem("sched_week_" + ANCHOR);
          } catch(e){}
          clearDataCaches();
          ME = null; DATA = null; render();
        }
        throw new Error(j.error || ("请求失败 " + r.status));
      }
      return j;
    });
  });
}

function load(opts){
  if (CURRENT_VIEW === "cycle2") { render(); return Promise.resolve(); }
  var silent = opts && opts.silent;
  if (CURRENT_VIEW === "month"){
    if (!MONTH_DATA) MONTH_DATA = loadLS("sched_month_" + MONTH_ANCHOR);
    if (MONTH_DATA){
      render();
      if (silent) return Promise.resolve();
    }
    return api("/api/month?month=" + MONTH_ANCHOR).then(function(j){
      applyMonth(j);
      render();
    }).catch(function(e){ if (e.message !== "unauth") toast(e.message, "err"); });
  }
  if (CURRENT_VIEW === "messages" || CURRENT_VIEW === "wishes" || CURRENT_VIEW === "trips"){
    render();
    return Promise.resolve();
  }
  if (CURRENT_VIEW === "summary" || CURRENT_VIEW === "anniv" || CURRENT_VIEW === "settings"){
    if (DATA){
      render();
      return Promise.resolve();
    }
    return api("/api/week?date=" + ANCHOR).then(function(j){
      applyWeek(j);
      render();
    }).catch(function(e){ if (e.message !== "unauth") toast(e.message, "err"); });
  }
  if (DATA){
    render();
    if (silent) {
      return api("/api/week?date=" + ANCHOR).then(function(j){
        applyWeek(j);
        render();
      }).catch(function(){});
    }
  }
  return api("/api/week?date=" + ANCHOR).then(function(j){
    applyWeek(j);
    render();
    // 预取月历手账数据，实现后续点击零延迟秒开
    if (!MONTH_DATA){
      api("/api/month?month=" + MONTH_ANCHOR).then(function(mj){
        applyMonth(mj);
      }).catch(function(){});
    }
  }).catch(function(e){ if (e.message !== "unauth") toast(e.message, "err"); });
}

/* 模态框打开与关闭（支持原生历史记录与物理/手势返回键）；打开期间锁住 body 滚动防穿透。
   每个弹窗与一条历史条目一一配对：首次打开压入；已开着（或条目已在栈顶）只刷新不叠加；
   在别的弹窗上层打开时顶替下层条目（编辑页顶替详情页），返回不留空条目。 */
function openModal(modalId){
  var el = $(modalId);
  if (!el) return;
  var already = el.classList.contains("show") ||
    (history.state && history.state.type === "modal" && history.state.id === modalId);
  var nested = !already && !!document.querySelector(".overlay.show:not(.uconfirm)");
  el.classList.add("show");
  document.body.style.overflow = "hidden";
  if (already) return;
  if (nested) history.replaceState({type:"modal", id:modalId}, "");
  else history.pushState({type:"modal", id:modalId}, "");
}

function closeAllModals(fromPop){
  var showed = false;
  document.querySelectorAll(".overlay.show:not(.uconfirm)").forEach(function(o){
    o.classList.remove("show");
    showed = true;
  });
  var pv = $("#ovPhotoViewer");
  if (pv && pv.style.display !== "none"){
    stopPvLiveVideo();
    pv.style.display = "none";
    pv.classList.remove("show");
    showed = true;
  }
  document.body.style.overflow = "";
  /* 主动关闭时消费配对的历史条目，返回键不会停在已关闭的弹窗上；
     popstate 路径（fromPop）条目已被弹出，不能再退 */
  if (!fromPop && history.state && history.state.type === "modal") history.back();
  return showed;
}

/* 照片大图关闭：与打开时压入的 history 条目配对，关闭按钮/Esc 都走返回，
   隐藏动作统一由 popstate 里的 closeAllModals 完成，避免两条路径双触发 */
function closePhotoViewer(){
  var pv = $("#ovPhotoViewer");
  if (!pv || pv.style.display === "none") return;
  if (history.state && history.state.modal === "photo"){
    history.back();
    return;
  }
  stopPvLiveVideo();
  pv.style.display = "none";
  pv.classList.remove("show");
  document.body.style.overflow = "";
}
function renderTopbarHTML(){
  var myIsA = ME.uid === "a";
  var myPill = '<span class="user-pill ' + ME.uid + '">' + (myIsA ? '🐶 ' : '🐾 ') + esc(ME.name) + '</span>';
  return '<header class="topbar">' +
      '<div class="brand" data-act="switch-view" data-v="week">' +
        '<img src="/img/dogheads.png" alt="线条小狗" class="brand-img">' +
        '<span class="brand-title">两人日程 🐾</span>' +
      '</div>' +
      '<div class="top-actions">' +
        myPill +
        '<button class="p-btn icon-btn" data-act="toggle-dark" id="darkToggleBtn" title="切换深色/浅色模式">' +
          (document.documentElement.classList.contains("dark") ? '☀️<span class="btn-txt"> 浅色</span>' : '🌙<span class="btn-txt"> 深色</span>') +
        '</button>' +
        '<button class="p-btn icon-btn" data-act="switch-view" data-v="settings" title="设置">⚙️</button>' +
      '</div>' +
    '</header>';
}
function renderTopBar(){
  return '<div class="header-box" id="headerBox">' + renderTopbarHTML() + '</div>';
}

function setAppHTML(html){
  var app = $("#app");
  var tpl = document.createElement("div");
  tpl.innerHTML = html;
  // 顶栏在视图间内容不变：整树重建会让 brand 图标/标题在重绘瞬间闪动，改为复用旧节点。
  // 周视图的 headerBox 里还有吸顶的周导航条，先试整块复用，不一致时退回复用 .topbar 节点。
  var newBox = tpl.querySelector("#headerBox");
  var curBox = app.querySelector("#headerBox");
  if (newBox && curBox){
    if (newBox.innerHTML === curBox.innerHTML){
      tpl.replaceChild(curBox, newBox);
    } else {
      var newBar = newBox.querySelector(".topbar");
      var curBar = curBox.querySelector(".topbar");
      if (newBar && curBar && newBar.innerHTML === curBar.innerHTML){
        newBar.parentNode.replaceChild(curBar, newBar);
      }
    }
  }
  // 横滑功能条（手账页签等）随整树重建会把横向滚动位置弹回最左：重建前后按原顺序保持不动。
  var segX = [];
  app.querySelectorAll(".seg-scroll").forEach(function(el){ segX.push(el.scrollLeft); });
  app.innerHTML = "";
  while (tpl.firstChild) app.appendChild(tpl.firstChild);
  app.querySelectorAll(".seg-scroll").forEach(function(el, i){
    if (i < segX.length) el.scrollLeft = segX[i];
  });
}

function render(){
  var detailWasOpen = !!(curDetail && document.querySelector("#ovDetail.show"));
  if (!ME){ renderLogin(); } else {
    if (CURRENT_VIEW === "month") renderMonthApp();
    else if (CURRENT_VIEW === "summary") renderSummaryPage();
    else if (CURRENT_VIEW === "messages") renderMessagesPage();
    else if (CURRENT_VIEW === "wishes") renderWishesPage();
    else if (CURRENT_VIEW === "trips") renderTripsPage();
    else if (CURRENT_VIEW === "anniv") renderAnnivPage();
    else if (CURRENT_VIEW === "settings") renderSettingsPage();
    else if (CURRENT_VIEW === "cycle2") Cycle2.renderPage();
    else renderApp();
  }
  if (detailWasOpen && curDetail && $("#ovDetail")) openDetail(curDetail.id, curDetail.date, false);
  Cycle2.mount();
  dismissSplash();
}

function renderLogin(){
  var users = (META && META.users) || [];
  $("#app").innerHTML =
  '<div class="login-wrap">' +
    '<form class="login" id="loginForm">' +
      '<div class="login-wave-corner"><img src="/img/dog-wave.png" alt=""></div>' +
      '<div class="login-hero"><img src="/img/dog-couple.webp?v=20261116a" alt="线条小狗贴贴"></div>' +
      '<h2>线条小狗 · 两人日程 🐾</h2>' +
      '<div class="sub">汪！选好狗狗身份，输入密码进入小窝~</div>' +
      '<div class="login-cards" id="loginSeg">' +
        users.map(function(u){
          var isA = u.uid === "a";
          var ico = isA ? "🐶" : "🐾";
          var sub = isA ? "暖暖小金毛" : "软软小白狗";
          var cls = "login-card" + (u.uid === loginUid ? (" on " + (isA ? "side-a" : "side-b")) : "");
          return '<div class="' + cls + '" data-act="pick-login" data-uid="' + u.uid + '">' +
            '<span class="ico">' + ico + '</span>' +
            '<span class="name">' + esc(u.name) + '</span>' +
            '<span class="desc">' + sub + '</span>' +
          '</div>';
        }).join("") +
      '</div>' +
      '<div class="field" style="text-align:left"><label>小窝密码 🔑</label>' +
        '<input type="password" id="loginPw" placeholder="请输入密码" autocomplete="current-password" required>' +
      '</div>' +
      '<button class="p-btn pri" style="width:100%;padding:11px;font-size:15px;justify-content:center;margin-top:6px">' +
        '🐾 汪！立即进入' +
      '</button>' +
    '</form>' +
  '</div>';
}

var PXH = 50, WSTART = 480, WEND = 1320;
function gpx(m){ return Math.round((m - WSTART) * PXH / 60); }

function eventBlock(ev, uid, date){
  var bits = [];
  if (ev.location) bits.push('📍 ' + esc(ev.location));
  if (ev.week_spec) bits.push('🗓️ ' + esc(ev.week_spec) + '周');
  var tip = ev.title + (ev.note ? '：' + ev.note : '');
  var top = gpx(t2m(ev.tstart)), h = Math.max(gpx(t2m(ev.tend)) - top, 18);
  var isBothCard = uid === "both";
  var jTag = (!isBothCard && ev.uid === "both") ? "💕 " : "";
  var bothBadge = (isBothCard && h >= 44) ? '<span class="both-badge">🐶 💕 🐾 两人共同</span>' : '';
  return '<div class="ev ' + uid + '" data-act="detail" data-id="' + ev.id + '" data-date="' + date +
    '" style="top:' + top +
    'px;height:' + h + 'px" title="' + esc(tip) + '">' +
    bothBadge +
    '<span class="et-pill">' + ev.tstart + '–' + ev.tend + '</span>' +
    '<span class="etitle">' + (isBothCard && h < 44 ? '💕 ' : jTag) + esc(ev.title) + '</span>' +
    (bits.length ? '<span class="em">' + bits.join(' · ') + '</span>' : '') +
    (ev.n_comments ? '<span class="cbadge" title="' + ev.n_comments + ' 条评论">💬 ' + ev.n_comments + '</span>' : '') +
    '<button class="del" data-act="del" data-id="' + ev.id + '" title="删除此日程">×</button></div>';
}

/* 将同一用户、时间相互重叠的日程进行动态比例智能分组：
   若重叠部分占其中任一日程自身时长的比例较高（>= 45%），或者重叠占两者较短者的比例很高，说明属于实质性冲突/重合，归为一组堆叠切换（避免大面积遮盖）；
   若重叠部分占比较小（如交接班微量重合、长活动蹭到短课尾巴 < 40%），且重叠时长没有超过短日程一半，则动态判定为独立日程，各自按实际起止时间渲染，绝不异常拉长。 */
function shouldMergeOverlapping(evA, evB){
  var sA = t2m(evA.tstart), eA = t2m(evA.tend);
  var sB = t2m(evB.tstart), eB = t2m(evB.tend);
  var overlap = Math.min(eA, eB) - Math.max(sA, sB);
  if (overlap <= 0) return false;

  var durA = Math.max(eA - sA, 1);
  var durB = Math.max(eB - sB, 1);
  var minDur = Math.min(durA, durB);

  // 1. 动态比例：重叠时间占较短日程的比例 (ratioMin)
  var ratioMin = overlap / minDur;

  // 2. 动态比例：重叠时间占整个时间跨度的比例（交并比 IoU）
  var unionSpan = Math.max(eA, eB) - Math.min(sA, sB);
  var iou = overlap / Math.max(unionSpan, 1);

  // 若重叠超过较短日程的 45%，或者整体重合度 (IoU) 超过 35%，视为同一时段日程合并为切换卡
  return ratioMin >= 0.45 || iou >= 0.35;
}

function groupOverlapping(list){
  var sorted = list.slice().sort(function(a, b){
    var d = t2m(a.tstart) - t2m(b.tstart);
    return d !== 0 ? d : t2m(a.tend) - t2m(b.tend);
  });
  var groups = [];
  sorted.forEach(function(ev){
    var last = groups[groups.length - 1];
    var joined = false;
    if (last){
      joined = last.some(function(o){
        return shouldMergeOverlapping(o, ev);
      });
    }
    if (joined){ last.push(ev); }
    else { groups.push([ev]); }
  });
  return groups;
}

/* 同一时间段多个日程：渲染为可点击切换的堆叠卡片。
   关键：折叠卡的位置与高度始终跟随「当前选中日程」自身的真实起止时间，
   绝不取整组的最早开始~最晚结束；否则 1 小时的健身会和 3 小时的组会一样高。 */
var STACK_MIN_H = 40;     /* 标签条 + 一行标题的最小可读高度（低于真实时长时才兜底） */
var STACK_TIGHT_H = 46;   /* 低于该高度：只留标题一行 */
var STACK_COMPACT_H = 68; /* 低于该高度：标题 + 时间，隐藏地点行，避免半截文字被裁 */

function stackFitCls(h){
  if (h < STACK_TIGHT_H) return " tight";
  if (h < STACK_COMPACT_H) return " compact";
  return "";
}

function eventStackHTML(group, uid, date){
  var first = group[0];
  var gid = "stk_" + uid + "_" + date.replace(/-/g, "") + "_" + first.id;

  var geos = group.map(function(ev){
    var etop = gpx(t2m(ev.tstart));
    return { top: etop, h: Math.max(gpx(t2m(ev.tend)) - etop, STACK_MIN_H) };
  });

  var tabs = group.map(function(ev, i){
    var jTag = ev.uid === "both" ? "💕" : "";
    return '<button type="button" class="ev-tab' + (i === 0 ? ' on' : '') +
      '" data-act="ev-tab" data-g="' + gid + '" data-i="' + i +
      '" data-top="' + geos[i].top + '" data-h="' + geos[i].h + '"' +
      ' title="' + esc(ev.title) + ' ' + ev.tstart + '–' + ev.tend +
      '（' + durTxt(t2m(ev.tstart), t2m(ev.tend)) + '）">' +
      (group.length > 1 ? (i + 1) + '· ' : '') + jTag + esc(ev.title) + '</button>';
  }).join("");

  var bodies = group.map(function(ev, i){
    var bits = [];
    if (ev.location) bits.push('📍 ' + esc(ev.location));
    if (ev.week_spec) bits.push('🗓️ ' + esc(ev.week_spec) + '周');
    var jTag = ev.uid === "both" ? "💕 " : "";
    return '<div class="ev-stack-body" data-b="' + gid + '" data-i="' + i + '" style="display:' + (i === 0 ? 'block' : 'none') + '">' +
      '<span class="et-pill">' + ev.tstart + '–' + ev.tend + '</span>' +
      '<span class="etitle">' + jTag + esc(ev.title) + '</span>' +
      (bits.length ? '<span class="em">' + bits.join(' · ') + '</span>' : '') +
      (ev.n_comments ? '<span class="cbadge" title="' + ev.n_comments + ' 条评论">💬 ' + ev.n_comments + '</span>' : '') +
      '<button class="del" data-act="del" data-id="' + ev.id + '" title="删除此日程">×</button>' +
    '</div>';
  }).join("");

  return '<div class="ev-stack ' + uid + stackFitCls(geos[0].h) +
    '" data-stack="' + gid + '" data-act="detail" data-id="' + first.id +
    '" data-date="' + date + '" style="top:' + geos[0].top + 'px;height:' + geos[0].h + 'px">' +
    '<div class="ev-tabs">' + tabs + '</div>' + bodies + '</div>';
}

/* ---------------- 月历手账视图渲染 ---------------- */
/* =========================================================================
   独立全功能页面视图（时光简报 / 纪念日 / 设置）
   ========================================================================= */

function renderSummaryPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      diarySegHTML("summary") +
      '<div class="page-header-card">' +
        '<div>' +
          '<div class="page-header-title">📔 月度时光胶囊</div>' +
          '<div class="page-header-desc">每个月最后一天，把这个月的日程、手账和留言收进一个胶囊</div>' +
        '</div>' +
        '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dogheads.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px">' +
          '<button type="button" class="p-btn" id="summaryPrevBtn" data-act="summary-month" data-d="-1" title="上一个月" style="padding:5px 12px;font-size:14px;line-height:1">‹</button>' +
          '<div id="summaryMonthLabel" style="font-size:14.5px;font-weight:800;color:var(--ink-primary)">月度胶囊</div>' +
          '<button type="button" class="p-btn" id="summaryNextBtn" data-act="summary-month" data-d="1" title="下一个月" style="padding:5px 12px;font-size:14px;line-height:1">›</button>' +
        '</div>' +
        '<div id="summaryContentBox" style="min-height:200px;font-size:13.5px;line-height:1.6"></div>' +
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-top:16px;flex-wrap:wrap;gap:8px;padding-top:14px;border-top:1px dashed var(--line-strong)">' +
          '<div style="display:flex;gap:8px">' +
            '<button type="button" class="p-btn pri" data-act="send-summary-wx" style="font-size:12.5px;padding:6px 14px">📲 推送到俩人微信</button>' +
            '<button type="button" class="p-btn" data-act="refresh-summary" title="按同样的材料重新写一版" style="font-size:12.5px;padding:6px 14px">🔄 重新生成</button>' +
          '</div>' +
        '</div>' +
      '</div>' +
    '</div>';

  html += bottomNavHTML();
  html += modalsHTML();
  setAppHTML(html);
  loadSummary(curSummaryMonth);
}

function renderMessagesPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      '<div class="page-header-card">' +
        '<div>' +
          '<div class="page-header-title">💬 汪汪信箱 · 消息通知</div>' +
          '<div class="page-header-desc">🐾 日程变动、温馨留言与手账动态提醒</div>' +
        '</div>' +
        '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dog-wave.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;flex-wrap:wrap;gap:8px">' +
          '<div class="seg" id="msgFilterSeg" style="max-width:200px;margin-bottom:0">' +
            '<div class="opt' + (curMsgFilter==="all"?" on":"") + '" data-act="msg-filter" data-v="all">全部</div>' +
            '<div class="opt' + (curMsgFilter==="unread"?" on":"") + '" data-act="msg-filter" data-v="unread">未读' + (UNREAD_COUNT > 0 ? ' (' + UNREAD_COUNT + ')' : '') + '</div>' +
          '</div>' +
          '<div style="display:flex;gap:6px">' +
            '<button type="button" class="p-btn" data-act="read-all-msgs" style="font-size:11.5px;padding:4px 10px" title="将所有未读消息标记为已读">✨ 一键已读</button>' +
            '<button type="button" class="p-btn" data-act="clear-read-msgs" style="font-size:11.5px;padding:4px 10px" title="清理所有已读通知">🧹 清理已读</button>' +
            '<button type="button" class="p-btn" data-act="refresh-msgs" style="font-size:11.5px;padding:4px 8px" title="刷新消息列表">🔄</button>' +
          '</div>' +
        '</div>' +
        '<div id="messagesListContainer"></div>' +
      '</div>' +
    '</div>';

  html += bottomNavHTML();
  html += modalsHTML();
  setAppHTML(html);
  loadMessages();
}

function loadMessages(){
  var box = $("#messagesListContainer");
  if (!MESSAGES_DATA){
    MESSAGES_DATA = loadLS("sched_messages");
  }
  if (MESSAGES_DATA){
    // 缓存秒出，后台静默对账；未读数以最新一次拉取为准，不用缓存旧值刷新角标
    renderMessagesList();
    api("/api/messages").then(function(res){
      if (!(res && res.ok)) return;
      MESSAGES_DATA = res.messages || [];
      saveLS("sched_messages", MESSAGES_DATA);
      setUnreadCount(res.unread_count);
      if (CURRENT_VIEW === "messages") renderMessagesList();
    }).catch(function(){});
    return;
  }
  if (box) box.innerHTML = '<div style="text-align:center;padding:30px 0;color:var(--ink-muted);font-weight:700">🐶 正在拉取信箱通知...</div>';
  api("/api/messages").then(function(res){
    if (res && res.ok){
      MESSAGES_DATA = res.messages || [];
      saveLS("sched_messages", MESSAGES_DATA);
      setUnreadCount(res.unread_count);
      renderMessagesList();
    }
  }).catch(function(err){
    if (box) box.innerHTML = '<div style="color:red;padding:20px 0;text-align:center">加载失败：' + esc(err.message) + '</div>';
  });
}

function renderMessagesList(){
  var box = $("#messagesListContainer");
  if (!box) return;
  var list = MESSAGES_DATA || [];
  if (curMsgFilter === "unread"){
    list = list.filter(function(m){ return !m.is_read; });
  }
  if (list.length === 0){
    box.innerHTML = '<div style="text-align:center;padding:40px 10px;color:var(--ink-muted)">' +
      '<div style="width:72px;height:72px;margin:0 auto 10px;opacity:0.85"><img src="/img/dogrest.webp?v=20261116a" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '<div style="font-size:14px;font-weight:800;color:var(--ink-primary);margin-bottom:4px">' + (curMsgFilter==="unread" ? "太棒啦，所有消息都已读完汪~ 🎉" : "信箱空空如也汪~ 📭") + '</div>' +
      '<div style="font-size:12px;opacity:0.8">对方添加新日程、发表留言或更新手账时，都会第一时间在这里提醒你 🐾</div>' +
    '</div>';
    return;
  }

  box.innerHTML = list.map(function(m){
    var isUnread = !m.is_read;
    var icon = "🔔";
    if (m.title.indexOf("留言") !== -1 || m.content.indexOf("留言") !== -1) icon = "💬";
    else if (m.title.indexOf("日程") !== -1 || m.content.indexOf("日程") !== -1) icon = "🗓️";
    else if (m.title.indexOf("手账") !== -1 || m.content.indexOf("手账") !== -1) icon = "📔";
    else if (m.title.indexOf("信") !== -1 || m.title.indexOf("简报") !== -1) icon = "💌";
    else if (m.title.indexOf("测试") !== -1) icon = "🐶";

    var actionHint = "";
    if (m.target_action){
      if (m.target_action.indexOf("event:") === 0) actionHint = '<span class="msg-action-hint">查看相关日程 ➜</span>';
      else if (m.target_action.indexOf("diary:") === 0) actionHint = '<span class="msg-action-hint">查看相关手账 ➜</span>';
      else if (m.target_action.indexOf("view:summary") === 0) actionHint = '<span class="msg-action-hint">查看时光胶囊 ➜</span>';
      else if (m.target_action.indexOf("view:settings") === 0) actionHint = '<span class="msg-action-hint">前往设置 ➜</span>';
    }

    var timeStr = esc(m.created_at || "");

    return '<div class="msg-card' + (isUnread ? ' unread' : '') + '" data-act="open-msg" data-mid="' + m.id + '" data-target-action="' + esc(m.target_action || "") + '">' +
      '<div class="msg-card-top">' +
        '<div class="msg-card-title">' +
          '<span>' + icon + '</span>' +
          '<span>' + esc(m.title) + '</span>' +
          (isUnread ? '<span class="msg-unread-tag">NEW</span>' : '') +
        '</div>' +
        '<div class="msg-card-time">' + timeStr + '</div>' +
      '</div>' +
      '<div class="msg-card-body">' + esc(m.content) + '</div>' +
      '<div class="msg-card-footer">' +
        '<div>' + actionHint + '</div>' +
        '<div style="display:flex;gap:6px">' +
          (isUnread ? '<button type="button" class="msg-btn-sm" data-act="read-one-msg" data-mid="' + m.id + '" title="标为已读">✓ 标为已读</button>' : '') +
          '<button type="button" class="msg-btn-sm del" data-act="del-one-msg" data-mid="' + m.id + '" title="删除此消息">🗑️</button>' +
        '</div>' +
      '</div>' +
    '</div>';
  }).join("");
}

function renderWishesPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      diarySegHTML("wishes") +

      '<div class="wish-progress-box" id="wishProgressBox">' +
        '<div style="display:flex;align-items:center;justify-content:space-between;font-size:12.5px;font-weight:800;color:var(--ink-primary)">' +
          '<span id="wishProgressText">🐶 正在统计俩汪心愿进度...</span>' +
          '<span id="wishProgressPct" style="color:var(--dog-a-text);flex-shrink:0">0%</span>' +
        '</div>' +
        '<div class="wish-progress-bar"><div class="wish-progress-fill" id="wishProgressFill" style="width:0%"></div></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div class="seg seg-scroll" id="wishCatSeg" style="margin-bottom:14px;padding-bottom:2px">' +
          '<div class="opt' + (curWishFilter==="all"?" on":"") + '" data-act="wish-filter" data-v="all">全部</div>' +
          '<div class="opt' + (curWishFilter==="travel"?" on":"") + '" data-act="wish-filter" data-v="travel">✈️ 旅行</div>' +
          '<div class="opt' + (curWishFilter==="food"?" on":"") + '" data-act="wish-filter" data-v="food">🍜 美食</div>' +
          '<div class="opt' + (curWishFilter==="movie"?" on":"") + '" data-act="wish-filter" data-v="movie">🎬 影音</div>' +
          '<div class="opt' + (curWishFilter==="life"?" on":"") + '" data-act="wish-filter" data-v="life">🏡 生活</div>' +
          '<div class="opt' + (curWishFilter==="other"?" on":"") + '" data-act="wish-filter" data-v="other">💡 其他</div>' +
          '<div class="opt' + (curWishFilter==="done"?" on":"") + '" data-act="wish-filter" data-v="done">🎉 已实现</div>' +
        '</div>' +
        '<div id="wishesListContainer"></div>' +
      '</div>' +
    '</div>' +
    '<button class="fab" data-act="open-add-wish" title="许下一个新心愿">✨ 许新愿 +</button>';

  html += bottomNavHTML();
  html += modalsHTML();
  setAppHTML(html);
  loadWishes();
}

function wishLocalStats(list){
  var total = list.length, done = 0;
  for (var i = 0; i < total; i++){ if (list[i].is_done == 1) done++; }
  return {"total": total, "done": done, "pending": total - done};
}
function storeWishes(list){
  WISHES_DATA = list;
  saveLS("sched_wishes", list);
}
function loadWishes(){
  var box = $("#wishesListContainer");
  if (!WISHES_DATA){
    WISHES_DATA = loadLS("sched_wishes");
  }
  if (WISHES_DATA){
    // 缓存秒出，后台静默对账；数据有变化才重画，避免列表跳动
    updateWishStats(wishLocalStats(WISHES_DATA));
    renderWishesList();
    api("/api/wishes").then(function(res){
      if (!(res && res.ok)) return;
      var changed = JSON.stringify(res.wishes || []) !== JSON.stringify(WISHES_DATA);
      storeWishes(res.wishes || []);
      if (!changed) return;
      if (CURRENT_VIEW === "wishes"){
        updateWishStats(res.stats);
        renderWishesList();
      }
    }).catch(function(){});
    return;
  }
  if (box) box.innerHTML = '<div style="text-align:center;padding:30px 0;color:var(--ink-muted);font-weight:700">🐶 正在翻开俩汪心愿清单...</div>';
  api("/api/wishes").then(function(res){
    if (res && res.ok){
      storeWishes(res.wishes || []);
      updateWishStats(res.stats);
      renderWishesList();
    }
  }).catch(function(err){
    if (box) box.innerHTML = '<div style="color:red;padding:20px 0;text-align:center">加载失败：' + esc(err.message) + '</div>';
  });
}

function updateWishStats(stats){
  if (!stats) return;
  var total = stats.total || 0;
  var done = stats.done || 0;
  var pending = stats.pending || 0;
  var pct = total > 0 ? Math.round((done / total) * 100) : 0;
  var pText = $("#wishProgressText");
  var pPct = $("#wishProgressPct");
  var pFill = $("#wishProgressFill");
  if (pText){
    pText.textContent = total > 0 
      ? ("✨ 俩汪已携手完成 " + done + " 个心愿，还有 " + pending + " 个美好正在奔赴中~ 💕")
      : "还没有添加心愿哦，在下方许下你们的第一个心愿吧 🐾";
  }
  if (pPct) pPct.textContent = pct + "%";
  if (pFill) pFill.style.width = pct + "%";
}

function renderWishesList(){
  var box = $("#wishesListContainer");
  if (!box) return;
  var list = WISHES_DATA || [];
  if (curWishFilter === "done"){
    list = list.filter(function(w){ return w.is_done; });
  } else if (curWishFilter !== "all"){
    list = list.filter(function(w){ return !w.is_done && w.category === curWishFilter; });
  }

  if (list.length === 0){
    box.innerHTML = '<div style="text-align:center;padding:40px 10px;color:var(--ink-muted)">' +
      '<div style="width:72px;height:72px;margin:0 auto 10px;opacity:0.85"><img src="/img/dogrest.webp?v=20261116a" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '<div style="font-size:14px;font-weight:800;color:var(--ink-primary);margin-bottom:4px">' +
        (curWishFilter === "done" ? "还没有标记实现的心愿汪~ 继续加油！" : "此分类下暂无心愿便签汪~ 🐾") +
      '</div>' +
      '<div style="font-size:12px;opacity:0.8">点击右上角「许新心愿」记录下你们将来想一起做的事吧 ✨</div>' +
    '</div>';
    return;
  }

  var catMap = {
    travel: "✈️ 旅行",
    food: "🍜 美食",
    movie: "🎬 影音",
    life: "🏡 生活",
    other: "💡 其他"
  };

  box.innerHTML = list.map(function(w){
    var isDone = !!w.is_done;
    var catLabel = catMap[w.category] || "✨ 心愿";
    var isA = w.creator_uid === "a";
    var creatorPill = '<span class="school-badge ' + (isA ? "a" : "b") + '" style="font-size:10px;padding:1px 6px">' + (isA ? "🐶 " : "🐾 ") + esc(w.creator_name || (isA ? "小金毛" : "小白狗")) + '</span>';

    return '<div class="wish-card' + (w.priority ? ' priority' : '') + (isDone ? ' done' : '') + '">' +
      '<button type="button" class="wish-del-btn" data-act="del-wish" data-wid="' + w.id + '" title="删除此心愿">🗑️</button>' +
      '<button type="button" class="wish-check-btn" data-act="toggle-wish" data-wid="' + w.id + '" title="' + (isDone ? "重新标记为待完成" : "打勾！实现这个心愿") + '">' +
        (isDone ? '✓' : '🐾') +
      '</button>' +
      '<div class="wish-content">' +
        '<div class="wish-tag-row">' +
          '<span class="wish-cat-tag">' + catLabel + '</span>' +
          (w.priority ? '<span class="wish-fire-tag">🔥 超级想去</span>' : '') +
        '</div>' +
        '<div class="wish-title">' + esc(w.title) + '</div>' +
        (w.note ? ('<div class="wish-note">📝 ' + esc(w.note) + '</div>') : '') +
        '<div class="wish-meta-row">' +
          '<div style="display:flex;align-items:center;gap:6px">' +
            creatorPill +
            '<span>' + esc(w.created_at || "") + '</span>' +
            (isDone && w.done_at ? ('<span style="color:var(--free-text);font-weight:700"> · 🎉 ' + esc(w.done_at) + ' 达成</span>') : '') +
          '</div>' +
          (isDone ? ('<div style="display:flex;gap:6px"><button type="button" class="msg-btn-sm" data-act="wish-to-diary" data-title="' + esc(w.title) + '" title="顺手记一篇手账纪念">📸 记手账</button></div>') : '') +
        '</div>' +
      '</div>' +
    '</div>';
  }).join("");
}

function renderAnnivPage(){
  var html = renderTopBar() +
    '<div class="page-container">' +
      '<div class="page-header-card">' +
        '<div>' +
          '<div class="page-header-title">💖 纪念日与倒计时</div>' +
          '<div class="page-header-desc">🐾 记录每一个相恋心动的日子与生日</div>' +
        '</div>' +
        '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dog-couple.webp?v=20261116a" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div style="font-size:13.5px;font-weight:800;color:var(--ink-primary);margin-bottom:12px;display:flex;align-items:center;gap:6px">' +
          '<span>📋 重要日子列表</span>' +
        '</div>' +
        '<div id="annivListContainer" style="margin-bottom:16px"></div>' +
      '</div>' +

      '<div class="page-main-card">' +
        '<div style="font-size:14.5px;font-weight:800;color:var(--ink-primary);margin-bottom:12px">🐾 添加新的纪念日</div>' +
        '<form id="annivForm">' +
          '<div class="field"><label>日子类型</label>' +
            '<div class="seg" id="annivTypeSeg">' +
              '<div class="opt on" data-act="anniv-type" data-v="love">💖 相恋纪念（累计天数）</div>' +
              '<div class="opt" data-act="anniv-type" data-v="birthday">🎂 生日/节日（每年倒计时）</div>' +
              '<div class="opt" data-act="anniv-type" data-v="countdown">⏳ 目标/大事件（单次倒计时）</div>' +
            '</div>' +
          '</div>' +
          '<div class="field"><label>纪念日名称</label><input id="an_title" required maxlength="60" placeholder="如: 我们相恋啦 / 菁宝生日 🎂"></div>' +
          '<div class="field"><label>日期</label><input type="date" id="an_date" required value="' + todayISO() + '"></div>' +
          '<button class="p-btn pri" style="margin-top:10px;width:100%;padding:10px;justify-content:center;font-size:14px">💖 记下这一天 🐾</button>' +
        '</form>' +
      '</div>' +
    '</div>';

  html += bottomNavHTML();
  html += modalsHTML();
  setAppHTML(html);
  renderAnnivList();
}

function renderSettingsPage(){ return Settings.render(); }

function renderMonthApp(){
  var ym = MONTH_ANCHOR; // "YYYY-MM"
  var parts = ym.split("-");
  var year = +parts[0], month = +parts[1];
  var u = (META && META.users) || [{uid:"a",name:"小金毛"},{uid:"b",name:"小白狗"}];

  // 计算本月日历格子 (周一到周日)
  var firstDay = new Date(year, month - 1, 1);
  var lastDay = new Date(year, month, 0);
  var startDayOfWeek = (firstDay.getDay() + 6) % 7; // 0=周一, 6=周日
  var totalDays = lastDay.getDate();

  var cellsHtml = "";
  // 周标题
  WD.forEach(function(w){
    cellsHtml += '<div class="month-head-cell">' + w + '</div>';
  });

  // 上月填充
  var prevMonthLastDay = new Date(year, month - 1, 0).getDate();
  for (var i = startDayOfWeek - 1; i >= 0; i--){
    var pDay = prevMonthLastDay - i;
    cellsHtml += '<div class="month-cell other-month"><div class="mday-head"><span class="mday-num">' + pDay + '</span></div></div>';
  }

  var byDate = (MONTH_DATA && MONTH_DATA.diaries_by_date) || {};
  var annivList = (MONTH_DATA && MONTH_DATA.anniversaries) || (DATA && DATA.anniversaries) || [];
  var todayStr = todayISO();

  // 本月各天
  for (var d = 1; d <= totalDays; d++){
    var dtStr = year + "-" + pad(month) + "-" + pad(d);
    var isToday = (dtStr === todayStr);
    var dayDiaries = byDate[dtStr] || [];
    
    // 纪念日判定
    var dayBadges = [];
    annivList.forEach(function(item){
      if (item.target_type === "birthday"){
        var bp = item.date.split("-");
        if (+bp[1] === month && +bp[2] === d){
          dayBadges.push('<span class="mday-badge" title="' + esc(item.title) + ' 生日">🎂</span>');
        }
      } else if (item.target_type === "love"){
        var lp = item.date.split("-");
        if (+lp[1] === month && +lp[2] === d){
          dayBadges.push('<span class="mday-badge" title="' + esc(item.title) + ' 纪念日">💖</span>');
        }
      } else {
        if (item.date === dtStr){
          dayBadges.push('<span class="mday-badge" title="' + esc(item.title) + ' 倒计时">⏳</span>');
        }
      }
    });
    if (dayDiaries.length > 1){
      dayBadges.push('<span class="mday-badge" title="这一天有 ' + dayDiaries.length + ' 篇手账">📔' + dayDiaries.length + '</span>');
    }

    var diaryPreview = "";
    if (dayDiaries.length > 0){
      var firstD = dayDiaries[0];
      var thumb = firstD.cover_photo ? ('/photos/tb_' + firstD.cover_photo) : '/img/dog-add.png';
      diaryPreview = '<div class="mday-polaroid">' +
        '<img src="' + thumb + '" class="mday-thumb" alt="">' +
        '<div class="mday-info">' + esc(firstD.title) + '</div>' +
      '</div>';
    }

    cellsHtml += '<div class="month-cell' + (isToday ? ' today' : '') + '" data-act="open-day-diary" data-date="' + dtStr + '">' +
      '<div class="mday-head">' +
        '<span class="mday-num">' + d + '</span>' +
        '<div class="mday-badges">' + dayBadges.join("") + '</div>' +
      '</div>' +
      diaryPreview +
    '</div>';
  }

  // 下月填充对齐 7 的倍数
  var currentCellCount = startDayOfWeek + totalDays;
  var remain = (7 - (currentCellCount % 7)) % 7;
  for (var n = 1; n <= remain; n++){
    cellsHtml += '<div class="month-cell other-month"><div class="mday-head"><span class="mday-num">' + n + '</span></div></div>';
  }

  var count = (MONTH_DATA && MONTH_DATA.total_count) || 0;

  var html =
  renderTopBar() +

  '<div class="page-container">' +
    diarySegHTML("month") +

    '<div class="month-cal-wrap" style="padding:0">' +
      '<div class="month-topcard">' +
      '<div class="week-nav">' +
        '<button class="p-btn" data-act="month-prev">‹ 上月</button>' +
        '<button class="p-btn pri" data-act="month-cur">🐾 本月</button>' +
        '<button class="p-btn" data-act="month-next">下月 ›</button>' +
      '</div>' +
      '<div style="font-size:15px;font-weight:800;color:var(--ink-primary)">' +
        '<button type="button" class="month-jump-btn" data-act="month-pick">🗓️ ' + year + ' 年 ' + month + ' 月 <span class="mp-caret">▾</span></button>' +
        ' · 俩汪足迹手账 🐾' +
      '</div>' +
      '<div style="font-size:12px;font-weight:700;color:var(--dog-a)">' +
        '✨ 本月已记录 ' + count + ' 篇出游美好回忆' +
      '</div>' +
    '</div>' +

    '<div class="month-grid">' + cellsHtml + '</div>' +
    '</div>' +
  '</div>' +
  '<button class="fab" data-act="open-add-diary" title="记下今天去哪玩啦">🐾 记手账 +</button>';

  html += bottomNavHTML();
  html += modalsHTML();

  setAppHTML(html);
}

function renderApp(){
  var u = DATA.users, wn = DATA.weeknums;
  var st0 = DATA.settings;
  WSTART = t2m(st0.window_start); WEND = t2m(st0.window_end);
  var CALH = Math.round((WEND - WSTART) * PXH / 60);

  /* 教学周次角标：随周次切换显示"第 N 周 · 单/双周"，帮助对上单双周课表 */
  function weekNumTag(n, ico){
    if (n == null) return "";
    return ico + "第" + n + "周·" + (n % 2 === 1 ? "单" : "双");
  }
  /* 教学周次角标拆两半：周次靠左、单双周靠右，周导航按钮居中；
     周次角标（带▾）= 按周跳转入口，今天按钮（带▾）= 按日期跳转入口 */
  var weekLeftHTML = "", weekRightHTML = "";
  if (wn.a != null || wn.b != null){
    if (wn.a != null && wn.a === wn.b){
      weekLeftHTML = '<button type="button" class="wl-weeknum" data-act="week-pick" title="按周次跳转">第 ' + wn.a + ' 周 <span class="mp-caret">▾</span></button>';
      weekRightHTML = '<button type="button" class="wl-weeknum wl-right">' + (wn.a % 2 === 1 ? "单周" : "双周") + '</button>';
    } else {
      weekRightHTML = '<button type="button" class="wl-weeknum wl-right" data-act="week-pick" title="按周次跳转">' + weekNumTag(wn.a, "🐶") + " " + weekNumTag(wn.b, "🐾") + ' <span class="mp-caret">▾</span></button>';
    }
  }

  var head = '<div class="corner">时间</div>';
  var body = '<div class="axcol" style="height:' + CALH + 'px">';
  for (var m = WSTART; m <= WEND; m += 60){
    var st = 'top:' + gpx(m) + 'px;';
    if (m === WSTART) st += 'transform:none;';
    else if (m === WEND) st += 'transform:translateY(-100%);';
    body += '<span class="axh" style="' + st + '">' + m2t(m) + '</span>';
  }
  body += '</div>';

  DATA.days.forEach(function(d){
    var isToday = d.date === todayISO();
    head += '<div class="dh' + (isToday ? ' today' : '') + '"' + (isToday ? ' id="colTodayHeader"' : ' id="dayhead-' + d.date + '"') + '>' +
      '<div class="dh-title">' +
        '<span class="dh-weekday">' + WD[d.weekday] + '</span>' +
        '<span class="dh-date">' + d.date.slice(5) + '</span>' +
      '</div>' +
      (isToday ? '<span class="today-badge">✨ 今天 ✨</span>' : '') +
      '<div class="dh-split">' +
        '<span class="part-a">🐶 ' + esc(u.a.name) + '</span>' +
        '<span class="part-b">🐾 ' + esc(u.b.name) + '</span>' +
      '</div>' +
    '</div>';

    var emptyDay = d.users.a.events.length === 0 && d.users.b.events.length === 0;
    body += '<div class="dcol' + (isToday ? ' today' : '') + '"' + (isToday ? ' id="colToday"' : ' id="daycol-' + d.date + '"') + ' style="height:' + CALH + 'px"><div class="divider"></div>' +
      (emptyDay ?
        '<div class="sleepy" title="今天俩汪都没安排，睡大觉~">' +
          '<div class="sleep-box">' +
            '<div class="sleep-zs">z Z Z 💤</div>' +
            '<img src="/img/dogrest.webp?v=20261116a" alt="睡大觉">' +
            '<div class="sleep-text">今天俩汪都放假<br>窝着睡大觉~ 💤</div>' +
          '</div>' +
        '</div>' : '');

    d.free.forEach(function(iv){
      var top = gpx(iv[0]), h = gpx(iv[1]) - top;
      body += '<div class="fband" style="top:' + top + 'px;height:' + h + 'px" title="共同空闲 ' +
        m2t(iv[0]) + '–' + m2t(iv[1]) + ' ' + durTxt(iv[0], iv[1]) + '">' +
        (h >= 24 ? '<span class="fdur-tag">🐾 贴贴 · ' + durTxt(iv[0], iv[1]) + ' 💕</span>' : '') +
      '</div>';
    });

    var evListA = d.users.a.events.filter(function(ev){ return ev.uid !== "both"; });
    var evListB = d.users.b.events.filter(function(ev){ return ev.uid !== "both"; });
    d.users.a.events.forEach(function(ev){
      if (ev.uid === "both") {
        var s = t2m(ev.tstart), e = t2m(ev.tend);
        var ovA = evListA.some(function(o){ return Math.max(t2m(o.tstart), s) < Math.min(t2m(o.tend), e); });
        var ovB = evListB.some(function(o){ return Math.max(t2m(o.tstart), s) < Math.min(t2m(o.tend), e); });
        if (ovA || ovB) {
          evListA.push(ev);
          evListB.push(ev);
        }
      }
    });
    var evBothSolo = d.users.a.events.filter(function(ev){
      if (ev.uid !== "both") return false;
      return !evListA.some(function(x){ return x.id === ev.id; });
    });

    [['a', evListA], ['b', evListB], ['both', evBothSolo]].forEach(function(pair){
      var uid = pair[0], list = pair[1];
      groupOverlapping(list).forEach(function(group){
        body += (group.length > 1) ? eventStackHTML(group, uid, d.date) : eventBlock(group[0], uid, d.date);
      });
    });
    body += '</div>';
  });

  var annivList = DATA.anniversaries || [];
  var todayStr = todayISO();
  var annivHtml = "";
  if (annivList.length > 0){
    annivHtml = '<div class="anniv-bar">' +
      annivList.map(function(item){
        var calc = calcAnniv(item, todayStr);
        return '<span class="anniv-capsule ' + calc.type + '" data-act="open-anniv-list" title="点击查看/管理纪念日">' +
          calc.icon + ' ' + calc.text +
        '</span>';
      }).join("") +
      '<button class="anniv-add-btn" data-act="open-anniv" title="添加纪念日/倒计时">＋ 记一个</button>' +
    '</div>';
  } else {
    annivHtml = '<div class="anniv-bar">' +
      '<button class="anniv-add-btn" data-act="open-anniv" style="margin:2px 0">' +
        '💖 记录第一个相恋纪念日 / 生日倒计时 🐾' +
      '</button>' +
    '</div>';
  }

  var html =
  '<div class="header-box" id="headerBox">' +
    renderTopbarHTML() +

    '<div class="weekbar-wrap">' +
      '<div class="weekbar-card">' +
        weekLeftHTML +
        '<div class="week-nav">' +
          '<button class="p-btn" data-act="prev">‹ 上周</button>' +
          '<button class="p-btn pri" data-act="date-pick" title="按日期跳转到任意一天"><span class="today-paw">🐾 </span>' + todayISO().slice(5) + ' <span class="mp-caret">▾</span></button>' +
          '<button class="p-btn" data-act="next">下周 ›</button>' +
        '</div>' +
        weekRightHTML +
      '</div>' +
    '</div>' +
    annivHtml +
  '</div>' +

  '<main><div class="calwrap"><div class="cal" style="--pxh:' + PXH + 'px">' + head + body + '</div></div></main>' +

  '<div class="dogwalk-section">' +
    '<div class="dogwalk-banner">' +
      '<span class="heart-pulse">💕</span>' +
      '<span>今天也在认真生活，努力奔向你</span>' +
      '<span class="heart-pulse">🐾</span>' +
    '</div>' +
    '<div class="dogwalk">' +
      '<div class="walker step-1"><img src="/img/dog-walk1.webp?v=20261116a" alt="线条小狗" loading="lazy" decoding="async"></div>' +
      '<div class="walker step-2"><img src="/img/dog-walk2.webp?v=20261116a" alt="线条小狗" loading="lazy" decoding="async"></div>' +
      '<div class="walker step-3"><img src="/img/dog-walk3.webp?v=20261116a" alt="线条小狗" loading="lazy" decoding="async"></div>' +
      '<div class="walker step-4"><img src="/img/dog-walk4.webp?v=20261116a" alt="线条小狗" loading="lazy" decoding="async"></div>' +
    '</div>' +
  '</div>' +
  '<button class="fab" data-act="open-add">🐾 记新日程 ＋</button>';

  html += bottomNavHTML();
  html += modalsHTML();

  /* 重渲染保留课表滚动位置（新增/编辑/留言都会触发整页重绘，不保留会跳回最左列） */
  var prevGridScroll = null;
  var cwOld = document.querySelector(".calwrap");
  if (cwOld) prevGridScroll = { left: cwOld.scrollLeft, top: cwOld.scrollTop };

  setAppHTML(html);

  if (prevGridScroll){
    var cwNew = document.querySelector(".calwrap");
    if (cwNew){ cwNew.scrollLeft = prevGridScroll.left; cwNew.scrollTop = prevGridScroll.top; }
  }
}

/* 月份跳转面板：手账月历与周期页共用。点各自的月份标签打开，选年选月直达。 */
var monthPickCb = null, monthPickCur = null, mpBrowseYear = 0;
function ensureMonthPicker(){
  var ov = $("#ovMonthPick");
  if (ov) return ov;
  ov = document.createElement("div");
  ov.className = "overlay";
  ov.id = "ovMonthPick";
  ov.innerHTML =
    '<div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3>跳到哪个月？🗓️</h3></div>' +
        '<img src="/img/dog-set.webp?v=20261116a" alt="" class="mhead-img">' +
      '</div>' +
      '<div class="mp-yearrow">' +
        '<button type="button" class="p-btn icon-btn" data-mp="year-prev" aria-label="上一年">‹</button>' +
        '<span class="mp-year" id="mpYear"></span>' +
        '<button type="button" class="p-btn icon-btn" data-mp="year-next" aria-label="下一年">›</button>' +
      '</div>' +
      '<div class="mp-grid" id="mpGrid"></div>' +
      '<div class="mp-foot"><button type="button" class="p-btn" data-mp="back-now">🐾 回到本月</button></div>' +
    '</div>';
  document.body.appendChild(ov);
  ov.addEventListener("click", function(e){
    if (e.target === ov){
      closeAllModals();
      return;
    }
    var b = e.target.closest("[data-mp]");
    if (!b) return;
    if (b.dataset.mp === "year-prev" || b.dataset.mp === "year-next"){
      mpBrowseYear += b.dataset.mp === "year-next" ? 1 : -1;
      paintMonthPickGrid();
      return;
    }
    if (b.dataset.mp === "back-now"){ pickMonthTo(todayISO().slice(0, 7)); return; }
    if (b.dataset.mp === "m"){ pickMonthTo(mpBrowseYear + "-" + b.dataset.m); }
  });
  return ov;
}
function paintMonthPickGrid(){
  $("#mpYear").textContent = mpBrowseYear + " 年";
  var nowY = +todayISO().slice(0, 4), nowM = +todayISO().slice(5, 7);
  var cells = "";
  for (var m = 1; m <= 12; m++){
    var mm = pad(m);
    var on = (mpBrowseYear + "-" + mm) === monthPickCur ? " on" : "";
    var now = (mpBrowseYear === nowY && m === nowM && !on) ? " now" : "";
    cells += '<button type="button" class="mp-m' + on + now + '" data-mp="m" data-m="' + mm + '">' + m + ' 月</button>';
  }
  $("#mpGrid").innerHTML = cells;
}
function pickMonthTo(ym){
  var cb = monthPickCb;
  monthPickCb = null;
  /* closeAllModals 会配对消费弹窗的历史条目，回退触发 popstate 平滑关闭 */
  closeAllModals();
  if (cb) cb(ym);
}
function openMonthPicker(ym, cb){
  monthPickCb = cb || null;
  monthPickCur = ym || todayISO().slice(0, 7);
  mpBrowseYear = +monthPickCur.slice(0, 4);
  ensureMonthPicker();
  paintMonthPickGrid();
  openModal("#ovMonthPick");
}

/* 周次跳转面板：点课表顶栏的周次角标打开，列出校历各周的日期区间，
   点行跳整周，展开可跳到该周任意一天。与月份面板共用弹窗/历史契约。 */
var wpWeek1 = null, wpEnd = 0, wpViewedWeek = 0;

function isoDiffDays(a, b){
  var p = function(s){ var q = s.split("-"); return new Date(+q[0], +q[1] - 1, +q[2]).getTime(); };
  return Math.round((p(a) - p(b)) / 86400000);
}

function ensureWeekPicker(){
  var ov = $("#ovWeekPick");
  if (ov) return ov;
  ov = document.createElement("div");
  ov.className = "overlay";
  ov.id = "ovWeekPick";
  ov.innerHTML =
    '<div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3>跳到哪一周？🗓️</h3></div>' +
        '<img src="/img/dog-set.webp?v=20261116a" alt="" class="mhead-img">' +
      '</div>' +
      '<div class="wp-field">' +
        '<span class="wp-label">按周次</span>' +
        '<input type="number" class="wp-num-input" id="wpNum" min="1" inputmode="numeric" aria-label="输入周次">' +
        '<span class="wp-zhou">周</span>' +
        '<select id="wpSel" aria-label="选择周次"></select>' +
      '</div>' +
      '<div class="wp-dates-hint" id="wpHint"></div>' +
      '<div class="mp-foot"><button type="button" class="p-btn" data-wp="back-now">🐾 回到本周</button></div>' +
    '</div>';
  document.body.appendChild(ov);
  ov.addEventListener("click", function(e){
    if (e.target === ov){ closeAllModals(); return; }
    var b = e.target.closest("[data-wp]");
    if (!b) return;
    if (b.dataset.wp === "back-now"){ pickWeekTo(weekNumOfDate(todayISO()), todayISO()); return; }
  });
  /* 下拉选周：change 即跳 */
  ov.querySelector("#wpSel").addEventListener("change", function(){
    var k = +this.value;
    $("#wpNum").value = k;
    paintWeekHint(k);
    pickWeekTo(k, null);
  });
  /* 数字输入：回车或失焦时校验并跳 */
  var num = ov.querySelector("#wpNum");
  var numGo = function(){
    var k = Math.round(+num.value);
    if (!k || k < 1 || k > wpEnd){
      toast("请输入 1 ~ " + wpEnd + " 之间的周次哦 🐾", "err");
      num.value = wpViewedWeek;
      return;
    }
    $("#wpSel").value = String(k);
    paintWeekHint(k);
    pickWeekTo(k, null);
  };
  num.addEventListener("change", numGo);
  num.addEventListener("keydown", function(e){ if (e.key === "Enter") numGo(); });
  return ov;
}

function weekNumOfDate(iso){
  /* 校历第几周；第 1 周周一之前返回 null */
  var diff = isoDiffDays(iso, wpWeek1);
  return diff < 0 ? null : Math.floor(diff / 7) + 1;
}

function wpWeekMonday(k){ return shiftISO(wpWeek1, (k - 1) * 7); }

function paintWeekHint(k){
  var mon = wpWeekMonday(k), end = shiftISO(mon, 6);
  $("#wpHint").textContent = "第 " + k + " 周：" + mon.slice(5) + " ~ " + end.slice(5) + "（" + (k % 2 === 1 ? "单" : "双") + "周）";
}

function paintWeekOptions(){
  var t = todayISO(), html = "";
  var todayWeek = weekNumOfDate(t);
  for (var k = 1; k <= wpEnd; k++){
    var mon = wpWeekMonday(k), end = shiftISO(mon, 6);
    var mark = (k === todayWeek) ? " · 本周" : "";
    html += '<option value="' + k + '">第 ' + k + ' 周（' + mon.slice(5) + ' ~ ' + end.slice(5) + '）' + mark + '</option>';
  }
  $("#wpSel").innerHTML = html;
}

function pickWeekTo(k, dayISO){
  if (k == null) k = weekNumOfDate(todayISO());
  if (k == null) return;
  var mon = wpWeekMonday(k);
  closeAllModals();
  ANCHOR = mon;
  DATA = null;
  load().then(function(){
    setTimeout(function(){ scrollToDay(dayISO || mon); }, 60);
  });
}

function openWeekPicker(){
  var wn = (DATA && DATA.weeknums) ? DATA.weeknums[ME.uid] : null;
  if (wn == null){
    toast("先在「设置」里填好校历第 1 周，才能按周跳转哦 🐾", "err");
    return;
  }
  /* 校历第 1 周周一 = 当前查看周周一 - (周次-1)*7 */
  wpWeek1 = shiftISO(DATA.week_start, -(wn - 1) * 7);
  var todayWeek = weekNumOfDate(todayISO()) || 1;
  wpViewedWeek = wn;
  wpEnd = Math.max(wn, todayWeek) + 8;
  ensureWeekPicker();
  paintWeekOptions();
  $("#wpNum").value = wn;
  $("#wpNum").max = wpEnd;
  $("#wpSel").value = String(wn);
  paintWeekHint(wn);
  openModal("#ovWeekPick");
}

/* 按日期跳转：入口在「今天」按钮的▾；不需要校历配置，任意日期可达 */
function ensureDatePick(){
  var ov = $("#ovDatePick");
  if (ov) return ov;
  ov = document.createElement("div");
  ov.className = "overlay";
  ov.id = "ovDatePick";
  ov.innerHTML =
    '<div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3>跳到哪一天？📅</h3></div>' +
        '<img src="/img/dog-set.webp?v=20261116a" alt="" class="mhead-img">' +
      '</div>' +
      '<div class="wp-field">' +
        '<span class="wp-label">按日期</span>' +
        '<input type="date" class="wp-date-input" id="dpDate" aria-label="选择日期">' +
      '</div>' +
      '<div class="mp-foot"><button type="button" class="p-btn" data-dp="back-today">🐾 回到今天</button></div>' +
    '</div>';
  document.body.appendChild(ov);
  ov.addEventListener("click", function(e){
    if (e.target === ov){ closeAllModals(); return; }
    var b = e.target.closest("[data-dp]");
    if (!b) return;
    if (b.dataset.dp === "back-today"){ jumpToDay(todayISO()); return; }
  });
  ov.querySelector("#dpDate").addEventListener("change", function(){
    if (this.value) jumpToDay(this.value);
  });
  ov.querySelector("#dpDate").addEventListener("keydown", function(e){
    if (e.key === "Enter" && this.value) jumpToDay(this.value);
  });
  return ov;
}

function openDatePick(){
  ensureDatePick();
  $("#dpDate").value = todayISO();
  openModal("#ovDatePick");
}

function jumpToDay(date){
  if (!date) return;
  var p = date.split("-");
  var wd = (new Date(+p[0], +p[1] - 1, +p[2]).getDay() + 6) % 7; /* 周一=0 */
  var mon = shiftISO(date, -wd);
  closeAllModals();
  ANCHOR = mon;
  DATA = null;
  load().then(function(){
    setTimeout(function(){ scrollToDay(date); }, 60);
  });
}

function bottomNavHTML(){
  var v = CURRENT_VIEW;
  var diaryOn = (v === "month" || v === "wishes" || v === "trips" || v === "summary") ? ' on' : '';
  return '<nav class="bottom-nav">' +
    '<div class="b-tab' + (v === "week" ? ' on' : '') + '" data-act="switch-view" data-v="week">' +
      '<span class="b-ico">🗓️</span><span class="b-txt">课表</span>' +
    '</div>' +
    '<div class="b-tab' + diaryOn + '" data-act="switch-view" data-v="month">' +
      '<span class="b-ico">📔</span><span class="b-txt">手账</span>' +
    '</div>' +
    '<div class="b-tab' + (v === "cycle2" ? ' on' : '') + '" data-act="switch-view" data-v="cycle2">' +
      '<span class="b-ico">🌷</span><span class="b-txt">周期</span>' +
    '</div>' +
    '<div class="b-tab' + (v === "messages" ? ' on' : '') + '" data-act="switch-view" data-v="messages">' +
      '<span class="b-ico">💬' + (UNREAD_COUNT > 0 ? ('<span class="b-badge" id="bUnreadBadge">' + (UNREAD_COUNT > 99 ? '99+' : UNREAD_COUNT) + '</span>') : '<span class="b-badge" id="bUnreadBadge" style="display:none"></span>') + '</span><span class="b-txt">消息</span>' +
    '</div>' +
  '</nav>';
}

function modalsHTML(){
  var u = (DATA && DATA.users) || (MONTH_DATA && MONTH_DATA.users) || {a:{name:"小金毛"},b:{name:"小白狗"}};
  var uidOpts = ["a","b","both"].map(function(uid){
    var sideCls = uid === "b" ? " b-side" : (uid === "both" ? " both-side" : "");
    var label = uid === "a" ? ("🐶 小金毛 · " + esc(u.a ? u.a.name : "a")) :
                (uid === "b" ? ("🐾 小白狗 · " + esc(u.b ? u.b.name : "b")) : "💕 两人共同");
    return '<div class="opt' + (uid===addUid?(" on" + sideCls):"") + '" data-act="pick-add" data-uid="' + uid + '">' +
      label +
    '</div>';
  }).join("");
  var wdOpts = WD.map(function(w,i){ return '<option value="' + i + '">' + w + '</option>'; }).join("");
  var wStart = (DATA && DATA.week_start) || todayISO();

  return '<div class="overlay" id="ovAdd"><div class="modal">' +
    '<div class="mhead">' +
      '<div class="mhead-left"><h3 id="addModalTitle">添加日程 🐾</h3></div>' +
      '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
    '</div>' +
    '<form id="addForm">' +
      '<div class="field"><label>谁的日程？</label><div class="seg" id="addSeg">' + uidOpts + '</div></div>' +
      '<div class="field"><label>重复类型</label><div class="seg" id="repSeg">' +
        '<div class="opt' + (addRep==="weekly"?" on":"") + '" data-act="rep" data-v="weekly">🔄 每周重复</div>' +
        '<div class="opt' + (addRep==="once"?" on":"") + '" data-act="rep" data-v="once">📅 单次日程</div></div></div>' +
      '<div class="field" id="fldWd"><label>星期几</label><select id="f_wd">' + wdOpts + '</select></div>' +
      '<div class="field" id="fldDt" style="display:none"><label>具体日期</label><input type="date" id="f_date" value="' + wStart + '"></div>' +
      '<div class="row2">' +
        '<div class="field"><label>开始时间</label><input type="time" id="f_ts" required value="19:00"></div>' +
        '<div class="field"><label>结束时间</label><input type="time" id="f_te" required value="21:00"></div></div>' +
      '<div class="field"><label>事项名称</label><input id="f_title" required maxlength="120" placeholder="如：组会 / 跑步 / 一起吃火锅 🍲"></div>' +
      '<div class="field"><label>地点（可选）</label><input id="f_loc" maxlength="200" placeholder="如：教学楼A101 / 图书馆"></div>' +
      '<div class="field"><label>备注（可选）</label><input id="f_note" maxlength="200" placeholder="给 TA 留个小提醒~"></div>' +
      '<div class="field" id="fldWs"><label>周次范围（可选：2-17 / 2-16双 / 1-4,9-12；留空=每周）</label><input id="f_ws" maxlength="60" placeholder="留空代表全学期每周"></div>' +
      '<div class="foot">' +
        '<button type="button" class="p-btn" data-act="close">取消</button>' +
        '<button class="p-btn pri" id="addModalSubmitBtn">🐾 记在小本本上！</button>' +
      '</div>' +
    '</form></div></div>' +

    '<div class="overlay" id="ovDetail"><div class="modal" id="ovDetailBox"></div></div>' +

    '<div class="overlay" id="ovDiaryDay"><div class="modal" id="ovDiaryDayBox"></div></div>' +

    '<div class="overlay" id="ovAddWish"><div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3>许下一个新心愿 ✨</h3></div>' +
        '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
      '</div>' +
      '<form id="addWishForm">' +
        '<div class="field"><label>谁的心愿？</label>' +
          '<div class="seg" id="wishUidSeg">' +
            '<div class="opt' + (addWishUid==="a"?" on":"") + '" data-act="pick-wish-uid" data-uid="a">🐶 ' + esc(u.a.name) + '</div>' +
            '<div class="opt' + (addWishUid==="b"?" on b-side":"") + '" data-act="pick-wish-uid" data-uid="b">🐾 ' + esc(u.b.name) + '</div>' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>心愿分类</label>' +
          '<div class="seg" id="wishAddCatSeg" style="flex-wrap:wrap">' +
            '<div class="opt on" data-act="pick-wish-cat" data-v="travel">✈️ 旅行打卡</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="food">🍜 美食探索</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="movie">🎬 影音娱乐</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="life">🏡 日常浪漫</div>' +
            '<div class="opt" data-act="pick-wish-cat" data-v="other">💡 奇思妙想</div>' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>心愿内容 💖</label>' +
          '<input id="wf_title" required maxlength="80" placeholder="如: 一起去海边看日出 / 去吃那家火锅">' +
        '</div>' +
        '<div class="field"><label>期盼程度</label>' +
          '<div class="seg" id="wishPrioSeg">' +
            '<div class="opt on" data-act="pick-wish-prio" data-v="0">✨ 普通心愿</div>' +
            '<div class="opt" data-act="pick-wish-prio" data-v="1">🔥 超级想做 (置顶标红)</div>' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>详细备注 / 攻略地点 (选填)</label>' +
          '<textarea id="wf_note" rows="2" maxlength="300" placeholder="如: 计划在初夏去、大众点评店铺名、注意事项等"></textarea>' +
        '</div>' +
        '<div class="mfoot" style="margin-top:14px;display:flex;gap:8px;justify-content:flex-end">' +
          '<button type="button" class="p-btn" data-act="close">取消</button>' +
          '<button class="p-btn pri">💖 贴进心愿本 🐾</button>' +
        '</div>' +
      '</form>' +
    '</div></div>' +

    '<div class="overlay" id="ovPhotoViewer" style="background:rgba(0,0,0,0.92);z-index:9999;display:none;align-items:center;justify-content:center;touch-action:none">' +
      '<div style="position:relative;width:100%;height:100%;display:flex;align-items:center;justify-content:center;overflow:hidden" id="pvContainer">' +
        '<img id="pvImage" src="" style="max-width:94vw;max-height:86vh;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.6);object-fit:contain;transition:transform .2s ease, opacity .2s ease;user-select:none;-webkit-user-select:none">' +
        '<video id="pvVideo" playsinline webkit-playsinline preload="metadata" style="position:absolute;max-width:94vw;max-height:86vh;border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.6);object-fit:contain;display:none;user-select:none;-webkit-user-select:none"></video>' +
        '<div id="pvIndicator" style="position:absolute;top:20px;left:50%;transform:translateX(-50%);color:#fff;background:rgba(0,0,0,0.5);padding:4px 12px;border-radius:20px;font-size:13px;font-weight:700">1 / 1</div>' +
        '<button type="button" data-act="pv-play-live" id="pvLiveBtn" style="position:absolute;top:20px;left:18px;display:none;align-items:center;gap:5px;background:rgba(255,255,255,0.22);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,0.4);color:#fff;padding:5px 12px;border-radius:999px;font-size:12px;font-weight:800;cursor:pointer;box-shadow:0 2px 10px rgba(0,0,0,0.35)"><span class="polaroid-live-dot"></span><span>LIVE 实况</span></button>' +
        '<button type="button" data-act="pv-save-photo" id="pvSaveBtn" style="position:absolute;bottom:calc(28px + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);display:inline-flex;align-items:center;gap:5px;background:rgba(255,255,255,0.22);backdrop-filter:blur(8px);border:1px solid rgba(255,255,255,0.38);color:#fff;padding:7px 16px;border-radius:999px;font-size:12.5px;font-weight:800;cursor:pointer;box-shadow:0 4px 14px rgba(0,0,0,0.4)">💾 保存图片</button>' +
        '<button type="button" data-act="pv-prev" id="pvBtnPrev" style="position:absolute;left:12px;top:50%;transform:translateY(-50%);background:rgba(255,255,255,0.25);border:none;border-radius:50%;width:42px;height:42px;color:#fff;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px)">‹</button>' +
        '<button type="button" data-act="pv-next" id="pvBtnNext" style="position:absolute;right:12px;top:50%;transform:translateY(-50%);background:rgba(255,255,255,0.25);border:none;border-radius:50%;width:42px;height:42px;color:#fff;font-size:20px;cursor:pointer;display:flex;align-items:center;justify-content:center;backdrop-filter:blur(4px)">›</button>' +
        '<button type="button" data-act="close-photo" class="pv-close" style="position:absolute;top:calc(18px + env(safe-area-inset-top));right:18px;background:rgba(239,68,68,0.9);color:#fff;border:none;border-radius:50%;width:36px;height:36px;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;box-shadow:0 2px 10px rgba(0,0,0,0.5)">✕</button>' +
      '</div>' +
    '</div>' +

    '<div class="overlay" id="ovAddDiary"><div class="modal">' +
      '<div class="mhead">' +
        '<div class="mhead-left"><h3 id="df_modal_title">记下今天去哪玩啦 🐾</h3></div>' +
        '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
      '</div>' +
      '<form id="addDiaryForm">' +
        '<input type="hidden" id="df_edit_id" value="">' +
        '<div class="field"><label>游玩日期</label><input type="date" id="df_date" required></div>' +
        '<div class="field"><label>游玩主题 / 事项</label><input id="df_title" required maxlength="80" placeholder="如：迪士尼一日游 🎡 / 武康路散步吃冰淇淋 🍦"></div>' +
        '<div class="row2">' +
          '<div class="field"><label>地点</label><input id="df_loc" maxlength="100" placeholder="如：上海迪士尼 / 外滩"></div>' +
          '<div class="field"><label>心情小贴纸</label>' +
            '<div style="display:flex;gap:4px">' +
              '<select id="df_mood" style="flex:1">' +
                '<option value="🥰 幸福贴贴">🥰 幸福贴贴</option>' +
                '<option value="🥳 快乐狂欢">🥳 快乐狂欢</option>' +
                '<option value="😋 撑成小猪">😋 撑成小猪</option>' +
                '<option value="🥱 累并快乐">🥱 累并快乐</option>' +
                '<option value="✨ 仪式感满满">✨ 仪式感满满</option>' +
                '<option value="💖 浪漫约会">💖 浪漫约会</option>' +
                '<option value="🍰 甜品治愈">🍰 甜品治愈</option>' +
                '<option value="🏕️ 户外露营">🏕️ 户外露营</option>' +
                '<option value="__custom__">✏️ 自定义心情...</option>' +
              '</select>' +
            '</div>' +
            '<input id="df_mood_custom" maxlength="30" placeholder="输入自定义心情（如：🐱 吸猫满足）" style="display:none;margin-top:4px">' +
          '</div>' +
        '</div>' +
        '<div class="field"><label>手账碎碎念 / 美好回忆</label><textarea id="df_content" rows="3" maxlength="4000" placeholder="今天遇到了什么好玩的事，拍了什么照片..." style="width:100%;border:1.5px solid var(--line-strong);border-radius:12px;padding:8px;font-family:inherit"></textarea></div>' +
        '<div class="field"><label>拍立得照片 & 实况 Live（最多 18 张，支持实况照片/短视频自动合成 📷）</label>' +
          '<div style="margin:4px 0 6px;display:flex;align-items:center;flex-wrap:wrap;gap:6px">' +
            '<button type="button" class="p-btn photo-add" data-act="trigger-upload-photo" style="display:inline-flex;align-items:center;gap:4px;padding:5px 12px;font-size:12px">' +
              '📷 选照片 / 实况动态' +
            '</button>' +
            '<span id="df_photo_status" style="font-size:11px;color:var(--ink-muted)">支持直接选视频或同名照片+视频合成 LIVE</span>' +
          '</div>' +
          '<input type="file" id="df_files" accept="image/*,video/*" multiple style="display:none">' +
          '<div id="df_preview" style="display:flex;flex-wrap:wrap;gap:8px;margin-top:8px"></div>' +
        '</div>' +
        '<div class="foot">' +
          '<button type="button" class="p-btn" data-act="close">取消</button>' +
          '<button class="p-btn pri" id="df_submit_btn">✨ 贴在手账本上！</button>' +
        '</div>' +
      '</form>' +
    '</div></div>';
}

/* ---------------- 日程详情与情侣留言 ---------------- */
var curDetail = null;
var pvPhotosList = [];
var pvCurrentIndex = 0;
var pvScale = 1, pvTx = 0, pvTy = 0;

function resetPvTransform(smooth){
  pvScale = 1; pvTx = 0; pvTy = 0;
  applyPvTransform(smooth);
}

function applyPvTransform(smooth){
  var img = $("#pvImage");
  var vid = $("#pvVideo");
  var tr = smooth ? "transform .2s cubic-bezier(0.2, 0.8, 0.2, 1), opacity .2s ease" : "none";
  var tf = "translate3d(" + pvTx + "px," + pvTy + "px,0) scale(" + pvScale + ")";
  if (img){ img.style.transition = tr; img.style.transform = tf; }
  if (vid){ vid.style.transition = tr; vid.style.transform = tf; }
}

function stopPvLiveVideo(){
  var vid = $("#pvVideo");
  var img = $("#pvImage");
  if (vid){
    try { vid.pause(); } catch(e){}
    vid.style.opacity = "0";
    vid.style.display = "none";
  }
  if (img) img.style.opacity = "1";
}

function playPvLiveVideo(){
  var item = pvPhotosList[pvCurrentIndex];
  if (!item || typeof item !== "object" || !item.live) return;
  var vid = $("#pvVideo");
  var img = $("#pvImage");
  if (!vid) return;
  vid.muted = true; // 移动端 WebView 默认静音启动以确保 100% 自动播放不被拦截
  vid.playsInline = true;
  vid.setAttribute("muted", "");
  vid.setAttribute("playsinline", "");
  vid.setAttribute("webkit-playsinline", "");
  vid.style.opacity = "0";
  vid.style.display = "block";
  vid.style.zIndex = "3";
  vid.style.pointerEvents = "none";

  vid.onplaying = function(){
    vid.style.opacity = "1";
  };
  vid.onended = function(){
    vid.style.opacity = "0";
    setTimeout(function(){ vid.style.display = "none"; }, 180);
  };
  vid.onerror = function(){
    vid.style.opacity = "0";
    vid.style.display = "none";
  };

  if (vid.getAttribute("data-src") !== item.live){
    vid.src = item.live;
    vid.setAttribute("data-src", item.live);
    try { vid.load(); } catch(e){}
  } else {
    try { vid.currentTime = 0; } catch(e){}
  }

  var p = vid.play();
  if (p && p.catch){
    p.catch(function(){
      setTimeout(function(){
        try { vid.muted = true; vid.play(); } catch(e){}
      }, 150);
    });
  }
}

function savePhotoUrlToDevice(imgUrl){
  if (!imgUrl) return;
  var lower = String(imgUrl).toLowerCase();
  var isVideo = lower.indexOf(".mp4") !== -1 || lower.indexOf(".mov") !== -1 || lower.indexOf(".webm") !== -1 || lower.indexOf("/live_") !== -1;
  var ext = isVideo ? ".mp4" : ".jpg";

  // 如果是图片且在 Android 客户端内，且当前客户端支持或保存的是静态图：
  if (!isVideo && window.AndroidApp && window.AndroidApp.saveImageToGallery){
    window.AndroidApp.saveImageToGallery(imgUrl);
    return;
  }
  // 如果 Android 客户端已升级支持原图/视频双模式保存
  if (isVideo && window.AndroidApp && window.AndroidApp.saveMediaToGallery){
    window.AndroidApp.saveMediaToGallery(imgUrl);
    return;
  }

  toast(isVideo ? "正在准备下载实况视频… 🐾" : "正在准备下载图片… 🐾");
  fetch(imgUrl, {credentials:"same-origin"}).then(function(r){ return r.blob(); }).then(function(blob){
    var bUrl = URL.createObjectURL(blob);
    var a = document.createElement("a");
    a.href = bUrl;
    a.download = "DogSchedule_" + Date.now() + ext;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(function(){ URL.revokeObjectURL(bUrl); }, 3000);
    toast(isVideo ? "✅ 实况视频已开始下载 🐾" : "✅ 已触发保存图片 🐾");
  }).catch(function(){
    var a = document.createElement("a");
    a.href = imgUrl;
    a.download = "DogSchedule_" + Date.now() + ext;
    a.target = "_blank";
    document.body.appendChild(a);
    a.click();
    a.remove();
  });
}

function showPhotoActionSheet(imgUrl, liveUrl){
  var old = $("#ovPhotoSheet");
  if (old) old.remove();
  var sheet = document.createElement("div");
  sheet.id = "ovPhotoSheet";
  sheet.style.cssText = "position:fixed;inset:0;background:rgba(0,0,0,0.55);z-index:10050;display:flex;align-items:flex-end;justify-content:center;padding:16px;padding-bottom:calc(16px + env(safe-area-inset-bottom));animation:fadeIn .15s ease";
  var saveBtnLabel = liveUrl ? "📸 保存实况照片到相册" : "💾 保存图片到手机相册";
  sheet.innerHTML =
    '<div style="width:100%;max-width:400px;background:var(--bg-card);border-radius:20px;padding:14px;box-shadow:0 10px 30px rgba(0,0,0,0.35);display:flex;flex-direction:column;gap:8px">' +
      '<div style="text-align:center;font-size:12.5px;font-weight:800;color:var(--ink-muted);padding:4px 0">🐾 手账照片操作</div>' +
      '<button type="button" class="p-btn pri" id="actSaveImgBtn" style="width:100%;justify-content:center;padding:11px;font-size:14px">' + saveBtnLabel + '</button>' +
      (liveUrl ? '<button type="button" class="p-btn" id="actSaveLiveBtn" style="width:100%;justify-content:center;padding:10px;font-size:13.5px">🎬 仅导出 MP4 视频</button>' : '') +
      '<button type="button" class="p-btn" id="actCancelSheetBtn" style="width:100%;justify-content:center;padding:10px;font-size:13.5px">取消</button>' +
    '</div>';
  document.body.appendChild(sheet);
  sheet.onclick = function(ev){
    if (ev.target === sheet) sheet.remove();
  };
  var bSave = sheet.querySelector("#actSaveImgBtn");
  if (bSave) bSave.onclick = function(){
    sheet.remove();
    savePhotoUrlToDevice(imgUrl);
  };
  var bLive = sheet.querySelector("#actSaveLiveBtn");
  if (bLive) bLive.onclick = function(){
    sheet.remove();
    savePhotoUrlToDevice(liveUrl);
  };
  var bCancel = sheet.querySelector("#actCancelSheetBtn");
  if (bCancel) bCancel.onclick = function(){
    sheet.remove();
  };
}

function scrollToDay(date){
  /* 水平滚动到指定日期的列；today 列沿用既有的专用 id */
  var wrap = document.querySelector(".calwrap");
  var target = document.getElementById("dayhead-" + date) ||
    document.getElementById("daycol-" + date) ||
    document.getElementById("colTodayHeader");
  if (wrap && target){
    var offset = target.offsetLeft - 58; // 扣除左侧时间轴宽度
    if (offset < 0) offset = 0;
    wrap.scrollTo({
      left: Math.max(0, offset - 10),
      behavior: "smooth"
    });
  }
}

function scrollToToday(){
  scrollToDay(todayISO());
}

function updatePhotoViewer(){
  var img = $("#pvImage");
  var vid = $("#pvVideo");
  var liveBtn = $("#pvLiveBtn");
  var ind = $("#pvIndicator");
  var bPrev = $("#pvBtnPrev");
  var bNext = $("#pvBtnNext");
  if (!img) return;
  var total = pvPhotosList.length;
  if (total === 0) return;
  stopPvLiveVideo();
  var cur = pvPhotosList[pvCurrentIndex];
  var imgUrl = (typeof cur === "object" && cur !== null) ? cur.src : cur;
  var liveUrl = (typeof cur === "object" && cur !== null) ? (cur.live || "") : "";
  img.src = imgUrl;
  if (vid){
    vid.removeAttribute("src");
    vid.setAttribute("data-src", "");
  }
  if (liveBtn){
    liveBtn.style.display = liveUrl ? "inline-flex" : "none";
  }
  var saveBtn = $("#pvSaveBtn");
  if (saveBtn){
    saveBtn.textContent = liveUrl ? "📸 保存实况照片" : "💾 保存图片";
  }
  resetPvTransform(false);
  if (ind) ind.textContent = (pvCurrentIndex + 1) + " / " + total;
  if (bPrev) bPrev.style.display = total > 1 ? "flex" : "none";
  if (bNext) bNext.style.display = total > 1 ? "flex" : "none";
  if (liveUrl){
    setTimeout(function(){
      if (pvPhotosList[pvCurrentIndex] === cur) playPvLiveVideo();
    }, 180);
  }
}

var curSummaryMonth = "";
var curSummaryData = null;

function defaultSummaryMonth(){
  var d = new Date();
  var y = d.getFullYear(), m = d.getMonth() + 1;
  var lastDay = new Date(y, m, 0).getDate();
  if (d.getDate() !== lastDay){ m -= 1; if (m === 0){ m = 12; y -= 1; } }
  return y + "-" + (m < 10 ? "0" : "") + m;
}

function shiftMonth(ym, delta){
  var y = parseInt(ym.slice(0,4), 10), m = parseInt(ym.slice(5,7), 10);
  var idx = y * 12 + (m - 1) + delta;
  var ny = Math.floor(idx / 12), nm = idx % 12 + 1;
  return ny + "-" + (nm < 10 ? "0" : "") + nm;
}

function loadSummary(month, force, silent){
  clearTimeout(loadSummary._t);
  if (month !== curSummaryMonth || force) loadSummary._polls = 0;
  if (month) curSummaryMonth = month;
  var box = $("#summaryContentBox");
  var cached = (!force && loadSummary._cache && loadSummary._cache.month === curSummaryMonth) ? loadSummary._cache : null;
  if (cached && !silent){
    // 上次看过的月份缓存秒出，随后静默对账最新版
    curSummaryData = cached;
    renderSummaryBox();
    silent = true;
  } else if (box && !cached){
    box.innerHTML = '<div style="text-align:center;padding:34px 0;color:var(--ink-muted);font-weight:700">🐶 正在翻这个月的日程和手账...<br><span style="font-size:11px;font-weight:normal;opacity:0.8">（若已配置AI模型，会按这些材料写一篇回顾）</span></div>';
  }
  var q = [];
  if (curSummaryMonth) q.push("month=" + curSummaryMonth);
  if (force) q.push("refresh=1");
  var url = "/api/summary" + (q.length ? "?" + q.join("&") : "");
  api(url).then(function(res){
    loadSummary._cache = res;
    curSummaryMonth = res.month;
    curSummaryData = res;
    renderSummaryBox();
    if (res.generating){
      // AI 版本后台生成中：先看数字版，隔几秒静默换正式版
      loadSummary._polls = (loadSummary._polls || 0) + 1;
      if (loadSummary._polls <= 30){
        clearTimeout(loadSummary._t);
        loadSummary._t = setTimeout(function(){
          if (CURRENT_VIEW === "summary") loadSummary(curSummaryMonth, false, true);
        }, 4000);
      }
    } else {
      loadSummary._polls = 0;
    }
  }).catch(function(e){
    if (e.message === "unauth") return;
    if (box && !cached) box.innerHTML = '<div style="color:red;padding:20px 0">加载失败：' + esc(e.message) + '</div>';
  });
}

function renderSummaryBox(){
  var box = $("#summaryContentBox");
  if (!box || !curSummaryData) return;
  var d = curSummaryData;
  var label = $("#summaryMonthLabel");
  if (label){
    label.innerHTML = esc(d.month_label || curSummaryMonth) +
      (d.is_current ? ' <span style="font-size:10.5px;font-weight:700;color:var(--ink-muted);background:var(--dog-a-bg);border:1px solid var(--dog-a-bd);border-radius:8px;padding:1px 6px;margin-left:4px">本月进行中</span>' : '');
  }
  var prevBtn = $("#summaryPrevBtn"), nextBtn = $("#summaryNextBtn");
  if (prevBtn){
    prevBtn.disabled = !d.has_prev;
    prevBtn.style.opacity = d.has_prev ? "1" : "0.35";
  }
  if (nextBtn){
    nextBtn.disabled = !d.has_next;
    nextBtn.style.opacity = d.has_next ? "1" : "0.35";
  }

  var aiTag = d.generating
    ? '<span style="font-size:10px;background:var(--bg-card);color:var(--ink-muted);border:1px solid var(--line-strong);padding:1px 6px;border-radius:8px;margin-left:auto;font-weight:700">✍️ AI 正在写…</span>'
    : (d.is_ai ? '<span style="font-size:10px;background:var(--dog-a-bg);color:var(--ink-muted);border:1px solid var(--dog-a-bd);padding:1px 6px;border-radius:8px;margin-left:auto;font-weight:700">AI 代笔</span>' : '');
  var html = '<div style="background:var(--bg-card-subtle);border:1.5px solid var(--line-strong);border-radius:16px;padding:14px;box-shadow:var(--shadow-sm)">' +
    '<div style="font-size:15px;font-weight:800;color:var(--ink-primary);margin-bottom:10px;display:flex;align-items:center;gap:6px">' +
      '<span>' + esc(d.title) + '</span>' + aiTag +
    '</div>' +
    '<div style="color:var(--ink-secondary);font-size:13.5px;line-height:1.85;white-space:pre-wrap;margin-bottom:10px;letter-spacing:0.2px">' + esc(d.content) + '</div>';

  if (d.stats && d.stats.length > 0){
    html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-top:10px;padding-top:10px;border-top:1px dashed var(--line-strong)">' +
      d.stats.map(function(s){
        return '<div style="background:var(--bg-card);border:1px solid var(--line-strong);border-radius:10px;padding:8px;text-align:center">' +
          '<div style="font-size:11px;color:var(--ink-muted)">' + esc(s.k) + '</div>' +
          '<div style="font-size:14px;font-weight:800;color:var(--dog-a);margin-top:2px">' + esc(s.v) + '</div>' +
        '</div>';
      }).join("") +
    '</div>';
  }
  html += '</div>';
  box.innerHTML = html;
}

/* =========================================================================
   旅行清单：一次旅行一份打包清单，按类别分组，物品 + 数量 + 备注
   ========================================================================= */
var TRIPS = null, TRIP_DETAIL = null, TRIP_ID = null, TRIP_DETAILS = {};
var tripNewOpen = false, tripEditingItem = null, tripFormCat = "doc";

function tripRange(t){
  if (!t.start_date && !t.end_date) return "日期还没定";
  if (t.start_date && t.end_date) return t.start_date.slice(5) + " – " + t.end_date.slice(5);
  return (t.start_date || t.end_date).slice(5) + (t.start_date ? " 出发" : " 返回");
}

function tripPct(t){
  return t.total ? Math.round(t.packed * 100 / t.total) : 0;
}

function tripBar(total, packed, barId){
  var pct = total ? Math.round(packed * 100 / total) : 0;
  return '<div class="wish-progress-bar" style="margin-top:7px"><div class="wish-progress-fill"' + (barId ? ' id="' + barId + '"' : '') + ' style="width:' + pct + '%"></div></div>';
}

function diarySegHTML(active){
  var items = [["month", "📔 足迹手账"], ["wishes", "✨ 心愿备忘"], ["trips", "✈️ 旅行清单"], ["summary", "💌 时光胶囊"]];
  return '<div style="margin-bottom:12px"><div class="seg seg-scroll" style="margin-bottom:0">' +
    items.map(function(it){
      return '<div class="opt' + (active === it[0] ? " on" : "") + '" data-act="switch-view" data-v="' + it[0] + '">' + it[1] + '</div>';
    }).join("") + '</div></div>';
}

function renderTripsPage(){
  var html = renderTopBar() + '<div class="page-container">' + diarySegHTML("trips");
  html += TRIP_DETAIL ? tripDetailHTML() : tripListHTML();
  html += '</div>' + bottomNavHTML() + modalsHTML();
  setAppHTML(html);
  if (!TRIP_DETAIL && !TRIPS) loadTrips();
  if (!TRIP_DETAIL && TRIPS){
    // 列表有缓存时静默对账，有变化才重画
    api("/api/trips").then(function(j){
      var changed = JSON.stringify(j) !== JSON.stringify(TRIPS);
      TRIPS = j;
      if (changed && !TRIP_ID && CURRENT_VIEW === "trips") render();
    }).catch(function(){});
  }
  if (TRIP_DETAIL && !TRIP_DETAIL.loaded) loadTrip(TRIP_ID);
  else if (TRIP_ID && !TRIP_DETAIL) loadTrip(TRIP_ID);   // 从历史条目恢复详情但缓存未命中
}

function loadTrips(){
  return api("/api/trips").then(function(j){
    if (TRIP_ID) return;              // 已经点进某次旅行，别用列表覆盖详情
    var changed = !TRIPS || JSON.stringify(j) !== JSON.stringify(TRIPS);
    TRIPS = j;
    if (changed) render();
  }).catch(function(e){ if (e.message !== "unauth") toast(e.message, "err"); });
}

function loadTrip(id){
  if (!id) return;
  var want = id;
  var prev = TRIP_DETAILS[id] || null;
  return api("/api/trips/detail?id=" + want).then(function(j){
    if (TRIP_ID !== want) return;      // 返回列表或切到别的旅行后，丢弃过期响应
    var changed = !prev || JSON.stringify(j) !== JSON.stringify(prev);
    TRIP_DETAIL = j;
    TRIP_DETAILS[id] = j;
    TRIP_DETAIL.loaded = true;
    if (changed) render();
  }).catch(function(e){
    if (TRIP_ID !== want) return;
    if (e.message !== "unauth") toast(e.message, "err");
    TRIP_DETAIL = null; TRIP_ID = null; TRIPS = null; render();
  });
}

function tripListHTML(){
  var html = '<div class="page-header-card">' +
    '<div>' +
      '<div class="page-header-title">✈️ 旅行清单</div>' +
      '<div class="page-header-desc">一次旅行一份清单：证件、洗漱、衣服，谁带什么写清楚</div>' +
    '</div>' +
    '<div style="width:44px;height:44px;flex-shrink:0"><img src="/img/dog-wave.png" alt="" style="width:100%;height:100%;object-fit:contain"></div>' +
  '</div>';

  html += '<div class="page-main-card">';
  html += '<div style="display:flex;align-items:center;justify-content:space-between;gap:8px">' +
    '<div style="font-size:13px;font-weight:800;color:var(--ink-primary)">' + (TRIPS ? (TRIPS.trips.length ? "共 " + TRIPS.trips.length + " 次旅行" : "还没有旅行清单") : "加载中…") + '</div>' +
    '<button class="p-btn pri" data-act="trip-new-toggle" style="font-size:12px;padding:5px 12px">' + (tripNewOpen ? "收起" : "＋ 新建旅行") + '</button>' +
  '</div>';

  if (tripNewOpen){
    html += '<div style="margin-top:12px;padding:12px;border:1.5px dashed var(--line-strong);border-radius:14px;background:var(--bg-card-subtle)">' +
      '<div style="display:grid;gap:8px">' +
        '<input id="trip_dest" placeholder="去哪儿？比如 青岛" maxlength="40" style="width:100%;padding:9px 11px;border:1.5px solid var(--line-strong);border-radius:12px;background:var(--bg-card);color:var(--ink-primary)">' +
        '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px">' +
          '<input id="trip_start" type="date" style="width:100%;padding:9px 11px;border:1.5px solid var(--line-strong);border-radius:12px;background:var(--bg-card);color:var(--ink-primary)">' +
          '<input id="trip_end" type="date" style="width:100%;padding:9px 11px;border:1.5px solid var(--line-strong);border-radius:12px;background:var(--bg-card);color:var(--ink-primary)">' +
        '</div>' +
        '<label style="display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-secondary)">' +
          '<input id="trip_tpl" type="checkbox" checked style="width:15px;height:15px"> 套用常用清单（证件/洗漱/护肤/衣物/电子/药品）' +
        '</label>' +
        '<div style="display:flex;gap:8px"><button class="p-btn pri" data-act="trip-create" style="flex:1;font-size:12.5px">建清单</button></div>' +
      '</div>' +
    '</div>';
  }
  html += '</div>';

  if (TRIPS && TRIPS.trips.length){
    html += '<div style="margin-top:12px">' + TRIPS.trips.map(function(t){
      var badge = t.status === "ongoing"
        ? '<span style="font-size:10.5px;font-weight:800;color:var(--dog-a-text);background:var(--dog-a-bg);border:1px solid var(--dog-a-bd);border-radius:8px;padding:1px 7px;flex-shrink:0">进行中</span>'
        : (t.status === "upcoming"
          ? '<span style="font-size:10.5px;font-weight:700;color:var(--ink-muted);background:var(--bg-card);border:1px solid var(--line-strong);border-radius:8px;padding:1px 7px;flex-shrink:0">待出发</span>'
          : '<span style="font-size:10.5px;font-weight:700;color:var(--ink-muted);background:var(--bg-card);border:1px solid var(--line-strong);border-radius:8px;padding:1px 7px;flex-shrink:0">已结束</span>');
      return '<div class="page-main-card" data-act="trip-open" data-id="' + t.id + '" style="cursor:pointer;margin-bottom:10px;' + (t.status === "done" ? "opacity:.78;" : "") + '">' +
        '<div style="display:flex;justify-content:space-between;align-items:flex-start;gap:8px">' +
          '<div style="min-width:0">' +
            '<div style="font-size:15px;font-weight:800;color:var(--ink-primary);overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(t.title || t.destination) + '</div>' +
            '<div style="font-size:11.5px;color:var(--ink-muted);margin-top:3px">' + esc(tripRange(t)) + ' · 已勾 ' + t.packed + '/' + t.total + '</div>' +
          '</div>' + badge +
        '</div>' + tripBar(t.total, t.packed) +
      '</div>';
    }).join("") + '</div>';
  } else if (TRIPS){
    html += '<div class="page-main-card" style="text-align:center;color:var(--ink-muted);font-size:12.5px;line-height:1.9;padding:26px 14px">' +
      '还没有旅行清单哦<br><span style="font-size:11.5px">建一个，出发前一晚就不会互相问「充电器你带了吗」</span></div>';
  }

  return html;
}

function tripUserNames(){
  var out = {a: "小金毛", b: "小白狗"};
  ((META && META.users) || []).forEach(function(u){
    if (u && (u.uid === "a" || u.uid === "b") && u.name) out[u.uid] = u.name;
  });
  return out;
}

function tripDetailHTML(){
  var d = TRIP_DETAIL, t = d.trip, items = d.items || [];
  var cats = d.categories || [];
  var html = '<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px">' +
    '<button class="p-btn" data-act="trip-back" style="font-size:12px;padding:5px 10px">‹ 全部旅行</button>' +
    '<div style="flex:1"></div>' +
    '<button class="p-btn" data-act="trip-delete" data-id="' + t.id + '" style="font-size:11.5px;padding:5px 9px;color:var(--ink-muted)">删除</button>' +
  '</div>';

  html += '<div class="page-header-card">' +
    '<div style="min-width:0">' +
      '<div class="page-header-title">✈️ ' + esc(t.title || t.destination) + '</div>' +
      '<div class="page-header-desc">' + esc(tripRange(t)) + ' · 已勾 <span data-role="trip-packed">' + d.stats.packed + '</span>/<span data-role="trip-total">' + d.stats.total + '</span></div>' +
    '</div>' +
  '</div>';

  html += '<div class="page-main-card">' + tripBar(d.stats.total, d.stats.packed, "tripBarFill") +
    '<div style="display:flex;justify-content:space-between;margin-top:6px;font-size:11px;color:var(--ink-muted)">' +
      '<span>' + (t.creator_name ? '由 ' + esc(t.creator_name) + ' 建' : '') + '</span>' +
      '<span data-role="trip-remaining">' + (d.stats.total - d.stats.packed) + ' 样还没勾</span>' +
    '</div></div>';

  // 编辑时就地把表单放在顶部，改完立刻能看到，不用滚到页面底部
  if (tripEditingItem) html += tripItemFormHTML();

  cats.forEach(function(c){
    var list = items.filter(function(i){ return i.category === c[0]; });
    if (!list.length) return;
    var packed = list.filter(function(i){ return i.is_packed; }).length;
    html += '<div class="page-main-card" style="margin-top:10px">' +
      '<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:2px">' +
        '<div style="font-size:13px;font-weight:800;color:var(--ink-primary)">' + esc(c[1]) + '</div>' +
        '<div style="font-size:11px;color:var(--ink-muted)" data-cat-count="' + c[0] + '">' + packed + '/' + list.length + '</div>' +
      '</div>' +
      '<div style="display:grid;grid-template-columns:22px minmax(52px,1fr) 42px 42px 28px;gap:5px;font-size:9.5px;color:var(--ink-muted);padding:2px 0 4px;border-bottom:1px solid var(--line-strong)">' +
        '<span></span><span>物品</span><span style="text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(tripUserNames().a) + '</span><span style="text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(tripUserNames().b) + '</span><span></span>' +
      '</div>' +
      list.map(function(i){
        var cell = function(v, cls){
          var none = !String(v || "").trim();
          return '<div class="' + cls + '" style="font-size:11.5px;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;' +
            (none ? 'color:var(--ink-muted);opacity:.45;' : (i.is_packed ? 'color:var(--ink-muted);' : 'color:var(--ink-secondary);')) +
            '" title="' + esc(none ? '不用带' : v) + '">' + esc(none ? '—' : v) + '</div>';
        };
        return '<div class="trip-row" data-row="' + i.id + '" style="display:grid;grid-template-columns:22px minmax(52px,1fr) 42px 42px 28px;align-items:center;gap:5px;padding:7px 0;border-bottom:1px dashed var(--line-strong)">' +
          '<button class="p-btn trip-check" data-act="trip-toggle" data-id="' + i.id + '" style="width:22px;height:22px;min-width:22px;padding:0;border-radius:7px;font-size:11px;transition:transform .12s ease,background-color .12s ease,border-color .12s ease;' + (i.is_packed ? 'background:var(--dog-a-bg);border-color:var(--dog-a-bd);color:var(--dog-a-text)' : '') + '">' + (i.is_packed ? '✓' : '') + '</button>' +
          '<div class="trip-name" style="font-size:13px;' + (i.is_packed ? 'font-weight:500;text-decoration:line-through;color:var(--ink-muted);' : 'font-weight:700;color:var(--ink-primary);') + 'overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="' + esc(i.name) + '">' + esc(i.name) + '</div>' +
          cell(i.qty_a, "trip-qa") +
          cell(i.qty_b, "trip-qb") +
          '<button class="p-btn" data-act="trip-item-edit" data-id="' + i.id + '" style="padding:4px 5px;min-width:28px;font-size:11px;color:var(--ink-muted)">改</button>' +
        '</div>';
      }).join("") +
      '<div style="padding-top:8px"><button class="p-btn" data-act="trip-item-new" data-cat="' + c[0] + '" style="font-size:11.5px;padding:4px 10px">＋ 加一件' + esc(c[1]) + '</button></div>' +
    '</div>';
  });

  if (!items.length){
    html += '<div class="page-main-card" style="text-align:center;color:var(--ink-muted);font-size:12.5px;padding:22px 12px">这份清单还是空的，下面加几样吧</div>';
  }

  if (!tripEditingItem) html += tripItemFormHTML();
  html += '<div style="margin-top:10px"><button class="p-btn" data-act="trip-copy-last" data-id="' + t.id + '" style="width:100%;font-size:12px;padding:8px">⬇️ 把上一份清单的物品复制过来</button></div>';
  return html;
}

function tripItemFormHTML(){
  var ed = tripEditingItem;
  var cats = (TRIP_DETAIL && TRIP_DETAIL.categories) || [];
  var cat = ed ? ed.category : (tripFormCat || "doc");
  var nm = tripUserNames();
  var meIsB = ME && ME.uid === "b";
  var defaultA = meIsB ? "" : "1";
  var defaultB = meIsB ? "1" : "";
  var qa = ed ? (ed.qty_a || "") : defaultA;
  var qb = ed ? (ed.qty_b || "") : defaultB;
  var inputStyle = 'width:100%;padding:8px 10px;border:1.5px solid var(--line-strong);border-radius:11px;background:var(--bg-card);color:var(--ink-primary)';
  return '<div class="page-main-card" id="tripItemForm" style="margin-top:10px' + (ed ? ';border:2px solid var(--dog-a-bd);box-shadow:0 6px 18px rgba(0,0,0,.08)' : '') + '">' +
    '<div style="font-size:12.5px;font-weight:800;color:var(--ink-primary);margin-bottom:9px">' + (ed ? "改这一项" : "加一样东西") + '</div>' +
    '<div style="display:grid;gap:7px">' +
      '<input id="ti_name" placeholder="物品名称，比如 充电器" maxlength="40" value="' + esc(ed ? ed.name : "") + '" style="' + inputStyle + '">' +
      '<select id="ti_cat" style="width:100%;padding:8px 10px;border:1.5px solid var(--line-strong);border-radius:11px;background:var(--bg-card);color:var(--ink-primary)">' +
        cats.map(function(c){ return '<option value="' + c[0] + '"' + (c[0] === cat ? " selected" : "") + '>' + esc(c[1]) + '</option>'; }).join("") +
      '</select>' +
      '<div style="display:grid;grid-template-columns:1fr 1fr;gap:7px">' +
        '<div><div style="font-size:10px;color:var(--ink-muted);margin-bottom:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(nm.a) + ' 带</div><input id="ti_qa" maxlength="10" value="' + esc(qa) + '" placeholder="1" style="' + inputStyle + '"></div>' +
        '<div><div style="font-size:10px;color:var(--ink-muted);margin-bottom:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">' + esc(nm.b) + ' 带</div><input id="ti_qb" maxlength="10" value="' + esc(qb) + '" placeholder="1" style="' + inputStyle + '"></div>' +
      '</div>' +
      '<div style="font-size:10.5px;color:var(--ink-muted);line-height:1.6">留空 = 这一方不用带；也可以写「没有」「用你的」「刷我的卡」</div>' +
      '<div style="display:flex;gap:7px">' +
        '<button class="p-btn pri" data-act="trip-item-save" style="flex:1;font-size:12px;padding:6px">' + (ed ? "保存" : "加进清单") + '</button>' +
        (ed ? '<button class="p-btn" data-act="trip-item-del" data-id="' + ed.id + '" style="font-size:12px;padding:6px 10px;color:#c0564f">删除</button>' : '') +
        (ed ? '<button class="p-btn" data-act="trip-item-cancel" style="font-size:12px;padding:6px 10px">取消</button>' : '') +
      '</div>' +
    '</div>' +
  '</div>';
}

function tripFormValue(id){
  var el = document.getElementById(id);
  return el ? String(el.value || "").trim() : "";
}

function tripScrollToItem(name){
  if (!name) return;
  var rows = document.querySelectorAll('div[style*="grid-template-columns:22px"]');
  for (var i = 0; i < rows.length; i++){
    if (rows[i].textContent.indexOf(name) === 0){
      rows[i].scrollIntoView({behavior: "smooth", block: "center"});
      return;
    }
  }
}

function tripAction(action, body){
  body.action = action;
  return api("/api/trips", {method:"POST", body: body});
}

/* 勾选走乐观更新：先改界面，再后台提交；失败回滚。不整页重绘，点起来不卡。 */
function tripSetRowPacked(id){
  var item = ((TRIP_DETAIL && TRIP_DETAIL.items) || []).filter(function(x){ return x.id === id; })[0];
  var row = document.querySelector('[data-row="' + id + '"]');
  if (!item || !row) return;
  var on = !!item.is_packed;
  var btn = row.querySelector('.trip-check');
  if (btn){
    btn.textContent = on ? "✓" : "";
    btn.style.background = on ? "var(--dog-a-bg)" : "";
    btn.style.borderColor = on ? "var(--dog-a-bd)" : "";
    btn.style.color = on ? "var(--dog-a-text)" : "";
  }
  var name = row.querySelector('.trip-name');
  if (name){
    name.style.textDecoration = on ? "line-through" : "none";
    name.style.color = on ? "var(--ink-muted)" : "var(--ink-primary)";
    name.style.fontWeight = on ? "500" : "700";
  }
  [row.querySelector('.trip-qa'), row.querySelector('.trip-qb')].forEach(function(c){
    if (!c) return;
    if (c.textContent.trim() === "—"){ c.style.color = "var(--ink-muted)"; c.style.opacity = ".45"; return; }
    c.style.opacity = "1";
    c.style.color = on ? "var(--ink-muted)" : "var(--ink-secondary)";
  });
}

function tripRefreshProgress(){
  var d = TRIP_DETAIL;
  if (!d) return;
  var items = d.items || [], packed = 0, cats = {};
  items.forEach(function(i){
    var c = i.category;
    cats[c] = cats[c] || {p: 0, t: 0};
    cats[c].t++;
    if (i.is_packed){ packed++; cats[c].p++; }
  });
  d.stats.packed = packed;
  d.stats.total = items.length;
  var put = function(sel, txt){ var el = document.querySelector(sel); if (el) el.textContent = txt; };
  put('[data-role="trip-packed"]', packed);
  put('[data-role="trip-total"]', items.length);
  put('[data-role="trip-remaining"]', (items.length - packed) + " 样还没勾");
  var fill = document.getElementById("tripBarFill");
  if (fill) fill.style.width = (items.length ? Math.round(packed * 100 / items.length) : 0) + "%";
  Object.keys(cats).forEach(function(c){
    put('[data-cat-count="' + c + '"]', cats[c].p + "/" + cats[c].t);
  });
}

function renderAnnivList(){
  var box = $("#annivListContainer");
  if (!box) return;
  var list = (DATA && DATA.anniversaries) || [];
  if (list.length === 0){
    box.innerHTML = '<div style="font-size:12px;color:var(--ink-muted);text-align:center;padding:12px 0">' +
      '还没有添加纪念日哦，在下方记一个吧 🐾</div>';
    return;
  }
  var todayStr = todayISO();
  box.innerHTML = list.map(function(item){
    var c = calcAnniv(item, todayStr);
    return '<div style="display:flex;align-items:center;justify-content:space-between;padding:6px 10px;background:var(--dog-a-bg);border:1px solid var(--dog-a-bd);color:var(--dog-a-text);border-radius:12px;margin-bottom:6px;font-size:12px">' +
      '<div><span style="font-size:14px;margin-right:4px">' + c.icon + '</span><strong>' + c.text + '</strong>' +
      '<span style="color:var(--ink-muted);font-size:11px;margin-left:6px">(' + esc(item.date) + ')</span></div>' +
      '<button class="p-btn danger" style="padding:2px 8px;font-size:11px" data-act="del-anniv" data-id="' + item.id + '">删除</button>' +
    '</div>';
  }).join("");
}

var DIARY_DAY_CACHE = {};
function diaryDayHTML(dtStr, list){
  var html = '<div class="mhead">' +
      '<div class="mhead-left"><h3>' + fmtDateCN(dtStr) + ' 俩汪足迹 🐾</h3></div>' +
      '<img src="/img/dog-wave.png" alt="" class="mhead-img">' +
    '</div>';

    if (list.length === 0){
      html += '<div style="text-align:center;padding:24px 0;color:var(--ink-muted);font-size:13px">' +
        '这一天还没有记录足迹哦~<br>' +
        '<button class="p-btn pri" style="margin-top:12px" data-act="open-add-diary" data-date="' + dtStr + '">🐾 记下今天的快乐！</button>' +
      '</div>';
    } else {
      list.forEach(function(d){
        var photosHtml = "";
        if (d.photos && d.photos.length > 0){
          var pListJson = encodeURIComponent(JSON.stringify(d.photos.map(function(p){
            return { src: "/photos/" + p.file_name, live: p.live_video ? ("/photos/" + p.live_video) : "" };
          })));
          photosHtml = '<div class="polaroid-gallery">' +
            d.photos.map(function(p, pIdx){
              var liveBadge = p.live_video ? '<span class="polaroid-live-badge"><span class="polaroid-live-dot"></span>LIVE</span>' : '';
              return '<div class="polaroid-card">' +
                liveBadge +
                '<img src="/photos/tb_' + p.file_name + '" class="polaroid-img" alt="" data-act="preview-photo" data-idx="' + pIdx + '" data-list="' + pListJson + '">' +
              '</div>';
            }).join("") +
          '</div>';
        }

        var dJson = encodeURIComponent(JSON.stringify(d));
        html += '<div class="diary-item-card">' +
          '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:8px;margin-bottom:6px">' +
            '<div style="min-width:0;flex:1">' +
              '<div style="font-size:15px;font-weight:800;color:var(--ink-primary);line-height:1.35;word-break:break-word">' + esc(d.title) + '</div>' +
              (d.mood ? ('<div style="margin-top:5px"><span style="display:inline-flex;align-items:center;font-size:11.5px;font-weight:700;background:var(--dog-a-bg);color:var(--dog-a-text);border:1px solid var(--dog-a-bd);border-radius:10px;padding:2px 8px">' + esc(d.mood) + '</span></div>') : '') +
            '</div>' +
            '<div style="display:flex;gap:4px;flex-shrink:0">' +
              '<button class="p-btn" style="padding:2px 8px;font-size:11px" data-act="edit-diary" data-diary="' + dJson + '">编辑</button>' +
              '<button class="p-btn danger" style="padding:2px 8px;font-size:11px" data-act="del-diary" data-id="' + d.id + '">删除</button>' +
            '</div>' +
          '</div>' +
          (d.location ? ('<div style="font-size:12px;color:var(--dog-a-text);font-weight:700;margin-bottom:6px;word-break:break-word;overflow-wrap:anywhere">📍 ' + esc(d.location) + '</div>') : '') +
          (d.content ? ('<div style="font-size:13px;line-height:1.5;color:var(--ink-secondary);white-space:pre-wrap;word-break:break-word;overflow-wrap:anywhere;margin-bottom:8px">' + esc(d.content) + '</div>') : '') +
          photosHtml +
          '<div style="font-size:11px;color:var(--ink-muted);text-align:right">由 ' + esc(d.author_name) + ' 记录于 ' + esc(fmtRecordTime(d.created_at, dtStr)) + '</div>' +
        '</div>';
      });

      html += '<div style="text-align:center;margin-top:12px">' +
        '<button class="p-btn pri" data-act="open-add-diary" data-date="' + dtStr + '">＋ 追加一篇手账</button>' +
      '</div>';
    }

  html += '<div class="foot" style="margin-top:14px"><button type="button" class="p-btn" data-act="close">关闭</button></div>';
  return html;
}
function resetDiaryForm(){
  if ($("#df_edit_id")) $("#df_edit_id").value = "";
  if ($("#df_modal_title")) $("#df_modal_title").textContent = "记下今天去哪玩啦 🐾";
  if ($("#df_submit_btn")) $("#df_submit_btn").textContent = "✨ 贴在手账本上！";
  if ($("#df_date")) $("#df_date").value = todayISO();
  if ($("#df_title")) $("#df_title").value = "";
  if ($("#df_loc")) $("#df_loc").value = "";
  if ($("#df_mood")) $("#df_mood").value = "🥰 幸福贴贴";
  if ($("#df_mood_custom")){ $("#df_mood_custom").value = ""; $("#df_mood_custom").style.display = "none"; }
  if ($("#df_content")) $("#df_content").value = "";
  if ($("#df_files")) $("#df_files").value = "";
  diaryPhotosToUpload = [];
  var prev = $("#df_preview");
  if (prev) prev.innerHTML = "";
}
function paintDiaryDay(dtStr, list){
  $("#ovDiaryDayBox").innerHTML = diaryDayHTML(dtStr, list);
  var ov = $("#ovDiaryDay");
  /* 刷新已打开的当日弹窗（删除/保存后对账）不再重复压历史 */
  if (ov && ov.classList.contains("show")) return;
  openModal("#ovDiaryDay");
}
function openDayDiaries(dtStr, force){
  activeDiaryDate = dtStr;
  var cached = DIARY_DAY_CACHE[dtStr];
  if (cached && !force){
    // 缓存秒开，后台静默对账；弹窗仍开着且数据有变化才重画
    paintDiaryDay(dtStr, cached);
    api("/api/diaries?date=" + dtStr).then(function(res){
      var list = res.diaries || [];
      var changed = JSON.stringify(list) !== JSON.stringify(cached);
      DIARY_DAY_CACHE[dtStr] = list;
      if (changed && activeDiaryDate === dtStr && $("#ovDiaryDay").classList.contains("show")){
        $("#ovDiaryDayBox").innerHTML = diaryDayHTML(dtStr, list);
      }
    }).catch(function(){});
    return;
  }
  api("/api/diaries?date=" + dtStr).then(function(res){
    var list = res.diaries || [];
    DIARY_DAY_CACHE[dtStr] = list;
    paintDiaryDay(dtStr, list);
  }).catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); });
}

var EVENT_DETAIL_CACHE = {};
function openDetail(id, date, force){
  curDetail = {id: +id, date: date || todayISO()};
  var q = curDetail.date ? ("?date=" + curDetail.date) : "";
  var key = id + "|" + (curDetail.date || "");
  var cached = (!force) ? EVENT_DETAIL_CACHE[key] : null;
  if (cached){
    // 缓存秒开，后台静默对账；弹窗仍开着且数据有变化才重画
    $("#ovDetailBox").innerHTML = detailHTML(cached);
    openModal("#ovDetail");
    api("/api/event/" + id + q).then(function(j){
      var changed = JSON.stringify(j) !== JSON.stringify(cached);
      EVENT_DETAIL_CACHE[key] = j;
      if (changed && curDetail && curDetail.id === +id && $("#ovDetail").classList.contains("show")){
        $("#ovDetailBox").innerHTML = detailHTML(j);
      }
    }).catch(function(){});
    return;
  }
  api("/api/event/" + id + q).then(function(j){
    EVENT_DETAIL_CACHE[key] = j;
    $("#ovDetailBox").innerHTML = detailHTML(j);
    openModal("#ovDetail");
  }).catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); });
}

function detailHTML(j){
  var ev = j.event, o = j.owner;
  var isBoth = o.uid === "both";
  var isA = o.uid === "a";
  var dateLine = "";
  if (curDetail && curDetail.date){
    var p = curDetail.date.split("-");
    var dt = new Date(+p[0], +p[1]-1, +p[2]);
    dateLine = curDetail.date.slice(5) + " " + WD[(dt.getDay() + 6) % 7];
  }
  var ownerPrefix = isBoth ? "💕 两人共同 · " : (isA ? "🐶 小金毛 · " : "🐾 小白狗 · ");
  var rows = '<div class="d-meta">' +
    '<div class="row"><span class="k">归属</span><span class="v">' +
      '<span class="school-badge ' + o.uid + '">' + ownerPrefix + esc(o.name) + '</span>' +
    '</span></div>' +
    '<div class="row"><span class="k">时间</span><span class="v">' + ev.tstart + "–" + ev.tend +
      (ev.repeat === "weekly" ? "（每周" + WD[ev.weekday] + "）" : "（单次 " + esc(ev.date) + "）") + '</span></div>' +
    (ev.location ? '<div class="row"><span class="k">地点</span><span class="v">📍 ' + esc(ev.location) + '</span></div>' : "") +
    (ev.week_spec ? '<div class="row"><span class="k">周次</span><span class="v">🗓️ ' + esc(ev.week_spec) + '周</span></div>' : "") +
    (ev.note ? '<div class="row"><span class="k">备注</span><span class="v">📝 ' + esc(ev.note) + '</span></div>' : "") +
    '</div>';

  var cmts = j.comments.length ? j.comments.map(function(c){
    var cIsA = c.uid === "a";
    return '<div class="cmt"><div class="ch">' +
      '<span class="cname ' + c.uid + '">' + (cIsA ? "🐶 " : "🐾 ") + esc(c.name || c.uid) + '</span>' +
      '<span class="ct">' + esc(c.created) + '</span>' +
      (ME && c.uid === ME.uid ? '<button class="cdel" data-act="del-cmt" data-id="' + c.id + '">删除</button>' : "") +
      '</div><div class="ctxt">' + esc(c.text) + '</div></div>';
  }).join("") : '<div class="empty-cmt">还没有情侣留言，发句甜甜的话吧~ 🐾</div>';

  var quickPhrases = [
    "🐾 收到汪！",
    "🥰 贴贴想你啦",
    "🍜 等你一起吃饭！",
    "🏃 准备出门啦",
    "❤️ 宝贝辛苦啦！",
    "✨ 准时到！"
  ].map(function(t){
    return '<span class="phrase-pill" data-act="quick-cmt" data-text="' + esc(t) + '">' + esc(t) + '</span>';
  }).join("");

  return '<div class="mhead">' +
      '<div class="mhead-left"><h3>' + esc(ev.title) + '</h3></div>' +
      '<img src="/img/dog-add.png" alt="" class="mhead-img">' +
    '</div>' +
    (dateLine ? '<div style="font-size:12px;color:var(--ink-muted);margin:-6px 0 10px;font-weight:600">当前查看 ' + dateLine + ' 的安排</div>' : "") +
    rows +
    '<div style="font-weight:800;font-size:13.5px;margin:14px 0 4px;color:var(--ink-primary);display:flex;align-items:center;gap:6px">' +
      '<span>情侣留言板 💕</span><span style="font-size:12px;color:var(--ink-light)">(' + j.comments.length + ')</span>' +
    '</div>' +
    '<div class="quick-phrases">' + quickPhrases + '</div>' +
    '<div class="cmts">' + cmts + '</div>' +
    '<div class="crow">' +
      '<input id="cmtText" maxlength="500" placeholder="和 TA 说点什么…（回车发送）">' +
      '<button class="p-btn pri" data-act="send-cmt" style="padding:8px 16px">🐾 发送</button>' +
    '</div>' +
    '<div class="foot" style="justify-content:space-between;margin-top:14px;flex-wrap:wrap;gap:8px">' +
      '<div style="display:flex;gap:6px">' +
        '<button class="p-btn pri" data-act="edit-event" data-id="' + ev.id + '">✏️ 编辑日程</button>' +
        '<button class="p-btn danger" data-act="del-event" data-id="' + ev.id + '">删除此日程</button>' +
      '</div>' +
      '<button class="p-btn" data-act="close">关闭</button>' +
    '</div>';
}

function refreshDetail(){
  if (curDetail) openDetail(curDetail.id, curDetail.date, true);
}

/* ---------------- 交互 ---------------- */
/* data-act 动作表：键为页面元素 data-act 值（原为一条 if/else 链，行为不变） */
var CLICK_ACTIONS = {

  /* ---- 登录与快捷操作 ---- */
  "pick-login": function(el, e) {
    var act = "pick-login";
    loginUid = el.getAttribute("data-uid");
    document.querySelectorAll("#loginSeg .login-card").forEach(function(o){
      var uid = o.getAttribute("data-uid");
      var isA = uid === "a";
      o.className = "login-card" + (uid === loginUid ? (" on " + (isA ? "side-a" : "side-b")) : "");
    });
  
  },
  "pick-add": function(el, e) {
    var act = "pick-add";
    addUid = el.getAttribute("data-uid");
    document.querySelectorAll("#addSeg .opt").forEach(function(o){
      var uid = o.getAttribute("data-uid");
      var side = uid === "b" ? "b-side" : (uid === "both" ? "both-side" : "");
      o.className = "opt" + (uid === addUid ? (" on" + (side ? " " + side : "")) : "");
    });
  
  },
  "rep": function(el, e) {
    var act = "rep";
   addRep = el.getAttribute("data-v");
   document.querySelectorAll("#repSeg .opt").forEach(function(o){
     o.classList.toggle("on", o.getAttribute("data-v") === addRep);
   });
   var fWd = $("#fldWd"), fDt = $("#fldDt"), fWs = $("#fldWs");
   if (fWd) fWd.style.display = addRep === "weekly" ? "" : "none";
   if (fDt) fDt.style.display = addRep === "once" ? "" : "none";
   if (fWs) fWs.style.display = addRep === "weekly" ? "" : "none";
 
  },
  "quick-cmt": function(el, e) {
    var act = "quick-cmt";
    var phrase = el.getAttribute("data-text");
    var inp = $("#cmtText");
    if (inp){
      inp.value = phrase;
      inp.focus();
    }
  
  },

  /* ---- 导航与全局 ---- */
  "prev": function(el, e) {
    var act = "prev"; ANCHOR = shiftISO(ANCHOR, -7); DATA = null; load(); 
  },
  "next": function(el, e) {
    var act = "next"; ANCHOR = shiftISO(ANCHOR, 7); DATA = null; load(); 
  },
  "open-add": function(el, e) {
    var act = "open-add";
    curEditingEventId = null;
    var addTitleEl = $("#addModalTitle");
    if (addTitleEl) addTitleEl.textContent = "添加日程 🐾";
    var addBtnEl = $("#addModalSubmitBtn");
    if (addBtnEl) addBtnEl.textContent = "🐾 记在小本本上！";
    addRep = "weekly";
    document.querySelectorAll("#repSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === "weekly");
    });
    var fWd = $("#fldWd"), fDt = $("#fldDt"), fWs = $("#fldWs");
    if (fWd) fWd.style.display = "";
    if (fDt) fDt.style.display = "none";
    if (fWs) fWs.style.display = "";
    if ($("#f_title")) $("#f_title").value = "";
    if ($("#f_loc")) $("#f_loc").value = "";
    if ($("#f_note")) $("#f_note").value = "";
    if ($("#f_ws")) $("#f_ws").value = "";
    if ($("#f_ts")) $("#f_ts").value = "19:00";
    if ($("#f_te")) $("#f_te").value = "21:00";
    /* 单次日程默认今天；今天不在当前查看的周里时，默认落到该周的周一 */
    if ($("#f_date")){
      var defDate = todayISO();
      if (DATA && DATA.week_start && (defDate < DATA.week_start || defDate > DATA.week_end)){
        defDate = DATA.week_start;
      }
      $("#f_date").value = defDate;
    }
    openModal("#ovAdd");
  
  },
  "set-theme": function(el, e) {
    var act = "set-theme";
    var theme = el.getAttribute("data-theme");
    try {
      if (theme === "system") localStorage.removeItem("sched_dark_mode");
      else localStorage.setItem("sched_dark_mode", theme === "dark" ? "1" : "0");
    } catch(e){}
    applyThemeMode();
  },

  "toggle-dark": function(el, e) {
    var act = "toggle-dark";
    var isDark = themeMode() === "dark" || (themeMode() === "system" && systemThemeMQ && systemThemeMQ.matches);
    try {
      localStorage.setItem("sched_dark_mode", isDark ? "0" : "1");
    } catch(e){}
    applyThemeMode();
  },

  "switch-view": function(el, e) {
    var act = "switch-view";
    var targetV = el.getAttribute("data-v");
    if (act === "open-settings") targetV = "settings";
    else if (act === "open-summary"){ targetV = "summary"; curSummaryMonth = ""; }
    else if (act === "open-anniv" || act === "open-anniv-list") targetV = "anniv";

    // 记录切换视图的历史记录（支持浏览器/手机返回键返回上一页）
    if (targetV !== CURRENT_VIEW){
      history.pushState({type:"view", v:targetV}, "");
    }

    // 底部导航/页签进入的都是页面顶层，旅行清单回到列表层
    if (targetV !== "trips"){ TRIP_ID = null; TRIP_DETAIL = null; }

    if (targetV === "week"){
      CURRENT_VIEW = "week";
      var curT = todayISO();
      if (ANCHOR !== curT){
        ANCHOR = curT;
        DATA = null;
      }
      load().then(function(){
        setTimeout(scrollToToday, 60);
      });
    } else {
      CURRENT_VIEW = targetV;
      load();
    }
  
  },
  "open-settings": function(el, e) {
    var act = "open-settings";
    var targetV = el.getAttribute("data-v");
    if (act === "open-settings") targetV = "settings";
    else if (act === "open-summary"){ targetV = "summary"; curSummaryMonth = ""; }
    else if (act === "open-anniv" || act === "open-anniv-list") targetV = "anniv";

    // 记录切换视图的历史记录（支持浏览器/手机返回键返回上一页）
    if (targetV !== CURRENT_VIEW){
      history.pushState({type:"view", v:targetV}, "");
    }

    // 底部导航/页签进入的都是页面顶层，旅行清单回到列表层
    if (targetV !== "trips"){ TRIP_ID = null; TRIP_DETAIL = null; }

    if (targetV === "week"){
      CURRENT_VIEW = "week";
      var curT = todayISO();
      if (ANCHOR !== curT){
        ANCHOR = curT;
        DATA = null;
      }
      load().then(function(){
        setTimeout(scrollToToday, 60);
      });
    } else {
      CURRENT_VIEW = targetV;
      load();
    }
  
  },
  "open-summary": function(el, e) {
    var act = "open-summary";
    var targetV = el.getAttribute("data-v");
    if (act === "open-settings") targetV = "settings";
    else if (act === "open-summary"){ targetV = "summary"; curSummaryMonth = ""; }
    else if (act === "open-anniv" || act === "open-anniv-list") targetV = "anniv";

    // 记录切换视图的历史记录（支持浏览器/手机返回键返回上一页）
    if (targetV !== CURRENT_VIEW){
      history.pushState({type:"view", v:targetV}, "");
    }

    // 底部导航/页签进入的都是页面顶层，旅行清单回到列表层
    if (targetV !== "trips"){ TRIP_ID = null; TRIP_DETAIL = null; }

    if (targetV === "week"){
      CURRENT_VIEW = "week";
      var curT = todayISO();
      if (ANCHOR !== curT){
        ANCHOR = curT;
        DATA = null;
      }
      load().then(function(){
        setTimeout(scrollToToday, 60);
      });
    } else {
      CURRENT_VIEW = targetV;
      load();
    }
  
  },
  "open-anniv": function(el, e) {
    var act = "open-anniv";
    var targetV = el.getAttribute("data-v");
    if (act === "open-settings") targetV = "settings";
    else if (act === "open-summary"){ targetV = "summary"; curSummaryMonth = ""; }
    else if (act === "open-anniv" || act === "open-anniv-list") targetV = "anniv";

    // 记录切换视图的历史记录（支持浏览器/手机返回键返回上一页）
    if (targetV !== CURRENT_VIEW){
      history.pushState({type:"view", v:targetV}, "");
    }

    // 底部导航/页签进入的都是页面顶层，旅行清单回到列表层
    if (targetV !== "trips"){ TRIP_ID = null; TRIP_DETAIL = null; }

    if (targetV === "week"){
      CURRENT_VIEW = "week";
      var curT = todayISO();
      if (ANCHOR !== curT){
        ANCHOR = curT;
        DATA = null;
      }
      load().then(function(){
        setTimeout(scrollToToday, 60);
      });
    } else {
      CURRENT_VIEW = targetV;
      load();
    }
  
  },
  "open-anniv-list": function(el, e) {
    var act = "open-anniv-list";
    var targetV = el.getAttribute("data-v");
    if (act === "open-settings") targetV = "settings";
    else if (act === "open-summary"){ targetV = "summary"; curSummaryMonth = ""; }
    else if (act === "open-anniv" || act === "open-anniv-list") targetV = "anniv";

    // 记录切换视图的历史记录（支持浏览器/手机返回键返回上一页）
    if (targetV !== CURRENT_VIEW){
      history.pushState({type:"view", v:targetV}, "");
    }

    // 底部导航/页签进入的都是页面顶层，旅行清单回到列表层
    if (targetV !== "trips"){ TRIP_ID = null; TRIP_DETAIL = null; }

    if (targetV === "week"){
      CURRENT_VIEW = "week";
      var curT = todayISO();
      if (ANCHOR !== curT){
        ANCHOR = curT;
        DATA = null;
      }
      load().then(function(){
        setTimeout(scrollToToday, 60);
      });
    } else {
      CURRENT_VIEW = targetV;
      load();
    }
  
  },

  /* ---- 月历 · 手账 · 照片 · 日程详情 ---- */
  "month-prev": function(el, e) {
    var act = "month-prev";
    var p = MONTH_ANCHOR.split("-"), y = +p[0], m = +p[1] - 1;
    if (m < 1){ m = 12; y--; }
    MONTH_ANCHOR = y + "-" + pad(m);
    MONTH_DATA = null;
    load();
  
  },
  "month-next": function(el, e) {
    var act = "month-next";
    var p = MONTH_ANCHOR.split("-"), y = +p[0], m = +p[1] + 1;
    if (m > 12){ m = 1; y++; }
    MONTH_ANCHOR = y + "-" + pad(m);
    MONTH_DATA = null;
    load();
  
  },
  "month-cur": function(el, e) {
    var act = "month-cur";
    MONTH_ANCHOR = todayISO().slice(0, 7);
    MONTH_DATA = null;
    load();

  },
  "month-pick": function(el, e) {
    openMonthPicker(MONTH_ANCHOR, function(ym){
      MONTH_ANCHOR = ym;
      MONTH_DATA = null;
      load();
    });
  },
  "week-pick": function(el, e) {
    openWeekPicker();
  },
  "date-pick": function(el, e) {
    openDatePick();
  },
  "open-day-diary": function(el, e) {
    var act = "open-day-diary";
    openDayDiaries(el.getAttribute("data-date"));
  
  },
  "open-add-diary": function(el, e) {
    var act = "open-add-diary";
    curEditingDiary = null;
    var dt = el.getAttribute("data-date") || todayISO();
    resetDiaryForm();
    if ($("#df_date")) $("#df_date").value = dt;
    openModal("#ovAddDiary");
  
  },

  /* ---- 导航与全局 ---- */
  "preview-photo": function(el, e) {
    var act = "preview-photo";
    try {
      var rawList = el.getAttribute("data-list");
      pvPhotosList = rawList ? JSON.parse(decodeURIComponent(rawList)) : [];
      pvCurrentIndex = +el.getAttribute("data-idx") || 0;
    } catch(e){
      pvPhotosList = [el.getAttribute("data-src") || el.src];
      pvCurrentIndex = 0;
    }
    updatePhotoViewer();
    var pv = $("#ovPhotoViewer");
    if (pv){
      var pvWasOpen = pv.style.display === "flex";
      pv.style.display = "flex";
      pv.classList.add("show");
      /* 已开着的大图只换内容（翻页/换图），历史条目不叠加 */
      if (!pvWasOpen){
        try {
          history.pushState({modal:"photo"}, "");
        } catch(e){}
      }
    }
  
  },

  /* ---- 月历 · 手账 · 照片 · 日程详情 ---- */
  "pv-prev": function(el, e) {
    var act = "pv-prev";
    if (pvPhotosList.length > 1){
      pvCurrentIndex = (pvCurrentIndex - 1 + pvPhotosList.length) % pvPhotosList.length;
      updatePhotoViewer();
    }
  
  },
  "pv-next": function(el, e) {
    var act = "pv-next";
    if (pvPhotosList.length > 1){
      pvCurrentIndex = (pvCurrentIndex + 1) % pvPhotosList.length;
      updatePhotoViewer();
    }
  
  },
  "pv-play-live": function(el, e) {
    var act = "pv-play-live";
    playPvLiveVideo();
  
  },
  "pv-save-photo": function(el, e) {
    var act = "pv-save-photo";
    var curItem = pvPhotosList[pvCurrentIndex];
    var imgUrl = (typeof curItem === "object" && curItem !== null) ? curItem.src : curItem;
    savePhotoUrlToDevice(imgUrl);
  
  },
  "close-photo": function(el, e) {
    var act = "close-photo";
    closePhotoViewer();
  
  },
  "trigger-upload-photo": function(el, e) {
    var act = "trigger-upload-photo";
    var fi = $("#df_files");
    if (fi) fi.click();
  
  },
  "edit-diary": function(el, e) {
    var act = "edit-diary";
    try {
      var d = JSON.parse(decodeURIComponent(el.getAttribute("data-diary")));
      curEditingDiary = d;
      if ($("#df_edit_id")) $("#df_edit_id").value = d.id;
      if ($("#df_modal_title")) $("#df_modal_title").textContent = "修改手账记录 ✏️";
      if ($("#df_submit_btn")) $("#df_submit_btn").textContent = "💾 保存手账修改";
      if ($("#df_date")) $("#df_date").value = d.date;
      if ($("#df_title")) $("#df_title").value = d.title;
      if ($("#df_loc")) $("#df_loc").value = d.location || "";
      var standardMoods = ["🥰 幸福贴贴", "🥳 快乐狂欢", "😋 撑成小猪", "🥱 累并快乐", "✨ 仪式感满满", "💖 浪漫约会", "🍰 甜品治愈", "🏕️ 户外露营"];
      if (standardMoods.indexOf(d.mood) !== -1){
        if ($("#df_mood")) $("#df_mood").value = d.mood;
        if ($("#df_mood_custom")){ $("#df_mood_custom").value = ""; $("#df_mood_custom").style.display = "none"; }
      } else {
        if ($("#df_mood")) $("#df_mood").value = "__custom__";
        if ($("#df_mood_custom")){ $("#df_mood_custom").value = d.mood || ""; $("#df_mood_custom").style.display = "block"; }
      }
      if ($("#df_content")) $("#df_content").value = d.content || "";
      if ($("#df_files")) $("#df_files").value = "";
      diaryPhotosToUpload = [];
      var prev = $("#df_preview");
      if (prev){
        prev.innerHTML = (d.photos || []).map(function(p){
          var liveTag = p.live_video ? '<span style="position:absolute;bottom:2px;left:2px;background:rgba(0,0,0,0.65);color:#fbbf24;font-size:8px;font-weight:800;padding:0 3px;border-radius:4px;pointer-events:none">LIVE</span>' : '';
          return '<div class="edit-photo-thumb" data-file="' + p.file_name + '" style="position:relative;display:inline-block">' +
            '<img src="/photos/tb_' + p.file_name + '" style="width:48px;height:48px;object-fit:cover;border-radius:6px;border:1px solid #ddd">' +
            liveTag +
            '<span class="del-photo-btn photo-del-x" data-act="remove-edit-photo" data-file="' + p.file_name + '" style="position:absolute;top:-5px;right:-5px;background:#ef4444;color:#fff;border-radius:50%;width:16px;height:16px;line-height:16px;text-align:center;font-size:11px;cursor:pointer;font-weight:bold">×</span>' +
          '</div>';
        }).join("");
      }
      openModal("#ovAddDiary");
    } catch(e){}
  
  },
  "remove-edit-photo": function(el, e) {
    var act = "remove-edit-photo";
    var pwrap = el.closest(".edit-photo-thumb");
    if (pwrap) pwrap.remove();
  
  },
  "del-diary": function(el, e) {
    var act = "del-diary";
    uiConfirm({icon:"📔", title:"删除这篇手账？", message:"手账文字与照片都会一起删掉。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      api("/api/diaries/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
        .then(function(){
          toast("已删除 🐾");
          load().then(function(){
            if (activeDiaryDate) openDayDiaries(activeDiaryDate, true);
          });
        })
        .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); });
    });
  
  },

  /* ---- 旅行清单 ---- */
  "trip-new-toggle": function(el, e) {
    var act = "trip-new-toggle";
    tripNewOpen = !tripNewOpen;
    render();
  
  },
  "trip-create": function(el, e) {
    var act = "trip-create";
    if (el.dataset.busy === "1") return;
    var tDest = tripFormValue("trip_dest");
    if (!tDest){ toast("先写上要去哪儿", "err"); markInputErr(document.getElementById("trip_dest")); return; }
    el.dataset.busy = "1";
    tripAction("create", {
      destination: tDest,
      start_date: tripFormValue("trip_start"),
      end_date: tripFormValue("trip_end"),
      use_template: !!($("#trip_tpl") && $("#trip_tpl").checked)
    }).then(function(res){
      tripNewOpen = false; TRIPS = null; TRIP_DETAIL = null; TRIP_ID = res.id;
      toast("✈️ 清单建好了");
      loadTrip(res.id);
      /* 新建后直接进入清单详情，与 trip-open 同样压入层级条目 */
      history.pushState({type:"view", v:"trips", trip:res.id}, "");
    }).catch(function(e){ toast("❌ " + e.message, "err"); })
      .finally(function(){ delete el.dataset.busy; });

  },
  "trip-open": function(el, e) {
    var act = "trip-open";
    var tid = parseInt(el.getAttribute("data-id"), 10);
    if (TRIP_ID === tid) return;       // 双击同一次旅行不重复压层级条目
    TRIP_ID = tid;
    tripEditingItem = null; tripNewOpen = false;
    TRIP_DETAIL = TRIP_DETAILS[TRIP_ID] || null;
    if (TRIP_DETAIL) render();        // 看过的清单秒开，随后静默对账
    loadTrip(TRIP_ID);
    /* 下钻一层清单页压入历史，返回手势/键回到列表 */
    history.pushState({type:"view", v:"trips", trip:TRIP_ID}, "");

  },
  "trip-back": function(el, e) {
    var act = "trip-back";
    TRIP_ID = null; TRIP_DETAIL = null; tripEditingItem = null;
    /* 回到列表：消费下钻时压入的条目，popstate 里按条目恢复列表 */
    if (history.state && history.state.trip) history.back();
    else render();                     // 无历史条目时（如新建后刷新）直接回列表，走缓存对账
  
  },
  "trip-delete": function(el, e) {
    var act = "trip-delete";
    uiConfirm({icon:"✈️", title:"删除整份清单？", message:"清单上的所有物品都会一起删掉。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      tripAction("delete", {id: parseInt(el.getAttribute("data-id"), 10)}).then(function(){
      delete TRIP_DETAILS[parseInt(el.getAttribute("data-id"), 10)];
      TRIP_ID = null; TRIP_DETAIL = null; TRIPS = null; toast("已删除");
      /* 从详情层删掉整份清单：消费下钻条目回到列表层 */
      if (history.state && history.state.trip) history.back();
      else render();
    }).catch(function(e){ toast("❌ " + e.message, "err"); });
    });
  
  },
  "trip-copy-last": function(el, e) {
    var act = "trip-copy-last";
    tripAction("copy_last", {id: parseInt(el.getAttribute("data-id"), 10)}).then(function(res){
      toast(res.copied ? ("复制了 " + res.copied + " 件") : "没有要新增的物品");
      return loadTrip(TRIP_ID);
    }).catch(function(e){ toast("❌ " + e.message, "err"); });
  
  },
  "trip-toggle": function(el, e) {
    var act = "trip-toggle";
    var togId = parseInt(el.getAttribute("data-id"), 10);
    var togItem = ((TRIP_DETAIL && TRIP_DETAIL.items) || []).filter(function(x){ return x.id === togId; })[0];
    if (!togItem) return;
    var nextState = togItem.is_packed ? 0 : 1;
    togItem.is_packed = nextState;
    if (navigator.vibrate){ try { navigator.vibrate(8); } catch(e){} }
    tripSetRowPacked(togId);      // 先动界面，立刻有反馈
    tripRefreshProgress();
    tripAction("item_toggle", {id: togId, packed: nextState})
      .catch(function(e){
        togItem.is_packed = nextState ? 0 : 1;   // 失败回滚
        tripSetRowPacked(togId);
        tripRefreshProgress();
        toast("❌ " + e.message, "err");
      });
  
  },
  "trip-item-new": function(el, e) {
    var act = "trip-item-new";
    tripEditingItem = null;
    tripFormCat = el.getAttribute("data-cat") || "doc";
    render();
    setTimeout(function(){
      var f = document.getElementById("tripItemForm");
      if (f) f.scrollIntoView({behavior: "smooth", block: "center"});
      var nameEl = $("#ti_name");
      if (nameEl) nameEl.focus({preventScroll: true});
    }, 40);
  
  },
  "trip-item-edit": function(el, e) {
    var act = "trip-item-edit";
    var iid = parseInt(el.getAttribute("data-id"), 10);
    tripEditingItem = ((TRIP_DETAIL && TRIP_DETAIL.items) || []).filter(function(i){ return i.id === iid; })[0] || null;
    render();
    setTimeout(function(){
      var f = document.getElementById("tripItemForm");
      if (f) f.scrollIntoView({behavior: "smooth", block: "center"});
      var nameEl = $("#ti_name");
      if (nameEl) nameEl.focus({preventScroll: true});
    }, 40);
  
  },
  "trip-item-cancel": function(el, e) {
    var act = "trip-item-cancel";
    tripEditingItem = null;
    render();
  
  },
  "trip-item-save": function(el, e) {
    var act = "trip-item-save";
    var tName = tripFormValue("ti_name");
    if (!tName){ toast("先写上物品名称", "err"); markInputErr(document.getElementById("ti_name")); return; }
    var itemBody = {name: tName, category: tripFormValue("ti_cat"),
                    qty_a: tripFormValue("ti_qa"), qty_b: tripFormValue("ti_qb")};
    var req;
    if (tripEditingItem){ itemBody.id = tripEditingItem.id; req = tripAction("item_update", itemBody); }
    else { itemBody.trip_id = TRIP_ID; req = tripAction("item_add", itemBody); }
    req.then(function(){
      tripEditingItem = null;
      return loadTrip(TRIP_ID).then(function(){
        setTimeout(function(){ tripScrollToItem(tName); }, 60);
      });
    })
       .catch(function(e){ toast("❌ " + e.message, "err"); });
  
  },
  "trip-item-del": function(el, e) {
    var act = "trip-item-del";
    uiConfirm({icon:"🧳", title:"删掉这一项？", message:"从清单里移除这件物品。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      tripAction("item_delete", {id: parseInt(el.getAttribute("data-id"), 10)}).then(function(){
        tripEditingItem = null;
        return loadTrip(TRIP_ID);
      }).catch(function(e){ toast("❌ " + e.message, "err"); });
    });
  
  },

  /* ---- 时光胶囊与纪念日 ---- */
  "summary-month": function(el, e) {
    var act = "summary-month";
    var delta = parseInt(el.getAttribute("data-d"), 10) || 0;
    if (!delta) return;
    loadSummary(shiftMonth(curSummaryMonth || defaultSummaryMonth(), delta));
  
  },
  "refresh-summary": function(el, e) {
    var act = "refresh-summary";
    loadSummary(curSummaryMonth, true);
  
  },
  "send-summary-wx": function(el, e) {
    var act = "send-summary-wx";
    api("/api/summary/push", {method:"POST", body:{month: curSummaryMonth}}).then(function(res){
      toast("📔 月度胶囊已推送到微信（已发送给 " + res.sent_count + " 人）");
    }).catch(function(err){
      toast("❌ " + err.message, "err");
    });
  
  },
  "anniv-type": function(el, e) {
    var act = "anniv-type";
    annivType = el.getAttribute("data-v") || "love";
    document.querySelectorAll("#annivTypeSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === annivType);
    });
  
  },
  "del-anniv": function(el, e) {
    var act = "del-anniv";
    uiConfirm({icon:"💖", title:"删除这个纪念日？", message:"删掉后就不再提醒这个日子啦。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      api("/api/anniversaries/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
        .then(function(){
          toast("已删除 🐾");
          DATA = null;
          return load();
        })
        .then(function(){ renderAnnivList(); })
        .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); });
    });
  
  },

  /* ---- 月历 · 手账 · 照片 · 日程详情 ---- */
  "ev-tab": function(el, e) {
    var act = "ev-tab";
    e.stopPropagation();
    var g = el.getAttribute("data-g");
    var idx = +el.getAttribute("data-i");
    document.querySelectorAll('[data-b="' + g + '"]').forEach(function(b){
      b.style.display = (+b.getAttribute("data-i") === idx) ? "block" : "none";
    });
    document.querySelectorAll('[data-g="' + g + '"].ev-tab').forEach(function(t){
      t.classList.toggle("on", +t.getAttribute("data-i") === idx);
    });
    var stk = el.closest(".ev-stack");
    if (stk){
      var bodies = stk.querySelectorAll(".ev-stack-body");
      if (bodies[idx]) stk.setAttribute("data-id", bodies[idx].querySelector(".del").getAttribute("data-id"));
      // 折叠卡的几何尺寸跟随当前选中日程的真实起止时间
      var nTop = el.getAttribute("data-top"), nH = el.getAttribute("data-h");
      if (nTop !== null && nH !== null){
        stk.style.top = nTop + "px";
        stk.style.height = nH + "px";
        stk.classList.remove("compact", "tight");
        var fit = stackFitCls(+nH);
        if (fit) stk.classList.add(fit.trim());
      }
    }
  
  },
  "detail": function(el, e) {
    var act = "detail"; openDetail(el.getAttribute("data-id"), el.getAttribute("data-date")); 
  },
  "send-cmt": function(el, e) {
    var act = "send-cmt";
    var inp = $("#cmtText");
    var text = inp ? inp.value.trim() : "";
    if (!text || !curDetail) return;
    if (el.dataset.busy === "1") return;
    el.dataset.busy = "1";
    var cmtDate = curDetail.date || todayISO();
    api("/api/comment", {method:"POST", body:{event_id:curDetail.id, text:text, date:cmtDate}})
      .then(function(){ return load(); })
      .then(function(){ refreshDetail(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); })
      .finally(function(){ delete el.dataset.busy; });

  },
  "del-cmt": function(el, e) {
    var act = "del-cmt";
    uiConfirm({icon:"💬", title:"删除这条留言？", message:"对方也将看不到这条留言。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      api("/api/comment/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
        .then(function(){ return load(); })
        .then(function(){ refreshDetail(); })
        .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); });
    });
  
  },
  "edit-event": function(el, e) {
    var act = "edit-event";
    var eid = +el.getAttribute("data-id");
    api("/api/event/" + eid).then(function(j){
      var ev = j.event;
      if (!ev) return;
      curEditingEventId = ev.id;
      var addTitleEl = $("#addModalTitle");
      if (addTitleEl) addTitleEl.textContent = "修改日程 🐾";
      var addBtnEl = $("#addModalSubmitBtn");
      if (addBtnEl) addBtnEl.textContent = "🐾 保存修改！";

      addUid = ev.uid;
      document.querySelectorAll("#addSeg .opt").forEach(function(o){
        var uid = o.getAttribute("data-uid");
        var side = uid === "b" ? "b-side" : (uid === "both" ? "both-side" : "");
        o.className = "opt" + (uid === addUid ? (" on" + (side ? " " + side : "")) : "");
      });

      addRep = ev.repeat || "weekly";
      document.querySelectorAll("#repSeg .opt").forEach(function(o){
        o.classList.toggle("on", o.getAttribute("data-v") === addRep);
      });

      var fWd = $("#fldWd"), fDt = $("#fldDt"), fWs = $("#fldWs");
      if (fWd) fWd.style.display = addRep === "weekly" ? "" : "none";
      if (fDt) fDt.style.display = addRep === "once" ? "" : "none";
      if (fWs) fWs.style.display = addRep === "weekly" ? "" : "none";

      if ($("#f_wd")) $("#f_wd").value = ev.weekday != null ? ev.weekday : 0;
      if ($("#f_date")) $("#f_date").value = ev.date || todayISO();
      if ($("#f_ts")) $("#f_ts").value = ev.tstart || "19:00";
      if ($("#f_te")) $("#f_te").value = ev.tend || "21:00";
      if ($("#f_title")) $("#f_title").value = ev.title || "";
      if ($("#f_loc")) $("#f_loc").value = ev.location || "";
      if ($("#f_note")) $("#f_note").value = ev.note || "";
      if ($("#f_ws")) $("#f_ws").value = ev.week_spec || "";

      // 编辑弹窗顶替详情弹窗的历史条目，返回时不留空条目
      openModal("#ovAdd");
      var ovDetail = $("#ovDetail");
      if (ovDetail) ovDetail.classList.remove("show");
    }).catch(function(err){ toast(err.message || "加载失败", "err"); });
  
  },
  "del-event": function(el, e) {
    var act = "del-event";
    uiConfirm({icon:"🗓️", title:"删除这条日程？", message:"日程和它下面的留言都会删掉。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      api("/api/events/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
        .then(function(){
          closeAllModals();
          curDetail = null; toast("已删除日程 🐾"); load();
        })
        .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); });
    });
  
  },

  /* ---- 弹窗 ---- */
  "close": function(el, e) {
    var act = "close";
    closeAllModals();
  },

  /* ---- 导航与全局 ---- */
  "logout": function(el, e) {
    var act = "logout";
    api("/api/logout", {method:"POST"}).catch(function(){}).then(function(){
      closeAllModals();
      try {
        localStorage.removeItem("sched_me");
        localStorage.removeItem("sched_week_" + ANCHOR);
        localStorage.removeItem("sched_month_" + MONTH_ANCHOR);
      } catch(e){}
      ME = null; DATA = null; MONTH_DATA = null;
      render();
    });
  
  },

  /* ---- 汪汪信箱 ---- */
  "msg-filter": function(el, e) {
    var act = "msg-filter";
    curMsgFilter = el.getAttribute("data-v") || "all";
    document.querySelectorAll("#msgFilterSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === curMsgFilter);
    });
    renderMessagesList();
  
  },
  "read-all-msgs": function(el, e) {
    var act = "read-all-msgs";
    api("/api/messages/read", {method:"POST", body:{all:true}}).then(function(res){
      toast("已全部标为已读 ✨");
      if (res && res.unread_count !== undefined) setUnreadCount(res.unread_count);
      else setUnreadCount(0);
      (MESSAGES_DATA || []).forEach(function(m){ m.is_read = 1; });
      renderMessagesList();
    }).catch(function(err){ toast(err.message || "操作失败", "err"); });
  
  },
  "clear-read-msgs": function(el, e) {
    var act = "clear-read-msgs";
    uiConfirm({icon:"🧹", title:"清理所有已读通知？", message:"未读的会保留，只清掉已读的。", okText:"清理"}).then(function(ok){
      if (!ok) return;
      api("/api/messages/delete", {method:"POST", body:{clear_read:true}}).then(function(res){
        toast("已清理已读通知 🧹");
        loadMessages();
      }).catch(function(err){ toast(err.message || "清理失败", "err"); });
    });
  
  },
  "refresh-msgs": function(el, e) {
    var act = "refresh-msgs";
    loadMessages();
  
  },
  "read-one-msg": function(el, e) {
    var act = "read-one-msg";
    var mid = +el.getAttribute("data-mid");
    api("/api/messages/read", {method:"POST", body:{id:mid}}).then(function(res){
      if (res && res.unread_count !== undefined) setUnreadCount(res.unread_count);
      var m = (MESSAGES_DATA || []).find(function(x){ return x.id === mid; });
      if (m) m.is_read = 1;
      renderMessagesList();
    }).catch(function(err){ toast(err.message || "操作失败", "err"); });
  
  },
  "del-one-msg": function(el, e) {
    var act = "del-one-msg";
    uiConfirm({icon:"📩", title:"删除这条消息？", message:"消息将从信箱里移除。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      var mid = +el.getAttribute("data-mid");
      api("/api/messages/delete", {method:"POST", body:{id:mid}}).then(function(res){
        toast("已删除消息 🐾");
        loadMessages();
      }).catch(function(err){ toast(err.message || "删除失败", "err"); });
    });
  
  },
  "open-msg": function(el, e) {
    var act = "open-msg";
    if (e.target.closest("[data-act='read-one-msg']") || e.target.closest("[data-act='del-one-msg']")){
      return;
    }
    var mid = +el.getAttribute("data-mid");
    var targetAction = el.getAttribute("data-target-action");
    var m = (MESSAGES_DATA || []).find(function(x){ return x.id === mid; });
    if (m && !m.is_read){
      m.is_read = 1;
      api("/api/messages/read", {method:"POST", body:{id:mid}}).then(function(res){
        if (res && res.unread_count !== undefined) setUnreadCount(res.unread_count);
      }).catch(function(){});
    }
    if (targetAction){
      window.handleTargetAction(targetAction);
    } else {
      renderMessagesList();
    }
  
  },

  /* ---- 导航与全局 ---- */
  "test-app-notice": function(el, e) {
    var act = "test-app-notice";
    if (window.AndroidApp && window.AndroidApp.postNotification) {
      window.AndroidApp.postNotification("🐾 线条小狗通知测试", "手机原生通知权限已正常开启！后续对方日程与留言都将直接推送给你 🐾");
      toast("已触发手机通知 🐾");
    }
    api("/api/notifications/test", {method:"POST"}).then(function(){
      if (!window.AndroidApp) toast("已向后端写入测试通知 🐾");
      pollMessages();
    }).catch(function(err){ toast(err.message || "请求失败", "err"); });
  
  },

  /* ---- 心愿 ---- */
  "open-add-wish": function(el, e) {
    var act = "open-add-wish";
    openModal("#ovAddWish");
  
  },

  /* ---- 登录与快捷操作 ---- */
  "pick-wish-uid": function(el, e) {
    var act = "pick-wish-uid";
    addWishUid = el.getAttribute("data-uid") || "a";
    document.querySelectorAll("#wishUidSeg .opt").forEach(function(o){
      var isTarget = o.getAttribute("data-uid") === addWishUid;
      o.classList.toggle("on", isTarget);
      if (o.getAttribute("data-uid") === "b") o.classList.toggle("b-side", isTarget);
    });
  
  },
  "pick-wish-cat": function(el, e) {
    var act = "pick-wish-cat";
    addWishCat = el.getAttribute("data-v") || "travel";
    document.querySelectorAll("#wishAddCatSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === addWishCat);
    });
  
  },
  "pick-wish-prio": function(el, e) {
    var act = "pick-wish-prio";
    addWishPrio = +el.getAttribute("data-v") || 0;
    document.querySelectorAll("#wishPrioSeg .opt").forEach(function(o){
      o.classList.toggle("on", +o.getAttribute("data-v") === addWishPrio);
    });
  
  },

  /* ---- 心愿 ---- */
  "toggle-wish": function(el, e) {
    var act = "toggle-wish";
    var wid = +el.getAttribute("data-wid");
    api("/api/wishes/toggle", {method:"POST", body:{id:wid}}).then(function(res){
      toast(res.is_done ? "🎉 恭喜！共同实现了一个心愿！" : "已重新标记为待完成 🐾");
      loadWishes();
    }).catch(function(err){ toast(err.message || "操作失败", "err"); });
  
  },
  "del-wish": function(el, e) {
    var act = "del-wish";
    uiConfirm({icon:"🌟", title:"删除这个心愿？", message:"心愿便签会从清单里移除。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      var wid = +el.getAttribute("data-wid");
      api("/api/wishes/delete", {method:"POST", body:{id:wid}}).then(function(){
        toast("已删除心愿 🐾");
        loadWishes();
      }).catch(function(err){ toast(err.message || "删除失败", "err"); });
    });
  
  },
  "wish-filter": function(el, e) {
    var act = "wish-filter";
    curWishFilter = el.getAttribute("data-v") || "all";
    document.querySelectorAll("#wishCatSeg .opt").forEach(function(o){
      o.classList.toggle("on", o.getAttribute("data-v") === curWishFilter);
    });
    renderWishesList();
  
  },
  "wish-to-diary": function(el, e) {
    var act = "wish-to-diary";
    if (CURRENT_VIEW === "month") return;   // 双击不重复压视图条目
    var wishTitle = el.getAttribute("data-title") || "";
    /* 心愿页跳到手账页是一层导航：压入历史，返回手势回到心愿页 */
    history.pushState({type:"view", v:"month"}, "");
    CURRENT_VIEW = "month";
    load().then(function(){
      setTimeout(function(){
        curEditingDiary = null;
        resetDiaryForm();
        if ($("#df_title")) $("#df_title").value = "实现了心愿: " + wishTitle;
        if ($("#df_content")) $("#df_content").value = "今天和 TA 一起打卡了心愿清单【" + wishTitle + "】！超级开心与满足~ 🥰";
        openModal("#ovAddDiary");
      }, 150);
    });
  
  },

  /* ---- 导航与全局 ---- */
  "battery-protect": function(el, e) {
    var act = "battery-protect";
    if (window.AndroidApp && window.AndroidApp.requestBatteryOptimization) {
      window.AndroidApp.requestBatteryOptimization();
    } else {
      toast("请在 Android 手机客户端中使用此功能 🐾", "err");
    }
  
  },
  "open-server-url": function(el, e) {
    var act = "open-server-url";
    if (window.AndroidApp && window.AndroidApp.openServerSettings) {
      window.AndroidApp.openServerSettings();
    }
  
  },
  "del": function(el, e) {
    var act = "del";
    e.stopPropagation();
    uiConfirm({icon:"🗓️", title:"删除这条日程？", message:"和对方的留言一起删掉。", danger:true, okText:"删除"}).then(function(ok){
      if (!ok) return;
      api("/api/events/delete", {method:"POST", body:{id:+el.getAttribute("data-id")}})
        .then(function(){ toast("已删除日程 🐾"); load(); })
        .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); });
    });
  
  },
};

document.addEventListener("click", function(e){
  var el = e.target.closest("[data-act]");
  if (!el) return;
  var h = CLICK_ACTIONS[el.getAttribute("data-act")];
  if (h) h(el, e);
});


document.addEventListener("submit", function(e){
  var f = e.target;
  /* 双击/回车连点只提交一次：进行中的表单吞掉后续 submit */
  if (f.dataset.busy === "1"){ e.preventDefault(); e.stopImmediatePropagation(); return; }
  if (f.id === "loginForm"){
    e.preventDefault();
    f.dataset.busy = "1";
    api("/api/login", {method:"POST", body:{uid:loginUid, password:$("#loginPw").value}})
      .then(function(j){
        ME = {uid:j.uid, name:j.name};
        ANCHOR = todayISO();
        CURRENT_VIEW = "week";
        saveLS("sched_me", ME);
        if (window.AndroidApp && window.AndroidApp.saveAuthUid) {
          window.AndroidApp.saveAuthUid(j.uid);
        }
        toast("欢迎回家，" + j.name + " 🐾");
        if (j.unread_count !== undefined) setUnreadCount(j.unread_count);
        if (j.week) {
          DATA = j.week;
          saveLS("sched_week_" + ANCHOR, DATA);
          render();
          if (CURRENT_VIEW === "week") setTimeout(scrollToToday, 80);
          pollMessages();
          if (!MONTH_DATA) {
            api("/api/month?month=" + MONTH_ANCHOR).then(function(mj){
              MONTH_DATA = mj;
              saveLS("sched_month_" + MONTH_ANCHOR, mj);
              if (mj && mj.unread_count !== undefined) setUnreadCount(mj.unread_count);
            }).catch(function(){});
          }
        } else {
          load().then(function(){ pollMessages(); });
        }
      })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); })
      .finally(function(){ delete f.dataset.busy; });
  } else if (f.id === "addForm"){
    e.preventDefault();
    f.dataset.busy = "1";
    var body = {
      uid: addUid, repeat: addRep,
      title: $("#f_title").value.trim(), location: $("#f_loc").value.trim(),
      note: $("#f_note").value.trim(), week_spec: $("#f_ws").value.trim(),
      tstart: $("#f_ts").value, tend: $("#f_te").value,
      weekday: addRep === "weekly" ? +$("#f_wd").value : null,
      date: addRep === "once" ? $("#f_date").value : ""
    };
    if (curEditingEventId) {
      body.id = curEditingEventId;
    }
    api(curEditingEventId ? "/api/events/update" : "/api/events", {method:"POST", body:body})
      .then(function(){
        closeAllModals();
        toast(curEditingEventId ? "已修改日程 🐾" : "已记下啦 🐾");
        curEditingEventId = null;
        load();
      })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); })
      .finally(function(){ delete f.dataset.busy; });
  } else if (f.id === "setForm"){
    e.preventDefault();
    f.dataset.busy = "1";
    var body = {
      name_a: $("#s_na").value.trim(), name_b: $("#s_nb").value.trim(),
      week1_a: $("#s_w1a").value, week1_b: $("#s_w1b").value,
      window_start: $("#s_ws").value, window_end: $("#s_we").value,
      min_gap: +$("#s_mg").value,
      wx_a: $("#s_wxa") ? $("#s_wxa").value.trim() : "",
      wx_b: $("#s_wxb") ? $("#s_wxb").value.trim() : "",
      llm_api_base: $("#s_llm_base") ? $("#s_llm_base").value.trim() : "",
      llm_api_key: $("#s_llm_key") ? $("#s_llm_key").value.trim() : "",
      llm_model: $("#s_llm_model") ? $("#s_llm_model").value.trim() : "deepseek-chat"
    };
    var pwOld = $("#s_old").value, pwNew = $("#s_new").value;
    api("/api/settings", {method:"POST", body:body}).then(function(){
      var p = (pwOld && pwNew) ? api("/api/password", {method:"POST", body:{old:pwOld, new:pwNew}}) : Promise.resolve();
      return p.then(function(){
        closeAllModals();
        toast("设置已保存 🐾");
        return api("/api/me").then(function(j){ ME = j; load(); });
      });
    }).catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); })
      .finally(function(){ delete f.dataset.busy; });
  } else if (f.id === "annivForm"){
    e.preventDefault();
    f.dataset.busy = "1";
    var body = {
      title: $("#an_title").value.trim(),
      date: $("#an_date").value,
      target_type: annivType
    };
    api("/api/anniversaries", {method:"POST", body:body})
      .then(function(){
        $("#an_title").value = "";
        toast("已记下这一天啦 💖");
        DATA = null;
        return load();
      })
      .then(function(){ renderAnnivList(); })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); })
      .finally(function(){ delete f.dataset.busy; });
  } else if (f.id === "addWishForm"){
    e.preventDefault();
    var title = ($("#wf_title") ? $("#wf_title").value : "").trim();
    if (!title){ toast("请输入心愿内容 🐾"); return; }
    f.dataset.busy = "1";
    var note = ($("#wf_note") ? $("#wf_note").value : "").trim();
    api("/api/wishes", {
      method: "POST",
      body: {
        creator_uid: addWishUid,
        title: title,
        category: addWishCat,
        priority: addWishPrio,
        note: note
      }
    }).then(function(){
      closeAllModals();
      if ($("#wf_title")) $("#wf_title").value = "";
      if ($("#wf_note")) $("#wf_note").value = "";
      toast("心愿已贴进清单啦 ✨");
      loadWishes();
    }).catch(function(err){ toast(err.message || "添加失败", "err"); })
      .finally(function(){ delete f.dataset.busy; });
  } else if (f.id === "addDiaryForm"){
    e.preventDefault();
    var sBtn = $("#df_submit_btn");
    if (sBtn && sBtn.disabled) return;
    var origBtnText = sBtn ? sBtn.textContent : "";
    if (sBtn){
      sBtn.disabled = true;
      sBtn.textContent = "正在保存中... 🐾";
    }

    var editId = $("#df_edit_id") ? $("#df_edit_id").value : "";
    var keepPhotos = [];
    document.querySelectorAll(".edit-photo-thumb").forEach(function(el){
      var fn = el.getAttribute("data-file");
      if (fn) keepPhotos.push(fn);
    });
    var mVal = $("#df_mood").value;
    if (mVal === "__custom__"){
      mVal = ($("#df_mood_custom") ? $("#df_mood_custom").value.trim() : "") || "✨ 特别的心情";
    }
    var body = {
      date: $("#df_date").value,
      title: $("#df_title").value.trim(),
      location: $("#df_loc").value.trim(),
      mood: mVal,
      content: $("#df_content").value.trim(),
      photos: diaryPhotosToUpload
    };
    var url = "/api/diaries";
    if (editId){
      body.id = +editId;
      body.keep_photos = keepPhotos;
      url = "/api/diaries/update";
    }
    var savedDate = body.date;
    var dayWasOpen = $("#ovDiaryDay") && $("#ovDiaryDay").classList.contains("show");
    api(url, {method:"POST", body:body})
      .then(function(){
        closeAllModals();
        toast(editId ? "手账修改已保存 🐾" : "手账已贴在小窝啦 🐾");
        diaryPhotosToUpload = [];
        load().then(function(){
          if (dayWasOpen && savedDate) openDayDiaries(savedDate, true);
        });
      })
      .catch(function(err){ if (err.message !== "unauth") toast(err.message, "err"); })
      .finally(function(){
        if (sBtn){
          sBtn.disabled = false;
          sBtn.textContent = origBtnText || (editId ? "💾 保存手账修改" : "✨ 贴在手账本上！");
        }
      });
  } else if (f.getAttribute("id") && /^cycle2/.test(f.getAttribute("id"))){
    // Cycle v2 forms are handled by cycle2.js; never fall through to a native submit.
    e.preventDefault();
  }
});

document.addEventListener("change", function(e){
  if (e.target && e.target.id === "df_mood"){
    var custInput = $("#df_mood_custom");
    if (custInput){
      if (e.target.value === "__custom__"){
        custInput.style.display = "block";
        custInput.focus();
      } else {
        custInput.style.display = "none";
      }
    }
  }
  if (e.target && e.target.id === "df_files"){
    var files = Array.from(e.target.files);
    var prev = $("#df_preview");
    var imgMap = {}, vidMap = {};

    function baseKey(name){
      return String(name || "").replace(/\.[^.]+$/, "").toLowerCase();
    }

    function appendPreviewItem(mediaObj){
      if (diaryPhotosToUpload.length >= 18){
        toast("手账最多贴 18 张照片哦，多余的没有选上 🐾", "err");
        return;
      }
      diaryPhotosToUpload.push(mediaObj);
      var pWrap = document.createElement("div");
      pWrap.style.position = "relative";
      pWrap.style.display = "inline-block";

      var thumbEl = document.createElement("img");
      thumbEl.src = mediaObj.image;
      thumbEl.style.cssText = "width:48px;height:48px;object-fit:cover;border-radius:6px;border:1px solid #ddd;display:block";
      pWrap.appendChild(thumbEl);

      if (mediaObj.live_video){
        var lTag = document.createElement("span");
        lTag.textContent = "LIVE";
        lTag.style.cssText = "position:absolute;bottom:2px;left:2px;background:rgba(0,0,0,0.68);color:#fbbf24;font-size:8px;font-weight:800;padding:0 3px;border-radius:4px;pointer-events:none";
        pWrap.appendChild(lTag);
      }

      var delX = document.createElement("span");
      delX.textContent = "×";
      delX.className = "photo-del-x";
      delX.style.cssText = "position:absolute;top:-5px;right:-5px;background:#ef4444;color:#fff;border-radius:50%;width:16px;height:16px;line-height:16px;text-align:center;font-size:11px;cursor:pointer;font-weight:bold";
      delX.onclick = function(){
        var idx = diaryPhotosToUpload.indexOf(mediaObj);
        if (idx !== -1) diaryPhotosToUpload.splice(idx, 1);
        pWrap.remove();
      };
      pWrap.appendChild(delX);
      if (prev) prev.appendChild(pWrap);
    }

    // 从安卓 Motion Photo (小米/华为/三星/OPPO/vivo/Pixel 实况照片单文件 JPG) 中无损提取内嵌 MP4 视频轨
    function extractEmbeddedMotionVideoB64(file, cb){
      try {
        var reader = new FileReader();
        reader.onload = function(e){
          try {
            var buf = e.target.result;
            var u8 = new Uint8Array(buf);
            var len = u8.length;
            // 在文件后 95% 区域搜索 MP4 头标识 'ftyp' (0x66 0x74 0x79 0x70)，前面 4 字节为 box size (通常 0x00 0x00 0x00 0x18~0x28)
            var startSearch = Math.max(1024, Math.floor(len * 0.05));
            var mp4Start = -1;
            for (var i = startSearch; i < len - 12; i++){
              if (u8[i] === 0x66 && u8[i+1] === 0x74 && u8[i+2] === 0x79 && u8[i+3] === 0x70){
                var boxLen = (u8[i-4] << 24) | (u8[i-3] << 16) | (u8[i-2] << 8) | u8[i-1];
                if (boxLen >= 16 && boxLen <= 128){
                  mp4Start = i - 4;
                  break;
                }
              }
            }
            if (mp4Start > 0 && (len - mp4Start) > 4096 && (len - mp4Start) < 25 * 1024 * 1024){
              var vBlob = new Blob([u8.subarray(mp4Start)], {type: "video/mp4"});
              var vr = new FileReader();
              vr.onload = function(ve){
                var origReader = new FileReader();
                origReader.onload = function(oe){
                  cb(ve.target.result || "", oe.target.result || "");
                };
                origReader.onerror = function(){ cb(ve.target.result || "", ""); };
                origReader.readAsDataURL(file);
              };
              vr.onerror = function(){ cb("", ""); };
              vr.readAsDataURL(vBlob);
            } else {
              cb("", "");
            }
          } catch(err){ cb("", ""); }
        };
        reader.onerror = function(){ cb("", ""); };
        reader.readAsArrayBuffer(file);
      } catch(err){ cb("", ""); }
    }

    function compressImageFile(file, cb){
      var reader = new FileReader();
      reader.onload = function(evt){
        var img = new Image();
        img.onload = function(){
          var canvas = document.createElement("canvas");
          var maxSide = 1200;
          var w = img.width, h = img.height;
          if (w > maxSide || h > maxSide){
            if (w > h){ h = Math.round(h * maxSide / w); w = maxSide; }
            else { w = Math.round(w * maxSide / h); h = maxSide; }
          }
          canvas.width = w; canvas.height = h;
          canvas.getContext("2d").drawImage(img, 0, 0, w, h);
          cb(canvas.toDataURL("image/jpeg", 0.82));
        };
        img.onerror = function(){
          toast("这张图片读取失败了，可能格式不支持，换个图试试吧 🐾", "err");
        };
        img.src = evt.target.result;
      };
      reader.onerror = function(){
        toast("这张图片读取失败了，请重选一次 🐾", "err");
      };
      reader.readAsDataURL(file);
    }

    function readVideoFileAndPoster(vFile, needPoster, cb){
      if (vFile.size > 25 * 1024 * 1024){
        toast("实况视频不能超过 25MB 哦 🐾", "err");
        return;
      }
      var vReader = new FileReader();
      vReader.onload = function(vevt){
        var videoB64 = vevt.target.result;
        if (!needPoster){
          cb(null, videoB64);
          return;
        }
        var url = URL.createObjectURL(vFile);
        var vid = document.createElement("video");
        vid.preload = "metadata";
        vid.muted = true;
        vid.playsInline = true;
        var done = false;
        function finishPoster(posterB64){
          if (done) return;
          done = true;
          try { URL.revokeObjectURL(url); } catch(e){}
          cb(posterB64, videoB64);
        }
        vid.onloadeddata = function(){
          try { vid.currentTime = Math.min(0.15, (vid.duration || 0) / 2); } catch(e){}
        };
        vid.onseeked = function(){
          try {
            var c = document.createElement("canvas");
            var maxS = 1000;
            var vw = vid.videoWidth || 640, vh = vid.videoHeight || 640;
            if (vw > maxS || vh > maxS){
              if (vw > vh){ vh = Math.round(vh * maxS / vw); vw = maxS; }
              else { vw = Math.round(vw * maxS / vh); vh = maxS; }
            }
            c.width = vw; c.height = vh;
            c.getContext("2d").drawImage(vid, 0, 0, vw, vh);
            finishPoster(c.toDataURL("image/jpeg", 0.82));
          } catch(e){ finishPoster(""); }
        };
        setTimeout(function(){
          if (!done){
            try {
              var c = document.createElement("canvas");
              c.width = vid.videoWidth || 480; c.height = vid.videoHeight || 480;
              c.getContext("2d").drawImage(vid, 0, 0, c.width, c.height);
              finishPoster(c.toDataURL("image/jpeg", 0.82));
            } catch(e){ finishPoster(""); }
          }
        }, 1200);
        vid.src = url;
      };
      vReader.readAsDataURL(vFile);
    }

    files.forEach(function(f){
      var k = baseKey(f.name) || ("_" + Math.random());
      if (f.type.indexOf("image/") === 0) imgMap[k] = f;
      else if (f.type.indexOf("video/") === 0) vidMap[k] = f;
    });

    // 1. 处理所有图片（优先匹配同名视频；若无同名视频，则自动检测并提取安卓 Motion Photo 内嵌实况视频轨并保留原厂实况 JPG！）
    Object.keys(imgMap).forEach(function(k){
      var iFile = imgMap[k];
      var pairedVid = vidMap[k];
      if (pairedVid) delete vidMap[k];
      compressImageFile(iFile, function(compB64){
        if (pairedVid){
          readVideoFileAndPoster(pairedVid, false, function(_, vB64){
            appendPreviewItem({ image: compB64, live_video: vB64 });
          });
        } else {
          extractEmbeddedMotionVideoB64(iFile, function(embeddedVidB64, origMotionJpgB64){
            appendPreviewItem({
              image: origMotionJpgB64 || compB64,
              live_video: embeddedVidB64 || ""
            });
          });
        }
      });
    });

    // 2. 处理单独选择的实况短视频（自动截取首帧做拍立得封面）
    Object.keys(vidMap).forEach(function(k){
      var vFile = vidMap[k];
      readVideoFileAndPoster(vFile, true, function(posterB64, vB64){
        if (posterB64){
          appendPreviewItem({ image: posterB64, live_video: vB64 });
        } else {
          toast("这个视频读取失败了，试试换一张照片或更小的视频 🐾", "err");
        }
      });
    });
  }
});

document.addEventListener("keydown", function(e){
  var pv = $("#ovPhotoViewer");
  var isOpen = pv && pv.style.display !== "none";
  if (e.key === "Escape"){
    if (isOpen){
      closePhotoViewer();
      return;
    }
  }
  if (isOpen && (e.key === "ArrowLeft" || e.key === "ArrowUp")){
    var bP = $("#pvBtnPrev"); if (bP) bP.click();
    return;
  }
  if (isOpen && (e.key === "ArrowRight" || e.key === "ArrowDown")){
    var bN = $("#pvBtnNext"); if (bN) bN.click();
    return;
  }
  if (e.target && e.target.id === "cmtText" && e.key === "Enter"){
    e.preventDefault();
    var btn = document.querySelector('[data-act="send-cmt"]');
    if (btn) btn.click();
  }
});

/* 拍立得大图交互：双指捏合缩放、双击缩放、放大后自由平移拖拽、单指边缘滑动切图 */
(function(){
  var startX = 0, startY = 0;
  var initDist = 0, initScale = 1;
  var isPanning = false, panStartX = 0, panStartY = 0, initialTx = 0, initialTy = 0;
  var lastTapTime = 0;
  var liveHoldTimer = null;

  function getDistance(t1, t2){
    var dx = t1.clientX - t2.clientX;
    var dy = t1.clientY - t2.clientY;
    return Math.sqrt(dx * dx + dy * dy);
  }

  document.addEventListener("touchstart", function(e){
    var pv = $("#ovPhotoViewer");
    if (!pv || pv.style.display === "none") return;

    if (e.touches.length === 2){
      // 双指捏合缩放开始
      initDist = getDistance(e.touches[0], e.touches[1]);
      initScale = pvScale;
      isPanning = false;
    } else if (e.touches.length === 1){
      var now = Date.now();
      if (now - lastTapTime < 280){
        // 双击快速缩放 (1x <-> 2.5x)
        if (pvScale > 1.05){
          resetPvTransform(true);
        } else {
          pvScale = 2.5;
          pvTx = 0;
          pvTy = 0;
          applyPvTransform(true);
        }
        lastTapTime = 0;
        return;
      }
      lastTapTime = now;

      startX = e.touches[0].clientX;
      startY = e.touches[0].clientY;

      clearTimeout(liveHoldTimer);
      if (pvScale <= 1.05){
        liveHoldTimer = setTimeout(function(){
          var curItem = pvPhotosList[pvCurrentIndex];
          var imgUrl = (typeof curItem === "object" && curItem !== null) ? curItem.src : curItem;
          var liveUrl = (typeof curItem === "object" && curItem !== null) ? (curItem.live || "") : "";
          if (liveUrl) playPvLiveVideo();
          if (!window.AndroidApp || window.AndroidApp.saveMediaToGallery){
            showPhotoActionSheet(imgUrl, liveUrl);
          }
        }, 420);
      }

      if (pvScale > 1.05){
        isPanning = true;
        panStartX = e.touches[0].clientX;
        panStartY = e.touches[0].clientY;
        initialTx = pvTx;
        initialTy = pvTy;
      }
    }
  }, {passive:false});

  document.addEventListener("touchmove", function(e){
    clearTimeout(liveHoldTimer);
    var pv = $("#ovPhotoViewer");
    if (!pv || pv.style.display === "none") return;

    if (e.touches.length === 2){
      // 捏合缩放中
      if (e.cancelable) e.preventDefault();
      var currentDist = getDistance(e.touches[0], e.touches[1]);
      if (initDist > 0){
        var newScale = initScale * (currentDist / initDist);
        pvScale = Math.min(Math.max(newScale, 0.8), 5.0);
        applyPvTransform(false);
      }
    } else if (e.touches.length === 1 && isPanning && pvScale > 1.05){
      // 放大状态下的拖拽移动
      if (e.cancelable) e.preventDefault();
      var dx = e.touches[0].clientX - panStartX;
      var dy = e.touches[0].clientY - panStartY;
      pvTx = initialTx + dx;
      pvTy = initialTy + dy;
      applyPvTransform(false);
    }
  }, {passive:false});

  document.addEventListener("touchend", function(e){
    clearTimeout(liveHoldTimer);
    var pv = $("#ovPhotoViewer");
    if (!pv || pv.style.display === "none") return;

    if (e.touches.length === 0){
      if (pvScale < 1.0){
        // 缩小于 1 倍时自动回弹到 1 倍
        resetPvTransform(true);
      } else if (pvScale > 1.05){
        // 放大状态下限制移动边界，防止移出视口
        var maxTx = (window.innerWidth * (pvScale - 1)) / 2 + 60;
        var maxTy = (window.innerHeight * (pvScale - 1)) / 2 + 60;
        pvTx = Math.max(-maxTx, Math.min(maxTx, pvTx));
        pvTy = Math.max(-maxTy, Math.min(maxTy, pvTy));
        applyPvTransform(true);
      }

      // 未放大状态下（pvScale <= 1.05），支持单指左右滑动手势切图
      if (pvScale <= 1.05 && e.changedTouches && e.changedTouches.length === 1 && !isPanning){
        var diffX = e.changedTouches[0].clientX - startX;
        var diffY = e.changedTouches[0].clientY - startY;
        if (Math.abs(diffX) > 45 && Math.abs(diffX) > Math.abs(diffY)){
          if (diffX > 0){
            var bP = $("#pvBtnPrev"); if (bP) bP.click();
          } else {
            var bN = $("#pvBtnNext"); if (bN) bN.click();
          }
        }
      }
      isPanning = false;
      initDist = 0;
    }
  }, {passive:true});

  // 全局分类长按手势：
  // 1) 手账拍立得图片 / 大图 -> 弹出「保存图片到相册」操作面板
  // 2) 课表日程卡片 -> 直接打开该日程的修改窗口
  // 3) 顶部「两人日程 🐾」Logo 标题 -> 弹出切换服务器地址（Android 客户端）
  var globalHoldTimer = null;
  var gStartX = 0, gStartY = 0;
  document.addEventListener("touchstart", function(e){
    if (!e.touches || e.touches.length !== 1) {
      clearTimeout(globalHoldTimer);
      return;
    }
    var pv = $("#ovPhotoViewer");
    if (pv && pv.style.display !== "none") return; // 大图查看器已由上面专属处理
    var t = e.target;
    if (!t || !t.closest) return;

    var polaroidImg = (window.AndroidApp && !window.AndroidApp.saveMediaToGallery) ? null : t.closest(".polaroid-img");
    var evCard = t.closest(".ev, .ev-stack");
    var brandLogo = t.closest(".brand");
    if (!polaroidImg && !evCard && !brandLogo) return;

    gStartX = e.touches[0].clientX;
    gStartY = e.touches[0].clientY;
    clearTimeout(globalHoldTimer);
    globalHoldTimer = setTimeout(function(){
      if (polaroidImg){
        var src = polaroidImg.getAttribute("src") || "";
        var liveUrl = "";
        try {
          var idx = +polaroidImg.getAttribute("data-idx") || 0;
          var list = JSON.parse(decodeURIComponent(polaroidImg.getAttribute("data-list") || "[]"));
          if (list[idx] && typeof list[idx] === "object") liveUrl = list[idx].live || "";
        } catch(err){}
        showPhotoActionSheet(src, liveUrl);
      } else if (evCard){
        var eid = evCard.getAttribute("data-id");
        var edate = evCard.getAttribute("data-date");
        if (eid) openDetail(eid, edate);
      } else if (brandLogo){
        if (window.AndroidApp && window.AndroidApp.openServerSettings){
          window.AndroidApp.openServerSettings();
        }
      }
    }, 480);
  }, {passive:true});

  document.addEventListener("touchmove", function(e){
    if (!globalHoldTimer || !e.touches || !e.touches[0]) return;
    if (Math.abs(e.touches[0].clientX - gStartX) > 12 || Math.abs(e.touches[0].clientY - gStartY) > 12){
      clearTimeout(globalHoldTimer);
    }
  }, {passive:true});

  document.addEventListener("touchend", function(){
    clearTimeout(globalHoldTimer);
  }, {passive:true});

  document.addEventListener("contextmenu", function(e){
    var t = e.target;
    if (!t || !t.closest) return;
    var polaroidImg = t.closest(".polaroid-img");
    var pvImg = t.closest("#pvImage, #pvVideo");
    if (polaroidImg || pvImg){
      e.preventDefault();
      // 若在旧版 v1.7.0 Android 客户端内，原生已弹弹窗；新版 v1.7.1+ 统一弹底部精致菜单
      if (window.AndroidApp && !window.AndroidApp.saveMediaToGallery) return;
      if (polaroidImg){
        var src = polaroidImg.getAttribute("src") || "";
        var liveUrl = "";
        try {
          var idx = +polaroidImg.getAttribute("data-idx") || 0;
          var list = JSON.parse(decodeURIComponent(polaroidImg.getAttribute("data-list") || "[]"));
          if (list[idx] && typeof list[idx] === "object") liveUrl = list[idx].live || "";
        } catch(err){}
        showPhotoActionSheet(src, liveUrl);
      } else {
        var curItem = pvPhotosList[pvCurrentIndex];
        var imgUrl = (typeof curItem === "object" && curItem !== null) ? curItem.src : curItem;
        var liveUrl = (typeof curItem === "object" && curItem !== null) ? (curItem.live || "") : "";
        showPhotoActionSheet(imgUrl, liveUrl);
      }
    }
  });
})();

/* ---------- 自绘日期/时间/下拉选择弹层：替代原生弹层。
   部分安卓内核（小米系等）的原生弹层不读取页面 color-scheme，
   深色页面下依旧白底刺眼；自绘弹层走 App 的 CSS 变量，深浅色天然跟随。 ---------- */
function uiSheetShell(opts){
  /* 防重入：真机 touch 与 click 可能双发，同一时刻只保留一层选择弹层 */
  document.querySelectorAll('.overlay.usheet').forEach(function(old){
    if (old._dismiss) old._dismiss(); else old.remove();
  });
  var ov = document.createElement("div");
  ov.className = "overlay usheet";
  ov.innerHTML = '<div class="modal sheet-modal">' +
    '<div class="sheet-head"><span class="sheet-title">' + (opts.title || "选择") + '</span>' +
    '<button type="button" class="sheet-close" aria-label="关闭">✕</button></div>' +
    opts.bodyHTML +
  '</div>';
  var settled = false;
  var mo = null;
  function finish(){
    if (settled) return;
    settled = true;
    document.removeEventListener("keydown", onKey);
    if (mo) mo.disconnect();
    ov.classList.remove("show");
    setTimeout(function(){ if (ov && ov.parentNode) ov.parentNode.removeChild(ov); }, 180);
  }
  function onKey(e){ if (e.key === "Escape") finish(); }
  ov._dismiss = finish;
  ov.addEventListener("click", function(e){ if (e.target === ov) finish(); });
  ov.querySelector(".sheet-close").addEventListener("click", finish);
  document.addEventListener("keydown", onKey);
  document.body.appendChild(ov);
  /* 视图重渲染即视为失效，弹层随之关闭（与 uiConfirm 同策略） */
  /* 仅在真正切换视图时关闭（消息轮询等局部重绘不该打断选择） */
  var entryView = CURRENT_VIEW;
  mo = new MutationObserver(function(){
    if (typeof CURRENT_VIEW !== "undefined" && CURRENT_VIEW !== entryView) finish();
  });
  mo.observe(document.getElementById("app"), {childList:true});
  requestAnimationFrame(function(){ ov.classList.add("show"); });
  return ov;
}
function uiDateSheet(input){
  if (!input || input.disabled) return;
  var parts = (input.value || todayISO()).split("-");
  var y = Math.min(Math.max(+parts[0] || 2026, 1970), 2100);
  var m = Math.min(Math.max(+parts[1] || 1, 1), 12);
  var selDay = +parts[2] || 0;
  var today = todayISO();
  var ov = uiSheetShell({ title: "选择日期 📅", bodyHTML:
    '<div class="sheet-sub">' +
      '<button type="button" class="sheet-nav" data-nav="-1">‹</button>' +
      '<span class="sheet-month"></span>' +
      '<button type="button" class="sheet-nav" data-nav="1">›</button>' +
    '</div>' +
    '<div class="sheet-wd"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div>' +
    '<div class="sheet-days"></div>' +
    '<div class="sheet-foot"><button type="button" class="sheet-link" data-act="today">回到今天</button></div>'
  });
  var daysEl = ov.querySelector(".sheet-days");
  var monthEl = ov.querySelector(".sheet-month");
  function render(){
    monthEl.textContent = y + " 年 " + m + " 月";
    var offset = (new Date(y, m - 1, 1).getDay() + 6) % 7;
    var dim = new Date(y, m, 0).getDate();
    var html = "";
    for (var i = 0; i < offset; i++) html += "<span></span>";
    for (var d = 1; d <= dim; d++){
      var iso = y + "-" + String(m).padStart(2, "0") + "-" + String(d).padStart(2, "0");
      var cls = "sheet-day" + (iso === today ? " today" : "") + (d === selDay ? " on" : "");
      html += '<button type="button" class="' + cls + '" data-v="' + iso + '">' + d + '</button>';
    }
    daysEl.innerHTML = html;
  }
  daysEl.addEventListener("click", function(e){
    var b = e.target.closest(".sheet-day");
    if (!b) return;
    input.value = b.getAttribute("data-v");
    input.dispatchEvent(new Event("input", {bubbles:true}));
    input.dispatchEvent(new Event("change", {bubbles:true}));
    ov._dismiss();
  });
  ov.querySelectorAll(".sheet-nav").forEach(function(b){
    b.addEventListener("click", function(){
      m += (+b.getAttribute("data-nav"));
      if (m < 1){ m = 12; y--; } else if (m > 12){ m = 1; y++; }
      render();
    });
  });
  ov.querySelector('[data-act="today"]').addEventListener("click", function(){
    var p = todayISO().split("-");
    y = +p[0]; m = +p[1]; selDay = +p[2];
    render();
  });
  render();
}
function uiTimeSheet(input){
  if (!input || input.disabled) return;
  var parts = (input.value || "19:00").split(":");
  var h = Math.min(Math.max(+parts[0] || 0, 0), 23);
  var mi = Math.min(Math.max(+parts[1] || 0, 0), 59);
  var mode = "h";
  var ov = uiSheetShell({ title: "选择时间 ⏰", bodyHTML:
    '<div class="clock-head"><div class="clock-time">' +
      '<button type="button" class="clock-seg clock-hh"></button>' +
      '<span class="clock-colon">:</span>' +
      '<button type="button" class="clock-seg clock-mm"></button>' +
    '</div></div>' +
    '<div class="clock-wrap"><div class="clock-dial"></div></div>' +
    '<div class="sheet-foot clock-foot">' +
      '<button type="button" class="sheet-link clock-clear">清除</button>' +
      '<span style="flex:1"></span>' +
      '<button type="button" class="sheet-link clock-cancel">取消</button>' +
      '<button type="button" class="sheet-ok">设置</button>' +
    '</div>'
  });
  var dial = ov.querySelector(".clock-dial");
  var hhEl = ov.querySelector(".clock-hh");
  var mmEl = ov.querySelector(".clock-mm");
  function pad(n){ return String(n).padStart(2, "0"); }
  function paintSegs(){
    hhEl.textContent = pad(h);
    mmEl.textContent = pad(mi);
    hhEl.classList.toggle("on", mode === "h");
    mmEl.classList.toggle("on", mode === "m");
  }
  function renderDial(){
    var html = "";
    var i, ang, r2, sel;
    if (mode === "h"){
      for (i = 1; i <= 12; i++){
        ang = i * 30 * Math.PI / 180;
        html += '<button type="button" class="clock-n" style="left:' + (118 + 94 * Math.sin(ang) - 15) + 'px;top:' + (118 - 94 * Math.cos(ang) - 15) + 'px">' + i + '</button>';
      }
      for (i = 0; i < 12; i++){
        var innerH = i === 0 ? 0 : 12 + i;
        ang = i * 30 * Math.PI / 180;
        html += '<button type="button" class="clock-n clock-n-in" style="left:' + (118 + 62 * Math.sin(ang) - 14) + 'px;top:' + (118 - 62 * Math.cos(ang) - 14) + 'px">' + pad(innerH) + '</button>';
      }
      r2 = (h === 0 || h > 12) ? 62 : 94;
      ang = (h % 12) * 30
      sel = pad(h);
    } else {
      for (i = 0; i < 60; i += 5){
        ang = i * 6 * Math.PI / 180;
        html += '<button type="button" class="clock-n" style="left:' + (118 + 94 * Math.sin(ang) - 15) + 'px;top:' + (118 - 94 * Math.cos(ang) - 15) + 'px">' + pad(i) + '</button>';
      }
      r2 = 94;
      ang = mi * 6
      sel = pad(mi);
    }
    html += '<div class="clock-hand" style="height:' + r2 + 'px;transform:rotate(' + ang + 'deg)"><div class="clock-knob">' + sel + '</div></div>' +
            '<div class="clock-center"></div>';
    dial.innerHTML = html;
  }
  dial.addEventListener("click", function(e){
    var rect = dial.getBoundingClientRect();
    var dx = e.clientX - (rect.left + 118);
    var dy = e.clientY - (rect.top + 118);
    var r = Math.hypot(dx, dy);
    if (r < 24) return;
    var deg = (Math.atan2(dy, dx) * 180 / Math.PI + 90 + 360) % 360;
    if (mode === "h"){
      var idx = Math.round(deg / 30) % 12;
      h = r < 78 ? (idx === 0 ? 0 : 12 + idx) : (idx === 0 ? 12 : idx);
      mode = "m";
    } else {
      mi = Math.round(deg / 6) % 60;
    }
    paintSegs();
    renderDial();
  });
  hhEl.addEventListener("click", function(){ mode = "h"; paintSegs(); renderDial(); });
  mmEl.addEventListener("click", function(){ mode = "m"; paintSegs(); renderDial(); });
  paintSegs();
  renderDial();
  var clearBtn = ov.querySelector(".clock-clear");
  var cancelBtn = ov.querySelector(".clock-cancel");
  var okBtn = ov.querySelector(".sheet-ok");
  clearBtn.addEventListener("click", function(){
    input.value = "";
    input.dispatchEvent(new Event("input", {bubbles:true}));
    input.dispatchEvent(new Event("change", {bubbles:true}));
    ov._dismiss();
  });
  cancelBtn.addEventListener("click", function(){ ov._dismiss(); });
  okBtn.addEventListener("click", function(){
    input.value = pad(h) + ":" + pad(mi);
    input.dispatchEvent(new Event("input", {bubbles:true}));
    input.dispatchEvent(new Event("change", {bubbles:true}));
    ov._dismiss();
  });
}
function uiSelectSheet(sel){
  if (!sel || sel.disabled) return;
  var opts = Array.prototype.slice.call(sel.options);
  if (!opts.length) return;
  var body = '<div class="sheet-opt-list">' + opts.map(function(o, i){
    return '<button type="button" class="sheet-opt' + (o.selected ? " on" : "") + (o.disabled ? " disabled" : "") + '" data-i="' + i + '">' +
      '<span class="sheet-opt-label">' + esc(o.textContent) + '</span><span class="sheet-dot"></span></button>';
  }).join("") + '</div>';
  var ov = uiSheetShell({ title: sel.getAttribute("aria-label") || "请选择", bodyHTML: body });
  ov.querySelector(".sheet-opt-list").addEventListener("click", function(e){
    var b = e.target.closest(".sheet-opt");
    if (!b || b.classList.contains("disabled")) return;
    sel.value = opts[+b.getAttribute("data-i")].value;
    sel.dispatchEvent(new Event("input", {bubbles:true}));
    sel.dispatchEvent(new Event("change", {bubbles:true}));
    ov._dismiss();
  });
}
function usheetOpenFor(el){
  if (el.tagName === "SELECT") return function(){ uiSelectSheet(el); };
  if (el.type === "date") return function(){ uiDateSheet(el); };
  if (el.type === "time") return function(){ uiTimeSheet(el); };
  return null;
}
/* 原生弹层由 focus/click 的默认行为弹出，pointerdown 上的 preventDefault 拦不住。
   做法：这三类控件统一 readonly + inputmode=none——原生 picker 与软键盘都不再弹，
   点击仍由自绘弹层接管；readonly 不影响 JS 赋值与 change 事件派发。 */
function usheetHarden(el){
  if (!el || !el.tagName) return;
  if (el.tagName === "SELECT"){
    el.dataset.usheet = "1";
    return;
  }
  if (el.type === "date" || el.type === "time"){
    el.dataset.usheet = "1";
    if (!el.readOnly) el.readOnly = true;
    if (el.getAttribute("inputmode") !== "none") el.setAttribute("inputmode", "none");
  }
}
function usheetHardenAll(root){
  (root || document).querySelectorAll('input[type=date],input[type=time],select').forEach(usheetHarden);
}
/* 动态渲染出来的新控件也要加固 */
function usheetHardenTree(node){
  if (!node || node.nodeType !== 1) return;
  if (node.matches && node.matches('input[type=date],input[type=time],select')) usheetHarden(node);
  if (node.querySelectorAll) usheetHardenAll(node);
}
if (window.MutationObserver){
  new MutationObserver(function(muts){
    for (var i = 0; i < muts.length; i++){
      var added = muts[i].addedNodes;
      for (var j = 0; j < added.length; j++) usheetHardenTree(added[j]);
    }
  }).observe(document.body, {childList: true, subtree: true});
}
var _usheetGate = 0;
document.addEventListener("click", function(e){
  var el = e.target.closest ? e.target.closest("input[type=date],input[type=time],select") : null;
  if (!el || el.disabled) return;
  /* select 下拉与 input picker 都在 click 的默认动作里，必须在这里拦 */
  e.preventDefault();
  /* 真机上 touch 与 click 可能先后各触发一次，窗口内只响应一次 */
  var now = Date.now();
  if (now - _usheetGate < 400) return;
  _usheetGate = now;
  var open = usheetOpenFor(el);
  if (!open) return;
  try { open(); } catch (err) {}
}, true);

window.addEventListener("popstate", function(e){
  // 主动关闭弹窗/确认框触发的那次回退，不做任何处理
  if (skipNextPopRender){ skipNextPopRender = false; return; }

  // 0. 确认弹窗在最上层时，物理返回只关闭确认弹窗，不动视图与历史
  var uc = document.querySelector(".overlay.uconfirm.show");
  if (uc && uc._dismiss){ uc._dismiss(); return; }
  // 0.2 自绘选择弹层在最上层时，物理返回只关闭它
  var us = document.querySelector(".overlay.usheet.show");
  if (us && us._dismiss){ us._dismiss(); return; }

  // 0.5 设置页有未保存修改时，返回手势先询问（与底部导航的离开守卫同一策略）
  if (CURRENT_VIEW === "settings" && window.Settings && Settings.isDirty() &&
      !(e.state && e.state.v === "settings")){
    history.pushState({type:"view", v:"settings"}, "");
    uiConfirm({icon:"📝", title:"尚未保存", message:"离开后这些修改会丢失，仍要继续吗？", okText:"离开"}).then(function(ok){
      if (!ok) return;
      Settings.clearDirty();
      history.back();
    });
    return;
  }

  // 1. 若当前有弹窗或照片大图打开，物理返回优先平滑关闭弹窗
  var closed = closeAllModals(true);
  if (closed) return;

  // 2. 若是从其他页面返回，按状态恢复视图
  if (e.state && e.state.v){
    CURRENT_VIEW = e.state.v;
    /* 旅行清单的层级跟历史条目走：条目里没带 trip 就是列表层 */
    if (CURRENT_VIEW === "trips"){
      TRIP_ID = e.state.trip || null;
      TRIP_DETAIL = TRIP_ID ? (TRIP_DETAILS[TRIP_ID] || null) : null;
    }
    render();
  } else if (e.state && e.state.type === "modal"){
    // 返回到周期记录页的条目（前进手势）：恢复记录表单
    if (e.state.id === "cycle2-record" && CURRENT_VIEW === "cycle2" && window.Cycle2) Cycle2.reopenRecord();
    // 其余弹窗条目：只关弹窗，视图保持不动
  } else if (!e.state || !e.state.v){
    // 默认回到最核心的课表主页
    if (CURRENT_VIEW !== "week"){
      CURRENT_VIEW = "week";
      render();
    }
  }
});

ANCHOR = todayISO();

// 1. 服务端直出数据极速水合（0 RTT 秒开）
var initData = window.__INITIAL_DATA__;
if (initData) {
  if (initData.meta) {
    META = initData.meta;
    saveLS("sched_meta", META);
  }
  if (initData.me) {
    ME = initData.me;
    saveLS("sched_me", ME);
    if (window.AndroidApp && window.AndroidApp.saveAuthUid) {
      window.AndroidApp.saveAuthUid(ME.uid);
    }
  }
  if (initData.week) {
    DATA = initData.week;
    saveLS("sched_week_" + ANCHOR, DATA);
    if (DATA.unread_count !== undefined) setUnreadCount(DATA.unread_count);
  }
}

// 2. 本地持久化缓存快速兜底（SWR 离线/弱网秒开）
if (!ME || !DATA) {
  if (!META) META = loadLS("sched_meta");
  if (!ME) ME = loadLS("sched_me");
  if (ME && !DATA) {
    DATA = loadLS("sched_week_" + ANCHOR);
    if (DATA && DATA.unread_count !== undefined) setUnreadCount(DATA.unread_count);
  }
}

// 3. 立即呈现首屏（消除任何空白等待）
if (ME && DATA) {
  render();
  if (CURRENT_VIEW === "week") setTimeout(scrollToToday, 80);
  // 后台静默校验与最新数据重同步
  setTimeout(function(){
    load({silent: true});
    pollMessages();
  }, 120);
} else if (!ME && META) {
  render();
} else {
  // 无 SSR 也无本地缓存时，并行发起拉取，消除串行瀑布
  Promise.all([
    api("/api/meta").then(function(j){ META = j; saveLS("sched_meta", j); }).catch(function(){}),
    api("/api/me").then(function(j){ ME = j; saveLS("sched_me", j); }).catch(function(){})
  ]).then(function(){
    if (ME) {
      if (window.AndroidApp && window.AndroidApp.saveAuthUid) {
        window.AndroidApp.saveAuthUid(ME.uid);
      }
      load().then(function(){
        if (CURRENT_VIEW === "week") setTimeout(scrollToToday, 100);
        pollMessages();
      });
    } else {
      render();
    }
  });
}

  window.handleTargetAction = function(action) {
    if (!action || typeof action !== "string") return;
    var parts = action.split(":");
    var type = parts[0];
    var val = parts[1] || "";

    if (type === "event") {
      var eid = parseInt(val);
      var eDate = parts[2] || "";
      if (!isNaN(eid)) {
        if (CURRENT_VIEW !== "week") {
          CURRENT_VIEW = "week";
          load().then(function(){
            setTimeout(function(){ openDetail(eid, eDate); }, 150);
          });
        } else {
          openDetail(eid, eDate);
        }
      }
    } else if (type === "diary") {
      if (CURRENT_VIEW !== "month") {
        CURRENT_VIEW = "month";
        load().then(function(){
          if (val) setTimeout(function(){ openDayDiaries(val); }, 150);
        });
      } else {
        if (val) openDayDiaries(val);
      }
    } else if (type === "view") {
      if (val && val !== CURRENT_VIEW) {
        CURRENT_VIEW = val;
        load();
      }
    } else if (type === "wish") {
      if (CURRENT_VIEW !== "wishes") {
        CURRENT_VIEW = "wishes";
        load();
      }
    }
  };

  function pollMessages() {
    if (!ME) return;
    if (document.hidden) return; // 后台标签页不发包；setInterval 自会排下一轮，节奏不变
    // 每 15s 只拉轻量的 /api/me 读未读数；完整消息列表仅在信箱页或确有新消息时才拉
    api("/api/me").then(function(res){
      if (!res || res.unread_count === undefined) return;
      var prevCount = UNREAD_COUNT;
      setUnreadCount(res.unread_count);
      if (CURRENT_VIEW === "messages") {
        api("/api/messages").then(function(r2){
          if (r2 && r2.ok){
            MESSAGES_DATA = r2.messages || [];
            renderMessagesList();
          }
        }).catch(function(){});
      } else if (res.unread_count > prevCount && prevCount >= 0) {
        api("/api/messages").then(function(r2){
          if (r2 && r2.ok){
            var latest = (r2.messages && r2.messages[0]) ? r2.messages[0] : null;
            if (latest) {
              toast("🔔 " + latest.title + "\n" + latest.content);
            } else {
              toast("🐾 收到新消息提醒！");
            }
          }
        }).catch(function(){});
      }
    }).catch(function(){});
  }
  setInterval(pollMessages, 15000);

/* 月历左右滑动切换上/下月（只认月历格子内起始的横滑，弹窗与纵向滚动不受影响） */
var swipeX = 0, swipeY = 0, swipeOn = false;
document.addEventListener("touchstart", function(e){
  swipeOn = false;
  if (CURRENT_VIEW !== "month") return;
  if (!e.touches || e.touches.length !== 1) return;
  var grid = e.target.closest && e.target.closest(".month-grid");
  if (!grid) return;
  swipeX = e.touches[0].clientX;
  swipeY = e.touches[0].clientY;
  swipeOn = true;
}, {passive:true});
document.addEventListener("touchend", function(e){
  if (!swipeOn) return;
  swipeOn = false;
  if (!e.changedTouches || !e.changedTouches.length) return;
  var dx = e.changedTouches[0].clientX - swipeX;
  var dy = e.changedTouches[0].clientY - swipeY;
  if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
  var h = CLICK_ACTIONS[dx < 0 ? "month-next" : "month-prev"];
  if (h) h(null, e);
}, {passive:true});

/* PWA：注册 Service Worker（Android 原生壳内不用，通知走原生桥） */
if ("serviceWorker" in navigator && !window.AndroidApp){
  navigator.serviceWorker.register("/sw.js").catch(function(){});
}
