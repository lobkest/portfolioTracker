// Pure logica voor het ETF-overlap-tabblad (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    // korteNamen = yahooNamen uit tabs/bijnamen.js ({ticker: {voorstel}}), of null zolang die er niet zijn.
    function etfWeergaveNaam(ticker, huidigeNaam, korteNamen) {
        const voorstel = korteNamen && korteNamen[ticker] ? korteNamen[ticker].voorstel : null;
        return voorstel || huidigeNaam || ticker;
    }

    const exportsObj = {
        etfWeergaveNaam,
    };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
