/* 线条小狗 · 两人日程 Service Worker
 * 策略：静态资源缓存优先 + 后台更新；页面网络优先（离线回退缓存骨架，数据由前端 localStorage 水合）；/api 永不缓存。
 * 资源更新靠 template 里的 ?v= 版本参数（新 URL 必然回源），无需手动清缓存。 */
var CACHE = "sched-static-v1";
var STATIC_ASSETS = [
  "/", "/app.css", "/app.js", "/cycle2.css", "/cycle2.js", "/settings_ui.css", "/settings_ui.js",
  "/img/dogheads.png", "/img/dog-couple.png", "/img/dog-couple.webp"
];

self.addEventListener("install", function(e){
  e.waitUntil(
    caches.open(CACHE)
      .then(function(c){ return c.addAll(STATIC_ASSETS); })
      .then(function(){ return self.skipWaiting(); })
  );
});

self.addEventListener("activate", function(e){
  e.waitUntil(
    caches.keys()
      .then(function(keys){
        return Promise.all(keys.filter(function(k){ return k !== CACHE; })
          .map(function(k){ return caches.delete(k); }));
      })
      .then(function(){ return self.clients.claim(); })
  );
});

self.addEventListener("fetch", function(e){
  if (e.request.method !== "GET") return;
  var url = new URL(e.request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ntfy/")) return;

  if (e.request.mode === "navigate") {
    // 页面：网络优先，失败回退到缓存首页（离线时靠前端本地缓存渲染）
    e.respondWith(
      fetch(e.request).then(function(r){
        if (r.ok){
          var copy = r.clone();
          caches.open(CACHE).then(function(c){ c.put("/", copy); });
        }
        return r;
      }).catch(function(){ return caches.match("/"); })
    );
    return;
  }

  // 静态资源：缓存优先 + 后台更新
  e.respondWith(
    caches.match(e.request).then(function(hit){
      var fresh = fetch(e.request).then(function(r){
        if (r.ok){
          caches.open(CACHE).then(function(c){ c.put(e.request, r.clone()); });
        }
        return r;
      });
      if (hit){
        fresh.catch(function(){});
        return hit;
      }
      return fresh;
    })
  );
});
