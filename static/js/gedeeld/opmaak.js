// Opmaak van datums (getallen: getallen.js), plus kleine DOM-helpers die meerdere tabbladen gebruiken.

function formatDatum(isoDatum) {
    const [jaar, maand, dag] = isoDatum.slice(0, 10).split("-");
    return `${dag}-${maand}-${jaar}`;
}

// CSS-klasse voor groen/rood; leeg bij een onbekende waarde.
function klasseVoorRendement(pct) {
    if (pct === null || pct === undefined) return "";
    return pct >= 0 ? "positief" : "negatief";
}

function kortNaam(naam, maxLen = 14) {
    return naam.length > maxLen ? naam.slice(0, maxLen - 1) + "…" : naam;
}

// Toont van een groep elementen alleen het genoemde (of geen, bij null).
function toonAlleen(ids, zichtbaarId) {
    ids.forEach(id => { document.getElementById(id).hidden = id !== zichtbaarId; });
}
