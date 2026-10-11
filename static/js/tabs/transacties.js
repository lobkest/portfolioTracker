// Tabblad Transacties: sorteerbare tabel met paginering. Sorteren en pagineren zelf staat in transacties.js.

// null tot het eerste bezoek aan het Transacties-tabblad.
let transactiesRuweLijst = null;
// promotie uit /transacties of het upload-antwoord; null zonder promotie.
let transactiesPromotie = null;
// Bewaard tussen tabwissels; maakSorteerbareTabel() werkt dit object bij.
const transactiesStaat = { sorteerKolom: "datum_tijd", sorteerRichting: "desc", pagina: 1, paginaGrootte: 25 };

// Rijen per pagina blijft staan: dat is een voorkeur, geen toestand van de portfolio.
function resetTransacties() {
    transactiesRuweLijst = null;
    transactiesPromotie = null;
    Object.assign(transactiesStaat, { sorteerKolom: "datum_tijd", sorteerRichting: "desc", pagina: 1 });
}

const TRANSACTIES_TOESTANDEN = ["transactiesLaden", "transactiesFout", "transactiesLeeg", "transactiesInhoud"];

const TRANSACTIE_STATUS_BADGES = {
    deels_verkocht: { tekst: "Deels verkocht", klasse: "badge badgeDeelsVerkocht" },
    gesloten: { tekst: "Gesloten", klasse: "badge badgeGesloten" },
};
const TRANSACTIE_RESULTAAT_UITLEG = "Verschil tussen de aankoopkoers van deze aankoop en de laatste koers; verkochte "
    + "stukken gaan FIFO (oudste aankoop eerst). Kan afwijken van het rendement per positie op Statistieken (GAK).";
const TRANSACTIE_DIVIDEND_UITLEG = "Netto dividend, pro rata verdeeld naar het aantal stukken van deze aankoop op de "
    + "uitbetaaldatum. Niet meegeteld in het resultaat.";

// Status zonder resultaat: een aankoop waarvan de koers ontbreekt.
function maakTransactieResultaatCel(r) {
    if (r.status == null) return maakCel("—");
    if (r.resultaat_eur === null) return maakCel("koers onbekend");
    const euro = `${r.resultaat_eur > 0 ? "+" : ""}${formatteerEuro(r.resultaat_eur)}`;
    const td = maakCel(r.resultaat_pct === null ? euro : `${euro} (${formatPct(r.resultaat_pct, 1, true)})`);
    td.className = r.status === "gesloten" ? "gedempt" : klasseVoorRendement(r.resultaat_eur);
    const badge = TRANSACTIE_STATUS_BADGES[r.status];
    if (badge) {
        const span = document.createElement("span");
        span.textContent = badge.tekst;
        span.className = badge.klasse;
        td.appendChild(span);
    }
    return td;
}

// Bedrag zoals betaald; de toegerekende promotiekorting als label eronder.
function maakTransactieKostenCel(r) {
    const td = maakCel(r.transactiekosten === null ? "—" : formatteerEuro(r.transactiekosten));
    if (r.kostenkorting_eur > 0) {
        const label = document.createElement("span");
        label.className = "promotieLabel";
        label.textContent = promotieLabelTekst(r.kostenkorting_eur);
        const tip = document.createElement("span");
        tip.className = "infoTip";
        tip.dataset.label = "promotie";
        tip.textContent = promotieUitlegTekst(r.kostenkorting_eur);
        label.appendChild(tip);
        td.appendChild(label);
        activeerInfoTip(tip);
    }
    return td;
}

function transactieDividendTekst(r) {
    return r.dividend_eur == null ? "—" : formatteerEuro(r.dividend_eur);
}

function maakPromotieTekenCel(r) {
    const td = maakCel(promotieTekenTekst(r));
    td.className = "promotieTeken";
    return td;
}

// De sleutels zijn de kolomnamen van sorteerTransacties() (transacties.js).
const TRANSACTIES_KOLOMMEN = [
    { label: "Datum & tijd", sleutel: "datum_tijd", renderTd: r => maakCel(datumTijdTekst(r)) },
    { label: "Product", sleutel: "product", mobielTekst: r => r.product, renderTd: r => maakCel(r.product) },
    { label: "ISIN", sleutel: "isin", renderTd: r => maakCel(r.isin ?? "—") },
    { label: "Beurs", sleutel: "beurs", renderTd: r => maakCel(r.beurs ?? "—") },
    {
        label: "Aantal",
        sleutel: "aantal",
        mobielTekst: r => aantalStuksTekst(r.aantal),
        renderTd: r => maakCel(formatGetal(r.aantal, 4, 0)),
    },
    { label: "Koers", sleutel: "koers", renderTd: r => maakCel(r.koers === null ? "onbekend" : formatteerEuro(r.koers)) },
    {
        label: "Wisselkoers",
        sleutel: "wisselkoers",
        renderTd: r => maakCel(r.wisselkoers === null ? "—" : formatGetal(r.wisselkoers, 4, 0)),
    },
    { label: "Totaal (EUR)", sleutel: "totaal_eur", renderTd: r => maakCel(formatteerEuro(r.totaal_eur)) },
    {
        label: "Transactiekosten (EUR)",
        sleutel: "transactiekosten",
        renderTd: maakTransactieKostenCel,
    },
    { label: "Resultaat", sleutel: "resultaat_eur", uitleg: TRANSACTIE_RESULTAAT_UITLEG, renderTd: maakTransactieResultaatCel },
    {
        label: "Dividend",
        sleutel: "dividend_eur",
        uitleg: TRANSACTIE_DIVIDEND_UITLEG,
        renderTd: r => maakCel(transactieDividendTekst(r)),
    },
    { label: "Promotie", alleenMobiel: true, mobielRol: "subwaarde", renderTd: maakPromotieTekenCel },
].map(kol => (kol.mobielRol ? kol : { ...kol, mobielRol: transactieMobielRol(kol.sleutel) }));

async function toonTransacties() {
    // Zonder code (niet opslaan) staat de lijst al in het upload-antwoord.
    if (!huidigeData.code) {
        ({ lijst: transactiesRuweLijst, promotie: transactiesPromotie } = leesTransactiesAntwoord(huidigeData));
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
        ({ lijst: transactiesRuweLijst, promotie: transactiesPromotie } = leesTransactiesAntwoord(data));
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
        maakSorteerbareTabel(TRANSACTIES_KOLOMMEN, transactiesRuweLijst, {
            staat: transactiesStaat, sorteer: sorteerTransacties, compactOpMobiel: true,
        })
    );
    const samenvatting = promotieSamenvattingTekst(transactiesPromotie, huidigeData.statistieken?.totalen);
    const promotieRegel = document.getElementById("transactiesPromotie");
    promotieRegel.textContent = samenvatting ?? "";
    promotieRegel.hidden = samenvatting === null;
}

document.getElementById("transactiesPaginaGrootte").addEventListener("change", (e) => {
    transactiesStaat.paginaGrootte = parseInt(e.target.value, 10);
    transactiesStaat.pagina = 1;
    renderTransactiesTabel();
});
