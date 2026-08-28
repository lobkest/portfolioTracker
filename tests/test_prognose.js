// Unit tests voor de rekenkern van het Prognose-tabblad (static/js/prognose.js).
// Draait via Node's ingebouwde testrunner, geen extra dependency nodig:
//   node --test tests/test_prognose.js
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const {
    maandRenteVanJaarPct,
    berekenPrognosePad,
    berekenGeinvesteerdPad,
    berekenPrognose,
    genereerToekomstDatums,
    valideerPrognoseInvoer,
    bouwPrognoseGrafiekData
} = require("../static/js/prognose.js");

const EPS = 1e-6;
function assertClose(actual, expected, epsilon = EPS, msg) {
    assert.ok(Math.abs(actual - expected) < epsilon, msg || `verwacht ${expected}, kreeg ${actual}`);
}

test("berekenPrognosePad: geen inleg, 1 jaar, 10% rendement, start 1000 -> 1100", () => {
    const pad = berekenPrognosePad(1000, 10, 1, 0, 0);
    assert.equal(pad.length, 13); // start + 12 maanden
    assertClose(pad[0], 1000);
    assertClose(pad[pad.length - 1], 1100);
});

test("berekenPrognosePad: jaarlijkse inleg 1000, 0% rendement, 3 jaar, start 0 -> 3000", () => {
    const pad = berekenPrognosePad(0, 0, 3, 1000, 0);
    assert.equal(pad.length, 37);
    assertClose(pad[pad.length - 1], 3000);
});

test("berekenPrognosePad: maandelijkse inleg 100, 0% rendement, 1 jaar, start 0 -> 1200", () => {
    const pad = berekenPrognosePad(0, 0, 1, 0, 100);
    assertClose(pad[pad.length - 1], 1200);
});

// Handmatige controle (na te rekenen, want maandelijkseInleg = 0 zodat de
// samengestelde groei per volledig jaar exact het jaarrendement oplevert):
//   jaar 1: 1000 * 1.10 = 1100, + jaarlijkse inleg 1000 = 2100
//   jaar 2: 2100 * 1.10 = 2310, + jaarlijkse inleg 1000 = 3310
test("berekenPrognosePad: gecombineerd, 10% rendement + jaarlijkse inleg 1000, 2 jaar, start 1000 -> 3310", () => {
    const pad = berekenPrognosePad(1000, 10, 2, 1000, 0);
    assertClose(pad[12], 2100, 1e-4);
    assertClose(pad[24], 3310, 1e-4);
});

test("berekenPrognosePad: 0 jaar vooruit geeft alleen het startpunt", () => {
    const pad = berekenPrognosePad(1000, 10, 0, 1000, 50);
    assert.deepEqual(pad, [1000]);
});

test("berekenGeinvesteerdPad: jaarlijks + maandelijks gecombineerd, 1 jaar", () => {
    // 500 start + 12 * 50 (maandelijks) + 1200 (jaarlijks bij maand 12) = 2300
    const pad = berekenGeinvesteerdPad(500, 1, 1200, 50);
    assertClose(pad[pad.length - 1], 2300);
});

test("berekenGeinvesteerdPad is lineair (geen rendement-effect)", () => {
    const pad = berekenGeinvesteerdPad(0, 1, 0, 100);
    assertClose(pad[6], 600);
    assertClose(pad[12], 1200);
});

test("berekenPrognose: laag/hoog gelijk aan midden -> band is een vlakke lijn (identiek aan midden)", () => {
    const resultaat = berekenPrognose({
        startWaarde: 1000,
        startGeinvesteerd: 500,
        jaren: 5,
        rendementPct: 6,
        laagPct: 6,
        hoogPct: 6,
        jaarlijkseInleg: 1000,
        maandelijkseInleg: 50
    });
    assert.deepEqual(resultaat.midden, resultaat.laag);
    assert.deepEqual(resultaat.midden, resultaat.hoog);
});

test("maandRenteVanJaarPct: 12x samengesteld geeft het jaarrendement terug", () => {
    const maandRente = maandRenteVanJaarPct(10);
    assertClose(Math.pow(1 + maandRente, 12), 1.10);
});

test("maandRenteVanJaarPct: 0% rendement -> 0% per maand", () => {
    assertClose(maandRenteVanJaarPct(0), 0);
});

test("genereerToekomstDatums: maandelijkse stappen na de startdatum", () => {
    assert.deepEqual(genereerToekomstDatums("2026-01-15", 2), ["2026-02-15", "2026-03-15"]);
});

test("genereerToekomstDatums: 0 maanden geeft een lege lijst", () => {
    assert.deepEqual(genereerToekomstDatums("2026-06-30", 0), []);
});

test("valideerPrognoseInvoer: geldige invoer geeft geen fouten of waarschuwing", () => {
    const r = valideerPrognoseInvoer({
        jaren: 10, rendementPct: 6, laagPct: 4, hoogPct: 10,
        jaarlijkseInleg: 1000, maandelijkseInleg: 0
    });
    assert.equal(r.geldig, true);
    assert.deepEqual(r.fouten, []);
    assert.equal(r.waarschuwing, null);
});

