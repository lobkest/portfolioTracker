// Unit tests voor de datasets van de dividendgrafiek (static/js/dividend.js).
//   node --test tests/test_dividend.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { DIVIDEND_ENKEL_PUNT_RADIUS, bouwDividendDatasets } = require("../static/js/dividend.js");

const NAMEN = { "AKZA.AS": "AKZO NOBEL NV", "ASML.AS": "ASML" };
const naam = ticker => NAMEN[ticker] || ticker;
const kleur = ticker => `kleur-${ticker}`;

test("één dividend: het enige punt is zichtbaar", () => {
    const datasets = bouwDividendDatasets(
        { datums: ["2026-05-07"], per_ticker: { "AKZA.AS": [6.54] } }, naam, kleur);
    assert.equal(datasets.length, 1);
    assert.equal(datasets[0].label, "AKZO NOBEL NV");
    assert.deepEqual(datasets[0].data, [6.54]);
    assert.equal(datasets[0].pointRadius, DIVIDEND_ENKEL_PUNT_RADIUS);
    assert.ok(datasets[0].pointRadius > 0);
    assert.ok(datasets[0].pointHoverRadius >= datasets[0].pointRadius);
});

test("meerdere dividenden van één aandeel: lijn zonder punten, zoals voorheen", () => {
    const datasets = bouwDividendDatasets(
        { datums: ["2025-05-07", "2026-05-07"], per_ticker: { "AKZA.AS": [6.54, 13.08] } }, naam, kleur);
    assert.equal(datasets.length, 1);
    assert.deepEqual(datasets[0].data, [6.54, 13.08]);
    assert.equal(datasets[0].pointRadius, 0);
    assert.equal(datasets[0].pointHoverRadius, 4);
    assert.equal(datasets[0].fill, true);
    assert.equal(datasets[0].borderWidth, 1.5);
});

test("meerdere aandelen: één dataset per ticker met eigen naam en kleur", () => {
    const datasets = bouwDividendDatasets(
        { datums: ["2025-05-07", "2026-05-07"], per_ticker: { "AKZA.AS": [6.54, 6.54], "ASML.AS": [0, 3.2] } },
        naam, kleur);
    assert.deepEqual(datasets.map(d => d.label), ["AKZO NOBEL NV", "ASML"]);
    assert.deepEqual(datasets.map(d => d.borderColor), ["kleur-AKZA.AS", "kleur-ASML.AS"]);
    assert.deepEqual(datasets.map(d => d.backgroundColor), ["kleur-AKZA.AS", "kleur-ASML.AS"]);
    assert.deepEqual(datasets[1].data, [0, 3.2]);
    assert.ok(datasets.every(d => d.pointRadius === 0));
});

test("meerdere aandelen op dezelfde ene datum: alle punten zichtbaar", () => {
    const datasets = bouwDividendDatasets(
        { datums: ["2026-05-07"], per_ticker: { "AKZA.AS": [6.54], "ASML.AS": [3.2] } }, naam, kleur);
    assert.equal(datasets.length, 2);
    assert.ok(datasets.every(d => d.pointRadius === DIVIDEND_ENKEL_PUNT_RADIUS));
});

test("onbekende ticker: de naam-functie bepaalt het label", () => {
    const datasets = bouwDividendDatasets(
        { datums: ["2026-05-07"], per_ticker: { "NL0000000000": [1] } }, naam, kleur);
    assert.equal(datasets[0].label, "NL0000000000");
});

test("geen dividenden: geen datasets", () => {
    assert.deepEqual(bouwDividendDatasets({ datums: [], per_ticker: {} }, naam, kleur), []);
});
