// Unit tests voor static/js/etf_overlap.js (naamkeuze in de ETF-overlap-matrix).
//   node --test tests/test_etf_overlap.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { etfWeergaveNaam } = require("../static/js/etf_overlap.js");

const KORTE_NAMEN = {
    VWRL: { long_name: "Vanguard FTSE All-World UCITS ETF", voorstel: "Vanguard All-World" },
    IWDA: { long_name: null, voorstel: null },
};

test("korte naam aanwezig: die wint van de huidige naam", () => {
    assert.equal(etfWeergaveNaam("VWRL", "Mijn VWRL", KORTE_NAMEN), "Vanguard All-World");
});

test("voorstel null: huidige naam blijft", () => {
    assert.equal(etfWeergaveNaam("IWDA", "iShares World", KORTE_NAMEN), "iShares World");
});

test("ticker ontbreekt in de korte namen: huidige naam blijft", () => {
    assert.equal(etfWeergaveNaam("EMIM", "iShares EM", KORTE_NAMEN), "iShares EM");
});

test("geen korte naam en geen huidige naam: de ticker", () => {
    assert.equal(etfWeergaveNaam("EMIM", undefined, KORTE_NAMEN), "EMIM");
    assert.equal(etfWeergaveNaam("IWDA", "", KORTE_NAMEN), "IWDA");
});

test("korte namen nog niet geladen (null): huidige naam, anders ticker", () => {
    assert.equal(etfWeergaveNaam("VWRL", "Mijn VWRL", null), "Mijn VWRL");
    assert.equal(etfWeergaveNaam("VWRL", null, null), "VWRL");
});
