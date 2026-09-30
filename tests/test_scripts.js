// Controleert de script-tags in de templates tegen de bestanden in static/js.
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
