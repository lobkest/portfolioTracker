// Controleert de script- en stylesheet-tags in de templates tegen de bestanden in static/, en de reset-lijst in app.js.
//   node --test tests/test_scripts.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const BASIS = path.join(__dirname, "..");
const JS_MAP = path.join(BASIS, "static", "js");

// Paden onder static/js, in laadvolgorde.
function scriptsIn(template) {
    const html = fs.readFileSync(path.join(BASIS, "templates", template), "utf8");
    return [...html.matchAll(/<script src="\{\{ url_for\('static', filename='js\/([^']+)'\) \}\}"><\/script>/g)].map(m => m[1]);
}

function bestandenIn(submap) {
    return fs.readdirSync(path.join(JS_MAP, submap)).filter(naam => naam.endsWith(".js")).map(naam => `${submap}/${naam}`);
}

// basis.html zit in beide pagina's.
const PAGINAS = {
    "start.html": [...scriptsIn("basis.html"), ...scriptsIn("start.html")],
    "portfolio.html": [...scriptsIn("basis.html"), ...scriptsIn("portfolio.html")],
};

test("elke pagina laadt scripts uit static/js", () => {
    for (const [pagina, scripts] of Object.entries(PAGINAS)) assert.ok(scripts.length > 0, pagina);
});

test("elk geladen script bestaat in static/js", () => {
    for (const [pagina, scripts] of Object.entries(PAGINAS)) {
        for (const script of scripts) {
            assert.ok(fs.existsSync(path.join(JS_MAP, script)), `${script} (in ${pagina}) bestaat niet`);
        }
    }
});

test("geen script wordt dubbel geladen", () => {
    for (const [pagina, scripts] of Object.entries(PAGINAS)) {
        const dubbel = scripts.filter((script, i) => scripts.indexOf(script) !== i);
        assert.deepEqual(dubbel, [], pagina);
    }
});

test("elk script-element in de templates gebruikt url_for of een cdnjs-adres", () => {
    for (const template of ["basis.html", "start.html", "portfolio.html"]) {
        const html = fs.readFileSync(path.join(BASIS, "templates", template), "utf8");
        const alle = [...html.matchAll(/<script\b[^>]*>/g)].length;
        const cdn = [...html.matchAll(/<script src="https:\/\/cdnjs\.cloudflare\.com\//g)].length;
        assert.equal(scriptsIn(template).length + cdn, alle, template);
    }
});

test("portfolio.html laadt elk bestand uit static/js/tabs en static/js/gedeeld", () => {
    for (const bestand of [...bestandenIn("tabs"), ...bestandenIn("gedeeld")]) {
        assert.ok(PAGINAS["portfolio.html"].includes(bestand), `${bestand} wordt niet geladen`);
    }
});

// app.js bouwt bij het laden TOON_PER_VIEW uit de toon-functies van de tabbladen.
test("portfolio.html laadt app.js als laatste, na gedeeld/ en tabs/", () => {
    const scripts = PAGINAS["portfolio.html"];
    assert.equal(scripts.at(-1), "app.js");
    const laatsteGedeeld = scripts.findLastIndex(s => s.startsWith("gedeeld/"));
    const eersteTab = scripts.findIndex(s => s.startsWith("tabs/"));
    assert.ok(laatsteGedeeld < eersteTab, "gedeeld/ hoort voor tabs/");
});

test("de startpagina laadt geen dashboard-scripts", () => {
    for (const script of PAGINAS["start.html"]) {
        assert.ok(!script.startsWith("tabs/") && !script.startsWith("gedeeld/") && script !== "app.js", script);
    }
});

// Tabbladen met toestand die bewust over portfolio's heen blijft staan (een weergavekeuze, geen data).
const BEWUST_ZONDER_RESET = {
    "land_sector.js": "taart/staaf-keuze",
    "bedrijven.js": "gekozen top-N; de grafiek wordt bij elk tekenen opnieuw gemaakt",
};

function leesTabs() {
    return bestandenIn("tabs").map(pad => ({ naam: path.basename(pad), code: fs.readFileSync(path.join(JS_MAP, pad), "utf8") }));
}

function resetLijst() {
    const appJs = fs.readFileSync(path.join(JS_MAP, "app.js"), "utf8");
    const blok = appJs.match(/const RESET_PER_TAB = \[([^\]]*)\]/);
    assert.ok(blok, "RESET_PER_TAB niet gevonden in app.js");
    return blok[1].split(",").map(naam => naam.trim()).filter(Boolean);
}

test("RESET_PER_TAB: elke genoemde functie bestaat in precies één tab-bestand, zonder parameters", () => {
    const tabs = leesTabs();
    for (const naam of resetLijst()) {
        const bestanden = tabs.filter(t => t.code.includes(`\nfunction ${naam}() {`));
        assert.equal(bestanden.length, 1, naam);
    }
});

test("RESET_PER_TAB: elk tab-bestand met eigen toestand heeft een reset in de lijst (of staat bewust apart)", () => {
    const lijst = resetLijst();
    for (const { naam, code } of leesTabs()) {
        const resets = [...code.matchAll(/^function (reset\w+)\(\) \{/gm)].map(m => m[1]);
        for (const reset of resets) assert.ok(lijst.includes(reset), `${reset} (${naam}) ontbreekt in RESET_PER_TAB`);
        const heeftToestand = /^let \w+/m.test(code);
        if (!heeftToestand || Object.hasOwn(BEWUST_ZONDER_RESET, naam)) continue;
        assert.ok(resets.length > 0, `${naam} heeft toestand maar geen reset-functie`);
    }
});

test("elk gelinkt CSS-bestand bestaat; basis.html (dus elke pagina) linkt er minstens één", () => {
    for (const template of ["basis.html", "start.html", "portfolio.html"]) {
        const html = fs.readFileSync(path.join(BASIS, "templates", template), "utf8");
        const links = [...html.matchAll(/<link rel="stylesheet" href="\{\{ url_for\('static', filename='([^']+)'\) \}\}">/g)].map(m => m[1]);
        for (const bestand of links) assert.ok(fs.existsSync(path.join(BASIS, "static", bestand)), `${bestand} (in ${template}) bestaat niet`);
        const alleLinks = [...html.matchAll(/<link rel="stylesheet"/g)].length;
        assert.equal(links.length, alleLinks, `${template}: stylesheet zonder url_for`);
        if (template === "basis.html") assert.ok(links.length > 0, "basis.html linkt geen CSS");
    }
});
