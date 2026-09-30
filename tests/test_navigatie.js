// Unit tests voor paden, hash-tabblad en startmeldingen (static/js/navigatie.js).
//   node --test tests/test_navigatie.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    STANDAARD_VIEW, MELDING_ONGELDIGE_CODE, MELDING_ONBEKENDE_CODE, MELDING_VERWIJDERD,
    VEREIST_CODE_JA, VEREIST_CODE_NEE,
    portfolioPad, startPadMetMelding, viewUitHash, viewInLijst, elementZichtbaar, maakTabWisselaar, startMeldingTekst,
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

test("elementZichtbaar: zonder data-vereist-code telt alleen het tabblad", () => {
    for (const heeftCode of [true, false]) {
        assert.equal(elementZichtbaar("land sector", undefined, "land", heeftCode), true);
        assert.equal(elementZichtbaar("land sector", undefined, "portfolio", heeftCode), false);
    }
});

test("elementZichtbaar: data-vereist-code 'ja' is alleen zichtbaar bij een opgeslagen portfolio", () => {
    assert.equal(elementZichtbaar("rendement", VEREIST_CODE_JA, "rendement", true), true);
    assert.equal(elementZichtbaar("rendement", VEREIST_CODE_JA, "rendement", false), false);
    assert.equal(elementZichtbaar("rendement", VEREIST_CODE_JA, "portfolio", true), false);
    assert.equal(elementZichtbaar("rendement", VEREIST_CODE_JA, "portfolio", false), false);
});

test("elementZichtbaar: data-vereist-code 'nee' is het spiegelbeeld (alleen bij 'niet opslaan')", () => {
    assert.equal(elementZichtbaar("portfolio", VEREIST_CODE_NEE, "portfolio", false), true);
    assert.equal(elementZichtbaar("portfolio", VEREIST_CODE_NEE, "portfolio", true), false);
    assert.equal(elementZichtbaar("portfolio", VEREIST_CODE_NEE, "rendement", false), false);
    assert.equal(elementZichtbaar("portfolio", VEREIST_CODE_NEE, "rendement", true), false);
});

test("elementZichtbaar: zonder data-views (binnen een tabblad-blok) telt alleen de code", () => {
    for (const view of ["portfolio", "xirr-rendement"]) {
        assert.equal(elementZichtbaar(undefined, VEREIST_CODE_JA, view, true), true);
        assert.equal(elementZichtbaar(undefined, VEREIST_CODE_JA, view, false), false);
        assert.equal(elementZichtbaar(undefined, VEREIST_CODE_NEE, view, true), false);
        assert.equal(elementZichtbaar(undefined, VEREIST_CODE_NEE, view, false), true);
        assert.equal(elementZichtbaar(undefined, undefined, view, true), true);
        assert.equal(elementZichtbaar(undefined, undefined, view, false), true);
    }
});

const LEGE_ELEMENTEN = new Set(["input", "hr", "br", "img", "meta", "link"]);

// Alle elementen van portfolio.html, elk met zijn attributen en zijn voorouders (buitenste eerst).
function leesPortfolioElementen() {
    const fs = require("node:fs");
    const path = require("node:path");
    const html = fs.readFileSync(path.join(__dirname, "..", "templates", "portfolio.html"), "utf8")
        .replace(/<!--[\s\S]*?-->/g, "")
        .replace(/\{%[\s\S]*?%\}/g, "");
    const elementen = [];
    const stapel = [];
    for (const m of html.matchAll(/<(\/?)([a-zA-Z][\w-]*)((?:"[^"]*"|[^>"])*)>/g)) {
        const [, sluit, tag, attribuutTekst] = m;
        if (sluit) {
            assert.equal(stapel.pop().tag, tag, `sluit-tag </${tag}> past niet`);
            continue;
        }
        const attrs = {};
        for (const a of attribuutTekst.matchAll(/([\w-]+)(?:="([^"]*)")?/g)) attrs[a[1]] = a[2] === undefined ? "" : a[2];
        const element = { tag, attrs, voorouders: [...stapel] };
        elementen.push(element);
        if (!LEGE_ELEMENTEN.has(tag)) stapel.push(element);
    }
    assert.equal(stapel.length, 0, "niet elk element is gesloten");
    return elementen;
}

const heeft = (element, attribuut) => Object.hasOwn(element.attrs, attribuut);
const tabbladVan = element => element.voorouders.find(v => (v.attrs.id || "").startsWith("tab-"));
const viewVanTabblad = tabblad => tabblad.attrs.id.slice("tab-".length);

function menuViews(elementen) {
    return elementen.filter(e => heeft(e, "data-view")).map(e => e.attrs["data-view"]);
}

