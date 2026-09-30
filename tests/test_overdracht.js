// Unit tests voor de eenmalige overdracht start -> portfolio-pagina (static/js/overdracht.js).
//   node --test tests/test_overdracht.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { OVERDRACHT_SLEUTEL, bewaarOverdracht, haalOverdracht } = require("../static/js/overdracht.js");

// Zelfde drie methodes als sessionStorage.
function nepOpslag(begin = {}) {
    const inhoud = { ...begin };
    return {
        inhoud,
        getItem: (k) => (k in inhoud ? inhoud[k] : null),
        setItem: (k, v) => { inhoud[k] = String(v); },
        removeItem: (k) => { delete inhoud[k]; },
    };
}

function volleOpslag() {
    const opslag = nepOpslag();
    opslag.setItem = () => { throw new Error("QuotaExceededError"); };
    return opslag;
}

test("bewaren en ophalen met dezelfde code geeft de data terug", () => {
    const opslag = nepOpslag();
    assert.equal(bewaarOverdracht(opslag, { code: "ABC", naam: "test" }), true);
    assert.deepEqual(haalOverdracht(opslag, "ABC"), { code: "ABC", naam: "test" });
});

test("niet opslaan: data zonder code hoort bij een pagina zonder code", () => {
    const opslag = nepOpslag();
    bewaarOverdracht(opslag, { code: null, verdeling: [] });
    assert.deepEqual(haalOverdracht(opslag, ""), { code: null, verdeling: [] });
});

test("wissen na lezen: een tweede keer ophalen geeft null", () => {
    const opslag = nepOpslag();
    bewaarOverdracht(opslag, { code: "ABC" });
    haalOverdracht(opslag, "ABC");
    assert.equal(OVERDRACHT_SLEUTEL in opslag.inhoud, false);
    assert.equal(haalOverdracht(opslag, "ABC"), null);
});

test("andere code: null, en de overdracht is toch gewist", () => {
    const opslag = nepOpslag();
    bewaarOverdracht(opslag, { code: "ABC" });
    assert.equal(haalOverdracht(opslag, "XYZ"), null);
    assert.equal(OVERDRACHT_SLEUTEL in opslag.inhoud, false);
});

test("overdracht met code wordt niet gebruikt op de pagina zonder code, en andersom", () => {
    const opslag = nepOpslag();
    bewaarOverdracht(opslag, { code: "ABC" });
    assert.equal(haalOverdracht(opslag, ""), null);
    bewaarOverdracht(opslag, { code: null });
    assert.equal(haalOverdracht(opslag, "ABC"), null);
});

test("geen overdracht aanwezig geeft null", () => {
    assert.equal(haalOverdracht(nepOpslag(), "ABC"), null);
});

test("kapotte JSON of een niet-object geeft null en wordt gewist", () => {
    for (const ruw of ["{niet af", "42", '"tekst"', "null"]) {
        const opslag = nepOpslag({ [OVERDRACHT_SLEUTEL]: ruw });
        assert.equal(haalOverdracht(opslag, ""), null, ruw);
        assert.equal(OVERDRACHT_SLEUTEL in opslag.inhoud, false, ruw);
    }
});

test("volle opslag: bewaren geeft false en gooit niet", () => {
    assert.equal(bewaarOverdracht(volleOpslag(), { code: "ABC" }), false);
});

test("geen opslag beschikbaar (null): bewaren geeft false, ophalen null", () => {
    assert.equal(bewaarOverdracht(null, { code: "ABC" }), false);
    assert.equal(haalOverdracht(null, "ABC"), null);
});

test("opslag die gooit bij lezen geeft null", () => {
    const opslag = nepOpslag();
    opslag.getItem = () => { throw new Error("SecurityError"); };
    assert.equal(haalOverdracht(opslag, "ABC"), null);
});
