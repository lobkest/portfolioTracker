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
    berekenDividendCumulatief,
    genereerToekomstDatums,
    valideerPrognoseInvoer,
    kiesHistorieHorizon,
    begrensRendement,
    historieVeldwaarden,
    historiePrognosePaden,
    bouwPrognoseGrafiekData,
    RENDEMENT_MIN_PCT,
    RENDEMENT_MAX_PCT,
    HISTORIE_MAX_HORIZON_JAREN,
    historieKwartielenTekst,
    historieMobielSubregel,
    historieMobielWaarde,
    historieMobielSubwaarde,
    dividendMobielSubregel,
    eigenDividendTekst,
    dividendMobielWaarde,
    dividendMobielSubwaarde,
    dividendTotaalTekst
} = require("../static/js/prognose.js");

test("kiesHistorieHorizon: afronden, begrenzen op maxHorizon, minimaal 1", () => {
    assert.equal(kiesHistorieHorizon(10, 9), 9);
    assert.equal(kiesHistorieHorizon(5, 9), 5);
    assert.equal(kiesHistorieHorizon(4.5, 9), 5);
    assert.equal(kiesHistorieHorizon(4.4, 9), 4);
    assert.equal(kiesHistorieHorizon(0, 9), 1);
    assert.equal(kiesHistorieHorizon(0.3, 9), 1);
    assert.equal(kiesHistorieHorizon(30, 1), 1);
    assert.equal(kiesHistorieHorizon(NaN, 9), 1);
});

test("kiesHistorieHorizon: zonder maxHorizon is de bovengrens HISTORIE_MAX_HORIZON_JAREN (60), niet de historielengte", () => {
    assert.equal(HISTORIE_MAX_HORIZON_JAREN, 60);
    assert.equal(kiesHistorieHorizon(25), 25);
    assert.equal(kiesHistorieHorizon(60), 60);
    assert.equal(kiesHistorieHorizon(75), 60);
    assert.equal(kiesHistorieHorizon(0), 1);
});

test("begrensRendement: binnen de grenzen ongewijzigd, daarbuiten afgekapt", () => {
    assert.deepEqual(begrensRendement(7.5), { waarde: 7.5, afgekapt: false });
    assert.deepEqual(begrensRendement(RENDEMENT_MAX_PCT), { waarde: RENDEMENT_MAX_PCT, afgekapt: false });
    assert.deepEqual(begrensRendement(45.2), { waarde: RENDEMENT_MAX_PCT, afgekapt: true });
    assert.deepEqual(begrensRendement(-35), { waarde: RENDEMENT_MIN_PCT, afgekapt: true });
});

test("historieVeldwaarden: p50/p10/p90 naar de velden, met wat er is afgekapt", () => {
    const r = historieVeldwaarden({ p10: -3.1, p25: 8, p50: 18.2, p75: 25, p90: 41.0 });
    assert.deepEqual(r.waarden, { rendement: 18.2, laag: -3.1, hoog: RENDEMENT_MAX_PCT });
    assert.deepEqual(r.afgekapt, [{ veld: "hoog", historisch: 41.0, begrensd: RENDEMENT_MAX_PCT }]);
    assert.deepEqual(historieVeldwaarden({ p10: 2, p25: 4, p50: 6, p75: 8, p90: 9 }).afgekapt, []);
});

// Groeifactoren voor maand 0..36: p10 daalt 1% per maand, p90 stijgt 2% per maand, de rest ertussen.
function testPaden() {
    const maanden = Array.from({ length: 37 }, (_, m) => m);
    return {
        p10: maanden.map(m => 1 - 0.01 * m),
        p25: maanden.map(() => 1),
        p50: maanden.map(m => 1 + 0.005 * m),
        p75: maanden.map(m => 1 + 0.01 * m),
        p90: maanden.map(m => 1 + 0.02 * m)
    };
}

test("historiePrognosePaden: startwaarde x groeifactor, afgekapt op het aantal jaren", () => {
    const r = historiePrognosePaden(testPaden(), 1000, 2);
    assert.deepEqual(Object.keys(r), ["p10", "p25", "p50", "p75", "p90"]);
    Object.values(r).forEach(pad => assert.equal(pad.length, 25));
    assert.equal(r.p50[0], 1000);
    assertClose(r.p50[24], 1120);
    assertClose(r.p10[12], 880);
    assertClose(r.p90[24], 1480);
    assert.equal(historiePrognosePaden(testPaden(), 1000, 0).p90.length, 1);
});

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
// prognoseResultaat in tabs/prognose.js data van de vorige portfolio kon hergebruiken.
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

