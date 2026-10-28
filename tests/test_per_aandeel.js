// Unit tests voor static/js/per_aandeel.js.
//   node --test tests/test_per_aandeel.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { aandeelLandSectorRegels, etfPositieWaarde, etfHoldingsBijschrift } = require("../static/js/per_aandeel.js");

test("bekend land en sector", () => {
    const regels = aandeelLandSectorRegels({ SHOP: { land: "Canada", sector: "Technology" } }, "SHOP");
    assert.deepEqual(regels, [
        { label: "Land", tekst: "Canada", onbekend: false },
        { label: "Sector", tekst: "Technology", onbekend: false },
    ]);
});

test("Unknown en ontbrekend worden 'onbekend'", () => {
    const regels = aandeelLandSectorRegels({ X: { land: "Unknown", sector: null } }, "X");
    assert.deepEqual(regels, [
        { label: "Land", tekst: "onbekend", onbekend: true },
        { label: "Sector", tekst: "onbekend", onbekend: true },
    ]);
});

test("geen entry (ETF) of nog geen verrijking geeft null", () => {
    assert.equal(aandeelLandSectorRegels({ SHOP: { land: "Canada", sector: "Technology" } }, "VWRL.AS"), null);
    assert.equal(aandeelLandSectorRegels(undefined, "SHOP"), null);
});

test("etfPositieWaarde: waarde van een positie in bezit, anders null", () => {
    const stats = { posities: [{ ticker: "VWCE", huidige_waarde: 1234.5 }, { ticker: "NUL", huidige_waarde: 0 }] };
    assert.equal(etfPositieWaarde(stats, "VWCE"), 1234.5);
    assert.equal(etfPositieWaarde(stats, "NUL"), 0);
    assert.equal(etfPositieWaarde(stats, "GESLOTEN"), null);
    assert.equal(etfPositieWaarde(null, "VWCE"), null);
});

test("etfHoldingsBijschrift: volledige lijst van de aanbieder", () => {
    const data = { bron: "provider_csv", aantal: 1500, gedekt_gewicht: 0.99, holdings: [{}] };
    assert.deepEqual(etfHoldingsBijschrift(data),
        { tekst: "Volledige lijst van de fondsaanbieder: 1500 bedrijven", grijs: false });
});

test("etfHoldingsBijschrift: alleen top 10 is grijs en noemt de dekking", () => {
    const data = { bron: "yfinance_top10", aantal: 10, gedekt_gewicht: 0.2345, holdings: [{}] };
    const bijschrift = etfHoldingsBijschrift(data);
    assert.equal(bijschrift.grijs, true);
    assert.match(bijschrift.tekst, /^Alleen de top 10 via Yahoo, samen 23[,.]5\s?% van het fonds$/);
});

test("etfHoldingsBijschrift: zonder holdings null", () => {
    assert.equal(etfHoldingsBijschrift({ bron: null, aantal: 0, gedekt_gewicht: 0, holdings: [] }), null);
    assert.equal(etfHoldingsBijschrift(null), null);
});
