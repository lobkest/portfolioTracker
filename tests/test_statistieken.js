// Unit tests voor de pure logica van Statistieken (static/js/statistieken.js).
//   node --test tests/test_statistieken.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { jaarSubregel } = require("../static/js/statistieken.js");

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
