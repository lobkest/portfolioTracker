// Bewaakt dat de infoTip bij "Niet opslaan" (start.html) alle tabbladen uit VIEWS_MET_CODE (app.js) noemt.
//   node --test tests/test_niet_opslaan_info.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { MENU_GROEPEN } = require("../static/js/menu.js");

const BASIS = path.join(__dirname, "..");

function viewsMetCode() {
    const app = fs.readFileSync(path.join(BASIS, "static", "js", "app.js"), "utf8");
    const m = app.match(/const VIEWS_MET_CODE = \[([^\]]*)\]/);
    assert.ok(m, "VIEWS_MET_CODE niet gevonden in app.js");
    return [...m[1].matchAll(/"([^"]+)"/g)].map(x => x[1]);
}

function infoTipTekst() {
    const html = fs.readFileSync(path.join(BASIS, "templates", "start.html"), "utf8");
    const tips = [...html.matchAll(/<span class="infoTip" data-label="Niet opslaan">([^<]*)<\/span>/g)];
    assert.equal(tips.length, 1, "precies één infoTip met data-label=\"Niet opslaan\"");
    return tips[0][1];
}

function labelVanView(view) {
    const alleViews = MENU_GROEPEN.flatMap(g => g.views);
    const gevonden = alleViews.find(v => v.view === view);
    assert.ok(gevonden, `view ${view} staat niet in het menu`);
    return gevonden.label;
}

test("infoTip bij Niet opslaan noemt elk tabblad uit VIEWS_MET_CODE", () => {
    const tekst = infoTipTekst();
    const views = viewsMetCode();
    assert.ok(views.length > 0);
    for (const view of views) {
        const label = labelVanView(view);
        assert.ok(tekst.includes(label), `label "${label}" (view ${view}) ontbreekt in de infoTip`);
    }
});
