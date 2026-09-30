// Opmaak van datums, bedragen en percentages, plus kleine DOM-helpers die meerdere tabbladen gebruiken.

function formatDatum(isoDatum) {
    const [jaar, maand, dag] = isoDatum.slice(0, 10).split("-");
    return `${dag}-${maand}-${jaar}`;
}

// Nederlandse notatie, minteken vóór het €-teken: -€1.234,56.
function formatteerEuro(bedrag, decimalen = 2) {
    if (bedrag === null || bedrag === undefined || Number.isNaN(bedrag)) return "onbekend";
    const teken = bedrag < 0 ? "-" : "";
    const getalTekst = new Intl.NumberFormat("nl-NL", {
        minimumFractionDigits: decimalen,
        maximumFractionDigits: decimalen,
    }).format(Math.abs(bedrag));
    return `${teken}€${getalTekst}`;
}

function formatPct(pct) {
    return (pct === null || pct === undefined) ? "onbekend" : `${pct.toFixed(2)}%`;
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
