// Unit tests voor de hoofdtabs/subtabs (static/js/menu.js) en de mobiele CSS.
// Draait via Node's ingebouwde testrunner, geen extra dependency nodig:
//   node --test tests/test_menu.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    MENU_GROEPEN, ONTWIKKEL_ONDERDELEN, ONTWIKKEL_OPSLAG_SLEUTEL, groepVanView, eersteView, zichtbareGroepen,
    ontwikkelViews, ontwikkelItems, ontwikkelAan, uitgeschakeldeViews, leesAanGezet, menuViews, scrollFades,
} = require("../static/js/menu.js");

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
    assert.deepEqual(views[i + 1], { view: "box3", label: "Box 3", inOntwikkeling: true });
    assert.equal(groepVanView("box3"), "overzicht");
});

test("overzicht: eerste subtab heet Samenvatting (view blijft portfolio)", () => {
    assert.deepEqual(MENU_GROEPEN.find(g => g.id === "overzicht").views[0], { view: "portfolio", label: "Samenvatting" });
    assert.ok(!MENU_GROEPEN.some(g => g.views.some(v => v.label === "Home")));
});

test("scrollFades: past alles, dan geen fade", () => {
    assert.deepEqual(scrollFades(0, 300, 300), { links: false, rechts: false });
});

test("scrollFades: breder dan het scherm, helemaal links: alleen rechts", () => {
    assert.deepEqual(scrollFades(0, 500, 300), { links: false, rechts: true });
});

test("scrollFades: halverwege: beide kanten", () => {
    assert.deepEqual(scrollFades(100, 500, 300), { links: true, rechts: true });
});

test("scrollFades: helemaal rechts (ook met een halve pixel afronding): alleen links", () => {
    assert.deepEqual(scrollFades(200, 500, 300), { links: true, rechts: false });
    assert.deepEqual(scrollFades(199.5, 500, 300), { links: true, rechts: false });
});

test("zichtbareGroepen: alleen groepen met minstens één toegestane view", () => {
    assert.deepEqual(zichtbareGroepen(["land", "instellingen-ticker"]).map(g => g.id), ["samenstelling", "instellingen"]);
    assert.deepEqual(zichtbareGroepen([]), []);
});

test("ontwikkelViews: box3 en verder alleen gemarkeerde views", () => {
    assert.ok(ontwikkelViews().some(v => v.view === "box3" && v.label === "Box 3"));
    const gemarkeerd = MENU_GROEPEN.flatMap(g => g.views).filter(v => v.inOntwikkeling).map(v => v.view);
    assert.deepEqual(ontwikkelViews().map(v => v.view), gemarkeerd);
});

test("uitgeschakeldeViews: standaard uit, aangezet niet meer", () => {
    assert.ok(uitgeschakeldeViews([]).includes("box3"));
    assert.deepEqual(uitgeschakeldeViews(["box3"]), []);
});

test("leesAanGezet: lege of kapotte opslag leest als []", () => {
    assert.equal(ONTWIKKEL_OPSLAG_SLEUTEL, "ontwikkelTabsAan");
    assert.deepEqual(leesAanGezet(null), []);
    assert.deepEqual(leesAanGezet(""), []);
    assert.deepEqual(leesAanGezet("{kapot"), []);
    assert.deepEqual(leesAanGezet('{"box3": true}'), []);
    assert.deepEqual(leesAanGezet('"box3"'), []);
    assert.deepEqual(leesAanGezet('["box3"]'), ["box3"]);
});

test("menuViews: uitgeschakelde ontwikkel-view blijft als grijze chip, andere niet-toegestane views vallen weg", () => {
    const overzicht = MENU_GROEPEN.find(g => g.id === "overzicht");
    const toegestaan = ["portfolio", "dividend"];
    const uit = menuViews(overzicht, toegestaan, ["box3"]);
    assert.deepEqual(uit.map(v => [v.view, v.uitgeschakeld]), [["portfolio", false], ["dividend", false], ["box3", true]]);

    const aan = menuViews(overzicht, [...toegestaan, "box3"], []);
    assert.equal(aan.find(v => v.view === "box3").uitgeschakeld, false);
    assert.ok(!aan.some(v => v.view === "transacties" || v.view === "statistieken"));
});

