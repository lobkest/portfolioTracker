// Pure logica voor de eenmalige overdracht van een portfolio-antwoord van de startpagina naar de
// portfolio-pagina (zonder DOM, getest onder Node). De opslag (sessionStorage) komt als parameter mee.

(function (root) {
    "use strict";

    const OVERDRACHT_SLEUTEL = "portfolioOverdracht";

    // false = niet bewaard (opslag vol of geblokkeerd); de aanroeper beslist wat dan.
    function bewaarOverdracht(opslag, data) {
        try {
            opslag.setItem(OVERDRACHT_SLEUTEL, JSON.stringify(data));
            return true;
        } catch (_) {
            return false;
        }
    }

    // Wist altijd na het lezen, ook bij een andere code: een overdracht is eenmalig.
    // code is "" of null voor 'niet opslaan'.
    function haalOverdracht(opslag, code) {
        let data;
        try {
            const ruw = opslag.getItem(OVERDRACHT_SLEUTEL);
            opslag.removeItem(OVERDRACHT_SLEUTEL);
            data = JSON.parse(ruw);
        } catch (_) {
            return null;
        }
        if (!data || typeof data !== "object") return null;
        if ((data.code || "") !== (code || "")) return null;
        return data;
    }

    const exportsObj = { OVERDRACHT_SLEUTEL, bewaarOverdracht, haalOverdracht };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
