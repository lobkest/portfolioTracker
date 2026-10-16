// Pure stroomverwerking van de laadbalk.
//   node --test tests/test_voortgang.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { verwerkStroomBuffer } = require("../static/js/voortgang.js");

test("hele regels worden geparsed, rest blijft leeg", () => {
    const r = verwerkStroomBuffer('{"a":1}\n{"b":2}\n');
    assert.deepEqual(r.regels, [{ a: 1 }, { b: 2 }]);
    assert.equal(r.rest, "");
});

test("een afgebroken laatste regel blijft in rest", () => {
    const r = verwerkStroomBuffer('{"a":1}\n{"b":');
    assert.deepEqual(r.regels, [{ a: 1 }]);
    assert.equal(r.rest, '{"b":');
});

test("lege regels worden overgeslagen", () => {
    const r = verwerkStroomBuffer('\n{"a":1}\n\n');
    assert.deepEqual(r.regels, [{ a: 1 }]);
});

test("meerdere chunks na elkaar geven alle regels, ook midden in een regel gesplitst", () => {
    let buffer = "";
    const alle = [];
    for (const chunk of ['{"a"', ':1}\n{"b":2', "}\n", '{"c":3}\n']) {
        const r = verwerkStroomBuffer(buffer + chunk);
        alle.push(...r.regels);
        buffer = r.rest;
    }
    assert.deepEqual(alle, [{ a: 1 }, { b: 2 }, { c: 3 }]);
    assert.equal(buffer, "");
});

test("lege buffer", () => {
    assert.deepEqual(verwerkStroomBuffer(""), { regels: [], rest: "" });
});

test("voortgangTekst plakt (gedaan/totaal) achter de staptekst, alleen bij een teller", () => {
    const { voortgangTekst } = require("../static/js/voortgang.js");
    assert.equal(voortgangTekst({ tekst: "Tickers zoeken", gedaan: 12, totaal: 30 }), "Tickers zoeken (12/30)");
    assert.equal(voortgangTekst({ tekst: "Tickers zoeken" }), "Tickers zoeken");
});