test("eersteView: slaat een uitgeschakelde ontwikkel-view over", () => {
    const uit = uitgeschakeldeViews([]);
    const toegestaan = ALLE_VIEWS.filter(v => !uit.includes(v));
    for (const groep of MENU_GROEPEN) {
        assert.ok(!uit.includes(eersteView(groep.id, toegestaan)), groep.id);
    }
    assert.equal(eersteView("overzicht", ["box3", "transacties"].filter(v => !uit.includes(v))), "transacties");
});

test("ontwikkelItems: eerst de views, dan de onderdelen, met labels", () => {
    const items = ontwikkelItems();
    assert.deepEqual(items.find(i => i.id === "box3"), { id: "box3", label: "Box 3" });
    assert.deepEqual(items.find(i => i.id === "rendement-pct"), { id: "rendement-pct", label: "Rendement in %" });
    assert.ok(items.findIndex(i => i.id === "box3") < items.findIndex(i => i.id === "rendement-pct"));
});

test("ONTWIKKEL_ONDERDELEN: geen id is ook een view-naam", () => {
    for (const { id } of ONTWIKKEL_ONDERDELEN) {
        assert.ok(!ALLE_VIEWS.includes(id), id);
    }
});

test("ontwikkelViews/uitgeschakeldeViews: onderdelen tellen niet mee", () => {
    assert.ok(!ontwikkelViews().some(v => v.view === "rendement-pct"));
    assert.ok(!uitgeschakeldeViews([]).includes("rendement-pct"));
    assert.deepEqual(uitgeschakeldeViews(["box3", "rendement-pct"]), []);
});

test("ontwikkelAan: alleen aan als het id in de lijst staat", () => {
    assert.equal(ontwikkelAan("rendement-pct", []), false);
    assert.equal(ontwikkelAan("rendement-pct", ["box3"]), false);
    assert.equal(ontwikkelAan("rendement-pct", ["rendement-pct"]), true);
});

test("een onbekend id in de opslag (oud onderdeel) wordt genegeerd", () => {
    const aan = leesAanGezet(JSON.stringify(["verdeling-over-tijd", "rendement-pct"]));
    assert.ok(!ontwikkelItems().some(i => i.id === "verdeling-over-tijd"));
    assert.deepEqual(uitgeschakeldeViews(aan), uitgeschakeldeViews(["rendement-pct"]));
    assert.equal(ontwikkelAan("rendement-pct", aan), true);
});

test("menuViews: een aangezet onderdeel verandert de chips niet", () => {
    const rendement = MENU_GROEPEN.find(g => g.id === "rendement");
    const toegestaan = ["rendement", "prognose", "prognose-huidig"];
    const zonder = menuViews(rendement, toegestaan, uitgeschakeldeViews([]));
    const met = menuViews(rendement, toegestaan, uitgeschakeldeViews(["rendement-pct"]));
    assert.deepEqual(met, zonder);
    assert.ok(met.every(v => !v.uitgeschakeld));
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

test("mobiel: fade-klassen van de subtabs gebruiken een masker (geen overlay die kliks blokkeert)", () => {
    assert.match(MOBIEL, /\.subTabs\.fadeRechts\s*\{[^}]*mask-image:\s*linear-gradient/);
    assert.match(MOBIEL, /\.subTabs\.fadeLinks\s*\{[^}]*mask-image:\s*linear-gradient/);
});

test("viewport-meta: apparaatbreedte en startschaal", () => {
    const meta = HTML.match(/<meta name="viewport"[^>]*>/)[0];
    assert.match(meta, /width=device-width/);
    assert.match(meta, /initial-scale=1/);
});
