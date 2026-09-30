// Unit tests voor paden, hash-tabblad en startmeldingen (static/js/navigatie.js).
//   node --test tests/test_navigatie.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    STANDAARD_VIEW, MELDING_ONGELDIGE_CODE, MELDING_ONBEKENDE_CODE, MELDING_VERWIJDERD,
    portfolioPad, startPadMetMelding, viewUitHash, startMeldingTekst,
} = require("../static/js/navigatie.js");

const VIEWS = ["portfolio", "rendement", "xirr-rendement", "instellingen-ticker"];

test("portfolioPad: absoluut pad met de code", () => {
    assert.equal(portfolioPad("ABC"), "/p/ABC");
});

test("startPadMetMelding: startpagina met de sleutel in de query", () => {
    assert.equal(startPadMetMelding(MELDING_ONBEKENDE_CODE), "/?melding=onbekende-code");
});

test("viewUitHash: geldige hash geeft die view, met of zonder #", () => {
    assert.equal(viewUitHash("#rendement", VIEWS), "rendement");
    assert.equal(viewUitHash("rendement", VIEWS), "rendement");
    assert.equal(viewUitHash("#xirr-rendement", VIEWS), "xirr-rendement");
});

test("viewUitHash: lege of ontbrekende hash geeft de standaard-view", () => {
    assert.equal(STANDAARD_VIEW, "portfolio");
    assert.equal(viewUitHash("", VIEWS), STANDAARD_VIEW);
    assert.equal(viewUitHash("#", VIEWS), STANDAARD_VIEW);
    assert.equal(viewUitHash(undefined, VIEWS), STANDAARD_VIEW);
});

test("viewUitHash: onbekende hash geeft de standaard-view", () => {
    assert.equal(viewUitHash("#bestaatniet", VIEWS), STANDAARD_VIEW);
    assert.equal(viewUitHash("#Rendement", VIEWS), STANDAARD_VIEW);
});

test("viewUitHash: view die niet in de lijst staat (bv. verborgen bij niet opslaan) geeft de standaard-view", () => {
    assert.equal(viewUitHash("#dividend", VIEWS), STANDAARD_VIEW);
});

test("startMeldingTekst: elke bekende sleutel heeft een tekst", () => {
    for (const sleutel of [MELDING_ONGELDIGE_CODE, MELDING_ONBEKENDE_CODE, MELDING_VERWIJDERD]) {
        const tekst = startMeldingTekst(sleutel);
        assert.equal(typeof tekst, "string", sleutel);
        assert.ok(tekst.length > 0, sleutel);
    }
});

test("startMeldingTekst: onbekende of ontbrekende sleutel geeft null", () => {
    assert.equal(startMeldingTekst("<script>alert(1)</script>"), null);
    assert.equal(startMeldingTekst("toString"), null);
    assert.equal(startMeldingTekst(null), null);
    assert.equal(startMeldingTekst(""), null);
});

test("de sleutel voor een ongeldige code is dezelfde als in app.py (MELDING_ONGELDIGE_CODE)", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const appPy = fs.readFileSync(path.join(__dirname, "..", "app.py"), "utf8");
    assert.ok(appPy.includes(`MELDING_ONGELDIGE_CODE = "${MELDING_ONGELDIGE_CODE}"`));
});
