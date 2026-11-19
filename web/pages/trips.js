/* 旅行清单页面：列表、打包状态与页内动作。 */
var Trips = (function(){
  /* =========================================================================
     旅行清单：一次旅行一份打包清单，按类别分组，物品 + 数量 + 备注
     ========================================================================= */
  var TRIPS = null, TRIP_DETAIL = null, TRIP_ID = null, TRIP_DETAILS = {};
  var tripPanel = null, tripFilter = "all", tripSearch = "", tripHidePacked = false;
  var tripClosed = {}, tripPending = {}, tripError = "", tripLoading = null;

  function tripRange(t){
    if (!t.start_date && !t.end_date) return "日期待定";
    if (t.start_date === t.end_date) return t.start_date;
    if (t.start_date && t.end_date){
      return t.start_date + " 至 " + (t.start_date.slice(0, 4) === t.end_date.slice(0, 4) ? t.end_date.slice(5) : t.end_date);
    }
    return (t.start_date || t.end_date) + (t.start_date ? " 出发" : " 返程");
  }

  function tripBar(total, packed){
    return '<progress class="trip-progress" aria-label="行李打包进度" value="' + packed + '" max="' + (total || 1) + '"></progress>';
  }

  function tripStatus(t){
    var labels = {ongoing:"旅途中", upcoming:"待出发", done:"已结束"};
    return '<span class="trip-stamp trip-stamp-' + t.status + '">' + labels[t.status] + '</span>';
  }

  function tripMark(){
    return '<svg class="trip-mark" viewBox="0 0 112 92" fill="none" aria-hidden="true">' +
      '<path d="M11 79C2 58 27 46 44 62c17 16 48 23 57 2 6-14-5-26-23-30" stroke="currentColor" stroke-width="1.5" stroke-dasharray="3 5"/>' +
      '<rect x="43" y="33" width="31" height="43" rx="5" transform="rotate(-12 43 33)" fill="var(--trip-paper)" stroke="currentColor" stroke-width="1.8"/>' +
      '<path d="m51 31-1-5 10-2 1 5m-6 12 5 24m4-26 5 24M27 15v10m-5-5h10m60 59v8m-4-4h8" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/>' +
      '<circle cx="84" cy="24" r="14" stroke="currentColor" stroke-width="1.4"/><path d="m78 24 7-2 2-5 2 1-1 5 4 2-1 2-5-1-4 4-2-1 2-4-4 1z" fill="currentColor"/>' +
      '</svg>';
  }

  function tripResetView(){
    tripPanel = null;
    tripFilter = "all";
    tripSearch = "";
    tripHidePacked = false;
    tripClosed = {};
    tripError = "";
  }

  function renderTripsPage(){
    var html = renderTopBar() + diarySegHTML("trips") + '<div class="page-container"><main class="trip-page">';
    if (TRIP_ID && !TRIP_DETAIL){
      html += '<button type="button" class="trip-btn trip-plain" data-act="trip-back">‹ 全部旅行</button>' + tripLoadHTML();
    } else html += TRIP_DETAIL ? tripDetailHTML() : tripListHTML();
    setAppHTML(html + '</main></div>' + bottomNavHTML() + modalsHTML());
    tripTrackGroups();
    tripBindSearch();
    if (TRIP_DETAIL && tripPanel) tripPlaceForm();
    else tripBindForm($("#trip_form"));
    if (!tripError && !tripLoading){
      if (TRIP_ID && !TRIP_DETAIL) loadTrip(TRIP_ID);
      else if (!TRIP_ID && !TRIPS) loadTrips();
    }
  }

  function tripLoadHTML(){
    if (tripError) return '<div class="trip-empty"><strong>' + esc(tripError) + '</strong><button class="trip-btn" data-act="trip-retry">重新加载</button></div>';
    return '<div class="trip-empty" role="status">正在翻开旅行手帐…</div>';
  }

  function loadTrips(){
    var req = api("/api/trips");
    tripLoading = req;
    return req.then(function(j){
      if (tripLoading !== req) return;
      TRIPS = j;
    }).catch(function(e){
      if (tripLoading === req && e.message !== "unauth") tripError = e.message;
    }).finally(function(){
      if (tripLoading !== req) return;
      tripLoading = null;
      if (!TRIP_ID && CURRENT_VIEW === "trips" && !tripPanel) render();
    });
  }

  function loadTrip(id){
    if (!id) return Promise.resolve();
    var req = api("/api/trips/detail?id=" + id);
    tripLoading = req;
    return req.then(function(j){
      if (tripLoading !== req || TRIP_ID !== id || CURRENT_VIEW !== "trips") return;
      j.items = j.items.map(function(item){ return tripPending[item.id] || item; });
      TRIP_DETAIL = TRIP_DETAILS[id] = j;
      if ($("#trip_groups")) tripUpdateDetail();
      else render();
      return j;
    }).catch(function(e){
      if (tripLoading !== req || TRIP_ID !== id || e.message === "unauth") return;
      if (!TRIP_DETAIL){ tripError = e.message; render(); }
      else toast(e.message, "err");
    }).finally(function(){
      if (tripLoading !== req) return;
      tripLoading = null;
    });
  }

  function tripListHTML(){
    var html = '<header class="trip-hero"><div><p class="trip-kicker">TRAVEL JOURNAL</p><h1>旅行清单</h1>' +
      '<p class="trip-lede">一起收拾，然后出发。</p></div>' + tripMark() +
      '<button class="trip-btn trip-primary" data-act="trip-new-toggle" aria-expanded="' + (tripPanel === "new") + '">＋ 新建旅行</button></header>';
    if (tripPanel === "new") html += tripFormHTML();
    if (!TRIPS) return html + tripLoadHTML();
    var active = TRIPS.trips.filter(function(t){ return t.status !== "done"; });
    var past = TRIPS.trips.filter(function(t){ return t.status === "done"; });
    function section(title, list){
      if (!list.length) return "";
      return '<section class="trip-section"><div class="trip-section-head"><h2>' + title + '</h2><span>' + list.length + ' 趟</span></div><div class="trip-tickets">' +
        list.map(function(t){
          var distinct = t.destination && t.title && t.destination !== t.title;
          return '<button class="trip-ticket" data-act="trip-open" data-id="' + t.id + '">' +
            '<span class="trip-ticket-top"><span class="trip-ticket-name">' + esc(t.title || t.destination) + '</span>' + tripStatus(t) + '</span>' +
            '<span class="trip-ticket-date">' + (distinct ? esc(t.destination) + ' · ' : '') + esc(tripRange(t)) + '</span>' +
            '<span class="trip-ticket-foot"><span>' + (t.total ? '已打包 ' + t.packed + ' / ' + t.total : '还没添加物品') + '</span><span aria-hidden="true">→</span></span>' +
            tripBar(t.total, t.packed) + '</button>';
        }).join("") + '</div></section>';
    }
    if (active.length || past.length) return html + section("接下来的旅行", active) + section("走过的旅程", past);
    return html + '<div class="trip-empty"><strong>准备你们的第一趟旅行</strong><p>从常用模板开始，或建一份空白清单。</p></div>';
  }

  function tripUserNames(){
    var out = {a: "小金毛", b: "小白狗"};
    ((META && META.users) || []).forEach(function(u){
      if (u && (u.uid === "a" || u.uid === "b") && u.name) out[u.uid] = u.name;
    });
    return out;
  }

  function tripQty(item, side){ return String(item["qty_" + side] || "").trim(); }
  function tripMe(){ return ME && ME.uid === "b" ? "b" : "a"; }
  function tripRemaining(packed, total){
    return !total ? "清单待整理" : packed === total ? "都装好了，可以出发" : "还剩 " + (total - packed) + " 件";
  }

  function tripHeroHTML(){
    var t = TRIP_DETAIL.trip, s = TRIP_DETAIL.stats;
    var distinct = t.destination && t.title && t.destination !== t.title;
    return '<header class="trip-hero trip-detail-hero"><div><p class="trip-kicker">PACKING LIST</p><h1>' + esc(t.title || t.destination) + '</h1>' +
      '<p class="trip-lede">' + (distinct ? esc(t.destination) + ' · ' : '') + esc(tripRange(t)) + '</p></div>' + tripStatus(t) +
      '<div class="trip-hero-progress"><div class="trip-progress-note"><strong>已打包 <span data-role="trip-packed">' + s.packed + '</span><span class="trip-progress-total"> / <span data-role="trip-total">' + s.total + '</span> 件</span></strong>' +
      '<span data-role="trip-remaining" aria-live="polite">' + tripRemaining(s.packed, s.total) + '</span></div>' + tripBar(s.total, s.packed) + '</div></header>';
  }

  function tripControlsHTML(){
    if (!TRIP_DETAIL.items.length) return "";
    var me = tripMe(), options = [["all", "全部"], [me, "我带"], [me === "a" ? "b" : "a", "对方带"]];
    return '<div class="trip-controls"><div class="trip-filters" role="group" aria-label="按携带人筛选">' + options.map(function(o){
      return '<button class="trip-filter" data-act="trip-filter" data-side="' + o[0] + '" aria-pressed="' + (tripFilter === o[0]) + '">' + o[1] + '</button>';
    }).join("") + '</div><label class="trip-search"><span aria-hidden="true">⌕</span><input id="trip_search" type="search" aria-label="搜索物品" placeholder="搜索物品" value="' + esc(tripSearch) + '"></label>' +
    '<button class="trip-filter trip-hide" data-act="trip-hide" aria-pressed="' + tripHidePacked + '"><span aria-hidden="true">' + (tripHidePacked ? '✓' : '') + '</span> 隐藏已打包</button></div>';
  }

  function tripDetailHTML(){
    var t = TRIP_DETAIL.trip;
    return '<div class="trip-nav"><button class="trip-btn trip-plain" data-act="trip-back">‹ 全部旅行</button>' +
      '<button class="trip-btn" data-act="trip-edit">编辑旅行</button></div>' + tripHeroHTML() +
      '<div class="trip-tools"><h2>行李清单</h2><button class="trip-btn trip-primary" data-act="trip-item-new">＋ 添加物品</button></div>' +
      '<div id="trip_panel"></div><div id="trip_controls">' + tripControlsHTML() + '</div>' +
      '<div id="trip_groups" class="trip-groups">' + tripGroupsHTML() + '</div>' +
      '<footer class="trip-footer"><button class="trip-btn" data-act="trip-copy-last" data-id="' + t.id + '">复制上一份清单</button>' +
      '<button class="trip-btn trip-danger" data-act="trip-delete" data-id="' + t.id + '">删除这趟旅行</button></footer>';
  }

  function tripGroupItems(){
    var grouped = {}, matched = {}, q = tripSearch.trim().toLowerCase();
    TRIP_DETAIL.items.forEach(function(i){
      if (tripFilter !== "all" && !tripQty(i, tripFilter)) return;
      if (q && i.name.toLowerCase().indexOf(q) === -1) return;
      var packed = tripFilter === "all" ? i.is_packed : i["packed_" + tripFilter];
      var cat = i.category;
      if (!matched[cat]) matched[cat] = {total:0, packed:0};
      matched[cat].total++;
      if (packed) matched[cat].packed++;
      if (!tripHidePacked || !packed) (grouped[cat] || (grouped[cat] = [])).push(i);
    });
    return {grouped:grouped, matched:matched};
  }

  function tripGroupsHTML(){
    var result = tripGroupItems(), names = tripUserNames(), cats = TRIP_DETAIL.categories;
    var marks = {doc:"证", toilet:"洗", skin:"护", cloth:"衣", tech:"电", med:"药", other:"杂"};
    var html = cats.map(function(c){
      var cat = c[0], rows = result.grouped[cat];
      if (!rows) return "";
      var s = result.matched[cat];
      return '<details class="trip-group" data-trip-category="' + cat + '"' + (tripClosed[cat] ? '' : ' open') + '>' +
        '<summary><span class="trip-category-mark" aria-hidden="true">' + marks[cat] + '</span><span class="trip-category-name">' + esc(c[1]) + '</span>' +
        '<span class="trip-count" data-cat-count="' + cat + '">' + s.packed + ' / ' + s.total + '</span><span class="trip-chevron" aria-hidden="true"></span></summary>' +
        rows.map(function(i){ return tripRowHTML(i, names); }).join("") +
        '<button class="trip-btn trip-group-add" data-act="trip-item-new" data-cat="' + cat + '">＋ 添加' + esc(c[1]) + '</button></details>';
    }).join("");
    if (html) return html;
    if (!TRIP_DETAIL.items.length){
      return '<div class="trip-empty"><strong>这趟还没开始收拾</strong><p>加一件想带的东西，或把上一份清单带过来。</p></div>';
    }
    var allDone = tripHidePacked && Object.keys(result.matched).length;
    return '<div class="trip-empty"><strong>' + (allDone ? '这一页都装好了' : '没有符合条件的物品') + '</strong>' +
      '<button class="trip-btn" data-act="trip-clear-filters">查看全部物品</button></div>';
  }

  function tripControlHTML(item, side, label, qty){
    var on = side === "all" ? item.is_packed : item["packed_" + side];
    var mixed = side === "all" && !on && (item.packed_a || item.packed_b);
    var box = '<span class="trip-box" aria-hidden="true">' + (mixed ? '−' : on ? '✓' : '') + '</span>';
    return '<button class="trip-check ' + (qty ? 'trip-person trip-person-' + side : 'trip-check-all') + '" role="checkbox" aria-checked="' + (mixed ? 'mixed' : !!on) +
      '" aria-label="' + esc(item.name + ' · ' + label) + '"' + (qty ? '' : ' title="' + esc(label) + '"') +
      ' data-act="trip-toggle" data-id="' + item.id + '" data-side="' + side + '"' + (tripPending[item.id] ? ' aria-disabled="true"' : '') + '>' + box +
      (qty ? '<span class="trip-person-name">' + esc(label) + '</span><span class="trip-person-qty">' + esc(qty) + '</span>' : label === '两人一起勾' ? '<span class="trip-both-label" aria-hidden="true">一起</span>' : '') + '</button>';
  }

  function tripRowHTML(i, names){
    var qa = tripQty(i, "a"), qb = tripQty(i, "b"), both = qa && qb;
    var group = both || (!qa && !qb);
    return '<div class="trip-row trip-row-' + (group ? 'group' : 'single') + (i.is_packed ? ' trip-packed' : '') + '" data-row="' + i.id + '">' +
      (group ? tripControlHTML(i, "all", both ? "两人一起勾" : "整项打包") : '') +
      '<div class="trip-row-content"><div class="trip-name">' + esc(i.name) + '</div>' +
      (qa || qb ? '<div class="trip-people">' + (qa ? tripControlHTML(i, "a", names.a, qa) : '') + (qb ? tripControlHTML(i, "b", names.b, qb) : '') + '</div>' : '') + '</div>' +
      '<button class="trip-row-edit" data-act="trip-item-edit" data-id="' + i.id + '" aria-label="编辑' + esc(i.name) + '">编辑</button></div>';
  }

  function tripFormHTML(){
    var editingTrip = tripPanel === "edit", newTrip = tripPanel === "new";
    var item = typeof tripPanel === "object" && tripPanel.item;
    var cats = TRIP_DETAIL ? TRIP_DETAIL.categories : [];
    var heading = newTrip ? "新建旅行" : editingTrip ? "编辑旅行" : item ? "编辑物品" : "添加物品";
    var html = '<form class="trip-form" id="trip_form" aria-label="' + heading + '"><h2>' + heading + '</h2>';
    if (editingTrip || newTrip){
      var t = editingTrip ? TRIP_DETAIL.trip : {};
      html += '<label>目的地<input id="trip_dest" maxlength="40" required placeholder="比如：青岛" value="' + esc(t.destination || t.title) + '"></label>' +
        '<div class="trip-form-pair"><label>出发日期<input id="trip_start" type="date" value="' + esc(t.start_date) + '"></label>' +
        '<label>返程日期<input id="trip_end" type="date" value="' + esc(t.end_date) + '"></label></div>' +
        (newTrip ? '<label class="trip-template"><input id="trip_tpl" type="checkbox" checked>套用常用物品模板</label>' : '');
    } else {
      var names = tripUserNames(), me = tripMe(), cat = item ? item.category : tripPanel.category;
      html += '<label>物品名称<input id="ti_name" maxlength="40" required placeholder="比如：充电器" value="' + esc(item && item.name || '') + '"></label>' +
        '<label>分类<select id="ti_cat" aria-label="分类">' + cats.map(function(c){
          return '<option value="' + c[0] + '"' + (c[0] === cat ? ' selected' : '') + '>' + esc(c[1]) + '</option>';
        }).join("") + '</select></label><div class="trip-form-pair">' + ["a", "b"].map(function(side){
          return '<label>' + esc(names[side]) + ' 带<input id="ti_q' + side + '" maxlength="10" placeholder="例如 1 件" value="' + esc(item ? tripQty(item, side) : me === side ? '1' : '') + '"></label>';
        }).join("") + '</div><p class="trip-form-hint">留空表示不用带；也可以写「2 件」或「用你的」。</p>';
    }
    return html + '<div class="trip-form-actions"><button type="submit" class="trip-btn trip-primary">' + (newTrip ? '建清单' : '保存') + '</button>' +
      '<button type="button" class="trip-btn" data-act="trip-panel-close">取消</button>' +
      (item ? '<button type="button" class="trip-btn trip-danger" data-act="trip-item-del" data-id="' + item.id + '">删除物品</button>' : '') + '</div></form>';
  }

  function tripFormValue(id){ return String(document.getElementById(id).value || "").trim(); }

  function tripAction(action, body){
    body.action = action;
    return api("/api/trips", {method:"POST", body:body});
  }

  function tripBindSearch(){
    var search = $("#trip_search");
    if (search) search.addEventListener("input", function(){ tripSearch = search.value; tripRenderGroups(); });
  }

  function tripBindForm(form){
    if (form) form.addEventListener("submit", function(e){ e.preventDefault(); tripSaveForm(form); });
  }

  function tripEditButton(id){ return $('[data-act="trip-item-edit"][data-id="' + id + '"]'); }

  function tripPlaceForm(form){
    if (!tripPanel) return;
    var host = $("#trip_panel");
    var edit = tripPanel.item && tripEditButton(tripPanel.item.id);
    var add = tripPanel.inline && $('[data-trip-category="' + tripPanel.category + '"] .trip-group-add');
    var anchor = edit && edit.parentElement || add;
    if (!form){
      host.innerHTML = tripFormHTML();
      form = $("#trip_form");
      tripBindForm(form);
    }
    if (anchor && anchor.getClientRects().length) anchor.after(form);
    else if (form.parentElement !== host) host.appendChild(form);
  }

  function tripSetPanel(panel, trigger){
    var form = $("#trip_form");
    if (form && form.dataset.busy) return;
    var previous = tripPanel;
    if (trigger && typeof panel === "object" && panel){
      panel.anchor = tripCapturePosition(trigger);
      panel.inline = !!trigger.dataset.cat;
    }
    tripPanel = panel;
    if (!TRIP_DETAIL){
      render();
      form = $("#trip_form");
    } else {
      if (form) form.remove();
      if (panel) tripPlaceForm();
      form = $("#trip_form");
    }
    if (form){
      var input = form.querySelector("input");
      input.focus();
    } else if (previous && previous.anchor) tripRestorePosition(previous.anchor, true);
  }

  function tripCapturePosition(target){
    var buttons = Array.from(document.querySelectorAll('.trip-row-edit')).filter(function(el){ return el.getClientRects().length; });
    var row = target && target.closest('.trip-row');
    var index = row ? buttons.indexOf(row.querySelector('.trip-row-edit')) : -1;
    if (index < 0) index = buttons.findIndex(function(el){ return el.getBoundingClientRect().bottom > 72; });
    var ordered = index < 0 ? [] : buttons.slice(index, index + 2).concat(buttons.slice(Math.max(0, index - 1), index));
    return {
      y:window.scrollY,
      rows:ordered.map(function(el){ return {id:el.dataset.id, top:el.getBoundingClientRect().top}; })
    };
  }

  function tripRestorePosition(position, focus){
    if (!position) return null;
    for (var i = 0; i < position.rows.length; i++){
      var mark = position.rows[i], el = tripEditButton(mark.id);
      if (!el || !el.getClientRects().length) continue;
      var top = Math.max(84, Math.min(mark.top, window.innerHeight - 140));
      window.scrollBy(0, el.getBoundingClientRect().top - top);
      if (focus) el.focus({preventScroll:true});
      return el.parentElement;
    }
    window.scrollTo(0, position.y);
    if (focus){
      var button = $('.trip-tools [data-act="trip-item-new"]');
      if (button) button.focus({preventScroll:true});
    }
    return null;
  }

  function tripRevealItem(item){
    var edit = tripEditButton(item.id);
    if (!edit) return false;
    edit.closest('details').open = true;
    edit.scrollIntoView({block:"nearest"});
    edit.focus({preventScroll:true});
    return true;
  }

  function tripResult(text, item, near){
    var result = $(".trip-result");
    if (result) result.remove();
    result = document.createElement("div");
    result.className = "trip-result";
    result.innerHTML = '<span role="status">' + esc(text) + '</span>' + (item ?
      '<button class="trip-btn" data-act="trip-reveal-item" data-id="' + item.id + '">查看这件物品</button>' : '');
    if (near) near.after(result);
    else $("#trip_groups").before(result);
  }

  function tripResetFilters(){
    tripFilter = "all"; tripSearch = ""; tripHidePacked = false;
    $("#trip_controls").innerHTML = tripControlsHTML();
    tripBindSearch();
    tripRenderGroups();
  }

  function tripOpen(id){
    if (TRIP_ID === id) return;
    tripResetView();
    tripLoading = null;
    TRIP_ID = id;
    TRIP_DETAIL = TRIP_DETAILS[id] || null;
    history.pushState({type:"view", v:"trips", trip:id, tripBack:history.state && history.state.v === "trips"}, "");
    render();
    if (TRIP_DETAIL) loadTrip(id);
  }

  function tripSaveForm(form){
    if (form.dataset.busy) return;
    var panel = tripPanel, id = TRIP_ID, action, body;
    if (panel === "new" || panel === "edit"){
      action = panel === "new" ? "create" : "update";
      var dest = tripFormValue("trip_dest");
      var t = TRIP_DETAIL && TRIP_DETAIL.trip;
      body = {id:id, destination:dest, start_date:tripFormValue("trip_start"), end_date:tripFormValue("trip_end")};
      if (t && t.title && t.destination && t.title !== t.destination) body.title = t.title;
      if (panel === "new") body.use_template = $("#trip_tpl").checked;
    } else {
      action = panel.item ? "item_update" : "item_add";
      body = {id:panel.item && panel.item.id, trip_id:id, name:tripFormValue("ti_name"), category:tripFormValue("ti_cat"),
              qty_a:tripFormValue("ti_qa"), qty_b:tripFormValue("ti_qb")};
    }
    var name = $(panel === "new" || panel === "edit" ? "#trip_dest" : "#ti_name");
    if (!name.value.trim()){ markInputErr(name); name.focus(); return; }
    var before = new Set(TRIP_DETAIL ? TRIP_DETAIL.items.map(function(i){ return i.id; }) : []);
    form.dataset.busy = "1";
    var submit = form.querySelector('[type="submit"]');
    submit.disabled = true;
    tripAction(action, body).then(function(j){
      TRIPS = null;
      if (CURRENT_VIEW !== "trips" || TRIP_ID !== id || tripPanel !== panel) return;
      toast(action === "create" ? "清单建好了" : "已保存");
      tripPanel = null;
      if (action === "create"){ tripOpen(j.id); return; }
      form.remove();
      if (body.category) delete tripClosed[body.category];
      return loadTrip(id).then(function(detail){
        if (!detail || tripPanel) return;
        var item = detail.items.find(function(i){
          return body.id ? i.id === body.id : !before.has(i.id) && i.name === body.name && i.category === body.category;
        });
        var near = tripRestorePosition(panel.anchor, !!(panel.item && item && item.category === panel.item.category));
        if (!item) return;
        if (!tripEditButton(item.id)){
          tripResult('「' + item.name + '」已保存，当前筛选暂未显示。', item, near);
        } else if (!panel.item || item.category !== panel.item.category){
          tripRevealItem(item);
        }
      });
    }).catch(function(e){ toast(e.message, "err"); }).finally(function(){ delete form.dataset.busy; submit.disabled = false; });
  }

  function tripTrackGroups(){
    document.querySelectorAll("[data-trip-category]").forEach(function(el){
      el.addEventListener("toggle", function(){ if (el.isConnected) tripClosed[el.dataset.tripCategory] = !el.open; });
    });
  }

  function tripRenderGroups(keepPosition){
    var groups = $("#trip_groups");
    if (!groups) return;
    var position = keepPosition && tripCapturePosition(document.activeElement);
    var form = $("#trip_form"), active = document.activeElement;
    var inForm = form && form.contains(active);
    var row = active.closest('.trip-row');
    var toggleSide = row && active.dataset.side;
    var result = groups.querySelector('.trip-result');
    if (result) groups.before(result);
    groups.innerHTML = tripGroupsHTML();
    tripTrackGroups();
    if (form){
      tripPlaceForm(form);
      if (inForm && document.activeElement !== active) active.focus({preventScroll:true});
    }
    if (position){
      var near = tripRestorePosition(position, false);
      if (row){
        var button = near && (toggleSide && near.querySelector('[data-side="' + toggleSide + '"]') || near.querySelector('.trip-row-edit'));
        if (button) button.focus({preventScroll:true});
      }
    }
  }

  function tripUpdateDetail(){
    var hero = $('.trip-detail-hero');
    var title = TRIP_DETAIL.trip.title || TRIP_DETAIL.trip.destination;
    var t = TRIP_DETAIL.trip;
    var distinct = t.destination && t.title && t.destination !== t.title;
    var status = hero.querySelector('.trip-stamp');
    hero.querySelector('h1').textContent = title;
    hero.querySelector('.trip-lede').textContent = (distinct ? t.destination + ' · ' : '') + tripRange(t);
    status.outerHTML = tripStatus(t);
    if (!!$("#trip_search") !== !!TRIP_DETAIL.items.length){
      $("#trip_controls").innerHTML = tripControlsHTML();
      tripBindSearch();
    }
    tripRenderGroups(true);
    tripRefreshProgress();
  }

  function tripSetRowPacked(item){
    var row = document.querySelector('[data-row="' + item.id + '"]');
    if (!row) return;
    row.classList.toggle("trip-packed", !!item.is_packed);
    row.querySelectorAll(".trip-check").forEach(function(btn){
      var side = btn.dataset.side;
      var on = side === "all" ? item.is_packed : item["packed_" + side];
      var mixed = side === "all" && !on && (item.packed_a || item.packed_b);
      btn.setAttribute("aria-checked", mixed ? "mixed" : String(!!on));
      if (tripPending[item.id]) btn.setAttribute("aria-disabled", "true");
      else btn.removeAttribute("aria-disabled");
      btn.querySelector(".trip-box").textContent = mixed ? "−" : on ? "✓" : "";
    });
  }

  function tripRefreshProgress(){
    if (!TRIP_DETAIL) return;
    var items = TRIP_DETAIL.items, packed = items.filter(function(i){ return i.is_packed; }).length;
    TRIP_DETAIL.stats = {packed:packed, total:items.length};
    var put = function(sel, txt){ var el = document.querySelector(sel); if (el) el.textContent = txt; };
    put('[data-role="trip-packed"]', packed);
    put('[data-role="trip-total"]', items.length);
    put('[data-role="trip-remaining"]', tripRemaining(packed, items.length));
    var bar = document.querySelector(".trip-hero-progress progress");
    if (bar){ bar.max = items.length || 1; bar.value = packed; }
    var matched = tripGroupItems().matched;
    Object.keys(matched).forEach(function(c){
      put('[data-cat-count="' + c + '"]', matched[c].packed + " / " + matched[c].total);
    });
  }

  function tripToggle(el){
    var id = Number(el.dataset.id), side = el.dataset.side, tripId = TRIP_ID;
    var item = TRIP_DETAIL.items.find(function(i){ return i.id === id; });
    if (!item || tripPending[id]) return;
    var form = $("#trip_form");
    if (form && form.dataset.busy && tripPanel && tripPanel.item && tripPanel.item.id === id) return;
    var prev = {packed_a:item.packed_a, packed_b:item.packed_b, is_packed:item.is_packed};
    var qa = tripQty(item, "a"), qb = tripQty(item, "b");
    var next = Number(!(side === "all" ? item.is_packed : item["packed_" + side]));
    if (side !== "b" && qa) item.packed_a = next;
    if (side !== "a" && qb) item.packed_b = next;
    item.is_packed = qa || qb ? Number((!qa || item.packed_a) && (!qb || item.packed_b)) : next;
    tripPending[id] = item;
    tripSetRowPacked(item);
    tripRefreshProgress();
    tripAction("item_toggle", {id:id, side:side, packed:next}).then(function(j){
      item.packed_a = j.packed_a;
      item.packed_b = j.packed_b;
      item.is_packed = j.is_packed;
      TRIPS = null;
    }).catch(function(e){
      Object.assign(item, prev);
      toast(e.message, "err");
    }).finally(function(){
      delete tripPending[id];
      if (TRIP_ID !== tripId || !TRIP_DETAIL || CURRENT_VIEW !== "trips") return;
      tripSetRowPacked(item);
      tripRefreshProgress();
      if (tripHidePacked && (tripFilter === "all" ? item.is_packed : item["packed_" + tripFilter])) tripRenderGroups(true);
    });
  }

  function restore(id){
    tripResetView();
    tripLoading = null;
    TRIP_ID = id || null;
    TRIP_DETAIL = TRIP_ID ? (TRIP_DETAILS[TRIP_ID] || null) : null;
    if (!TRIP_ID) TRIPS = null;
  }

  function leave(){
    tripResetView();
    tripLoading = null;
    TRIP_ID = null;
    TRIP_DETAIL = null;
    TRIPS = null;
  }

  function refresh(){
    if (TRIP_DETAIL) loadTrip(TRIP_ID);
  }

  function goBack(){
    if (history.state && history.state.trip && history.state.tripBack) history.back();
    else {
      history.replaceState({type:"view", v:"trips"}, "");
      leave();
      render();
    }
  }

  return {
    render: renderTripsPage,
    hasDetail: function(){ return !!TRIP_ID; },
    leave: leave,
    restore: restore,
    refresh: refresh,
    goBack: goBack,
    actions: {
      "trip-new-toggle": function(){ tripSetPanel(tripPanel === "new" ? null : "new"); },
      "trip-edit": function(){ tripSetPanel(tripPanel === "edit" ? null : "edit"); },
      "trip-panel-close": function(){ tripSetPanel(null); },
      "trip-open": function(el){ tripOpen(Number(el.dataset.id)); },
      "trip-retry": function(){ tripError = ""; render(); },
      "trip-back": goBack,
      "trip-filter": function(el){
        tripFilter = el.dataset.side;
        document.querySelectorAll('[data-act="trip-filter"]').forEach(function(btn){
          btn.setAttribute("aria-pressed", String(btn === el));
        });
        tripRenderGroups();
      },
      "trip-hide": function(el){
        tripHidePacked = !tripHidePacked;
        el.setAttribute("aria-pressed", String(tripHidePacked));
        el.querySelector("span").textContent = tripHidePacked ? "✓" : "";
        tripRenderGroups();
      },
      "trip-clear-filters": function(){
        tripResetFilters();
        $("#trip_search").focus();
      },
      "trip-reveal-item": function(el){
        var item = TRIP_DETAIL.items.find(function(i){ return i.id === Number(el.dataset.id); });
        el.closest('.trip-result').remove();
        if (!item) return;
        if (!tripEditButton(item.id)) tripResetFilters();
        tripRevealItem(item);
      },
      "trip-toggle": function(el){ tripToggle(el); },
      "trip-item-new": function(el){ tripSetPanel({category:el.dataset.cat || "other"}, el); },
      "trip-item-edit": function(el){
        var item = TRIP_DETAIL.items.find(function(i){ return i.id === Number(el.dataset.id); });
        if (item && !tripPending[item.id]) tripSetPanel({item:item}, el);
      },
      "trip-delete": function(el){
        var id = Number(el.dataset.id);
        uiConfirm({icon:"✈️", title:"删除整份清单？", message:"清单上的所有物品都会一起删掉。", danger:true, okText:"删除"}).then(function(ok){
          if (!ok) return;
          tripAction("delete", {id:id}).then(function(){
            delete TRIP_DETAILS[id]; TRIPS = null;
            if (CURRENT_VIEW === "trips" && TRIP_ID === id) goBack();
            toast("已删除");
          }).catch(function(e){ toast(e.message, "err"); });
        });
      },
      "trip-item-del": function(el){
        var id = TRIP_ID, panel = tripPanel, form = $("#trip_form");
        if (!form || form.dataset.busy) return;
        uiConfirm({icon:"🧳", title:"删掉这一项？", message:"从清单里移除这件物品。", danger:true, okText:"删除"}).then(function(ok){
          if (!ok || tripPanel !== panel || CURRENT_VIEW !== "trips") return;
          form.dataset.busy = "1";
          form.querySelector('[type="submit"]').disabled = true;
          return tripAction("item_delete", {id:Number(el.dataset.id)}).then(function(){
            TRIPS = null;
            if (CURRENT_VIEW !== "trips" || TRIP_ID !== id || tripPanel !== panel) return;
            tripPanel = null;
            form.remove();
            return loadTrip(id).then(function(detail){ if (detail && !tripPanel) tripRestorePosition(panel.anchor, true); });
          }).catch(function(e){ toast(e.message, "err"); }).finally(function(){
            delete form.dataset.busy;
            form.querySelector('[type="submit"]').disabled = false;
          });
        });
      },
      "trip-copy-last": function(el){
        var id = TRIP_ID, before = new Set(TRIP_DETAIL.items.map(function(i){ return i.id; }));
        el.disabled = true;
        tripAction("copy_last", {id:id}).then(function(res){
          TRIPS = null;
          if (CURRENT_VIEW !== "trips" || TRIP_ID !== id) return;
          if (!res.copied){ toast("没有要新增的物品"); return; }
          toast("复制了 " + res.copied + " 件");
          return loadTrip(id).then(function(detail){
            if (!detail) return;
            var added = new Set(detail.items.filter(function(i){ return !before.has(i.id); }).map(function(i){ return i.category; }));
            var labels = detail.categories.filter(function(c){ return added.has(c[0]); }).map(function(c){ return c[1]; });
            tripResult("已复制 " + res.copied + " 件物品" + (labels.length ? "：" + labels.join("、") : ""), null, el.closest('footer'));
          });
        }).catch(function(e){ toast(e.message, "err"); }).finally(function(){ el.disabled = false; });
      },
    }
  };
})();
