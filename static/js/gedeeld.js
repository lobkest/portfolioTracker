// DOM-helpers die de startpagina en de portfolio-pagina delen.

// Iets ruimer dan de standaard van fetchMetTimeout().
const UPLOAD_TIMEOUT_MS = 60000;

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

function lokaleOpslag() {
    try {
        return window.localStorage;
    } catch (_) {
        return null;
    }
}

// De x-knop zet input.value = "", zodat het bestand echt niet meegaat en `change` weer afgaat.
// Geeft de update-functie terug voor momenten zonder `change` (reset, terugknop).
function koppelBestandWisKnop(inputId) {
    const input = document.getElementById(inputId);
    const rij = document.getElementById(`${inputId}Keuze`);
    const naam = document.getElementById(`${inputId}Naam`);
    const wisKnop = document.getElementById(`${inputId}WisKnop`);

    function werkBij() {
        const namen = Array.from(input.files || [], (f) => f.name);
        const weergave = bestandSelectieWeergave(namen);
        naam.textContent = weergave.tekst;
        naam.title = weergave.tekst;
        rij.hidden = !weergave.zichtbaar;
    }

    input.addEventListener("change", werkBij);
    wisKnop.addEventListener("click", () => {
        input.value = "";
        werkBij();
        // De knop verdwijnt: focus terug op het veld voor toetsenbordgebruikers.
        input.focus();
    });
    werkBij();
    return werkBij;
}
