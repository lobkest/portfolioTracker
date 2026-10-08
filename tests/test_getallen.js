// Unit tests voor de Nederlandse getalnotatie (static/js/getallen.js).
//   node --test tests/test_getallen.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { formatGetal, formatteerEuro, formatPct } = require("../static/js/getallen.js");

test("formatGetal: komma als decimaalteken, punt voor duizendtallen", () => {
    assert.equal(formatGetal(1234.5), "1.234,50");
    assert.equal(formatGetal(1234567.891, 2), "1.234.567,89");
    assert.equal(formatGetal(-1234.5, 1), "-1.234,5");
    assert.equal(formatGetal(0), "0,00");
    assert.equal(formatGetal(0.12345, 3), "0,123");
});

test("formatGetal: minDecimalen 0 laat nullen achteraan weg", () => {
    assert.equal(formatGetal(1.5, 4, 0), "1,5");
    assert.equal(formatGetal(3, 4, 0), "3");
    assert.equal(formatGetal(0.12345, 4, 0), "0,1235");
});

test("formatGetal: geen -0 na afronden", () => {
    assert.equal(formatGetal(-0.001, 2), "0,00");
    assert.equal(formatGetal(-0.004, 2, 0), "0");
});

test("formatGetal: null, undefined, NaN en Infinity worden 'onbekend'", () => {
    for (const x of [null, undefined, NaN, Infinity, "12"]) assert.equal(formatGetal(x), "onbekend");
});

test("formatteerEuro: minteken vóór het €-teken", () => {
    assert.equal(formatteerEuro(4147.97), "€4.147,97");
    assert.equal(formatteerEuro(-1234.56), "-€1.234,56");
    assert.equal(formatteerEuro(0), "€0,00");
    assert.equal(formatteerEuro(1234567, 0), "€1.234.567");
    assert.equal(formatteerEuro(-0.001), "€0,00");
    assert.equal(formatteerEuro(null), "onbekend");
    assert.equal(formatteerEuro(NaN), "onbekend");
});

test("formatPct: Nederlandse notatie", () => {
    assert.equal(formatPct(20.31), "20,31%");
    assert.equal(formatPct(-5.5), "-5,50%");
    assert.equal(formatPct(0), "0,00%");
    assert.equal(formatPct(1234.5, 1), "1.234,5%");
    assert.equal(formatPct(76.25, 0), "76%");
    assert.equal(formatPct(null), "onbekend");
    assert.equal(formatPct(undefined), "onbekend");
    assert.equal(formatPct(NaN), "onbekend");
});

test("formatPct: metTeken zet + alleen voor positieve waarden", () => {
    assert.equal(formatPct(3.1, 1, true), "+3,1%");
    assert.equal(formatPct(-3.1, 1, true), "-3,1%");
    assert.equal(formatPct(0, 1, true), "0,0%");
    assert.equal(formatPct(0.01, 1, true), "0,0%");
});
