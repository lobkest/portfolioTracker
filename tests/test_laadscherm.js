// Unit tests voor de pure logica van het laadscherm (static/js/laadscherm.js).
//   node --test tests/test_laadscherm.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    WEETJES,
    formatteerWachttijd,
    voortgang,
    kiesVolgendWeetje,
    isPingOk
} = require("../static/js/laadscherm.js");

test("formatteerWachttijd: enkelvoud, meervoud en minuten", () => {
    assert.equal(formatteerWachttijd(0), "0 seconden");
    assert.equal(formatteerWachttijd(1), "1 seconde");
    assert.equal(formatteerWachttijd(23), "23 seconden");
    assert.equal(formatteerWachttijd(59.9), "59 seconden");
    assert.equal(formatteerWachttijd(60), "1 min 0 sec");
    assert.equal(formatteerWachttijd(65), "1 min 5 sec");
    assert.equal(formatteerWachttijd(125), "2 min 5 sec");
    assert.equal(formatteerWachttijd(-3), "0 seconden");
});

test("voortgang: begrensd tussen 0 en 1", () => {
    assert.equal(voortgang(0, 50), 0);
    assert.equal(voortgang(25, 50), 0.5);
    assert.equal(voortgang(50, 50), 1);
    assert.equal(voortgang(80, 50), 1);
    assert.equal(voortgang(-5, 50), 0);
    assert.equal(voortgang(10, 0), 1);
});

test("kiesVolgendWeetje: nooit de huidige index, altijd binnen bereik", () => {
    for (let huidig = 0; huidig < 5; huidig++) {
        for (const r of [0, 0.25, 0.5, 0.75, 0.999999, 1]) {
            const volgend = kiesVolgendWeetje(huidig, 5, () => r);
            assert.notEqual(volgend, huidig);
            assert.ok(volgend >= 0 && volgend < 5);
        }
    }
});

test("kiesVolgendWeetje: random 0 geeft de eerste andere, bijna 1 de laatste andere", () => {
    assert.equal(kiesVolgendWeetje(0, 4, () => 0), 1);
    assert.equal(kiesVolgendWeetje(2, 4, () => 0), 0);
    assert.equal(kiesVolgendWeetje(3, 4, () => 0.999), 2);
    assert.equal(kiesVolgendWeetje(0, 4, () => 0.999), 3);
});

test("kiesVolgendWeetje: bij aantal 1 (of 0) altijd 0", () => {
    assert.equal(kiesVolgendWeetje(0, 1, () => 0.7), 0);
    assert.equal(kiesVolgendWeetje(0, 0, () => 0.7), 0);
});

test("isPingOk: alleen een object met ok === true", () => {
    assert.equal(isPingOk({ ok: true }), true);
    assert.equal(isPingOk({ ok: false }), false);
    assert.equal(isPingOk({ ok: "true" }), false);
    assert.equal(isPingOk(null), false);
    assert.equal(isPingOk(undefined), false);
    assert.equal(isPingOk("<html>Render wordt wakker</html>"), false);
    assert.equal(isPingOk(true), false);
});

test("WEETJES: gevulde lijst met alleen niet-lege teksten", () => {
    assert.equal(WEETJES.length, 14);
    for (const weetje of WEETJES) assert.ok(typeof weetje === "string" && weetje.length > 0);
});
