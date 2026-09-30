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

    // Voor data-views en data-verberg-buiten: tabbladnamen gescheiden door spaties.
    function viewInLijst(lijst, view) {
        return (lijst || "").split(" ").includes(view);
    }

    const VEREIST_CODE_JA = "ja";
    const VEREIST_CODE_NEE = "nee";

    // views = data-views, vereistCode = data-vereist-code; een ontbrekend attribuut stelt geen eis.
    function elementZichtbaar(views, vereistCode, view, heeftCode) {
        if (views !== undefined && !viewInLijst(views, view)) return false;
        if (vereistCode === VEREIST_CODE_JA) return heeftCode;
        if (vereistCode === VEREIST_CODE_NEE) return !heeftCode;
        return true;
    }

    // Vaste tekst per sleutel: de query zelf komt nooit op de pagina. Onbekende sleutel: null.
    function startMeldingTekst(sleutel) {
        return Object.hasOwn(START_MELDING_TEKSTEN, sleutel) ? START_MELDING_TEKSTEN[sleutel] : null;
    }

    const exportsObj = {
        START_PAD, ANALYSE_PAD, STANDAARD_VIEW, MELDING_PARAM,
        MELDING_ONGELDIGE_CODE, MELDING_ONBEKENDE_CODE, MELDING_VERWIJDERD,
        VEREIST_CODE_JA, VEREIST_CODE_NEE,
        portfolioPad, startPadMetMelding, viewUitHash, viewInLijst, elementZichtbaar, startMeldingTekst,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
