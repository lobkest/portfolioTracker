// Unit tests voor de melding "rekeningoverzicht beslaat niet de hele periode" (static/js/rekeningoverzicht.js).
//   node --test tests/test_rekeningoverzicht.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { rekeningoverzichtMelding } = require("../static/js/rekeningoverzicht.js");

const ONVOLLEDIG = {
    volledig: false,
    redenen: ["begint_na_eerste_transactie", "netto_gestort_nul"],
    redenen_tekst: ["Het rekeningoverzicht begint op 01-10-2026, de eerste transactie is van 27-01-2021.", "Netto gestort is nul."],
    eerste_regel_datum: "2026-10-01",
    laatste_regel_datum: "2026-10-08",
    eerste_transactie_datum: "2021-01-27",
};

test("volledig overzicht of geen veld: geen melding", () => {
    assert.equal(rekeningoverzichtMelding({ ...ONVOLLEDIG, volledig: true, redenen_tekst: [] }, true), null);
    assert.equal(rekeningoverzichtMelding(undefined, true), null);
    assert.equal(rekeningoverzichtMelding(null, false), null);
});

test("onvolledig overzicht: kop, redenen en periode met datums als dd-mm-jjjj", () => {
    const m = rekeningoverzichtMelding(ONVOLLEDIG, true);
    assert.match(m.kop, /beslaat niet de hele periode/);
    assert.match(m.kop, /Netto gestort, vrije ruimte \(cash\) en Totaal W\/V/);
    assert.deepEqual(m.redenen, ONVOLLEDIG.redenen_tekst);
    assert.equal(m.periode, "overzicht: 01-10-2026 t/m 08-10-2026; transacties vanaf 27-01-2021");
});

test("met code: link naar Instellingen", () => {
    const m = rekeningoverzichtMelding(ONVOLLEDIG, true);
    assert.equal(m.linkTekst, "Upload een volledig overzicht");
    assert.equal(m.linkNaarInstellingen, true);
});

test("niet opslaan: alleen tekst, geen link", () => {
    const m = rekeningoverzichtMelding(ONVOLLEDIG, false);
    assert.equal(m.linkTekst, "Upload bij de volgende analyse het volledige rekeningoverzicht");
    assert.equal(m.linkNaarInstellingen, false);
});

test("zonder datums geen periode", () => {
    const m = rekeningoverzichtMelding({ volledig: false, redenen_tekst: ["x"] }, true);
    assert.equal(m.periode, null);
});
