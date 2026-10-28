// Pure logica voor het laadscherm tijdens de Render-opstart (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    const VERWACHTE_WACHTTIJD_S = 50;
    const MAX_WACHTTIJD_S = 120;
    const WEETJE_INTERVAL_MS = 8000;
    const PING_INTERVAL_MS = 2000;

    const WEETJES = [
        "Rente-op-rente: rendement levert zelf ook weer rendement op. Hoe langer je belegt, hoe groter dat effect.",
        "Een wereldwijde ETF zoals MSCI World bevat ruim 1.300 bedrijven. Op de pagina \"Top 10 bedrijven\" zie je welke je via je ETF's het meest bezit.",
        "Accumulerende ETF's herbeleggen dividend automatisch, uitkerende ETF's betalen het uit.",
        "De lopende kosten (TER) van een fonds lijken klein, maar tellen over tientallen jaren flink op.",
        "XIRR houdt rekening met wanneer je geld inlegt. TWR meet de prestatie los van je stortingen. Beide staan onder \"XIRR & rendement\".",
        "Na een aandelensplitsing heb je meer aandelen tegen een lagere koers, maar je bezit blijft even veel waard. Deze app corrigeert daar automatisch voor.",
        "Twee ETF's kunnen grotendeels dezelfde bedrijven bevatten. Bekijk het bij \"ETF-overlap\".",
        "Op Nederlandse dividenden wordt 15% dividendbelasting ingehouden.",
        "Upload ook je rekeningoverzicht om je dividend (bruto, belasting, netto) te zien.",
        "Met je 3-lettercode haal je je dashboard later terug zonder opnieuw te uploaden.",
        "Kies \"niet opslaan\" voor een eenmalige analyse: dan wordt er niets bewaard.",
        "Vergelijk je rendement met de S&P 500, de Nasdaq 100 of de AEX onder \"Rendement\".",
        "Bij \"Per aandeel\" zie je precies wanneer je kocht en verkocht, op de koersgrafiek.",
        "Benieuwd naar later? De \"Prognose\" rekent met jouw eigen inleg en rendement."
    ];

    function formatteerWachttijd(seconden) {
        const totaal = Math.max(0, Math.floor(seconden));
        if (totaal < 60) return `${totaal} ${totaal === 1 ? "seconde" : "seconden"}`;
        return `${Math.floor(totaal / 60)} min ${totaal % 60} sec`;
    }

    function voortgang(seconden, verwacht) {
        if (!(verwacht > 0)) return 1;
        return Math.min(1, Math.max(0, seconden / verwacht));
    }

    function kiesVolgendWeetje(huidigeIndex, aantal, random) {
        if (aantal <= 1) return 0;
        // Kies uit de overige aantal-1 plekken en schuif voorbij de huidige: nooit hetzelfde weetje.
        const stap = Math.min(aantal - 2, Math.floor(random() * (aantal - 1)));
        return stap < huidigeIndex ? stap : stap + 1;
    }

    function isPingOk(data) {
        return typeof data === "object" && data !== null && data.ok === true;
    }

    const exportsObj = {
        VERWACHTE_WACHTTIJD_S,
        MAX_WACHTTIJD_S,
        WEETJE_INTERVAL_MS,
        PING_INTERVAL_MS,
        WEETJES,
        formatteerWachttijd,
        voortgang,
        kiesVolgendWeetje,
        isPingOk
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
