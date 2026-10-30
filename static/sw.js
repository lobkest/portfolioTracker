const CACHE_NAAM = 'laadscherm-v5';
const LAADSCHERM = '/static/laden.html';
const BESTANDEN = [LAADSCHERM, '/static/js/laadscherm.js'];
const DOORLAAT_PARAM = '_wakker';
const APP_PADEN = ['/'];
const APP_PAD_VOORVOEGSELS = ['/p/'];

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

function isAppPad(pad) {
    return APP_PADEN.includes(pad) || APP_PAD_VOORVOEGSELS.some((voorvoegsel) => pad.startsWith(voorvoegsel));
}

self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    // Deze bestanden altijd uit de cache: de server kan slapen.
    if (BESTANDEN.includes(url.pathname)) {
        event.respondWith(caches.match(url.pathname).then((antwoord) => antwoord || fetch(event.request)));
        return;
    }
    if (event.request.mode !== 'navigate') return;
    if (url.searchParams.has(DOORLAAT_PARAM) || !isAppPad(url.pathname)) return;
    // Direct het laadscherm: de pagina pollt zelf /ping en stuurt door met DOORLAAT_PARAM.
    event.respondWith(caches.match(LAADSCHERM).then((antwoord) => antwoord || fetch(event.request)));
});
