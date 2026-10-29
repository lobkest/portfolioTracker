// Unit tests voor de rekenkern van het Transacties-tabblad (static/js/transacties.js).
// Draait via Node's ingebouwde testrunner, geen extra dependency nodig:
//   node --test tests/test_transacties.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    sorteerTransacties, totaalPaginas, pagineer, paginaKnoppen, leesTransactiesAntwoord, promotieLabelTekst, promotieUitlegTekst, promotieSamenvattingTekst,
    transactieMobielRol, datumTijdTekst, aantalStuksTekst, promotieTekenTekst,
} = require("../static/js/transacties.js");

const RIJEN = [
    { datum: "2024-03-01", product: "B BV", aantal: 5, koers: 20, totaal_eur: 100 },
    { datum: "2024-01-01", product: "A BV", aantal: 20, koers: 5, totaal_eur: 100 },
    { datum: "2024-02-01", product: "C BV", aantal: 1, koers: 300, totaal_eur: 300 },
];

test("sorteerTransacties: datum_tijd oplopend", () => {
    const resultaat = sorteerTransacties(RIJEN, "datum_tijd", "asc");
    assert.deepEqual(resultaat.map(r => r.datum), ["2024-01-01", "2024-02-01", "2024-03-01"]);
});

test("sorteerTransacties: datum_tijd aflopend (meest recent eerst)", () => {
    const resultaat = sorteerTransacties(RIJEN, "datum_tijd", "desc");
    assert.deepEqual(resultaat.map(r => r.datum), ["2024-03-01", "2024-02-01", "2024-01-01"]);
});

test("sorteerTransacties: product alfabetisch", () => {
    const resultaat = sorteerTransacties(RIJEN, "product", "asc");
    assert.deepEqual(resultaat.map(r => r.product), ["A BV", "B BV", "C BV"]);
});

test("sorteerTransacties: numerieke kolommen (aantal/koers/totaal_eur)", () => {
    assert.deepEqual(sorteerTransacties(RIJEN, "aantal", "asc").map(r => r.aantal), [1, 5, 20]);
    assert.deepEqual(sorteerTransacties(RIJEN, "koers", "desc").map(r => r.koers), [300, 20, 5]);
    assert.deepEqual(sorteerTransacties(RIJEN, "totaal_eur", "asc").map(r => r.totaal_eur), [100, 100, 300]);
});

const MET_RESULTAAT = [
    { product: "verkoop", resultaat_eur: null, dividend_eur: null },
    { product: "winst", resultaat_eur: 50, dividend_eur: 2 },
    { product: "oud", dividend_eur: 0 },
    { product: "verlies", resultaat_eur: -20, dividend_eur: 7.5 },
];

test("sorteerTransacties: resultaat_eur, zonder waarde onderaan in beide richtingen", () => {
    assert.deepEqual(sorteerTransacties(MET_RESULTAAT, "resultaat_eur", "asc").map(r => r.product),
        ["verlies", "winst", "verkoop", "oud"]);
    assert.deepEqual(sorteerTransacties(MET_RESULTAAT, "resultaat_eur", "desc").map(r => r.product),
        ["winst", "verlies", "verkoop", "oud"]);
});

test("sorteerTransacties: dividend_eur, 0 is een waarde en null onderaan", () => {
    assert.deepEqual(sorteerTransacties(MET_RESULTAAT, "dividend_eur", "asc").map(r => r.product),
        ["oud", "winst", "verlies", "verkoop"]);
    assert.deepEqual(sorteerTransacties(MET_RESULTAAT, "dividend_eur", "desc").map(r => r.product),
        ["verlies", "winst", "oud", "verkoop"]);
});

test("sorteerTransacties: muteert de meegegeven array niet", () => {
    const kopie = RIJEN.map(r => ({ ...r }));
    sorteerTransacties(RIJEN, "datum_tijd", "asc");
    assert.deepEqual(RIJEN, kopie);
});

