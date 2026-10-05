// Unit tests voor static/js/per_aandeel.js.
//   node --test tests/test_per_aandeel.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { aandeelLandSectorRegels } = require("../static/js/per_aandeel.js");

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