test("portfolio.html: elke naam in data-views en data-verberg-buiten is een bestaand tabblad", () => {
    const elementen = leesPortfolioElementen();
    const views = menuViews(elementen);
    assert.ok(views.length > 0);
    for (const element of elementen) {
        for (const attribuut of ["data-views", "data-verberg-buiten"]) {
            if (!heeft(element, attribuut)) continue;
            for (const naam of element.attrs[attribuut].split(" ")) {
                assert.ok(views.includes(naam), `onbekend tabblad "${naam}" in ${attribuut} van ${element.attrs.id || element.tag}`);
            }
        }
    }
});

test("portfolio.html: geen dubbele id's", () => {
    const ids = leesPortfolioElementen().filter(e => heeft(e, "id")).map(e => e.attrs.id);
    const dubbel = ids.filter((id, i) => ids.indexOf(id) !== i);
    assert.deepEqual(dubbel, []);
});

test("portfolio.html: elk tabblad uit het menu heeft precies één eigen blok tab-<view>", () => {
    const elementen = leesPortfolioElementen();
    const blokken = elementen.filter(e => (e.attrs.id || "").startsWith("tab-"));
    assert.deepEqual(blokken.map(viewVanTabblad), menuViews(elementen));
    for (const blok of blokken) {
        assert.equal(blok.attrs["data-views"], viewVanTabblad(blok), blok.attrs.id);
        assert.equal(tabbladVan(blok), undefined, `${blok.attrs.id} staat in een ander tabblad-blok`);
    }
});

test("portfolio.html: elk data-grafiek-plek zit in een tabblad-blok, hooguit één per tabblad", () => {
    const plekken = leesPortfolioElementen().filter(e => heeft(e, "data-grafiek-plek"));
    for (const plek of plekken) {
        const tabblad = tabbladVan(plek);
        assert.ok(tabblad, "data-grafiek-plek buiten een tabblad-blok");
        assert.ok(heeft(tabblad, "data-views"), tabblad.attrs.id);
    }
    const views = plekken.map(p => viewVanTabblad(tabbladVan(p)));
    assert.deepEqual(views, [...new Set(views)]);
});

test("portfolio.html: de gedeelde grafiek heeft een plek op de tabbladen met een grafiek op het gedeelde canvas", () => {
    const plekken = leesPortfolioElementen().filter(e => heeft(e, "data-grafiek-plek"));
    assert.deepEqual(
        plekken.map(p => viewVanTabblad(tabbladVan(p))),
        ["portfolio", "rendement", "peraandeel", "peraandeelaankoop", "verdeling", "land", "sector", "xirr-rendement", "prognose", "dividend"],
    );
});

test("portfolio.html: de zoomknop staat alleen op de tabbladen met een zoombare lijngrafiek", () => {
    const elementen = leesPortfolioElementen();
    const zoombaar = elementen.filter(e => heeft(e, "data-zoombaar"));
    for (const plek of zoombaar) assert.ok(heeft(plek, "data-grafiek-plek"), "data-zoombaar zonder data-grafiek-plek");
    assert.deepEqual(
        zoombaar.map(p => viewVanTabblad(tabbladVan(p))),
        ["portfolio", "rendement", "peraandeel", "peraandeelaankoop", "xirr-rendement", "prognose", "dividend"],
    );
    const zoomknop = elementen.find(e => e.attrs.id === "resetZoomBtn");
    assert.ok(zoomknop.voorouders.some(v => v.attrs.id === "chartWrapper"), "de zoomknop verhuist mee met de grafiek");
});

test("portfolio.html: de gedeelde grafiek zelf staat buiten de tabblad-blokken en bevat het canvas", () => {
    const elementen = leesPortfolioElementen();
    assert.equal(tabbladVan(elementen.find(e => e.attrs.id === "chartWrapper")), undefined);
    const canvas = elementen.find(e => e.attrs.id === "rendementChart");
    assert.ok(canvas.voorouders.some(v => v.attrs.id === "chartWrapper"));
});

test("portfolio.html: data-vereist-code is 'ja' of 'nee'", () => {
    for (const element of leesPortfolioElementen()) {
        if (!heeft(element, "data-vereist-code")) continue;
        assert.ok([VEREIST_CODE_JA, VEREIST_CODE_NEE].includes(element.attrs["data-vereist-code"]), element.attrs.id || element.tag);
    }
});

