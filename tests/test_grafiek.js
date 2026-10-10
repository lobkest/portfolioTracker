// Unit tests voor zoomOpties() en taartLabelMinPct() (static/js/gedeeld/grafiek.js); Chart en document zijn gestubd.
//   node --test tests/test_grafiek.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function laadGrafiek(smal = false) {
    const code = fs.readFileSync(path.join(__dirname, "..", "static", "js", "gedeeld", "grafiek.js"), "utf8");
    const zoomIcoon = { hidden: false };
    const context = {
        Chart: { register: () => {} },
        ChartDataLabels: {},
        document: { getElementById: () => zoomIcoon },
        window: { matchMedia: () => ({ matches: smal }) },
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    return { zoomOpties: context.zoomOpties, taartLabelMinPct: context.taartLabelMinPct, zoomIcoon };
}

test("zoomOpties: x-as begrensd op het oorspronkelijke databereik", () => {
    const { zoomOpties } = laadGrafiek();
    const opties = zoomOpties();
    assert.equal(opties.limits.x.min, "original");
    assert.equal(opties.limits.x.max, "original");
});

test("zoomOpties: geen limiet op de y-as", () => {
    const { zoomOpties } = laadGrafiek();
    assert.equal(zoomOpties().limits.y, undefined);
});

test("taartLabelMinPct: op een smal scherm een hogere drempel (5% breed, 8% smal)", () => {
    assert.equal(laadGrafiek(false).taartLabelMinPct(), 5);
    assert.equal(laadGrafiek(true).taartLabelMinPct(), 8);
});
