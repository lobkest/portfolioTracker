// Unit tests voor de datasets van de dividendgrafiek (static/js/dividend.js).
//   node --test tests/test_dividend.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    DIVIDEND_START_DAGEN_VOOR_EERSTE_UITKERING, bepaalDividendStart, bouwDividendTrapreeksen, bouwDividendDatasets,
} = require("../static/js/dividend.js");

const NAMEN = { "AKZA.AS": "AKZO NOBEL NV", "ASML.AS": "ASML" };
const naam = ticker => NAMEN[ticker] || ticker;
const kleur = ticker => `kleur-${ticker}`;

test("één uitkering: start op 0, sprong op de uitkeringsdatum, vlak tot vandaag", () => {
    const trap = bouwDividendTrapreeksen(
        { datums: ["2026-05-07"], per_ticker: { "AKZA.AS": [6.54] } }, "2025-01-15", "2026-10-09");
    assert.deepEqual(trap.start, { datum: "2025-01-15", bron: "eerste_transactie" });
    assert.deepEqual(trap.per_ticker["AKZA.AS"], [
        { x: "2025-01-15", y: 0 },
        { x: "2026-05-07", y: 6.54 },
        { x: "2026-10-09", y: 6.54 },
    ]);
    const [dataset] = bouwDividendDatasets(trap, naam, kleur);
    // "before": vlak op de vorige waarde tot het volgende punt, dan de sprong.
    assert.equal(dataset.stepped, "before");
    assert.equal(dataset.fill, true);
    assert.equal(dataset.pointRadius, 0);
});

test("twee uitkeringen bij verschillende ETF's: zelfde start- en einddatum en x-waarden", () => {
    const trap = bouwDividendTrapreeksen(
        { datums: ["2025-05-07", "2026-05-07"], per_ticker: { "AKZA.AS": [6.54, 6.54], "ASML.AS": [0, 3.2] } },
        "2024-03-01", "2026-10-09");
    const xPerTicker = Object.values(trap.per_ticker).map(r => r.map(p => p.x));
    assert.deepEqual(xPerTicker[0], ["2024-03-01", "2025-05-07", "2026-05-07", "2026-10-09"]);
    assert.deepEqual(xPerTicker[1], xPerTicker[0]);
    assert.deepEqual(trap.per_ticker["ASML.AS"].map(p => p.y), [0, 0, 3.2, 3.2]);
    assert.deepEqual(trap.per_ticker["AKZA.AS"].map(p => p.y), [0, 6.54, 6.54, 6.54]);
});

test("zonder eerste transactie: eerste uitkering min 30 dagen", () => {
    assert.equal(DIVIDEND_START_DAGEN_VOOR_EERSTE_UITKERING, 30);
    assert.deepEqual(bepaalDividendStart("2026-05-07", undefined),
        { datum: "2026-04-07", bron: "eerste_uitkering_min_dagen" });
    // Over een maand- en jaargrens: 15-01-2026 - 30 dagen = 16-12-2025.
    assert.equal(bepaalDividendStart("2026-01-15", null).datum, "2025-12-16");
});

test("eerste transactie op of na de eerste uitkering: terugval op min 30 dagen", () => {
    assert.equal(bepaalDividendStart("2026-05-07", "2026-05-07").bron, "eerste_uitkering_min_dagen");
    assert.equal(bepaalDividendStart("2026-05-07", "2026-06-01").datum, "2026-04-07");
});

test("uitkering vandaag: geen dubbel eindpunt", () => {
    const trap = bouwDividendTrapreeksen(
        { datums: ["2026-10-09"], per_ticker: { "AKZA.AS": [1] } }, "2026-01-02", "2026-10-09");
    assert.deepEqual(trap.per_ticker["AKZA.AS"].map(p => p.x), ["2026-01-02", "2026-10-09"]);
});

test("meerdere aandelen: één dataset per ticker met eigen naam en kleur", () => {
    const trap = bouwDividendTrapreeksen(
        { datums: ["2026-05-07"], per_ticker: { "AKZA.AS": [6.54], "NL0000000000": [1] } }, null, "2026-10-09");
    const datasets = bouwDividendDatasets(trap, naam, kleur);
    assert.deepEqual(datasets.map(d => d.label), ["AKZO NOBEL NV", "NL0000000000"]);
    assert.deepEqual(datasets.map(d => d.borderColor), ["kleur-AKZA.AS", "kleur-NL0000000000"]);
    assert.deepEqual(datasets.map(d => d.backgroundColor), ["kleur-AKZA.AS", "kleur-NL0000000000"]);
});

test("geen dividenden: geen datasets", () => {
    const trap = bouwDividendTrapreeksen({ datums: [], per_ticker: {} }, "2025-01-01", "2026-10-09");
    assert.equal(trap.start, null);
    assert.deepEqual(bouwDividendDatasets(trap, naam, kleur), []);
});

const { dividendSubregel, dividendSubwaarde } = require("../static/js/dividend.js");

test("dividendSubregel: EUR wordt niet genoemd, vreemde valuta wel", () => {
    assert.equal(dividendSubregel("01-10-2026", "EUR"), "01-10-2026");
    assert.equal(dividendSubregel("01-10-2026", null), "01-10-2026");
    assert.equal(dividendSubregel("01-10-2026", "USD"), "01-10-2026 · USD");
});

test("dividendSubwaarde: met belasting bruto en aftrek, zonder alleen bruto", () => {
    assert.equal(dividendSubwaarde(25.11, -3.77), "bruto €25,11 · -€3,77");
    assert.equal(dividendSubwaarde(25.11, 3.77), "bruto €25,11 · -€3,77");
    assert.equal(dividendSubwaarde(25.11, 0), "bruto €25,11");
    assert.equal(dividendSubwaarde(25.11, -0.001), "bruto €25,11");
    assert.equal(dividendSubwaarde(25.11, null), "bruto €25,11");
});
