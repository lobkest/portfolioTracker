// Unit tests voor de melding "transactiebestand lijkt niet de hele periode te beslaan" (static/js/transactiebestand.js).
//   node --test tests/test_transactiebestand.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { transactiebestandMelding } = require("../static/js/transactiebestand.js");

const EEN_REDEN = {
    volledig: false,
    redenen: [{ code: "negatief_aantal", tekst: "Er is een verkoop van ACME op 12-03-2025 zonder voldoende eerdere aankopen; eerdere transacties lijken te ontbreken.", items: [] }],
    eerste_transactie_datum: "2025-03-12",
    laatste_transactie_datum: "2026-10-08",
};

test("volledig bestand of geen veld: geen melding", () => {
    assert.equal(transactiebestandMelding({ ...EEN_REDEN, volledig: true, redenen: [] }, true), null);
    assert.equal(transactiebestandMelding(undefined, true), null);
    assert.equal(transactiebestandMelding(null, false), null);
});

test("één reden: voorzichtige kop, reden en periode met datums als dd-mm-jjjj", () => {
    const m = transactiebestandMelding(EEN_REDEN, true);
    assert.match(m.kop, /lijkt niet de hele periode te beslaan/);
    assert.deepEqual(m.redenen, [EEN_REDEN.redenen[0].tekst]);
    assert.equal(m.periode, "transacties: 12-03-2025 t/m 08-10-2026");
});

test("meerdere redenen blijven in volgorde, inclusief 'en N andere' uit de backend-tekst", () => {
    const status = {
        ...EEN_REDEN,
        redenen: [
            { code: "negatief_aantal", tekst: "Er zijn verkopen zonder voldoende eerdere aankopen: A (01-01-2025), B (02-01-2025) en 2 andere." },
            { code: "dividend_zonder_positie", tekst: "Er is dividend ontvangen zonder bekende positie: C (03-01-2025)." },
        ],
    };
    const m = transactiebestandMelding(status, true);
    assert.equal(m.redenen.length, 2);
    assert.match(m.redenen[0], /en 2 andere/);
    assert.match(m.redenen[1], /dividend/);
});

test("met code: link naar Instellingen", () => {
    const m = transactiebestandMelding(EEN_REDEN, true);
    assert.equal(m.linkTekst, "Upload een volledig transactiebestand");
    assert.equal(m.linkNaarInstellingen, true);
});

test("niet opslaan: alleen tekst, geen link", () => {
    const m = transactiebestandMelding(EEN_REDEN, false);
    assert.equal(m.linkTekst, "Upload bij de volgende analyse het volledige transactiebestand");
    assert.equal(m.linkNaarInstellingen, false);
});

test("zonder datums geen periode", () => {
    assert.equal(transactiebestandMelding({ volledig: false, redenen: [] }, true).periode, null);
});
