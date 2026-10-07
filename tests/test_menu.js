// Unit tests voor de hoofdtabs/subtabs (static/js/menu.js) en de mobiele CSS.
// Draait via Node's ingebouwde testrunner, geen extra dependency nodig:
//   node --test tests/test_menu.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const { MENU_GROEPEN, groepVanView, eersteView, zichtbareGroepen } = require("../static/js/menu.js");

const ALLE_VIEWS = MENU_GROEPEN.flatMap(g => g.views.map(v => v.view));

test("elke tabblad-view uit portfolio.html (behalve xirr-rendement) zit in precies één groep", () => {
    const html = require("node:fs").readFileSync(require("node:path").join(__dirname, "..", "templates", "portfolio.html"), "utf8");
    const views = [...html.matchAll(/<div id="tab-([\w-]+)" data-views="/g)].map(m => m[1]);
    assert.ok(views.length > 0);
    assert.ok(!views.includes("xirr-rendement"));
    for (const view of views) {
        assert.equal(ALLE_VIEWS.filter(v => v === view).length, 1, view);
    }
    assert.deepEqual([...ALLE_VIEWS].sort(), [...views].sort());
});

test("groepVanView: view naar groep, instellingen-views naar instellingen, onbekend naar null", () => {
    assert.equal(groepVanView("dividend"), "overzicht");
    assert.equal(groepVanView("land"), "samenstelling");
    assert.equal(groepVanView("beurs"), "samenstelling");
    assert.equal(groepVanView("instellingen-diagnostiek"), "instellingen");
    assert.equal(groepVanView("xirr-rendement"), null);
});

test("eersteView: eerste toegestane subtab van de groep", () => {
    assert.equal(eersteView("overzicht", ALLE_VIEWS), "portfolio");
    assert.equal(eersteView("instellingen", ["instellingen-ticker", "instellingen-diagnostiek"]), "instellingen-ticker");
    assert.equal(eersteView("posities", ["dividend"]), null);
    assert.equal(eersteView("bestaatniet", ALLE_VIEWS), null);
});

test("instellingen: Bestanden bijwerken staat direct na Algemeen", () => {
    const views = MENU_GROEPEN.find(g => g.id === "instellingen").views.map(v => v.view);
    assert.deepEqual(views.slice(0, 2), ["instellingen", "instellingen-bestanden"]);
    assert.equal(groepVanView("instellingen-bestanden"), "instellingen");
});

test("rendement: Huidige portfolio staat direct na Prognose", () => {
    const views = MENU_GROEPEN.find(g => g.id === "rendement").views.map(v => v.view);
    assert.deepEqual(views.slice(views.indexOf("prognose"), views.indexOf("prognose") + 2), ["prognose", "prognose-huidig"]);
    assert.equal(groepVanView("prognose-huidig"), "rendement");
});

test("overzicht: Box 3 staat direct na Dividend", () => {
    const views = MENU_GROEPEN.find(g => g.id === "overzicht").views;
    const i = views.findIndex(v => v.view === "dividend");
    assert.deepEqual(views[i + 1], { view: "box3", label: "Box 3" });
    assert.equal(groepVanView("box3"), "overzicht");
});

test("zichtbareGroepen: alleen groepen met minstens één toegestane view", () => {
    assert.deepEqual(zichtbareGroepen(["land", "instellingen-ticker"]).map(g => g.id), ["samenstelling", "instellingen"]);
    assert.deepEqual(zichtbareGroepen([]), []);
});

// --- Regressiebewaking mobiele CSS/viewport (geen DOM nodig: leest de bronbestanden) ---

const fs = require("node:fs");
const path = require("node:path");

const CSS = fs.readFileSync(path.join(__dirname, "..", "static", "css", "style.css"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "templates", "basis.html"), "utf8");

// Geeft de inhoud van het eerste @media-blok waarvan de voorwaarde met
// `begin` start (accolades geteld, dus geneste regels blijven binnen het blok).
function mediaBlok(begin) {
    const start = CSS.indexOf(`@media ${begin}`);
    assert.notEqual(start, -1, `@media ${begin} niet gevonden`);
    const open = CSS.indexOf("{", start);
    let diepte = 0;
    for (let i = open; i < CSS.length; i++) {
        if (CSS[i] === "{") diepte++;
        if (CSS[i] === "}") diepte--;
        if (diepte === 0) return CSS.slice(open + 1, i);
    }
    throw new Error("onafgesloten @media-blok");
}

const MOBIEL = mediaBlok("(max-width: 768px)");

test("mobiel: invoervelden hebben font-size 16px (voorkomt iOS-autozoom bij focus)", () => {
    assert.match(MOBIEL, /input,\s*select,\s*textarea\s*\{[^}]*font-size:\s*16px/);
});

test("desktop: font-size van invoervelden is buiten de media query niet gezet", () => {
    const buitenMedia = CSS.replace(MOBIEL, "");
    assert.doesNotMatch(buitenMedia, /(^|\n)(input|select|textarea)[^{]*\{[^}]*font-size/);
});

test("mobiel: hoofdtabs zijn een vaste onderbalk met ruimte voor de safe-area", () => {
    const balk = MOBIEL.match(/\.hoofdTabs\s*\{([^}]*)\}/)[1];
    assert.match(balk, /position:\s*fixed/);
    assert.match(balk, /bottom:\s*0/);
    assert.match(balk, /padding-bottom:\s*env\(safe-area-inset-bottom\)/);
});

test("mobiel: de content krijgt padding-bottom zodat de onderbalk niets bedekt", () => {
    const content = MOBIEL.match(/\.content\s*\{([^}]*)\}/)[1];
    assert.match(content, /padding-bottom:\s*calc\([^)]*env\(safe-area-inset-bottom\)/);
});

test("mobiel: subtabs scrollen horizontaal", () => {
    const sub = MOBIEL.match(/\.subTabs\s*\{([^}]*)\}/)[1];
    assert.match(sub, /overflow-x:\s*auto/);
    assert.match(sub, /flex-wrap:\s*nowrap/);
});

test("viewport-meta: apparaatbreedte en startschaal", () => {
    const meta = HTML.match(/<meta name="viewport"[^>]*>/)[0];
    assert.match(meta, /width=device-width/);
    assert.match(meta, /initial-scale=1/);
});
