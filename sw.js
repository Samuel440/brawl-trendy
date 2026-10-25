// Brawl Trendy – jen kvůli 🔔 oznámením pro grafika (na mobilu jdou systémová okna jen přes service worker).
// Nic necachuje a nestahuje, stránka se dál načítá normálně z webu.
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
// kliknutí na oznámení → otevřít (nebo přepnout na) stránku rovnou na tom videu z kalendáře
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const url = new URL('./#cal=' + encodeURIComponent(e.notification.data?.id || ''), self.registration.scope).href;
  e.waitUntil(self.clients.matchAll({type:'window', includeUncontrolled:true}).then(list => {
    const w = list.find(c => c.url.startsWith(self.registration.scope));
    if (w) return w.focus().then(c => c.navigate ? c.navigate(url) : c);
    return self.clients.openWindow(url);
  }));
});
