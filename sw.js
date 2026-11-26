// Agent Şəbəkəsi — tətbiq kimi quraşdırmaq üçün. Məlumat həmişə serverdən gəlir (api.php keşlənmir).
const C = 'as-v1';
self.addEventListener('install', e => { e.waitUntil(caches.open(C).then(c => c.addAll(['/', '/icon-192.png'])).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  const u = new URL(e.request.url);
  if (e.request.method !== 'GET' || u.pathname.endsWith('api.php')) return;
  e.respondWith(fetch(e.request).then(r => { const cp = r.clone(); caches.open(C).then(c => c.put(e.request, cp)); return r; })
    .catch(() => caches.match(e.request).then(r => r || caches.match('/'))));
});