test("berekenDividendCumulatief: yield 0 geeft alleen nullen", () => {
    assert.deepEqual(berekenDividendCumulatief([1000, 1100, 1200], 0), [0, 0, 0]);
});

test("berekenDividendCumulatief: vaste waarde 1000 met 12% yield geeft na 12 maanden 120", () => {
    const cumulatief = berekenDividendCumulatief(new Array(13).fill(1000), 0.12);
    assert.equal(cumulatief.length, 13);
    assertClose(cumulatief[1], 10);
    assertClose(cumulatief[12], 120);
});

test("bouwPrognoseGrafiekData: zonder dividendYield blijft het aantal datasets gelijk, met precies één extra", () => {
    const zonder = bouwPrognoseGrafiekData(chartDataVoor("ABC"), eenvoudigeInvoer);
    assert.equal(zonder.datasets.length, 6);
    const met = bouwPrognoseGrafiekData(chartDataVoor("ABC"), { ...eenvoudigeInvoer, dividendYield: 0.12 });
    assert.equal(met.datasets.length, 7);
    const lijn = met.datasets.find(ds => ds.label === "Waarde + verwacht dividend, niet herbelegd (€)");
    // 0% koersrendement: waarde blijft 8500, plus 12 x 85 dividend.
    assert.equal(lijn.data[0].y, 8500);
    assertClose(lijn.data[12].y, 8500 + 1020);
    assert.deepEqual(met.datasets.filter(ds => ds !== lijn), zonder.datasets);
});

test("valideerPrognoseInvoer: zonder inlegvelden (metInleg false) zijn ontbrekende inleggen geen fout", () => {
    const invoer = { jaren: 10, rendementPct: 6, laagPct: 4, hoogPct: 10 };
    assert.equal(valideerPrognoseInvoer(invoer).geldig, false);
    const r = valideerPrognoseInvoer(invoer, { metInleg: false });
    assert.equal(r.geldig, true);
    assert.deepEqual(r.fouten, []);
    assert.equal(valideerPrognoseInvoer({ ...invoer, jaren: -1 }, { metInleg: false }).geldig, false);
});

test("bouwPrognoseGrafiekData: met paden middenlijn p50 en twee banden (p10–p90, p25–p75)", () => {
    const invoer = { ...eenvoudigeInvoer, jaren: 2, paden: testPaden() };
    const { datasets } = bouwPrognoseGrafiekData(chartDataVoor("ABC"), invoer);
    const per = label => datasets.find(ds => ds.label === label);
    assert.equal(datasets.length, 8);
    const midden = per("Waarde — prognose (€)");
    assert.equal(midden.data.length, 25);
    assert.equal(midden.data[0].y, 8500);
    assertClose(midden.data[24].y, 8500 * 1.12);
    assertClose(per("Bandbreedte p10–p90").data[12].y, 8500 * 0.88);
    assertClose(per("Bandbreedte p10–p90 (hoog)").data[24].y, 8500 * 1.48);
    assert.equal(per("Bandbreedte p10–p90").fill, "-1");
    assert.equal(per("Waarschijnlijk p25–p75").data[24].y, 8500);
    assertClose(per("Waarschijnlijk p25–p75 (hoog)").data[24].y, 8500 * 1.24);
    assert.equal(per("Waarschijnlijk p25–p75").fill, "-1");
    // De bovenrand staat direct vóór zijn onderrand, anders vult "-1" de verkeerde ruimte.
    const i = datasets.indexOf(per("Waarschijnlijk p25–p75"));
    assert.equal(datasets[i - 1].label, "Waarschijnlijk p25–p75 (hoog)");
    assert.equal(per("Bandbreedte laag–hoog"), undefined);
});

test("bouwPrognoseGrafiekData: met paden rekent de dividendlijn over p50", () => {
    const invoer = { ...eenvoudigeInvoer, jaren: 1, paden: testPaden(), dividendYield: 0.12 };
    const { datasets } = bouwPrognoseGrafiekData(chartDataVoor("ABC"), invoer);
    const lijn = datasets.find(ds => ds.label === "Waarde + verwacht dividend, niet herbelegd (€)");
    // Maand 1: p50 = 8500 x 1,005 plus 1% dividend over de startwaarde 8500.
    assertClose(lijn.data[1].y, 8500 * 1.005 + 85);
});

test("historieKwartielenTekst: korte p25–p75-regel", () => {
    assert.equal(historieKwartielenTekst(10.94, 15.7, 10), "Waarschijnlijk 10,9–15,7% per jaar (historie, 10 jaar)");
});

const telendePositie = {
    status: "ok", gewicht: 0.123, beschikbare_jaren: 8.5, historie_vanaf: 2016,
    cagr_pct: 9.84, laag_1j_pct: -12.3, hoog_1j_pct: 28,
};

