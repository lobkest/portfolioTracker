// Unit tests voor de periodeknoppen (static/js/periode.js).
//   node --test tests/test_periode.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { GRAFIEK_PERIODES, grensDatum, startIndexVoorPeriode, periodeBeschikbaar } = require("../static/js/periode.js");

const LABELS = ["2024-01-02", "2024-06-03", "2025-01-02", "2025-06-02", "2025-10-15", "2026-01-05", "2026-06-01", "2026-10-15"];

test("grensDatum: 12 maanden terug, schrikkeldag valt terug op 28 februari", () => {
    assert.equal(grensDatum("2026-10-16", 12), "2025-10-16");
    assert.equal(grensDatum("2028-02-29", 12), "2027-02-28");
    assert.equal(grensDatum("2026-01-31", 1), "2025-12-31");
    assert.equal(grensDatum("2026-03-31", 1), "2026-02-28");
});

test("1 jaar terug midden in de data: grens 2025-10-16, dus label 2026-01-05", () => {
    assert.equal(startIndexVoorPeriode(LABELS, 12, "2026-10-16"), 5);
});

test("data korter dan een jaar: index 0 en niet beschikbaar", () => {
    const kort = ["2026-03-02", "2026-06-01", "2026-10-15"];
    assert.equal(startIndexVoorPeriode(kort, 12, "2026-10-16"), 0);
    assert.equal(periodeBeschikbaar(kort, 12, "2026-10-16"), false);
});

test("data ouder dan de grens: beschikbaar", () => {
    assert.equal(periodeBeschikbaar(LABELS, 12, "2026-10-16"), true);
});

test("grens op een dag zonder notering: eerstvolgende label", () => {
    // 2025-10-18 is een zaterdag; het eerstvolgende label is maandag.
    const labels = ["2025-10-16", "2025-10-17", "2025-10-20", "2026-10-16"];
    assert.equal(startIndexVoorPeriode(labels, 12, "2026-10-18"), 2);
});

test("grens exact op een label: dat label", () => {
    assert.equal(startIndexVoorPeriode(["2025-10-15", "2026-10-15"], 12, "2026-10-15"), 0);
});

test("schrikkeldag als vandaag", () => {
    const labels = ["2027-02-26", "2027-02-27", "2027-03-01", "2028-02-29"];
    assert.equal(startIndexVoorPeriode(labels, 12, "2028-02-29"), 2);
});

test("grens ná het laatste label: index van het laatste label", () => {
    assert.equal(startIndexVoorPeriode(["2024-01-02", "2024-02-01"], 12, "2026-10-16"), 1);
});

test("maanden null: altijd 0 en beschikbaar", () => {
    assert.equal(startIndexVoorPeriode(LABELS, null, "2026-10-16"), 0);
    assert.equal(periodeBeschikbaar([], null, "2026-10-16"), true);
});

test("GRAFIEK_PERIODES: Alles en 1 jaar", () => {
    assert.deepEqual(GRAFIEK_PERIODES.map(p => p.id), ["alles", "1j"]);
});
