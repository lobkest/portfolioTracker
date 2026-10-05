// Pure logica voor de koersmelding bovenaan het dashboard en de splitlabels in de koersgrafiek (zonder DOM, getest onder Node).

(function (root) {
    "use strict";

    function getalNL(x) {
        return String(Number(x.toFixed(2))).replace(".", ",");
    }

    function positieNamen(posities) {
        return posities.map(p => (p.naam && p.naam !== p.ticker ? `${p.naam} (${p.ticker})` : p.ticker)).join(", ");
    }

    function aantalPosities(n) {
        return n === 1 ? "1 positie" : `${n} posities`;
    }

    // null als alle posities koersen hebben.
    function koersMeldingTekst(onvolledig, ontbreken) {
        const delen = [];
        if (onvolledig && onvolledig.length) {
            delen.push(`Nog geen koersen voor ${aantalPosities(onvolledig.length)} (${positieNamen(onvolledig)}): het ophalen ` +
                "kostte te lang en gaat verder als je het portfolio opnieuw opent. Tot dan zijn waarde en rendement te laag.");
        }
        if (ontbreken && ontbreken.length) {
            delen.push(`Yahoo Finance geeft geen koersen voor ${aantalPosities(ontbreken.length)} (${positieNamen(ontbreken)}): ` +
                "die tellen niet mee in de waarde, de inleg wel.");
        }
        return delen.length ? `⚠️ ${delen.join(" ")}` : null;
    }

    // Yahoo-ratio: 4 = 4 nieuwe stukken voor 1 oud, 0,05 = 1 nieuw voor 20 oud.
    function splitLabel(ratio) {
        return ratio >= 1 ? `Split ${getalNL(ratio)}:1` : `Reverse split 1:${getalNL(1 / ratio)}`;
    }

    // Index van de eerste grafiekdatum op of na de splitdatum (ISO-datums), of -1.
    function splitLabelIndex(labelsIso, splitDatum) {
        return labelsIso.findIndex(label => label >= splitDatum);
    }

    const exportsObj = { koersMeldingTekst, splitLabel, splitLabelIndex };

    if (typeof module !== "undefined" && module.exports) {
        module.exports = exportsObj;
    } else {
        Object.assign(root, exportsObj);
    }
})(typeof window !== "undefined" ? window : globalThis);
