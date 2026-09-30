// Pure logica voor paden, het tabblad in de URL-hash en de meldingen op de startpagina (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    const START_PAD = "/";
    const ANALYSE_PAD = "/analyse";
    const PORTFOLIO_PAD_PREFIX = "/p/";
    const STANDAARD_VIEW = "portfolio";
    const MELDING_PARAM = "melding";

    const MELDING_ONGELDIGE_CODE = "ongeldige-code";
    const MELDING_ONBEKENDE_CODE = "onbekende-code";
    const MELDING_VERWIJDERD = "verwijderd";

    const START_MELDING_TEKSTEN = {
        [MELDING_ONGELDIGE_CODE]: "Dat is geen geldige portfolio-code.",
        [MELDING_ONBEKENDE_CODE]: "Er is geen portfolio gevonden met deze code.",
        [MELDING_VERWIJDERD]: "Portfolio verwijderd.",
    };

    function portfolioPad(code) {
        return PORTFOLIO_PAD_PREFIX + encodeURIComponent(code);
    }

    function startPadMetMelding(sleutel) {
        return `${START_PAD}?${MELDING_PARAM}=${encodeURIComponent(sleutel)}`;
    }

    function viewUitHash(hash, geldigeViews) {
        const view = (hash || "").replace(/^#/, "");
        return geldigeViews.includes(view) ? view : STANDAARD_VIEW;
    }

    // Vaste tekst per sleutel: de query zelf komt nooit op de pagina. Onbekende sleutel: null.
    function startMeldingTekst(sleutel) {
        return Object.hasOwn(START_MELDING_TEKSTEN, sleutel) ? START_MELDING_TEKSTEN[sleutel] : null;
    }

    const exportsObj = {
        START_PAD, ANALYSE_PAD, STANDAARD_VIEW, MELDING_PARAM,
        MELDING_ONGELDIGE_CODE, MELDING_ONBEKENDE_CODE, MELDING_VERWIJDERD,
        portfolioPad, startPadMetMelding, viewUitHash, startMeldingTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
