/* 线条小狗 · 两人日程 Service Worker
 * 策略：静态资源缓存优先 + 后台更新；页面网络优先（离线回退缓存骨架，数据由前端 localStorage 水合）；/api 永不缓存。
 * 预缓存 URL 与页面模板中的资源版本保持一致。 */
var CACHE = "sched-static-v4";
var STATIC_ASSETS = [
  "/", "/app.css?v=20261118journaltabs1", "/cycle2.css?v=20261118cycle2", "/settings_ui.css?v=20261115a",
  "/cycle2.js?v=20261118cycle2", "/settings_ui.js?v=20261117refresh1",
  "/trips.js?v=20261119layout1", "/app.js?v=20261119layout1", "/manifest.webmanifest",
  "/img/dogheads.png", "/img/dog-couple.png", "/img/dog-couple.webp?v=20261116a"
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
  // 照片和实况视频使用 HTTP 缓存，Range 请求不能由整文件缓存应答。
  if (url.pathname.startsWith("/photos/") || e.request.headers.has("Range")) return;

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
