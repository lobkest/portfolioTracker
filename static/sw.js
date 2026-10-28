const CACHE_NAAM = 'laadscherm-v1';
const LAADSCHERM = '/static/laden.html';
const BESTANDEN = [LAADSCHERM, '/static/js/laadscherm.js'];
const PING_TIMEOUT_MS = 3000;

self.addEventListener('install', (event) => {
    event.waitUntil(caches.open(CACHE_NAAM).then((cache) => cache.addAll(BESTANDEN)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (event) => {
    event.waitUntil(
        caches.keys()
            .then((namen) => Promise.all(namen.filter((naam) => naam !== CACHE_NAAM).map((naam) => caches.delete(naam))))
            .then(() => self.clients.claim())
    );
});

async function serverIsWakker() {
    const stop = new AbortController();
    const timer = setTimeout(() => stop.abort(), PING_TIMEOUT_MS);
    try {
        const antwoord = await fetch('/ping', { cache: 'no-store', signal: stop.signal });
        const data = await antwoord.json();
        return !!data && data.ok === true;
    } catch (fout) {
        return false;
    } finally {
        clearTimeout(timer);
    }
}

self.addEventListener('fetch', (event) => {
    if (event.request.mode !== 'navigate') return;
    event.respondWith((async () => {
        if (await serverIsWakker()) return fetch(event.request);
        const cache = await caches.open(CACHE_NAAM);
        return (await cache.match(LAADSCHERM)) || fetch(event.request);
    })());
});
