// Pure logica voor de "gekozen bestand + x-knop"-rij (DOM-kant: koppelBestandWisKnop() in gedeeld.js) en de bijwerken-melding.

(function (root) {
    "use strict";

    function bestandSelectieWeergave(bestandsnamen) {
        const namen = Array.isArray(bestandsnamen) ? bestandsnamen : [];
        if (namen.length === 0) {
            return { zichtbaar: false, tekst: "" };
        }
        if (namen.length === 1) {
            return { zichtbaar: true, tekst: namen[0] };
        }
        return { zichtbaar: true, tekst: `${namen.length} bestanden` };
    }

    // samenvatting: het veld "bijwerken" uit het antwoord van /api/portfolio/<code>/bijwerken.
    function bijwerkenSuccesTekst(samenvatting) {
        const aantal = samenvatting.nieuwe_transacties;
        let tekst = aantal === 0
            ? "Geen nieuwe transacties gevonden."
            : `${aantal} nieuwe transactie${aantal === 1 ? "" : "s"} toegevoegd.`;
        if (samenvatting.dividend_verwerkt) tekst += " Rekeningoverzicht (dividend) verwerkt.";
        return tekst;
    }

    const exportsObj = { bestandSelectieWeergave, bijwerkenSuccesTekst };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
