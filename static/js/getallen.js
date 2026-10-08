// Getalnotatie in het Nederlands (punt voor duizendtallen, komma als decimaalteken), zonder DOM, getest onder Node.

(function (root) {
    "use strict";

    const ONBEKEND = "onbekend";

    function isGetal(x) {
        return typeof x === "number" && Number.isFinite(x);
    }

    // Zonder dit wordt -0,001 met 2 decimalen "-0,00".
    function zonderMinNul(x, maxDecimalen) {
        return Math.abs(x) < 0.5 * 10 ** -maxDecimalen ? 0 : x;
    }

    // Standaard vast aantal decimalen; minDecimalen = 0 laat nullen achteraan weg (1,5 i.p.v. 1,5000).
    function formatGetal(x, maxDecimalen = 2, minDecimalen = maxDecimalen) {
        if (!isGetal(x)) return ONBEKEND;
        return new Intl.NumberFormat("nl-NL", {
            minimumFractionDigits: minDecimalen,
            maximumFractionDigits: maxDecimalen,
        }).format(zonderMinNul(x, maxDecimalen));
    }

    // Minteken vóór het €-teken: -€1.234,56.
    function formatteerEuro(bedrag, decimalen = 2) {
        if (!isGetal(bedrag)) return ONBEKEND;
        const afgerond = zonderMinNul(bedrag, decimalen);
        const teken = afgerond < 0 ? "-" : "";
        return `${teken}€${formatGetal(Math.abs(afgerond), decimalen)}`;
    }

    // pct is al een percentage (20.31 → "20,31%"); metTeken zet "+" voor positieve waarden.
    function formatPct(pct, decimalen = 2, metTeken = false) {
        if (!isGetal(pct)) return ONBEKEND;
        const tekst = formatGetal(pct, decimalen);
        return `${metTeken && zonderMinNul(pct, decimalen) > 0 ? "+" : ""}${tekst}%`;
    }

    const exportsObj = { formatGetal, formatteerEuro, formatPct };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
