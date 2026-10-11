// Unit tests voor de pure logica van Statistieken (static/js/statistieken.js).
//   node --test tests/test_statistieken.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { jaarSubregel, dividendIsBekend, positieDividend, dividendTekst } = require("../static/js/statistieken.js");

test("jaarSubregel: onvolledig jaar toont dagen en ingelegd", () => {
    assert.equal(jaarSubregel({ dagen_verstreken: 282, pct_van_jaar: 77.0, ingelegd: 7933.58 }), "282 d · ingelegd €7.933,58");
});

test("jaarSubregel: volledig jaar alleen ingelegd, ook in een schrikkeljaar", () => {
    assert.equal(jaarSubregel({ dagen_verstreken: 365, pct_van_jaar: 100.0, ingelegd: 1200 }), "ingelegd €1.200,00");
    assert.equal(jaarSubregel({ dagen_verstreken: 366, pct_van_jaar: 100.0, ingelegd: -50 }), "ingelegd -€50,00");
});

test("jaarSubregel: 365 dagen in een schrikkeljaar is niet compleet", () => {
    assert.equal(jaarSubregel({ dagen_verstreken: 365, pct_van_jaar: 99.7, ingelegd: 0 }), "365 d · ingelegd €0,00");
});

test("dividendIsBekend: alleen als totalen.dividend_netto een getal is, ook 0", () => {
    assert.equal(dividendIsBekend({ dividend_netto: 12.5 }), true);
    assert.equal(dividendIsBekend({ dividend_netto: 0 }), true);
    assert.equal(dividendIsBekend({ dividend_netto: null }), false);
    assert.equal(dividendIsBekend({}), false);
    assert.equal(dividendIsBekend(undefined), false);
});

test("positieDividend: bekend bedrag en bekend nul blijven staan", () => {
    assert.equal(positieDividend({ dividend_ontvangen: 3.21 }, true), 3.21);
    assert.equal(positieDividend({ dividend_ontvangen: 0 }, true), 0);
});

test("positieDividend: onbekend bij ontbrekend veld of zonder rekeningoverzicht", () => {
    assert.equal(positieDividend({}, true), null);
    assert.equal(positieDividend({ dividend_ontvangen: null }, true), null);
    // Zonder rekeningoverzicht levert de backend per positie toch 0.
    assert.equal(positieDividend({ dividend_ontvangen: 0 }, false), null);
});

test("dividendTekst: bedrag, nul en onbekend", () => {
    assert.equal(dividendTekst(3.21), "€3,21");
    assert.equal(dividendTekst(0), "€0,00");
    assert.equal(dividendTekst(null), "–");
    assert.equal(dividendTekst(undefined), "–");
});

test("gemengd: alleen echt ontbrekende posities worden een streepje", () => {
    const posities = [{ dividend_ontvangen: 10 }, { dividend_ontvangen: 0 }, {}];
    assert.deepEqual(posities.map(p => dividendTekst(positieDividend(p, true))), ["€10,00", "€0,00", "–"]);
    assert.deepEqual(posities.map(p => dividendTekst(positieDividend(p, false))), ["–", "–", "–"]);
});
