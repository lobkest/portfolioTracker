// Unit tests voor zoomOpties() (static/js/gedeeld/grafiek.js); Chart en document zijn gestubd.
//   node --test tests/test_grafiek.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

function laadGrafiek() {
    const code = fs.readFileSync(path.join(__dirname, "..", "static", "js", "gedeeld", "grafiek.js"), "utf8");
    const zoomIcoon = { hidden: false };
    const context = {
        Chart: { register: () => {} },
        ChartDataLabels: {},
        document: { getElementById: () => zoomIcoon },
    };
    vm.createContext(context);
    vm.runInContext(code, context);
    return { zoomOpties: context.zoomOpties, zoomIcoon };
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
