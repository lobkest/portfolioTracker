// Pure logica voor de "gekozen bestand + x-knop"-rij (DOM-kant: koppelBestandWisKnop() in gedeeld.js),
// de knoppen van de startpagina en de bijwerken-melding.

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

    function isExcelBestandsnaam(naam) {
        return /\.xlsx?$/i.test(String(naam || ""));
    }

    function uploadKnopActief(aantalTransactiebestanden) {
        return aantalTransactiebestanden > 0;
    }

    function normaliseerCode(invoer) {
        return String(invoer || "").trim().toUpperCase();
    }

    // Alleen de lengte: het codepatroon zelf controleert de backend (portfolio_admin.py).
    function ophaalKnopActief(invoer, codeLengte) {
        return normaliseerCode(invoer).length === codeLengte;
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

    const exportsObj = {
        bestandSelectieWeergave, bijwerkenSuccesTekst, isExcelBestandsnaam,
        uploadKnopActief, normaliseerCode, ophaalKnopActief,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
