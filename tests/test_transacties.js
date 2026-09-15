// Unit tests voor de rekenkern van het Transacties-tabblad (static/js/transacties.js).
// Draait via Node's ingebouwde testrunner, geen extra dependency nodig:
//   node --test tests/test_transacties.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { sorteerTransacties, totaalPaginas, pagineer } = require("../static/js/transacties.js");

const RIJEN = [
    { datum: "2024-03-01", product: "B BV", aantal: 5, koers: 20, totaal_eur: 100 },
    { datum: "2024-01-01", product: "A BV", aantal: 20, koers: 5, totaal_eur: 100 },
    { datum: "2024-02-01", product: "C BV", aantal: 1, koers: 300, totaal_eur: 300 },
];

test("sorteerTransacties: datum oplopend", () => {
    const resultaat = sorteerTransacties(RIJEN, "datum", "asc");
    assert.deepEqual(resultaat.map(r => r.datum), ["2024-01-01", "2024-02-01", "2024-03-01"]);
});

test("sorteerTransacties: datum aflopend (meest recent eerst)", () => {
    const resultaat = sorteerTransacties(RIJEN, "datum", "desc");
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

test("sorteerTransacties: muteert de meegegeven array niet", () => {
    const kopie = RIJEN.map(r => ({ ...r }));
    sorteerTransacties(RIJEN, "datum", "asc");
    assert.deepEqual(RIJEN, kopie);
});

test("sorteerTransacties: koers=null (bv. corporate-action-rij) blijft onderaan bij aflopend", () => {
    const metNull = RIJEN.concat([{ datum: "2024-04-01", product: "D BV", aantal: 1, koers: null, totaal_eur: 0 }]);
    const resultaat = sorteerTransacties(metNull, "koers", "desc");
    assert.equal(resultaat[resultaat.length - 1].koers, null);
});

test("sorteerTransacties: tijd oplopend, null (nog niet herbepaald) eerst", () => {
    const metTijd = [
        { datum: "2024-01-01", product: "A BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: "13:39" },
        { datum: "2024-01-01", product: "B BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: "09:05" },
        { datum: "2024-01-01", product: "C BV", aantal: 1, koers: 1, totaal_eur: 1, tijd: null },
    ];
    const resultaat = sorteerTransacties(metTijd, "tijd", "asc");
    assert.deepEqual(resultaat.map(r => r.tijd), [null, "09:05", "13:39"]);
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
