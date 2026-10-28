// Unit tests voor de pure helpers van het tabblad Koersen (static/js/koersoverzicht.js).
//   node --test tests/test_koersoverzicht.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { KOERS_PERIODES, STANDAARD_KOERS_PERIODE, isGestippeld, ranglijstRijen } = require("../static/js/koersoverzicht.js");

test("isGestippeld: alleen als de positie op beide dagen niet in bezit was", () => {
    assert.equal(isGestippeld([true, true], 1), false);
    assert.equal(isGestippeld([false, true], 1), false);
    assert.equal(isGestippeld([true, false], 1), false);
    assert.equal(isGestippeld([false, false], 1), true);
});

test("isGestippeld: het eerste punt heeft geen segment", () => {
    assert.equal(isGestippeld([false, false], 0), false);
});

test("ranglijstRijen behoudt de volgorde en laat null staan", () => {
    const rijen = ranglijstRijen([
        { ticker: "AAA", naam: "Aaa", pct_periode: 4.2, pct: [0, 4.2], koers_begin: 10, koers_nu: 10.42 },
        { ticker: "BBB", naam: "Bbb", pct_periode: null, pct: [null, null], koers_begin: null, koers_nu: null },
    ]);
    assert.deepEqual(rijen, [
        { naam: "Aaa", ticker: "AAA", pct: 4.2, koersBegin: 10, koersNu: 10.42 },
        { naam: "Bbb", ticker: "BBB", pct: null, koersBegin: null, koersNu: null },
    ]);
});

test("perioden: zelfde volgorde als de backend, standaard Dit jaar", () => {
    assert.deepEqual(KOERS_PERIODES.map(p => p.id), ["maand", "ytd", "1m", "1j", "alles"]);
    assert.ok(KOERS_PERIODES.some(p => p.id === STANDAARD_KOERS_PERIODE));
});