test("historie mobiel: positie die meetelt", () => {
    assert.equal(historieMobielSubregel(telendePositie), "12,3% · 8,5 jaar data (vanaf 2016)");
    assert.equal(historieMobielWaarde(telendePositie), "9,8%/jaar");
    assert.equal(historieMobielSubwaarde(telendePositie), "1j: -12,3% – 28%");
});

test("historie mobiel: positie die niet meetelt toont korte status en geen range", () => {
    const p = { ...telendePositie, status: "te_kort", beschikbare_jaren: 0.4, historie_vanaf: 2026, cagr_pct: null };
    assert.equal(historieMobielSubregel(p), "12,3% · 0,4 jaar data (vanaf 2026)");
    assert.equal(historieMobielWaarde(p), "telt niet mee");
    assert.equal(historieMobielSubwaarde(p), "");
});

test("historie mobiel: zonder koershistorie, gewicht of jaren", () => {
    const p = { status: "geen_koers", gewicht: null, beschikbare_jaren: null, historie_vanaf: null, laag_1j_pct: null };
    assert.equal(historieMobielSubregel(p), "—");
    assert.equal(historieMobielWaarde(p), "geen koershistorie");
    assert.equal(historieMobielWaarde({ status: "iets_nieuws" }), "iets_nieuws");
});

test("dividend mobiel: meegetelde positie", () => {
    const p = { aantal: 12.5, per_aandeel_jaar: 0.85, valuta: "USD", meegeteld: true, netto_eur_jaar: 8.4, belasting_fractie: 0.15 };
    assert.equal(dividendMobielSubregel(p), "12,5 st × 0,85 USD/jaar");
    assert.equal(dividendMobielWaarde(p), "€8,40");
    assert.equal(dividendMobielSubwaarde(p), "bronbelasting 15%");
});

test("dividend mobiel: niet meegeteld en zonder dividend per aandeel", () => {
    const p = { aantal: 3, per_aandeel_jaar: null, valuta: null, meegeteld: false, netto_eur_jaar: null, belasting_fractie: 0 };
    assert.equal(dividendMobielSubregel(p), "3 st");
    assert.equal(dividendMobielWaarde(p), "niet meegeteld");
    assert.equal(dividendMobielSubwaarde(p), "bronbelasting 0%");
});

test("dividend mobiel: 60 st × dividend per aandeel per jaar", () => {
    assert.equal(dividendMobielSubregel({ aantal: 60, per_aandeel_jaar: 1.4852, valuta: "EUR" }), "60 st × 1,4852 EUR/jaar");
    assert.equal(dividendMobielSubregel({ aantal: 60, per_aandeel_jaar: 1.4852, valuta: null }), "60 st × 1,4852/jaar");
});

test("eigenDividendTekst: bruto, aantal uitkeringen en afwijking per aandeel t.o.v. Yahoo", () => {
    const p = { eigen_bruto_eur_jaar: 18.05, eigen_aantal_uitkeringen: 2, afwijking_fractie: 0 };
    assert.equal(eigenDividendTekst(p), "€18,05 bruto (2 uitk.), per aandeel 0% afwijking t.o.v. Yahoo");
    assert.equal(eigenDividendTekst({ ...p, afwijking_fractie: 0.052 }), "€18,05 bruto (2 uitk.), per aandeel +5,2% afwijking t.o.v. Yahoo");
    assert.equal(eigenDividendTekst({ ...p, afwijking_fractie: -0.1 }), "€18,05 bruto (2 uitk.), per aandeel -10% afwijking t.o.v. Yahoo");
    assert.equal(eigenDividendTekst({ ...p, afwijking_fractie: null }), "€18,05 bruto (2 uitk.)");
    assert.equal(eigenDividendTekst({ eigen_bruto_eur_jaar: null }), "—");
});

test("dividendTotaalTekst: totaal, yield en noot zonder rekeningoverzicht", () => {
    assert.deepEqual(
        dividendTotaalTekst({ totaal_netto_eur_jaar: 123.4, yield_netto: 0.0123, huidige_waarde_eur: 10000, eigen_data: false }),
        { hoofd: "Totaal netto €123,40 per jaar · netto yield 1,23% van €10.000,00", noot: "Geen rekeningoverzicht geüpload: geen vergelijking met eigen dividend." });
    assert.deepEqual(
        dividendTotaalTekst({ totaal_netto_eur_jaar: 0, yield_netto: null, huidige_waarde_eur: 0, eigen_data: true }),
        { hoofd: "Totaal netto €0,00 per jaar", noot: "" });
});
