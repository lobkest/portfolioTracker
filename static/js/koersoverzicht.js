// Pure logica voor het tabblad Koersen (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // Zelfde id's en volgorde als PERIODES in koersoverzicht.py.
    const KOERS_PERIODES = [
        { id: "maand", label: "Deze maand" },
        { id: "ytd", label: "Dit jaar" },
        { id: "1m", label: "1 maand" },
        { id: "1j", label: "1 jaar" },
        { id: "alles", label: "Alles" },
    ];
    const STANDAARD_KOERS_PERIODE = "ytd";

    // Het segment van punt i-1 naar i is gestippeld als de positie op beide dagen niet in bezit was.
    function isGestippeld(inBezit, i) {
        return i > 0 && !inBezit[i - 1] && !inBezit[i];
    }

    // De volgorde komt van de backend; pct blijft null als er geen waarde is.
    function ranglijstRijen(reeksen) {
        return reeksen.map(r => ({ naam: r.naam, ticker: r.ticker, pct: r.pct_periode }));
    }

    const exportsObj = { KOERS_PERIODES, STANDAARD_KOERS_PERIODE, isGestippeld, ranglijstRijen };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
