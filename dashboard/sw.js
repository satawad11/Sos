/* Service worker: installable app + push notifications.
   Pages and the API always go to the network (no stale dashboard after an update);
   only fonts / icons / map library are cached. */
const V = "sos-static-v1";
const STATIC = ["fonts/", "icons/", "vendor/"];

self.addEventListener("install", e => { self.skipWaiting(); });
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => k !== V).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener("fetch", e => {
  const r = e.request, u = new URL(r.url);
  if (r.method !== "GET" || u.origin !== location.origin || u.pathname.includes("/api/") || u.pathname.endsWith("/ws")) return;
  if (r.mode === "navigate") {
    e.respondWith(fetch(r).catch(() => new Response(
      '<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>ออฟไลน์</title>' +
      '<body style="font-family:system-ui,sans-serif;display:grid;place-items:center;min-height:90vh;text-align:center;color:#0f1b2d">' +
      '<div><div style="font-size:48px">📡</div><h2>ไม่มีอินเทอร์เน็ต</h2><p>เชื่อมต่อเน็ตแล้วลองใหม่<br>เหตุฉุกเฉินยังแจ้งผ่าน LINE ตามปกติ</p>' +
      '<button onclick="location.reload()" style="font-size:16px;padding:10px 20px;border-radius:10px;border:0;background:#1d4ed8;color:#fff">ลองอีกครั้ง</button></div>',
      {headers: {"content-type": "text/html; charset=utf-8"}})));
    return;
  }
  if (STATIC.some(p => u.pathname.includes("/" + p))) {
    e.respondWith(caches.open(V).then(c => c.match(r).then(hit => hit || fetch(r).then(res => { if (res.ok) c.put(r, res.clone()); return res; }))));
  }
});

self.addEventListener("push", e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = {title: "ระบบแจ้งเหตุฉุกเฉิน", body: e.data && e.data.text()}; }
  e.waitUntil(self.registration.showNotification(d.title || "ระบบแจ้งเหตุฉุกเฉิน", {
    body: d.body || "", tag: d.tag || "sos", renotify: true, requireInteraction: !!d.urgent,
    icon: "icons/icon-192.png", badge: "icons/badge-96.png", lang: "th",
    vibrate: d.urgent ? [400, 150, 400, 150, 800] : [200], data: {url: d.url || "./"},
  }));
});

self.addEventListener("notificationclick", e => {
  e.notification.close();
  const url = new URL(e.notification.data && e.notification.data.url || "./", self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({type: "window", includeUncontrolled: true}).then(cs => {
    for (const c of cs) if (c.url.startsWith(self.registration.scope)) return c.focus().then(w => w && w.navigate ? w.navigate(url) : w);
    return self.clients.openWindow(url);
  }));
});
