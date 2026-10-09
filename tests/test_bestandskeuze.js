// Unit tests voor de rekenkern van de bestand-wegklikken-rij
// (static/js/bestandskeuze.js). Draait via Node's ingebouwde testrunner:
//   node --test tests/test_bestandskeuze.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    bestandSelectieWeergave, bijwerkenSuccesTekst, isExcelBestandsnaam,
    uploadKnopActief, normaliseerCode, ophaalKnopActief,
} = require("../static/js/bestandskeuze.js");

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

test("bijwerkenSuccesTekst: aantal nieuwe transacties, enkelvoud en meervoud", () => {
    assert.equal(bijwerkenSuccesTekst({ nieuwe_transacties: 0, dividend_verwerkt: false }), "Geen nieuwe transacties gevonden.");
    assert.equal(bijwerkenSuccesTekst({ nieuwe_transacties: 1, dividend_verwerkt: false }), "1 nieuwe transactie toegevoegd.");
    assert.equal(bijwerkenSuccesTekst({ nieuwe_transacties: 3, dividend_verwerkt: false }), "3 nieuwe transacties toegevoegd.");
});

test("bijwerkenSuccesTekst: vermeldt een verwerkt rekeningoverzicht", () => {
    assert.equal(
        bijwerkenSuccesTekst({ nieuwe_transacties: 0, dividend_verwerkt: true }),
        "Geen nieuwe transacties gevonden. Rekeningoverzicht (dividend) verwerkt.",
    );
});

test("excel-bestandsnaam: .xls en .xlsx, hoofdletters maken niet uit", () => {
    assert.equal(isExcelBestandsnaam("Transactions.xls"), true);
    assert.equal(isExcelBestandsnaam("Account.XLSX"), true);
    assert.equal(isExcelBestandsnaam("Account.csv"), false);
    assert.equal(isExcelBestandsnaam("xlsx"), false);
    assert.equal(isExcelBestandsnaam(undefined), false);
});

test("uploadknop alleen actief met een transactiebestand", () => {
    assert.equal(uploadKnopActief(0), false);
    assert.equal(uploadKnopActief(1), true);
});

test("code normaliseren: spaties weg, hoofdletters", () => {
    assert.equal(normaliseerCode("  abc "), "ABC");
    assert.equal(normaliseerCode(null), "");
});

test("ophaalknop alleen actief bij precies de codelengte", () => {
    assert.equal(ophaalKnopActief("", 3), false);
    assert.equal(ophaalKnopActief("ab", 3), false);
    assert.equal(ophaalKnopActief(" abc ", 3), true);
    assert.equal(ophaalKnopActief("ABCD", 3), false);
});
