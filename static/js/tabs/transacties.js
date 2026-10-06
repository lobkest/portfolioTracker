// Tabblad Transacties: sorteerbare tabel met paginering. Sorteren en pagineren zelf staat in transacties.js.

// null tot het eerste bezoek aan het Transacties-tabblad.
let transactiesRuweLijst = null;
// Bewaard tussen tabwissels; maakSorteerbareTabel() werkt dit object bij.
const transactiesStaat = { sorteerKolom: "datum_tijd", sorteerRichting: "desc", pagina: 1, paginaGrootte: 25 };

// Rijen per pagina blijft staan: dat is een voorkeur, geen toestand van de portfolio.
function resetTransacties() {
    transactiesRuweLijst = null;
    Object.assign(transactiesStaat, { sorteerKolom: "datum_tijd", sorteerRichting: "desc", pagina: 1 });
}

const TRANSACTIES_TOESTANDEN = ["transactiesLaden", "transactiesFout", "transactiesLeeg", "transactiesInhoud"];

// De sleutels zijn de kolomnamen van sorteerTransacties() (transacties.js).
const TRANSACTIES_KOLOMMEN = [
    {
        label: "Datum & tijd",
        sleutel: "datum_tijd",
        renderTd: r => maakCel(r.tijd === null ? formatDatum(r.datum) : `${formatDatum(r.datum)} ${r.tijd}`),
    },
    { label: "Product", sleutel: "product", renderTd: r => maakCel(r.product) },
    { label: "ISIN", sleutel: "isin", renderTd: r => maakCel(r.isin ?? "—") },
    { label: "Beurs", sleutel: "beurs", renderTd: r => maakCel(r.beurs ?? "—") },
    {
        label: "Aantal",
        sleutel: "aantal",
        renderTd: r => maakCel(r.aantal.toLocaleString("nl-NL", { maximumFractionDigits: 4 })),
    },
    { label: "Koers", sleutel: "koers", renderTd: r => maakCel(r.koers === null ? "onbekend" : formatteerEuro(r.koers)) },
    {
        label: "Wisselkoers",
        sleutel: "wisselkoers",
        renderTd: r => maakCel(r.wisselkoers === null ? "—" : r.wisselkoers.toLocaleString("nl-NL", { maximumFractionDigits: 4 })),
    },
    { label: "Totaal (EUR)", sleutel: "totaal_eur", renderTd: r => maakCel(formatteerEuro(r.totaal_eur)) },
    {
        label: "Transactiekosten (EUR)",
        sleutel: "transactiekosten",
        renderTd: r => maakCel(r.transactiekosten === null ? "—" : formatteerEuro(r.transactiekosten)),
    },
];

async function toonTransacties() {
    // Zonder code (niet opslaan) staat de lijst al in het upload-antwoord.
    if (!huidigeData.code) {
        transactiesRuweLijst = huidigeData.transacties_lijst || [];
    } else if (transactiesRuweLijst === null) {
        toonAlleen(TRANSACTIES_TOESTANDEN, "transactiesLaden");
        let res, data;
        try {
            res = await fetch(`/api/portfolio/${huidigeData.code}/transacties`);
            data = await res.json();
        } catch (e) {
            toonAlleen(TRANSACTIES_TOESTANDEN, "transactiesFout");
            return;
        }
        if (!res.ok) {
            toonAlleen(TRANSACTIES_TOESTANDEN, "transactiesFout");
            return;
        }
        transactiesRuweLijst = data.lijst;
    }

    renderTransactiesTabel();
}

function renderTransactiesTabel() {
    if (!transactiesRuweLijst || transactiesRuweLijst.length === 0) {
        toonAlleen(TRANSACTIES_TOESTANDEN, "transactiesLeeg");
        return;
    }
    toonAlleen(TRANSACTIES_TOESTANDEN, "transactiesInhoud");

    document.getElementById("transactiesPaginaGrootte").value = String(transactiesStaat.paginaGrootte);
    // sorteerTransacties i.p.v. de standaardsortering: ook tekst en datum+tijd, over alle pagina's heen.
    document.getElementById("transactiesTabel").replaceChildren(
        maakSorteerbareTabel(TRANSACTIES_KOLOMMEN, transactiesRuweLijst, { staat: transactiesStaat, sorteer: sorteerTransacties })
    );
}

document.getElementById("transactiesPaginaGrootte").addEventListener("change", (e) => {
    transactiesStaat.paginaGrootte = parseInt(e.target.value, 10);
    transactiesStaat.pagina = 1;
    renderTransactiesTabel();
});
