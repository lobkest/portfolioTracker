// Pure logica voor het tabblad Statistieken (zonder DOM, getest onder Node).

(function (root) {
    "use strict";
    const { formatteerEuro } = typeof module !== "undefined" && module.exports ? require("./getallen.js") : root;

    // Mobiele rij van Rendement per jaar: dagen alleen bij een onvolledig jaar.
    function jaarSubregel(jaar) {
        const ingelegd = `ingelegd ${formatteerEuro(jaar.ingelegd)}`;
        return jaar.pct_van_jaar >= 100 ? ingelegd : `${jaar.dagen_verstreken} d · ${ingelegd}`;
    }

    // Per positie levert de backend altijd een getal, ook zonder rekeningoverzicht; alleen
    // totalen.dividend_netto is dan null.
    function dividendIsBekend(totalen) {
        return Boolean(totalen) && totalen.dividend_netto != null;
    }

    function positieDividend(positie, bekend) {
        if (!bekend || positie.dividend_ontvangen == null) return null;
        return Number(positie.dividend_ontvangen);
    }

    function dividendTekst(bedrag) {
        return bedrag == null ? "–" : formatteerEuro(bedrag);
    }

    const exportsObj = { jaarSubregel, dividendIsBekend, positieDividend, dividendTekst };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
