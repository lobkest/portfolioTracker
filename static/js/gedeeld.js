// DOM-helpers die de startpagina en de portfolio-pagina delen.

// Breekt af als de server te lang stil blijft (zie CLAUDE.md: Yahoo en tickers); gooit Error("TIMEOUT").
async function fetchMetTimeout(url, opties, timeoutMs = 55000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeoutMs);
    try {
        return await fetch(url, { ...(opties || {}), signal: controller.signal });
    } catch (fout) {
        if (fout.name === "AbortError") {
            throw new Error("TIMEOUT");
        }
        throw fout;
    } finally {
        clearTimeout(timer);
    }
}

// Voorkomt dubbelklikken of wegnavigeren tijdens een serververzoek.
function toonLaadOverlay(tekst) {
    document.getElementById("laadOverlayTekst").textContent = tekst;
    document.getElementById("laadOverlay").hidden = false;
}

function verbergLaadOverlay() {
    document.getElementById("laadOverlay").hidden = true;
}

// Het opvragen zelf kan al gooien als de browser opslag blokkeert.
function sessieOpslag() {
    try {
        return window.sessionStorage;
    } catch (_) {
        return null;
    }
}
