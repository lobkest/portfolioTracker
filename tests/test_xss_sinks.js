// Waakhond: in static/js mag HTML alleen een lege of vaste tekst zijn, nooit data.
//   node --test tests/test_xss_sinks.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

const JS_MAP = path.join(__dirname, "..", "static", "js");

const VASTE_TEKST = /^\s*(?:"[^"\\\n]*"|'[^'\\\n]*'|`[^`$\\]*`)\s*$/;
const TOEWIJZING = /\.(?:innerHTML|outerHTML)\s*\+?(?<![=!<>])=(?!=)([^;]*);/g;
const INSERT = /\.insertAdjacentHTML\(\s*[^,()]+,([^;]*)\)\s*;/g;
const ANDERE_SINKS = /\bdocument\.write(?:ln)?\s*\(|\beval\s*\(|\bnew\s+Function\s*\(/g;

function jsBestanden(map) {
    return fs.readdirSync(map, { withFileTypes: true }).flatMap(item => {
        const pad = path.join(map, item.name);
        if (item.isDirectory()) return jsBestanden(pad);
        return item.name.endsWith(".js") ? [pad] : [];
    });
}

function onveiligeSinks(bron) {
    const gevonden = [];
    for (const m of bron.matchAll(TOEWIJZING)) if (!VASTE_TEKST.test(m[1])) gevonden.push(m[0]);
    for (const m of bron.matchAll(INSERT)) if (!VASTE_TEKST.test(m[1])) gevonden.push(m[0]);
    for (const m of bron.matchAll(ANDERE_SINKS)) gevonden.push(m[0]);
    return gevonden;
}

test("static/js heeft geen innerHTML/outerHTML/insertAdjacentHTML met data", () => {
    const bestanden = jsBestanden(JS_MAP);
    assert.ok(bestanden.length > 10);
    for (const bestand of bestanden) {
        const gevonden = onveiligeSinks(fs.readFileSync(bestand, "utf8"));
        assert.deepEqual(gevonden, [], `${path.relative(JS_MAP, bestand)}: HTML uit niet-vaste tekst`);
    }
});

test("de waakhond herkent veilige en onveilige toewijzingen", () => {
    assert.deepEqual(onveiligeSinks('el.innerHTML = "";'), []);
    assert.deepEqual(onveiligeSinks("el.innerHTML = '<option value=\"\">Geen</option>';"), []);
    assert.deepEqual(onveiligeSinks("el.innerHTML = `vast`;"), []);
    assert.deepEqual(onveiligeSinks("if (el.innerHTML === x) {}"), []);
    assert.equal(onveiligeSinks("el.innerHTML = naam;").length, 1);
    assert.equal(onveiligeSinks("el.innerHTML += '<b>' + naam;").length, 1);
    assert.equal(onveiligeSinks("el.innerHTML = `<b>${naam}</b>`;").length, 1);
    assert.equal(onveiligeSinks("el.outerHTML = naam;").length, 1);
    assert.equal(onveiligeSinks("el.insertAdjacentHTML('beforeend', naam);").length, 1);
    assert.deepEqual(onveiligeSinks("el.insertAdjacentHTML('beforeend', '<hr>');"), []);
    assert.equal(onveiligeSinks("document.write(x)").length, 1);
});
