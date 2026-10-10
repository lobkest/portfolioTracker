// Unit tests voor de actieve naambron en het opslaan bij blur (static/js/bijnamen.js).
//   node --test tests/test_bijnamen.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { actieveNaamBron, bijnaamGewijzigd, naamOptieTekst } = require("../static/js/bijnamen.js");

test("de bron met dezelfde naam als de bijnaam is actief", () => {
    assert.equal(actieveNaamBron(["ASML HOLDING", "ASML Holding N.V.", "ASML"], "ASML"), 2);
    assert.equal(actieveNaamBron(["ASML HOLDING", "ASML Holding N.V.", "ASML"], "ASML HOLDING"), 0);
});

test("eigen bijnaam: geen bron actief", () => {
    assert.equal(actieveNaamBron(["ASML HOLDING", "ASML Holding N.V.", "ASML"], "Chipmachines"), -1);
});

test("meerdere bronnen met dezelfde naam: de eerste is actief", () => {
    assert.equal(actieveNaamBron(["Apple", "Apple Inc.", "Apple"], "Apple"), 0);
    assert.equal(actieveNaamBron(["APPLE INC", "Apple", "Apple"], "Apple"), 1);
});

test("ontbrekende bronnamen worden nooit actief", () => {
    assert.equal(actieveNaamBron(["Apple", undefined, null], undefined), -1);
    assert.equal(actieveNaamBron(["Apple", undefined, null], ""), -1);
    assert.equal(actieveNaamBron([undefined, null, "Apple"], "Apple"), 2);
});

test("blur slaat alleen op bij een gewijzigde waarde", () => {
    assert.equal(bijnaamGewijzigd("ASML", "ASML"), false);
    assert.equal(bijnaamGewijzigd("  ASML  ", "ASML"), false);
    assert.equal(bijnaamGewijzigd("Chipmachines", "ASML"), true);
});

test("lege invoer slaat niets op", () => {
    assert.equal(bijnaamGewijzigd("", "ASML"), false);
    assert.equal(bijnaamGewijzigd("   ", "ASML"), false);
    assert.equal(bijnaamGewijzigd(undefined, "ASML"), false);
});

test("naamOptieTekst: de naam zelf, anders waarom die ontbreekt", () => {
    assert.equal(naamOptieTekst("ASML Holding N.V.", "Yahoo-naam", false), "ASML Holding N.V.");
    assert.equal(naamOptieTekst(null, "Yahoo-naam", true), "nog niet opgehaald");
    assert.equal(naamOptieTekst(undefined, "Korte naam", false), "Geen korte naam");
    assert.equal(naamOptieTekst("", "Excel-naam", false), "Geen excel-naam");
});