test("portfolio.html: code-tekst, benchmark-keuzes en de XIRR-grafiek vereisen een code; 'niet opgeslagen' juist geen", () => {
    const elementen = leesPortfolioElementen();
    const vereist = id => elementen.find(e => e.attrs.id === id).attrs["data-vereist-code"];
    assert.equal(vereist("codeText"), VEREIST_CODE_JA);
    assert.equal(vereist("nietOpgeslagenText"), VEREIST_CODE_NEE);
    assert.equal(vereist("benchmarkSelectWrapper"), VEREIST_CODE_JA);
    assert.equal(vereist("eigenAandeelSelectWrapper"), VEREIST_CODE_JA);
    for (const id of ["codeText", "nietOpgeslagenText"]) {
        assert.equal(tabbladVan(elementen.find(e => e.attrs.id === id)).attrs.id, "tab-portfolio", id);
    }
    const xirrPlek = elementen.find(e => heeft(e, "data-grafiek-plek") && tabbladVan(e).attrs.id === "tab-xirr-rendement");
    assert.equal(xirrPlek.attrs["data-vereist-code"], VEREIST_CODE_JA);
});

test("portfolio.html: 'laatst bijgewerkt' wordt buiten Portfolio-home verborgen", () => {
    const element = leesPortfolioElementen().find(e => e.attrs.id === "laatstBijgewerktText");
    assert.equal(element.attrs["data-verberg-buiten"], "portfolio");
});

// De id's in deze lijsten worden via een variabele opgezocht; test_pagina_routes.py ziet ze daardoor niet.
test("portfolio.html: de toestand- en prognose-id's uit de tabblad-scripts bestaan", () => {
    const fs = require("node:fs");
    const path = require("node:path");
    const jsMap = path.join(__dirname, "..", "static", "js");
    const alleJs = fs.readdirSync(jsMap, { recursive: true })
        .filter(naam => naam.endsWith(".js"))
        .map(naam => fs.readFileSync(path.join(jsMap, naam), "utf8"))
        .join("\n");
    const ids = new Set(leesPortfolioElementen().map(e => e.attrs.id));
    for (const naam of ["DIVIDEND_TOESTANDEN", "TRANSACTIES_TOESTANDEN", "PROGNOSE_VELD_IDS"]) {
        const blok = alleJs.match(new RegExp(`const ${naam} = [\\[{]([^\\]}]*)[\\]}]`));
        assert.ok(blok, naam);
        const gevonden = [...blok[1].matchAll(/"([^"]+)"/g)].map(m => m[1]);
        assert.ok(gevonden.length > 0, naam);
        for (const id of gevonden) assert.ok(ids.has(id), `${id} uit ${naam} ontbreekt in portfolio.html`);
    }
});

// Nepklok: timers gaan pas af bij loopAf(), zodat de volgorde van wissels vastligt.
function maakNepklok() {
    const timers = new Map();
    let volgnummer = 0;
    return {
        setTimeout: (fn, ms) => { timers.set(++volgnummer, { fn, ms }); return volgnummer; },
        clearTimeout: id => { timers.delete(id); },
        loopAf: () => { const lopend = [...timers.values()]; timers.clear(); lopend.forEach(t => t.fn()); },
        aantal: () => timers.size,
        wachttijden: () => [...timers.values()].map(t => t.ms),
    };
}

function maakWisselProef() {
    const klok = maakNepklok();
    const toegepast = [];
    let fades = 0;
    const wissel = maakTabWisselaar(view => toegepast.push(view), () => { fades += 1; }, 90, klok);
    return { klok, toegepast, wissel, fades: () => fades };
}

test("maakTabWisselaar: eerst uitfaden, pas na de wachttijd toepassen", () => {
    const { klok, toegepast, wissel, fades } = maakWisselProef();
    wissel("rendement");
    assert.equal(fades(), 1);
    assert.deepEqual(toegepast, []);
    assert.deepEqual(klok.wachttijden(), [90]);
    klok.loopAf();
    assert.deepEqual(toegepast, ["rendement"]);
});

test("maakTabWisselaar: een tweede wissel tijdens het wachten wint, de oude timer vuurt niet meer", () => {
    const { klok, toegepast, wissel, fades } = maakWisselProef();
    wissel("rendement");
    wissel("dividend");
    assert.deepEqual(toegepast, ["dividend"]);
    assert.equal(klok.aantal(), 0);
    klok.loopAf();
    assert.deepEqual(toegepast, ["dividend"]);
    assert.equal(fades(), 1);
});

test("maakTabWisselaar: snel doorklikken eindigt altijd op het laatst gekozen tabblad", () => {
    const { klok, toegepast, wissel } = maakWisselProef();
    for (const view of ["rendement", "land", "sector", "dividend", "prognose", "portfolio"]) wissel(view);
    klok.loopAf();
    assert.equal(toegepast.at(-1), "portfolio");
    assert.equal(klok.aantal(), 0);
});

test("maakTabWisselaar: na een afgeronde wissel fadet de volgende weer eerst", () => {
    const { klok, toegepast, wissel, fades } = maakWisselProef();
    wissel("rendement");
    klok.loopAf();
    wissel("land");
    assert.equal(fades(), 2);
    assert.deepEqual(toegepast, ["rendement"]);
    klok.loopAf();
    assert.deepEqual(toegepast, ["rendement", "land"]);
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
