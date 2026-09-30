// Unit tests voor paden, hash-tabblad en startmeldingen (static/js/navigatie.js).
//   node --test tests/test_navigatie.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    STANDAARD_VIEW, MELDING_ONGELDIGE_CODE, MELDING_ONBEKENDE_CODE, MELDING_VERWIJDERD,
    portfolioPad, startPadMetMelding, viewUitHash, viewInLijst, startMeldingTekst,
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

test("viewInLijst: alleen een hele tabbladnaam telt, geen deel ervan", () => {
    assert.equal(viewInLijst("land sector", "land"), true);
    assert.equal(viewInLijst("land sector", "sector"), true);
    assert.equal(viewInLijst("xirr-rendement", "rendement"), false);
    assert.equal(viewInLijst("instellingen-bijnamen", "instellingen"), false);
    assert.equal(viewInLijst("land sector", "portfolio"), false);
});

test("viewInLijst: lege of ontbrekende lijst bevat geen enkel tabblad", () => {
    assert.equal(viewInLijst("", "portfolio"), false);
    assert.equal(viewInLijst(undefined, "portfolio"), false);
});

function leesPortfolioTemplate() {
    const fs = require("node:fs");
    const path = require("node:path");
    return fs.readFileSync(path.join(__dirname, "..", "templates", "portfolio.html"), "utf8");
}

function viewsVanElement(html, id, attribuut) {
    const tag = html.match(new RegExp(`<[^>]*id="${id}"[^>]*>`))[0];
    return tag.match(new RegExp(`${attribuut}="([^"]*)"`))[1].split(" ");
}

test("portfolio.html: elke naam in data-views en data-verberg-buiten is een bestaand tabblad", () => {
    const html = leesPortfolioTemplate();
    const tabbladen = [...html.matchAll(/data-view="([^"]+)"/g)].map(m => m[1]);
    assert.ok(tabbladen.length > 0);
    for (const m of html.matchAll(/data-(?:views|verberg-buiten)="([^"]*)"/g)) {
        for (const naam of m[1].split(" ")) {
            assert.ok(tabbladen.includes(naam), `onbekend tabblad "${naam}" in ${m[0]}`);
        }
    }
});

test("portfolio.html: de gedeelde grafiek staat op de tabbladen met een grafiek op het gedeelde canvas", () => {
    assert.deepEqual(
        viewsVanElement(leesPortfolioTemplate(), "chartWrapper", "data-views"),
        ["portfolio", "rendement", "peraandeel", "peraandeelaankoop", "verdeling", "land", "sector", "xirr-rendement", "prognose", "dividend"],
    );
});

test("portfolio.html: de zoomknop staat alleen op de tabbladen met een zoombare lijngrafiek", () => {
    const zoom = viewsVanElement(leesPortfolioTemplate(), "resetZoomBtn", "data-views");
    assert.deepEqual(zoom, ["portfolio", "rendement", "peraandeel", "peraandeelaankoop", "xirr-rendement", "prognose", "dividend"]);
    const grafiek = viewsVanElement(leesPortfolioTemplate(), "chartWrapper", "data-views");
    for (const view of zoom) assert.ok(grafiek.includes(view), view);
});

test("portfolio.html: 'laatst bijgewerkt' wordt buiten Portfolio-home verborgen", () => {
    assert.deepEqual(viewsVanElement(leesPortfolioTemplate(), "laatstBijgewerktText", "data-verberg-buiten"), ["portfolio"]);
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
