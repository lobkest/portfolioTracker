// Unit tests voor static/js/box3.js (invoer naar getallen, grafiekreeksen).
//   node --test tests/test_box3.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { leesBox3Bedrag, bouwBox3Invoer, box3GrafiekReeksen, box3RenteWaarschuwing, box3AllesVerkopenReeks } = require("../static/js/box3.js");

test("leesBox3Bedrag: Nederlandse notatie, leeg is 0", () => {
    assert.equal(leesBox3Bedrag(""), 0);
    assert.equal(leesBox3Bedrag(undefined), 0);
    assert.equal(leesBox3Bedrag("12500"), 12500);
    assert.equal(leesBox3Bedrag("12.500"), 12500);
    assert.equal(leesBox3Bedrag("1.234.567"), 1234567);
    assert.equal(leesBox3Bedrag("1.234,56"), 1234.56);
    assert.equal(leesBox3Bedrag("€ 250,5"), 250.5);
    assert.equal(leesBox3Bedrag("12.5"), 12.5);
});

test("leesBox3Bedrag: onleesbaar is null, negatief blijft negatief (backend keurt af)", () => {
    assert.equal(leesBox3Bedrag("veel"), null);
    assert.equal(leesBox3Bedrag("1,2,3"), null);
    assert.equal(leesBox3Bedrag("-100"), -100);
});

test("bouwBox3Invoer: getallen, partner als bool, ongeldige sleutels apart", () => {
    const { invoer, ongeldig } = bouwBox3Invoer({
        banktegoeden: "20.000", schulden: "", overige_bezittingen: "abc", fiscale_partner: 1,
    });
    assert.deepEqual(invoer, { fiscale_partner: true, banktegoeden: 20000, schulden: 0 });
    assert.deepEqual(ongeldig, ["overige_bezittingen"]);
});

test("box3GrafiekReeksen: per stelsel de belasting, null zonder berekening", () => {
    const berekening = {
        jaren: [
            { jaar: 2022, huidig: { berekend: false }, aanwas: { belasting: 100 }, vermogenswinst: { belasting: 0 } },
            { jaar: 2023, huidig: { belasting: 50 }, aanwas: { belasting: 0 }, vermogenswinst: { belasting: 20 } },
        ],
    };
    assert.deepEqual(box3GrafiekReeksen(berekening), {
        labels: ["2022", "2023"], huidig: [null, 50], aanwas: [100, 0], vermogenswinst: [0, 20],
    });
});

test("box3RenteWaarschuwing: spaargeld zonder spaarrente en zonder overig rendement", () => {
    assert.equal(box3RenteWaarschuwing({ banktegoeden: 70000 }), true);
    assert.equal(box3RenteWaarschuwing({ banktegoeden: 70000, spaarrente_pct: 0, rendement_ander_vermogen: 0 }), true);
    assert.equal(box3RenteWaarschuwing({ banktegoeden: 70000, spaarrente_pct: 1.5 }), false);
    assert.equal(box3RenteWaarschuwing({ banktegoeden: 70000, rendement_ander_vermogen: 200 }), false);
    assert.equal(box3RenteWaarschuwing({ banktegoeden: 0 }), false);
    assert.equal(box3RenteWaarschuwing({}), false);
});

test("bouwBox3Invoer: spaarrente met komma", () => {
    assert.equal(bouwBox3Invoer({ spaarrente_pct: "1,5" }).invoer.spaarrente_pct, 1.5);
});

test("box3AllesVerkopenReeks: alleen het lopende jaar krijgt een staaf", () => {
    const jaren = [{ jaar: 2025 }, { jaar: 2026 }];
    assert.deepEqual(box3AllesVerkopenReeks({ jaren, b_alles_verkopen: { jaar: 2026, belasting: 1512 } }), [null, 1512]);
    assert.deepEqual(box3AllesVerkopenReeks({ jaren, b_alles_verkopen: null }), [null, null]);
});