test("valideerPrognoseInvoer: rendement op de ondergrens (-20%) is nog geldig", () => {
    const r = valideerPrognoseInvoer({
        jaren: 10, rendementPct: -20, laagPct: -20, hoogPct: 10,
        jaarlijkseInleg: 0, maandelijkseInleg: 0
    });
    assert.equal(r.geldig, true);
});

test("valideerPrognoseInvoer: rendement buiten -20%..30% geeft een fout", () => {
    const r = valideerPrognoseInvoer({
        jaren: 10, rendementPct: -25, laagPct: -25, hoogPct: 10,
        jaarlijkseInleg: 0, maandelijkseInleg: 0
    });
    assert.equal(r.geldig, false);
    assert.ok(r.fouten.length > 0);
});

test("valideerPrognoseInvoer: lage kant >= hoge kant van de bandbreedte geeft een fout", () => {
    const r = valideerPrognoseInvoer({
        jaren: 10, rendementPct: 6, laagPct: 10, hoogPct: 4,
        jaarlijkseInleg: 0, maandelijkseInleg: 0
    });
    assert.equal(r.geldig, false);
});

test("valideerPrognoseInvoer: negatieve inleg geeft een fout", () => {
    const r = valideerPrognoseInvoer({
        jaren: 10, rendementPct: 6, laagPct: 4, hoogPct: 10,
        jaarlijkseInleg: -100, maandelijkseInleg: 0
    });
    assert.equal(r.geldig, false);
});

test("valideerPrognoseInvoer: rendement buiten eigen bandbreedte -> waarschuwing, geen fout", () => {
    const r = valideerPrognoseInvoer({
        jaren: 10, rendementPct: 15, laagPct: 4, hoogPct: 10,
        jaarlijkseInleg: 0, maandelijkseInleg: 0
    });
    assert.equal(r.geldig, true);
    assert.ok(r.waarschuwing);
});

// Regressietests voor de bug waarbij het Prognose-tabblad data van een eerder
// geopende portfolio (code "RNA") bleef tonen i.p.v. de actief geladen
// portfolio: bouwPrognoseGrafiekData nam voorheen zijn brondata uit de
// globale `huidigeData` i.p.v. als parameter, waardoor een niet-gereset
// prognoseResultaat in app.js data van de vorige portfolio kon hergebruiken.
// Deze tests bewijzen dat de functie zelf zuiver is: ze gebruikt precies de
// chart_data die wordt meegegeven, nooit een vaste/onthouden waarde.
const eenvoudigeInvoer = { jaren: 1, rendement: 0, laag: 0, hoog: 0, jaarlijks: 0, maandelijks: 0 };

function chartDataVoor(portfolio) {
    // Twee duidelijk verschillende, plausibele portfolio's (zoals twee echte
    // codes zouden zijn) om te bevestigen dat de output per portfolio verschilt.
    if (portfolio === "RNA") {
        return { labels: ["2024-01-01", "2024-02-01"], waarde: [1000, 1200], geinvesteerd: [1000, 1000] };
    }
    return { labels: ["2025-06-01", "2025-07-01"], waarde: [8000, 8500], geinvesteerd: [7000, 7200] };
}

function historischeWaardeReeks(resultaat) {
    return resultaat.datasets.find(ds => ds.label === "Waarde (€)").data;
}

function prognoseWaardeReeks(resultaat) {
    return resultaat.datasets.find(ds => ds.label === "Waarde — prognose (€)").data;
}

test("bouwPrognoseGrafiekData: gebruikt de meegegeven chart_data, niet een vaste/vorige waarde", () => {
    const resultaatRNA = bouwPrognoseGrafiekData(chartDataVoor("RNA"), eenvoudigeInvoer);
    const resultaatAndereCode = bouwPrognoseGrafiekData(chartDataVoor("ABC"), eenvoudigeInvoer);

    assert.notDeepEqual(resultaatRNA, resultaatAndereCode);

    const historischRNA = historischeWaardeReeks(resultaatRNA);
    assert.equal(historischRNA[historischRNA.length - 1].x, "2024-02-01");
    assert.equal(historischRNA[historischRNA.length - 1].y, 1200);

    const historischAndereCode = historischeWaardeReeks(resultaatAndereCode);
    assert.equal(historischAndereCode[historischAndereCode.length - 1].x, "2025-07-01");
    assert.equal(historischAndereCode[historischAndereCode.length - 1].y, 8500);
});

test("bouwPrognoseGrafiekData: prognosereeks sluit aan op het laatste historische punt van de meegegeven data", () => {
    const resultaat = bouwPrognoseGrafiekData(chartDataVoor("ABC"), eenvoudigeInvoer);
    const prognose = prognoseWaardeReeks(resultaat);
    // Eerste punt van de prognosereeks is het boundary-punt: zelfde datum en
    // waarde als het laatste historische punt van de meegegeven chart_data.
    assert.equal(prognose[0].x, "2025-07-01");
    assert.equal(prognose[0].y, 8500);
});

test("bouwPrognoseGrafiekData: twee aanroepen met dezelfde code/data geven identiek resultaat (determinisme, geen verborgen state)", () => {
    const a = bouwPrognoseGrafiekData(chartDataVoor("RNA"), eenvoudigeInvoer);
    const b = bouwPrognoseGrafiekData(chartDataVoor("RNA"), eenvoudigeInvoer);
    assert.deepEqual(a, b);
});
