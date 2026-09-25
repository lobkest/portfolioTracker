// Unit tests voor de rekenkern van de bestand-wegklikken-rij
// (static/js/bestandskeuze.js). Draait via Node's ingebouwde testrunner:
//   node --test tests/test_bestandskeuze.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { bestandSelectieWeergave } = require("../static/js/bestandskeuze.js");

test("geen bestand gekozen: rij verborgen, geen tekst", () => {
    assert.deepEqual(bestandSelectieWeergave([]), { zichtbaar: false, tekst: "" });
});

test("null/undefined telt als geen bestand", () => {
    assert.deepEqual(bestandSelectieWeergave(null), { zichtbaar: false, tekst: "" });
    assert.deepEqual(bestandSelectieWeergave(undefined), { zichtbaar: false, tekst: "" });
});

test("één bestand: rij zichtbaar met de bestandsnaam", () => {
    assert.deepEqual(
        bestandSelectieWeergave(["Transactions.xlsx"]),
        { zichtbaar: true, tekst: "Transactions.xlsx" }
    );
});

test("meerdere bestanden: rij zichtbaar met het aantal", () => {
    assert.deepEqual(
        bestandSelectieWeergave(["a.xlsx", "b.xls"]),
        { zichtbaar: true, tekst: "2 bestanden" }
    );
});
