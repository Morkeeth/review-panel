// Review panel service worker. Network only: review pages, data and media are never stored or replayed.
// It keeps just the branded offline page and the logo, so a launch without signal shows something useful.
const SHELL = ['/pwa/offline.html', '/pwa/logo.svg'];
const CACHE = 'review-shell-v4';
self.addEventListener('install', event => {
  event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting()));
});
async function pruneReviewShellCaches(){
 const keys=await caches.keys();await Promise.all(keys.filter(k=>k.startsWith('review-shell-')&&k!==CACHE).map(k=>caches.delete(k)));
 const current=await caches.open(CACHE);
 for(const request of await current.keys()){
  const u=new URL(request.url);if(u.origin!==location.origin||!SHELL.includes(u.pathname)||u.search)await current.delete(request);
 }
}
self.addEventListener('activate',event=>{event.waitUntil(pruneReviewShellCaches().then(()=>self.clients.claim()).then(()=>self.clients.matchAll({type:'window'}).then(clients=>clients.forEach(c=>c.postMessage({type:'panel-sw-updated'})))));});
self.addEventListener('message',event=>{
 const d=event.data;if(!d||d.type!=='review-queue-version'||! /^[a-f0-9]{64}$/.test(d.version||''))return;
 event.waitUntil(pruneReviewShellCaches().then(()=>event.source?.postMessage({type:'review-cache-cleared',version:d.version})));
});
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== location.origin || event.request.method !== 'GET') return;
  if (SHELL.includes(url.pathname)) {
    event.respondWith(fetch(event.request, {cache:'no-store'}).catch(() => caches.open(CACHE).then(c => c.match(url.pathname))));
  } else if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request, {cache:'no-store'}).catch(() => caches.open(CACHE).then(c => c.match('/pwa/offline.html'))));
  } else {
    event.respondWith(fetch(event.request, {cache:'no-store'}));
  }
});