test("sorteerTransacties: koers=null (bv. corporate-action-rij) blijft onderaan bij aflopend", () => {
    const metNull = RIJEN.concat([{ datum: "2024-04-01", product: "D BV", aantal: 1, koers: null, totaal_eur: 0 }]);
    const resultaat = sorteerTransacties(metNull, "koers", "desc");
    assert.equal(resultaat[resultaat.length - 1].koers, null);
});

test("sorteerTransacties: datum_tijd op dezelfde dag sorteert op tijd, null (onbekend) eerst", () => {
    const metTijd = [
        { datum: "2024-01-01", product: "A BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: "13:39" },
        { datum: "2024-01-01", product: "B BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: "09:05" },
        { datum: "2024-01-01", product: "C BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: null },
    ];
    const resultaat = sorteerTransacties(metTijd, "datum_tijd", "asc");
    assert.deepEqual(resultaat.map(r => r.tijd), [null, "09:05", "13:39"]);
});

test("sorteerTransacties: datum_tijd weegt datum zwaarder dan tijd", () => {
    const rijen = [
        { datum: "2024-01-02", product: "A BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: "09:00" },
        { datum: "2024-01-01", product: "B BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: "17:30" },
        { datum: "2024-01-02", product: "C BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: "08:15" },
    ];
    // Nieuwste eerst: 2 jan 09:00, 2 jan 08:15, dan pas 1 jan 17:30 (hoge tijd
    // van een oudere dag mag niet vóór een nieuwere dag komen).
    const resultaat = sorteerTransacties(rijen, "datum_tijd", "desc");
    assert.deepEqual(resultaat.map(r => r.product), ["A BV", "C BV", "B BV"]);
});

test("sorteerTransacties: transactiekosten=null blijft onderaan bij aflopend", () => {
    const metKosten = [
        { datum: "2024-01-01", product: "A BV", aantal: 1, koers: 1, totaal_eur: 1, transactiekosten: -2 },
        { datum: "2024-01-01", product: "B BV", aantal: 1, koers: 1, totaal_eur: 1, transactiekosten: null },
        { datum: "2024-01-01", product: "C BV", aantal: 1, koers: 1, totaal_eur: 1, transactiekosten: -0.5 },
    ];
    const resultaat = sorteerTransacties(metKosten, "transactiekosten", "desc");
    assert.equal(resultaat[resultaat.length - 1].transactiekosten, null);
});

test("totaalPaginas: rond af naar boven", () => {
    assert.equal(totaalPaginas(25, 25), 1);
    assert.equal(totaalPaginas(26, 25), 2);
    assert.equal(totaalPaginas(50, 25), 2);
});

test("totaalPaginas: minimaal 1 pagina, ook bij 0 rijen", () => {
    assert.equal(totaalPaginas(0, 25), 1);
});

test("paginaKnoppen: één pagina", () => {
    assert.deepEqual(paginaKnoppen(1, 1), [1]);
});

test("paginaKnoppen: tot en met 7 pagina's alle nummers", () => {
    assert.deepEqual(paginaKnoppen(3, 7), [1, 2, 3, 4, 5, 6, 7]);
    assert.deepEqual(paginaKnoppen(1, 2), [1, 2]);
});

test("paginaKnoppen: begin", () => {
    assert.deepEqual(paginaKnoppen(1, 120), [1, 2, "…", 120]);
    assert.deepEqual(paginaKnoppen(3, 120), [1, 2, 3, 4, "…", 120]);
});

test("paginaKnoppen: midden", () => {
    assert.deepEqual(paginaKnoppen(7, 120), [1, "…", 6, 7, 8, "…", 120]);
    assert.deepEqual(paginaKnoppen(4, 120), [1, 2, 3, 4, 5, "…", 120]);
});

test("paginaKnoppen: eind", () => {
    assert.deepEqual(paginaKnoppen(120, 120), [1, "…", 119, 120]);
    assert.deepEqual(paginaKnoppen(118, 120), [1, "…", 117, 118, 119, 120]);
});

test("paginaKnoppen: 7 pagina's zonder ellips, 8 pagina's met gat", () => {
    assert.deepEqual(paginaKnoppen(4, 7), [1, 2, 3, 4, 5, 6, 7]);
    assert.deepEqual(paginaKnoppen(1, 8), [1, 2, "…", 8]);
});

test("pagineer: knipt de juiste rijen per pagina", () => {
    const rijen = Array.from({ length: 60 }, (_, i) => ({ id: i + 1 }));
    assert.deepEqual(pagineer(rijen, 25, 1).map(r => r.id), Array.from({ length: 25 }, (_, i) => i + 1));
    assert.deepEqual(pagineer(rijen, 25, 3).map(r => r.id), [51, 52, 53, 54, 55, 56, 57, 58, 59, 60]);
});

test("pagineer: klemt een te hoog paginanummer op de laatste pagina", () => {
    const rijen = Array.from({ length: 30 }, (_, i) => ({ id: i + 1 }));
    assert.deepEqual(pagineer(rijen, 25, 99).map(r => r.id), Array.from({ length: 5 }, (_, i) => i + 26));
});

test("pagineer: klemt een paginanummer < 1 op pagina 1", () => {
    const rijen = Array.from({ length: 30 }, (_, i) => ({ id: i + 1 }));
    assert.deepEqual(pagineer(rijen, 25, 0)[0].id, 1);
});

const PROMOTIE = { bedrag_eur: 7, datum: "2025-04-10", regels: [{ datum: "2025-04-10", bedrag_eur: 7 }] };
const TOTALEN_KOSTEN = { totale_transactiekosten: 35, totale_transactiekosten_betaald: 42 };

test("promotieLabelTekst en promotieUitlegTekst: bedrag op 2 decimalen, woord 'toegerekend'", () => {
    assert.equal(promotieLabelTekst(1), "promotie −€1,00");
    assert.equal(promotieLabelTekst(1.5), "promotie −€1,50");
    assert.equal(promotieUitlegTekst(3), "DEGIRO-promotie: €3,00 kostenkorting, toegerekend aan deze transactie.");
});

test("promotieSamenvattingTekst: één promoregel, volledig verrekend, datum dd-mm-jjjj", () => {
    assert.equal(
        promotieSamenvattingTekst(PROMOTIE, TOTALEN_KOSTEN),
        "Promotie DEGIRO 10-04-2025: €7,00 kostenkorting, toegerekend aan de eerste transacties. "
        + "Netto kosten: €35,00 (betaald €42,00).",
    );
});

test("promotieSamenvattingTekst: gedeeltelijk verrekend noemt het restant", () => {
    const promotie = { bedrag_eur: 4.5, datum: "2025-04-10", regels: [{ datum: "2025-04-10", bedrag_eur: 7 }] };
    assert.match(promotieSamenvattingTekst(promotie, null), /€4,50 kostenkorting.*€2,50 niet verrekend/);
});

test("promotieSamenvattingTekst: meerdere promoregels", () => {
    const promotie = {
        bedrag_eur: 12, datum: "2025-09-01",
        regels: [{ datum: "2025-04-10", bedrag_eur: 7 }, { datum: "2025-06-01", bedrag_eur: 3 }, { datum: "2025-09-01", bedrag_eur: 2 }],
    };
    assert.match(promotieSamenvattingTekst(promotie, TOTALEN_KOSTEN),
        /^Promoties DEGIRO 10-04-2025, 01-06-2025 en 01-09-2025: €12,00 kostenkorting/);
});

test("promotieSamenvattingTekst: zonder promotie of zonder toegepaste korting null", () => {
    assert.equal(promotieSamenvattingTekst(null, TOTALEN_KOSTEN), null);
    assert.equal(promotieSamenvattingTekst(undefined, undefined), null);
    assert.equal(promotieSamenvattingTekst({ bedrag_eur: 0, datum: "2025-04-10", regels: [] }, TOTALEN_KOSTEN), null);
});

test("promotieSamenvattingTekst: zonder statistieken-totalen geen netto-zin", () => {
    assert.equal(
        promotieSamenvattingTekst(PROMOTIE, undefined),
        "Promotie DEGIRO 10-04-2025: €7,00 kostenkorting, toegerekend aan de eerste transacties.",
    );
});

test("leesTransactiesAntwoord: nieuwe vorm met en zonder promotie", () => {
    const lijst = [{ datum: "2025-01-01", kostenkorting_eur: 0 }];
    assert.deepEqual(leesTransactiesAntwoord({ lijst, promotie: PROMOTIE }), { lijst, promotie: PROMOTIE });
    assert.deepEqual(leesTransactiesAntwoord({ lijst, promotie: null }), { lijst, promotie: null });
    assert.deepEqual(leesTransactiesAntwoord({ lijst }), { lijst, promotie: null });
});

test("leesTransactiesAntwoord: upload-antwoord bij niet opslaan", () => {
    const lijst = [{ datum: "2025-01-01", kostenkorting_eur: 7 }];
    assert.deepEqual(leesTransactiesAntwoord({ transacties_lijst: lijst, transacties_promotie: PROMOTIE }), { lijst, promotie: PROMOTIE });
    assert.deepEqual(leesTransactiesAntwoord({ transacties_lijst: lijst, transacties_promotie: null }), { lijst, promotie: null });
    assert.deepEqual(leesTransactiesAntwoord({ transacties_lijst: [] }), { lijst: [], promotie: null });
    assert.deepEqual(leesTransactiesAntwoord({}), { lijst: [], promotie: null });
});

test("promotieSamenvattingTekst: zelfde tekst uit /transacties en uit het upload-antwoord", () => {
    const promotie = {
        bedrag_eur: 10, datum: "2025-06-01",
        regels: [{ datum: "2025-04-10", bedrag_eur: 7 }, { datum: "2025-06-01", bedrag_eur: 3 }],
    };
    const uitRoute = leesTransactiesAntwoord({ lijst: [], promotie }).promotie;
    const uitUpload = leesTransactiesAntwoord({ transacties_lijst: [], transacties_promotie: promotie }).promotie;
    const verwacht = "Promoties DEGIRO 10-04-2025 en 01-06-2025: €10,00 kostenkorting, toegerekend aan de eerste transacties.";
    assert.equal(promotieSamenvattingTekst(uitRoute, undefined), verwacht);
    assert.equal(promotieSamenvattingTekst(uitUpload, undefined), verwacht);
});

test("transactieMobielRol: product, datum, aantal en totaal compact; de rest in de details", () => {
    assert.equal(transactieMobielRol("product"), "titel");
    assert.equal(transactieMobielRol("datum_tijd"), "subregel");
    assert.equal(transactieMobielRol("aantal"), "subregel");
    assert.equal(transactieMobielRol("totaal_eur"), "waarde");
    for (const sleutel of ["isin", "beurs", "koers", "wisselkoers", "transactiekosten", "resultaat_eur", "dividend_eur"]) {
        assert.equal(transactieMobielRol(sleutel), "detail", sleutel);
    }
});

test("datumTijdTekst: dd-mm-jjjj, met tijd als die er is", () => {
    assert.equal(datumTijdTekst({ datum: "2025-04-10", tijd: "09:30" }), "10-04-2025 09:30");
    assert.equal(datumTijdTekst({ datum: "2025-04-10", tijd: null }), "10-04-2025");
    assert.equal(datumTijdTekst({ datum: "2025-04-10" }), "10-04-2025");
});

test("aantalStuksTekst: enkelvoud bij 1 stuk, verkoop negatief", () => {
    assert.equal(aantalStuksTekst(1), "1 stuk");
    assert.equal(aantalStuksTekst(-1), "-1 stuk");
    assert.equal(aantalStuksTekst(12), "12 stuks");
    assert.equal(aantalStuksTekst(-2.5), "-2,5 stuks");
    assert.equal(aantalStuksTekst(1234), "1.234 stuks");
});

test("promotieTekenTekst: alleen bij een toegerekende korting", () => {
    assert.equal(promotieTekenTekst({ kostenkorting_eur: 3 }), "promotie");
    assert.equal(promotieTekenTekst({ kostenkorting_eur: 0 }), "");
    assert.equal(promotieTekenTekst({}), "");
});
