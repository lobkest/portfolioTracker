// Unit tests voor de rekenkern van het Diagnostiek-subtabblad
// (static/js/diagnostiek.js). Draait via Node's ingebouwde testrunner:
//   node --test tests/test_diagnostiek.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    voegMeldingenSamen, telPerNiveau, groepeerPerCategorie, diagnostiekTellerTekst,
} = require("../static/js/diagnostiek.js");

const m = (categorie, niveau, tekst, sleutel = tekst) => ({ categorie, niveau, tekst, sleutel });

test("voegMeldingenSamen: lege of ontbrekende invoer geeft lege lijst", () => {
    assert.deepEqual(voegMeldingenSamen(null, undefined), []);
    assert.deepEqual(voegMeldingenSamen([], []), []);
});

test("voegMeldingenSamen: nieuwe meldingen achteraan", () => {
    const a = m("Wisselkoersen", "GOED", "a");
    const b = m("Wisselkoersen", "INFO", "b");
    assert.deepEqual(voegMeldingenSamen([a], [b]), [a, b]);
});

test("voegMeldingenSamen: zelfde categorie + sleutel -> nieuwste wint, op de oude plek", () => {
    const oud = m("Wisselkoersen", "GOED", "USD oud", "USDEUR=X");
    const ander = m("Wisselkoersen", "GOED", "GBP", "GBPEUR=X");
    const nieuw = m("Wisselkoersen", "FOUT", "USD nieuw", "USDEUR=X");
    assert.deepEqual(voegMeldingenSamen([oud, ander], [nieuw]), [nieuw, ander]);
});

test("voegMeldingenSamen: zelfde sleutel in andere categorie blijft apart", () => {
    const a = m("Wisselkoersen", "GOED", "a", "x");
    const b = m("Koersen", "GOED", "b", "x");
    assert.equal(voegMeldingenSamen([a], [b]).length, 2);
});

test("voegMeldingenSamen: wijzigt de invoer niet", () => {
    const bestaand = [m("Wisselkoersen", "GOED", "a", "k")];
    voegMeldingenSamen(bestaand, [m("Wisselkoersen", "FOUT", "b", "k")]);
    assert.equal(bestaand[0].tekst, "a");
});

test("telPerNiveau: telt per niveau, onbekend niveau telt als INFO", () => {
    const telling = telPerNiveau([
        m("W", "FOUT", "1"), m("W", "LET_OP", "2"), m("W", "LET_OP", "3"), m("W", "RAAR", "4"),
    ]);
    assert.deepEqual(telling, { FOUT: 1, LET_OP: 2, INFO: 1, GOED: 0 });
    assert.deepEqual(telPerNiveau(null), { FOUT: 0, LET_OP: 0, INFO: 0, GOED: 0 });
});

test("groepeerPerCategorie: binnen categorie ernstigste eerst", () => {
    const groepen = groepeerPerCategorie([
        m("Wisselkoersen", "GOED", "g"), m("Wisselkoersen", "FOUT", "f"), m("Wisselkoersen", "LET_OP", "l"),
    ]);
    assert.equal(groepen.length, 1);
    assert.deepEqual(groepen[0].meldingen.map(x => x.niveau), ["FOUT", "LET_OP", "GOED"]);
});

test("groepeerPerCategorie: categorie met ernstigste melding eerst, anders volgorde van voorkomen", () => {
    const groepen = groepeerPerCategorie([
        m("A", "GOED", "a"), m("B", "INFO", "b"), m("C", "FOUT", "c"), m("D", "INFO", "d"),
    ]);
    assert.deepEqual(groepen.map(g => g.categorie), ["C", "B", "D", "A"]);
});

test("groepeerPerCategorie: lege invoer", () => {
    assert.deepEqual(groepeerPerCategorie([]), []);
    assert.deepEqual(groepeerPerCategorie(undefined), []);
});

test("diagnostiekTellerTekst: alleen niveaus met meldingen, ernstigste eerst", () => {
    assert.equal(diagnostiekTellerTekst({ FOUT: 1, LET_OP: 2, INFO: 0, GOED: 3 }), "1 fout, 2 let op, 3 goed");
    assert.equal(diagnostiekTellerTekst({ FOUT: 0, LET_OP: 0, INFO: 0, GOED: 0 }), "");
});
